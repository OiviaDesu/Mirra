"""Own backend identities and translate only this broker's session handles."""
import base64
import time

from transport import EndpointError
from wire import platform_ltk

SESSION_BASE = 0x6F000000
SESSION_END = 0x70000000
MAX_SESSIONS = 16  # The endpoint's isolated-session limit.
MAX_RESET_SECONDS = 10  # Leave room inside the shim's 15-second socket timeout.
NO_MEMORY = -536870211  # kIOReturnNoMemory (0xe00002bd)
UNAVAIWORKSPACELE = -536870212  # kIOReturnError (0xe00002bc)


class AksBroker:
    """Calls are serialized by the socket server, including health and cleanup."""
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.identities = {}
        self.classes = {}
        self.sessions = {}
        self.counter = 0
        self.wire_keys = {}
        self.failure = None

    def reset_owned_sessions(self):
        # One client request, one overall endpoint timeout, at most 16 actual
        # handles. Never guess handles or reset the phone's system keybags.
        deadline = time.monotonic() + min(self.endpoint.timeout, MAX_RESET_SECONDS)
        first_error = 0
        count = 0
        for virtual, real in list(self.sessions.items()):
            try:
                response = self.endpoint.call('reset', session=real, data='', _deadline=deadline)
            except EndpointError:
                self.failure = self.failure or 'owned_session_reset_transport_failed'
                return {'status': UNAVAIWORKSPACELE, 'error': 'owned_session_reset_transport_failed',
                        'reset_count': count, 'remaining_sessions': len(self.sessions)}
            status = response.get('status', UNAVAIWORKSPACELE)
            if type(status) is not int:
                status = UNAVAIWORKSPACELE
            if status == 0:
                self.sessions.pop(virtual)
                count += 1
            elif not first_error:
                first_error = status
        if first_error:
            self.failure = self.failure or 'owned_session_reset_failed'
        return {'status': first_error, 'reset_count': count, 'remaining_sessions': len(self.sessions)}

    def execute(self, request):
        operation = request.get('operation')
        if operation == 'health':
            if self.endpoint.process.poll() is not None:
                self.failure = self.failure or 'endpoint_exited'
            alive = self.failure is None
            if alive and self.classes:
                identity = next(iter(self.classes.values()))['identity']
                try:
                    status = self.endpoint.call('validate', identity=identity).get('status')
                    if type(status) is not int or status != 0:
                        self.failure = 'identity_validation_failed'
                except EndpointError:
                    # The transport has abandoned this command stream. Report a
                    # confirmed failure, instead of dropping the health reply.
                    self.failure = 'endpoint_transport_failed'
                alive = self.failure is None
            response = {'ready': alive, 'identities': len(self.identities), 'sessions': len(self.sessions),
                        'capabilities': ['reset_owned_sessions']}
            if self.failure:
                response['error'] = self.failure
            return response
        if operation == 'reset_owned_sessions':
            return self.reset_owned_sessions()
        if self.failure:
            return {'status': UNAVAIWORKSPACELE, 'error': 'broker_requires_restart'}
        if operation == 'generate':
            key_class = request.get('key_class', 9)
            if type(key_class) is not int or key_class not in (9, 10, 11):
                return {'error': 'invalid_key_class'}
            if key_class not in self.classes:
                response = self.endpoint.call('generate', attested=True, key_class=key_class)
                if response.get('status') != 0:
                    return response
                wire, _proof = platform_ltk(response['ltk'], 3)
                self.wire_keys[key_class] = wire
                self.identities[wire] = response['identity']
                self.classes[key_class] = response
                self.identities[response['ltk']] = response['identity']
            response = self.classes[key_class]
            wire = self.wire_keys[key_class]
            return {'status': 0, 'data': wire, 'bytes': len(base64.b64decode(wire))}
        if operation in {'validate', 'sign_remote', 'session_signed'}:
            identity = self.identities.get(request.get('local_ltk'))
            if identity is None:
                return {'error': 'local_key_not_owned'}
            if operation == 'session_signed':
                if self.counter >= SESSION_END - SESSION_BASE - 1:
                    return {'status': UNAVAIWORKSPACELE, 'error': 'session_namespace_exhausted'}
                if len(self.sessions) >= MAX_SESSIONS:
                    return {'status': NO_MEMORY, 'error': 'owned_session_limit'}
            fields = {'identity': identity}
            if operation != 'validate':
                fields.update(remote_ltk=request.get('remote_ltk', ''),
                              remote_key_class=request.get('remote_key_class', 9),
                              flags=request.get('flags', 0), parameters=request.get('parameters', ''))
            response = dict(self.endpoint.call(operation, **fields))
            if operation == 'session_signed' and response.get('status') == 0:
                real = response.get('session')
                if type(real) is not int or real < 0 or real in self.sessions.values():
                    self.failure = 'invalid_session_handle'
                    raise EndpointError('Backend returned an invalid or duplicate session handle')
                self.counter += 1
                virtual = SESSION_BASE + self.counter
                self.sessions[virtual] = real
                response['session'] = virtual
            return response
        if operation in {'step', 'token', 'confirm', 'setup', 'reset'}:
            virtual = request.get('session')
            if type(virtual) is not int or virtual not in self.sessions:
                return {'error': 'session_not_owned'}
            response = self.endpoint.call(operation, session=self.sessions[virtual], data=request.get('data', ''))
            if operation == 'reset' and response.get('status') == 0:
                self.sessions.pop(virtual)
            return response
        if operation in {'get_state', 'drop'}:
            return self.endpoint.call(operation, data=request.get('data', ''))
        return {'error': 'unknown_operation'}
