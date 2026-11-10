import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from bridgepaths import ENVIRONMENT_SCRIPT, helper_command
from provisioning import RemoteSetupError, remote_helper
from transport import Endpoint


class BridgePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Mirra path test ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'; self.bin.mkdir()
        self.bundle = self.root / 'bundle'; self.bundle.mkdir()
        self.data = self.root / 'data'; self.data.mkdir()
        self.env = {**os.environ, 'PATH': str(self.bin) + ':/usr/bin:/bin',
                    'MIRRA_TEST_BUNDLE': str(self.bundle), 'MIRRA_TEST_DATA': str(self.data)}
        self.tool('jbroot', '''import os,sys
from pathlib import Path
p = sys.argv[1]
base = os.environ['MIRRA_TEST_DATA'] if p == '/var' or p.startswith('/var/') else os.environ['MIRRA_TEST_BUNDLE']
print(str(Path(base) / p.lstrip('/')))
''')
        self.tool('rootfs', 'import sys\nprint("/rootfs" + sys.argv[1])\n')
        # RootHide's bootstrap shell applies vroot to exec(), but it leaves
        # arguments passed to a native binary intact. Model that boundary
        # separately from the jbroot command. A normal host shell alone would
        # hide a second translation of an already-native executable path.
        self.tool('sh', '''import os,sys
from pathlib import Path
args = sys.argv[1:]
if len(args) > 3 and args[2] == 'mirra-helper':
    p = args[3]
    base = os.environ['MIRRA_TEST_DATA'] if p == '/var' or p.startswith('/var/') else os.environ['MIRRA_TEST_BUNDLE']
    args[3] = str(Path(base) / p.lstrip('/'))
os.execve('/bin/sh', ['/bin/sh', *args], os.environ)
''')

    def tool(self, name, body):
        path = self.bin / name
        path.write_text('#!' + sys.executable + '\n' + body)
        path.chmod(0o700)
        return path

    def helper(self, body, suffix="helper with 'quotes' $(touch unwanted)"):
        virtual = '/usr/local/libexec/Mirra/' + suffix
        physical = self.bundle / virtual.lstrip('/')
        physical.parent.mkdir(parents=True, exist_ok=True)
        physical.write_text('#!' + sys.executable + '\n' + body)
        physical.chmod(0o700)
        return virtual, physical

    def run_command(self, command, data=None):
        return subprocess.run(command, input=data, capture_output=True, env=self.env, timeout=5)

    def test_environment_uses_working_converters_without_recording_random_root(self):
        reply = self.run_command(['/bin/sh', '-c', ENVIRONMENT_SCRIPT])
        self.assertEqual(reply.returncode, 0, reply.stderr)
        report = json.loads(reply.stdout)
        self.assertEqual(report, {'path_mode': 'roothide', 'path_translation_verified': True})
        self.assertNotIn(str(self.root), reply.stdout.decode())

    def test_partial_roothide_install_does_not_fall_back_to_cache_deployment(self):
        (self.bin / 'rootfs').unlink()
        reply = self.run_command(['/bin/sh', '-c', ENVIRONMENT_SCRIPT])
        self.assertEqual(reply.returncode, 1)
        self.assertEqual(json.loads(reply.stdout)['error'], 'roothide_path_translation_unavailable')

    def test_traditional_rootless_and_rootful_filesystem_detection(self):
        (self.bin / 'jbroot').unlink()
        (self.bin / 'rootfs').unlink()
        bootstrap = self.root / 'bootstrap with spaces'
        script = ENVIRONMENT_SCRIPT.replace('/var/jb', shlex.quote(str(bootstrap)))
        reply = self.run_command(['/bin/sh', '-c', script])
        self.assertEqual(reply.returncode, 0, reply.stderr)
        self.assertNotIn('rootless', json.loads(reply.stdout))
        (bootstrap / 'usr/bin').mkdir(parents=True)
        reply = self.run_command(['/bin/sh', '-c', script])
        self.assertEqual(reply.returncode, 0, reply.stderr)
        self.assertEqual(json.loads(reply.stdout), {'path_mode': 'native',
            'path_translation_verified': False, 'rootless': True})

    def test_roothide_converters_take_priority_over_var_jb(self):
        bootstrap = self.root / 'bootstrap'
        (bootstrap / 'usr/bin').mkdir(parents=True)
        script = ENVIRONMENT_SCRIPT.replace('/var/jb', shlex.quote(str(bootstrap)))
        reply = self.run_command(['/bin/sh', '-c', script])
        self.assertEqual(reply.returncode, 0, reply.stderr)
        self.assertEqual(json.loads(reply.stdout)['path_mode'], 'roothide')

    def test_failed_or_invalid_converters_produce_a_specific_error(self):
        for body in ('import sys\nsys.exit(3)\n', 'print("not-an-absolute-root")\n', 'print("/")\n'):
            with self.subTest(body=body):
                self.tool('jbroot', body)
                reply = self.run_command(['/bin/sh', '-c', ENVIRONMENT_SCRIPT])
                self.assertEqual(reply.returncode, 1)
                self.assertEqual(json.loads(reply.stdout)['error'], 'roothide_path_translation_unavailable')

    def test_native_launch_keeps_the_existing_argv(self):
        args = ['/var/mobile/endpoint with spaces', '/var/mobile/certificate.der']
        self.assertEqual(helper_command(args), args)

    def test_capability_launch_uses_bootstrap_execution_and_keeps_json_stdout(self):
        virtual, physical = self.helper('import json,sys\nprint(json.dumps(sys.argv))\n')
        reply = self.run_command(helper_command([virtual, '--capabilities'], 'roothide', observed=True))
        self.assertEqual(reply.returncode, 0, reply.stderr)
        self.assertEqual(json.loads(reply.stdout), [str(physical), '--capabilities'])
        events = [json.loads(line[len('MIRRA_HELPER '):]) for line in reply.stderr.decode().splitlines()
                  if line.startswith('MIRRA_HELPER ')]
        self.assertEqual(events[-1], {'stage': 'capabilities', 'event': 'exit', 'exit_status': 0})
        self.assertFalse((self.root / 'unwanted').exists())

    def test_native_executable_path_is_not_translated_twice_by_bootstrap(self):
        virtual, _ = self.helper('print("ready")\n')
        wrong = ['sh', '-c', 'mirra_program=$(jbroot "$1"); exec "$mirra_program" "$2"',
                 'mirra-helper', virtual, '--capabilities']
        failed = self.run_command(wrong)
        self.assertNotEqual(failed.returncode, 0)
        correct = self.run_command(helper_command([virtual, '--capabilities'], 'roothide'))
        self.assertEqual(correct.returncode, 0, correct.stderr)
        self.assertEqual(correct.stdout, b'ready\n')

    def test_certificate_filename_is_native_while_flag_and_bytes_are_preserved(self):
        virtual, physical = self.helper('''import hashlib,json,sys
from pathlib import Path
data = sys.stdin.buffer.read()
Path(sys.argv[2]).write_bytes(data)
print(json.dumps({'argv':sys.argv, 'sha256':hashlib.sha256(data).hexdigest()}))
''')
        certificate = "/var/mobile/cert with 'quotes' $(touch unwanted).der"
        target = self.data / certificate.lstrip('/'); target.parent.mkdir(parents=True)
        payload = bytes(range(256)) + b'\0\r\nfixture certificate'
        reply = self.run_command(helper_command([virtual, '--export-certificate', certificate],
                                               'roothide', observed=True), payload)
        self.assertEqual(reply.returncode, 0, reply.stderr)
        self.assertEqual(target.read_bytes(), payload)
        report = json.loads(reply.stdout)
        self.assertEqual(report['argv'], [str(physical), '--export-certificate', str(target)])
        self.assertEqual(report['sha256'], hashlib.sha256(payload).hexdigest())

    def test_live_endpoint_preserves_binding_request_stream_and_new_jailbreak_root(self):
        certificate = '/var/mobile/certificate.der'
        payload = b'fixture binding bytes'
        target = self.data / certificate.lstrip('/'); target.parent.mkdir(parents=True); target.write_bytes(payload)
        virtual, physical = self.helper('''import hashlib,json,sys
from pathlib import Path
print(json.dumps({'ready':True,'protocol_version':1,'dcrt_sha256':hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest()}),flush=True)
for line in sys.stdin:
    request=json.loads(line)
    if request['operation']=='exit':
        print('{"closed":true}',flush=True); break
    print(json.dumps({'status':0,'echo':request}),flush=True)
''')
        digest = hashlib.sha256(payload).hexdigest()
        args = helper_command([virtual, certificate], 'roothide')
        # Exercise the real transport against the launched child. This protects
        # stdout framing and certificate binding, not just generated strings.
        with patch.dict(os.environ, self.env):
            with Endpoint(command=args, timeout=2, expected_certificate_sha256=digest) as endpoint:
                result = endpoint.call('sign', payload='literal $() and quotes')
                self.assertEqual(result['echo']['payload'], 'literal $() and quotes')
        moved = self.root / 'new jailbreak bundle'; self.bundle.rename(moved)
        self.env['MIRRA_TEST_BUNDLE'] = str(moved)
        with patch.dict(os.environ, self.env):
            with Endpoint(command=args, timeout=2, expected_certificate_sha256=digest) as endpoint:
                self.assertTrue(endpoint.ready['ready'])

    def test_actual_child_signal_is_captured_instead_of_ssh_handshake_noise(self):
        virtual, _ = self.helper('import os,signal\nos.kill(os.getpid(),signal.SIGKILL)\n')
        class LocalConnection:
            path_mode = 'roothide'
            def ssh_command(self, arguments):
                # Preserve the SSH remote-shell quoting boundary without
                # contacting any device or using access credentials.
                return ['/bin/sh', '-c', shlex.join(arguments)]
        with patch.dict(os.environ, self.env), self.assertRaises(RemoteSetupError) as caught:
            remote_helper(LocalConnection(), [virtual, '--capabilities'])
        error = caught.exception
        self.assertEqual(error.code, 'helper_terminated')
        self.assertEqual(error.details['remote_exit_status'], 137)
        self.assertEqual(error.details['helper_stage'], 'capabilities')
        self.assertIn('exit_status', str(error))


if __name__ == '__main__':
    unittest.main()
