"""Check only the configured bridge before opening Apple's Mirroring app."""
from activity import emit
from readiness import observe


def check(service, *, observer=observe):
    """Observe live bridge/launch health without changing the service.

    Apple's app owns target discovery and radio eligibility. Do not query them
    here: a permission-restricted radio reading can appear to be powered off.
    """
    report = observer(service)
    emit('Connection check', 'bridge result', state=report['state'])
    if report['state'] != 'ready':
        return {'state': 'blocked', 'title': 'Your setup phone isn’t ready',
                'message': report['detail'], 'report': report}
    return {'state': 'ready', 'report': report,
            'mirroring_connection_verified': None}
