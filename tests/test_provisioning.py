import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from provisioning import new_profile, pin_host, fingerprints, authorize, deploy, exclusive_write, RemoteSetupError, signing_tool, remote_helper
from bridgepaths import ENVIRONMENT_SCRIPT, ROOTHIDE_ENTITLEMENTS
from compiler import write_link_stubs
from configuration import load_config
from wire import wrap


def fixture_ltk():
    # Structurally valid test data, not a device key or certificate.
    return base64.b64encode(wrap(0x30, b''.join(wrap(tag, bytes([tag]) * size)
        for tag, size in ((0x81, 1), (0x85, 600), (0x86, 32), (0x82, 32), (0x83, 32), (0x84, 32))))).decode()


class ProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / 'profile.json'
        self.path.write_text('{}')
        self.config = replace(load_config(self.path, root=self.root), transport='usb', product_type='iPhone11,2')
        self.line = b'[127.0.0.1]:2222 ssh-ed25519 ' + base64.b64encode(b'host-key-test-fixture') + b'\n'
        self.fingerprint = fingerprints(self.line)[0][0]

    def test_host_key_mismatch_does_not_save_a_pin(self):
        reply = subprocess.CompletedProcess([], 0, self.line, b'')
        with patch('provisioning.subprocess.run', return_value=reply):
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                pin_host(self.config, 'SHA256:' + 'A' * 43)
        self.assertFalse(self.config.known_hosts_file.exists())

    def test_verified_pin_is_saved_once_and_not_overwritten(self):
        reply = subprocess.CompletedProcess([], 0, self.line, b'')
        with patch('provisioning.subprocess.run', return_value=reply):
            self.assertTrue(pin_host(self.config, self.fingerprint)['host_key_verified'])
        before = self.config.known_hosts_file.read_bytes()
        with patch('provisioning.subprocess.run') as command:
            with self.assertRaises(FileExistsError):
                pin_host(self.config, self.fingerprint)
            command.assert_not_called()
        self.assertEqual(self.config.known_hosts_file.read_bytes(), before)

    def test_enrollment_uses_configured_host_key_alias(self):
        config = replace(self.config, host_key_alias='owned-backend')
        with patch('provisioning.subprocess.run', return_value=subprocess.CompletedProcess([], 0, self.line, b'')):
            pin_host(config, self.fingerprint)
        self.assertEqual(config.known_hosts_file.read_text().split()[0], 'owned-backend')

    def test_other_device_profile_gets_generic_private_paths(self):
        output = self.root / 'new-device/access.json'
        new_profile(output, 'mobile', product='iPhone12,3', serial='EXAMPLE-DEVICE', transport='ssh', host='192.0.2.5')
        config = load_config(output)
        self.assertEqual(config.product_type, 'iPhone12,3')
        self.assertEqual(config.ssh_port, 22)
        self.assertEqual(config.identity_file.name, 'identity')
        self.assertEqual(config.identity_file.stat().st_mode & 0o077, 0)
        self.assertFalse(config.known_hosts_file.exists())
        self.assertFalse(config.certificate_file.exists())
        with self.assertRaises(FileExistsError):
            new_profile(output, 'mobile')

    def test_existing_output_blocks_deployment_before_device_access(self):
        with patch('provisioning.remote') as remote:
            with self.assertRaises(FileExistsError):
                deploy(self.config, self.path)
            remote.assert_not_called()

    def test_usb_profile_supports_nonstandard_ssh_port_and_serial_only(self):
        output = self.root / 'a11/access.json'
        new_profile(output, 'root', transport='usb', serial='EXAMPLE-A11', port=2333, device_port=44)
        config = load_config(output)
        self.assertEqual((config.listen_port, config.device_port), (2333, 44))
        self.assertEqual(config.product_type, '')

    def test_invalid_usb_ssh_port_is_refused_before_key_generation(self):
        with patch('provisioning.subprocess.run') as command:
            with self.assertRaises(ValueError):
                new_profile(self.root / 'bad/access.json', 'root', serial='EXAMPLE', device_port=65536)
            command.assert_not_called()

    def test_signer_probe_uses_shell_path_and_reports_an_executable(self):
        # Execute the real remote shell script locally with a rootless-like
        # PATH. This catches quoting and noninteractive-shell lookup mistakes.
        directory = self.root / 'rootless bin'; directory.mkdir()
        tool = directory / 'ldid'; tool.write_text('#!/bin/sh\nexit 0\n'); tool.chmod(0o700)
        def shell(config, arguments):
            return subprocess.check_output(['/bin/sh', '-c', arguments[2]], env={'PATH': str(directory)})
        with patch('provisioning.remote', side_effect=shell):
            self.assertEqual(signing_tool(self.config), str(tool))

    def test_rootless_signer_is_used_by_absolute_path(self):
        with patch('provisioning.remote', return_value=b'/var/jb/usr/bin/ldid\n'):
            self.assertEqual(signing_tool(self.config), '/var/jb/usr/bin/ldid')
        for response in (b'', b'ldid\n', b'/usr/bin/ldid\nnoise'):
            with self.subTest(response=response), patch('provisioning.remote', return_value=response):
                with self.assertRaises(RemoteSetupError): signing_tool(self.config)

    def test_bootstrap_cannot_authorize_on_another_host(self):
        public = Path(str(self.config.identity_file) + '.pub')
        public.parent.mkdir(parents=True)
        public.write_text('ssh-ed25519 dGVzdA== fixture\n')
        bootstrap = replace(self.config, listen_port=2323)
        with patch('provisioning.subprocess.run') as command:
            with self.assertRaisesRegex(ValueError, 'same host'):
                authorize(self.config, bootstrap)
            command.assert_not_called()

    def test_graphical_password_goes_directly_to_ssh_and_cancel_does_not_verify_login(self):
        public = Path(str(self.config.identity_file) + '.pub')
        public.parent.mkdir(parents=True)
        public.write_text('ssh-ed25519 dGVzdA== fixture\n')
        self.config.user_file.write_text('mobile\n')
        helper = self.root / 'password'; helper.write_text('fixture'); helper.chmod(0o700)
        with patch('provisioning.subprocess.run', side_effect=subprocess.CalledProcessError(255, 'ssh')) as command, \
                patch('provisioning.remote') as verify:
            with self.assertRaises(subprocess.CalledProcessError):
                authorize(self.config, interactive=True, askpass=helper)
        arguments, options = command.call_args.args[0], command.call_args.kwargs
        self.assertIn('StrictHostKeyChecking=yes', arguments)
        self.assertIn('BatchMode=no', arguments)
        self.assertEqual(options['input'], public.read_bytes())
        self.assertEqual(options['env']['SSH_ASKPASS'], str(helper))
        self.assertEqual(options['env']['SSH_ASKPASS_REQUIRE'], 'force')
        self.assertTrue(options['start_new_session'])
        verify.assert_not_called()


class ImportStubTests(unittest.TestCase):
    def test_unknown_import_cannot_be_silently_mapped_to_a_library(self):
        declarations = {'libraries': {'objc': {'symbols': ['_objc_msgSend'], 'framework': False,
                                              'install_name': '/usr/lib/libobjc.A.dylib'}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'Unreviewed iOS imports'):
                write_link_stubs(root, ['_unknown_new_abi'], declarations)
            self.assertEqual(list(root.iterdir()), [])

    def test_selector_calls_use_the_objc_runtime_and_ios_target(self):
        declarations = {'libraries': {'objc': {'symbols': ['_objc_msgSend'], 'framework': False,
                                              'install_name': '/usr/lib/libobjc.A.dylib'}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            imports = write_link_stubs(root, ['_objc_msgSend$length'], declarations)
            self.assertEqual(imports, ['_objc_msgSend'])
            text = (root / 'libobjc.tbd').read_text()
            self.assertIn('arm64-ios', text)
            self.assertNotIn('arm64-macos', text)


class CertificateFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        path = self.root / 'access.json'; path.write_text('{}')
        self.config = replace(load_config(path, root=self.root), transport='usb', product_type='iPhone11,2')
        self.binary = self.root / 'endpoint'; self.binary.write_bytes(b'fixture binary')
        self.certificate = b'fixture DER'; self.digest = hashlib.sha256(self.certificate).hexdigest()

    def attempt(self, cached_error=None, ltk=None, path_mode='native', **options):
        calls = []
        inputs = []
        def command(config, arguments, data=None, timeout=20):
            calls.append(arguments)
            inputs.append((arguments, data))
            if arguments == ['sh', '-c', ENVIRONMENT_SCRIPT]:
                return json.dumps({'path_mode': path_mode, 'path_translation_verified': path_mode == 'roothide'}).encode()
            if arguments[:2] == ['sh', '-c'] and 'mirra_signer=' in arguments[2]: return b'/var/jb/usr/bin/ldid\n'
            if '--capabilities' in arguments: return b'{"required_symbols_available":true}'
            if '--export-certificate' in arguments or '--issue-certificate' in arguments:
                if '--export-certificate' in arguments and cached_error: raise cached_error
                return json.dumps({'certificate_exported': True, 'sha256': self.digest}).encode()
            if arguments[0] == 'cat': return self.certificate
            return b''
        with patch('provisioning.remote', side_effect=command), \
                patch('provisioning.require_ios_endpoint', return_value={'platform': 'ios'}), \
                patch('provisioning.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'platform IOS\n', '')), \
                patch('provisioning.Endpoint') as endpoint:
            instance = endpoint.return_value.__enter__.return_value
            instance.ready = {'dcrt_sha256': self.digest}
            instance.call.return_value = {'status': 0, 'identity': 'fixture', 'ltk': fixture_ltk() if ltk is None else ltk}
            try:
                result = deploy(self.config, self.root / 'ready.json', self.binary, **options)
            finally:
                self.calls = calls
                self.inputs = inputs
        return result

    def test_cached_certificate_is_reused_without_issuance(self):
        result = self.attempt(issue_if_missing=True)
        self.assertFalse(result['certificate_issued'])
        self.assertFalse(any('--issue-certificate' in call for call in self.calls))
        self.assertTrue(result['wire_layout_validated'])
        self.assertTrue(any(call[0] == '/var/jb/usr/bin/ldid' for call in self.calls))

    def test_roothide_separates_code_and_data_and_saves_runtime_translation(self):
        import plistlib
        result = self.attempt(path_mode='roothide', issue_if_missing=True)
        self.assertTrue(result['remote_endpoint'].startswith('/usr/local/libexec/Mirra/'))
        self.assertTrue(result['remote_certificate'].startswith('/var/mobile/Library/Caches/Mirra/'))
        self.assertEqual(result['path_mode'], 'roothide')
        configured = load_config(self.root / 'ready.json')
        self.assertEqual(configured.path_mode, 'roothide')
        self.assertEqual(configured.remote_endpoint, result['remote_endpoint'])
        self.assertEqual(configured.remote_certificate, result['remote_certificate'])
        entitlements = next(data for args, data in self.inputs if data and data.startswith(b'<?xml'))
        values = plistlib.loads(entitlements)
        self.assertTrue(all(values[name] is True for name in ROOTHIDE_ENTITLEMENTS))
        self.assertTrue(values['com.apple.keystore.device'])
        self.assertFalse(any('--issue-certificate' in call for call in self.calls))

    def test_roothide_publication_resume_retains_both_paths_and_mode(self):
        def interrupted(path, data):
            if Path(path).name == 'ready.provisioning.json': raise OSError('Simulated interruption')
            return exclusive_write(path, data)
        with patch('provisioning.exclusive_write', side_effect=interrupted):
            with self.assertRaises(OSError): self.attempt(path_mode='roothide', resume=True)
        self.attempt(resume=True)
        configured = load_config(self.root / 'ready.json')
        self.assertEqual(configured.path_mode, 'roothide')
        self.assertTrue(configured.remote_endpoint.startswith('/usr/local/libexec/Mirra/'))
        self.assertTrue(configured.remote_certificate.startswith('/var/mobile/Library/Caches/Mirra/'))
        self.assertEqual(self.calls, [])

    def test_signal_received_after_authentication_is_not_a_missing_certificate(self):
        stderr = ('debug1: handshake detail\n' * 200 + 'Authenticated to phone using "publickey".\n'
                  + 'debug1: client_input_channel_req: channel 0 rtype exit-signal reply 0\n')
        self.config.user_file.parent.mkdir(parents=True, exist_ok=True)
        self.config.user_file.write_text('mobile')
        reply = subprocess.CompletedProcess([], 255, b'', stderr.encode())
        with patch('provisioning.activity.run', return_value=reply):
            with self.assertRaises(RemoteSetupError) as caught:
                remote_helper(self.config, ['/var/mobile/endpoint', '--capabilities'])
        self.assertEqual(caught.exception.code, 'helper_terminated')
        self.assertTrue(caught.exception.details['remote_exit_signal_received'])
        self.assertIn('Authenticated to', str(caught.exception))

    def test_bridge_key_layout_is_checked_before_publishing_ready_profile(self):
        with self.assertRaisesRegex(ValueError, 'attested signing identity'):
            self.attempt(ltk=base64.b64encode(b'unsupported-key-layout').decode())
        self.assertFalse((self.root / 'ready.json').exists())
        self.assertFalse((self.root / 'certificate.der').exists())

    def test_missing_cache_requests_issuance_then_verifies_matching_certificate(self):
        result = self.attempt(RemoteSetupError('No cache', 'cached_certificate_unavailable'), issue_if_missing=True)
        self.assertTrue(result['certificate_issued'])
        self.assertEqual(sum('--issue-certificate' in call for call in self.calls), 1)
        self.assertEqual((self.root / 'certificate.der').read_bytes(), self.certificate)
        self.assertTrue((self.root / 'ready.json').exists())

    def test_unrelated_ssh_error_does_not_request_certificate_issuance(self):
        with self.assertRaises(RemoteSetupError):
            self.attempt(RemoteSetupError('cached_certificate_unavailable mentioned in stderr'), issue_if_missing=True)
        self.assertFalse(any('--issue-certificate' in call for call in self.calls))
        self.assertFalse((self.root / 'ready.json').exists())

    def test_cli_does_not_issue_without_explicit_choice(self):
        with self.assertRaises(RemoteSetupError):
            self.attempt(RemoteSetupError('No cache', 'cached_certificate_unavailable'))
        self.assertFalse(any('--issue-certificate' in call for call in self.calls))

    def interrupt_publication(self, name):
        def interrupted(path, data):
            if Path(path).name == name:
                raise OSError('Simulated process interruption')
            return exclusive_write(path, data)
        with patch('provisioning.exclusive_write', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'interruption'):
                self.attempt(resume=True)
        self.assertFalse((self.root / 'ready.json').exists())

    def test_restart_after_certificate_publication_does_not_redeploy_or_reissue(self):
        self.interrupt_publication('ready.provisioning.json')
        self.assertEqual((self.root / 'certificate.der').read_bytes(), self.certificate)
        result = self.attempt(resume=True)
        self.assertTrue(result['fresh_attested_key_validated'])
        self.assertEqual(self.calls, [])
        self.assertTrue((self.root / 'ready.json').exists())

    def test_restart_after_report_publication_commits_ready_profile(self):
        self.interrupt_publication('ready.json')
        self.assertTrue((self.root / 'ready.provisioning.json').exists())
        self.attempt(resume=True)
        self.assertEqual(self.calls, [])
        self.assertTrue((self.root / 'ready.json').exists())

    def test_restart_refuses_changed_certificate_and_preserves_it(self):
        self.interrupt_publication('ready.json')
        path = self.root / 'certificate.der'; path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.attempt(resume=True)
        self.assertEqual(path.read_bytes(), b'changed')
        self.assertFalse((self.root / 'ready.json').exists())

    def test_usb_restart_accepts_new_temporary_port_but_not_another_device(self):
        self.interrupt_publication('ready.json')
        self.config = replace(self.config, serial='OTHERPHONE')
        with self.assertRaisesRegex(ValueError, 'different bridge'):
            self.attempt(resume=True)
        self.config = replace(self.config, serial='', listen_port=23456)
        self.attempt(resume=True)
        self.assertEqual(load_config(self.root / 'ready.json').listen_port, 23456)

    def test_restart_requires_a_fresh_matching_attested_key(self):
        self.interrupt_publication('ready.json')
        self.digest = '0' * 64  # Endpoint now presents a different certificate.
        with self.assertRaisesRegex(RuntimeError, 'different device certificate'):
            self.attempt(resume=True)
        self.assertFalse((self.root / 'ready.json').exists())


if __name__ == '__main__':
    unittest.main()
