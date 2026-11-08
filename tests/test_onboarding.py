import base64
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from configuration import load_config
from installation import NativeService
from onboarding import SetupWizard, SetupIssue
from provisioning import fingerprints, RemoteSetupError
from state import write_state


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.service = NativeService(self.root / 'Mirra')
        self.wizard = SetupWizard(service=self.service, askpass='/fake/password')
        signer = patch('onboarding.signing_tool', return_value='/var/jb/usr/bin/ldid')
        self.signer = signer.start(); self.addCleanup(signer.stop)
        self.wizard.host_check = Mock()
        self.key = b'phone ssh-ed25519 ' + base64.b64encode(b'fixture-public-host-key') + b'\n'
        self.fingerprint = fingerprints(self.key)[0][0]
        self.wizard.scan_keys = Mock(return_value=subprocess.CompletedProcess([], 0, self.key, b''))
        self.settings = {'transport': 'ssh', 'host': '192.0.2.5', 'port': 22, 'user': 'mobile'}

    def begin(self):
        return self.wizard.begin(self.settings)

    def authorize_stage(self):
        state = self.begin()
        _, access, _ = self.wizard.paths(state['id'])
        config = load_config(access)
        config.known_hosts_file.write_bytes(self.key); config.known_hosts_file.chmod(0o600)
        return self.wizard.trust(state['id'], self.fingerprint)

    def test_scan_does_not_trust_or_authorize_a_phone(self):
        state = self.begin()
        self.assertEqual(state['phase'], 'trust')
        self.assertEqual(state['fingerprint'], self.fingerprint)
        _, access, _ = self.wizard.paths(state['id'])
        config = load_config(access)
        self.assertFalse(config.known_hosts_file.exists())
        self.assertEqual(config.identity_file.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('identity', json.dumps(state))
        resumed = SetupWizard(service=self.service).describe()
        self.assertEqual(resumed['id'], state['id'])
        self.assertEqual(resumed['phase'], 'trust')

    def test_wrong_fingerprint_never_enrolls_or_advances(self):
        state = self.begin()
        with patch('onboarding.pin_host') as pin:
            with self.assertRaises(ValueError): self.wizard.trust(state['id'], 'SHA256:wrong')
            pin.assert_not_called()
        self.assertEqual(self.wizard.load()['phase'], 'trust')

    def test_changed_host_key_leaves_the_trust_step_retryable(self):
        state = self.begin()
        with patch('onboarding.pin_host', side_effect=RuntimeError('Host key changed')):
            with self.assertRaises(RuntimeError): self.wizard.trust(state['id'], self.fingerprint)
        self.assertEqual(self.wizard.load()['phase'], 'trust')

    def test_authorization_uses_password_helper_without_passing_a_password(self):
        state = self.authorize_stage()
        with patch('onboarding.remote', side_effect=[RuntimeError('Permission denied'), b'']), \
                patch('onboarding.authorize', return_value={}) as authorize:
            result = self.wizard.connect(state['id'])
        self.assertEqual(result['phase'], 'deploy')
        self.assertEqual(authorize.call_args.kwargs, {'bootstrap': None, 'interactive': True, 'askpass': '/fake/password'})

    def test_successful_authorization_is_reused_after_lost_ui_reply(self):
        state = self.authorize_stage()
        with patch('onboarding.remote', return_value=b''), patch('onboarding.authorize') as authorize:
            self.assertEqual(self.wizard.connect(state['id'])['phase'], 'deploy')
            authorize.assert_not_called()

    def test_provisioning_does_not_switch_the_running_service(self):
        state = self.authorize_stage()
        saved = self.wizard.load(); saved['phase'] = 'deploy'; self.wizard.save(saved)
        def deployed(config, output, **kwargs):
            self.assertTrue(kwargs['issue_if_missing'])
            write_state(output, config.as_dict())
            report = {'certificate_issued': False, 'capabilities': {'product_type': 'iPhone11,2', 'os_version': '17.2'}}
            write_state(output.with_suffix('.provisioning.json'), report)
            return report
        with patch('onboarding.deploy', side_effect=deployed), \
                patch.object(self.service, 'stop') as stop, patch.object(self.service, 'install') as install:
            result = self.wizard.provision(state['id'])
            self.assertEqual(result['phase'], 'ready')
            stop.assert_not_called(); install.assert_not_called()
        self.assertEqual(SetupWizard(service=self.service).describe()['phase'], 'ready')

    def test_ready_profile_resume_rechecks_wire_compatibility_without_redeploying(self):
        state = self.authorize_stage()
        saved = self.wizard.load(); saved['phase'] = 'deploy'; self.wizard.save(saved)
        _, access, ready = self.wizard.paths(state['id'])
        config = load_config(access)
        config.certificate_file.write_bytes(b'fixture-certificate')
        write_state(ready, config.as_dict())
        with patch('onboarding.verify_key', side_effect=ValueError('Unsupported wire layout')) as verify, \
                patch('onboarding.deploy') as deploy:
            with self.assertRaisesRegex(ValueError, 'Unsupported wire layout'):
                self.wizard.provision(state['id'])
            verify.assert_called_once()
            deploy.assert_not_called()
        self.assertEqual(self.wizard.load()['phase'], 'deploy')

    def test_failed_enable_preserves_ready_setup_for_retry(self):
        state = self.authorize_stage(); saved = self.wizard.load(); saved['phase'] = 'ready'; self.wizard.save(saved)
        _, access, ready = self.wizard.paths(state['id']); write_state(ready, load_config(access).as_dict())
        with patch.object(self.service, 'stop', side_effect=RuntimeError('Cleanup still running')), \
                patch.object(self.service, 'prepare') as prepare:
            with self.assertRaises(RuntimeError): self.wizard.finish(state['id'])
            prepare.assert_not_called()
        self.assertEqual(self.wizard.load()['phase'], 'ready')

    def test_unsupported_host_has_no_key_or_device_side_effect(self):
        self.wizard.host_check.side_effect = RuntimeError('Unsupported host')
        with patch('onboarding.new_profile') as create:
            with self.assertRaises(RuntimeError): self.begin()
            create.assert_not_called()
        self.assertFalse(self.wizard.active.exists())

    def test_invalid_new_setup_does_not_mark_previous_setup_failed(self):
        previous = self.begin(); before = self.wizard.load()
        new = SetupWizard(service=self.service); new.host_check = Mock()
        with self.assertRaises(ValueError): new.begin({**self.settings, 'port': 'invalid'})
        result = new.failure(None, 'begin', ValueError('invalid'))
        self.assertEqual(result['phase'], 'new')
        self.assertEqual(self.wizard.load(previous['id']), before)

    def test_host_rejection_explains_compatibility_without_creating_a_profile(self):
        del self.wizard.host_check
        with patch('onboarding.inspect_media_profile', return_value={'compatible': False}), \
                patch('onboarding.new_profile') as create:
            with self.assertRaises(SetupIssue) as caught:
                self.begin()
            create.assert_not_called()
        result = self.wizard.failure(None, 'begin', caught.exception)
        self.assertEqual(result['error_code'], 'unsupported_host')
        self.assertIn('25G332', result['error'])
        self.assertFalse(self.wizard.active.exists())

    def test_unavailable_probe_does_not_claim_the_host_is_unsupported(self):
        del self.wizard.host_check
        with patch('onboarding.inspect_media_profile', side_effect=RuntimeError('/private/diagnostic')):
            with self.assertRaises(SetupIssue) as caught:
                self.begin()
        result = self.wizard.failure(None, 'begin', caught.exception)
        self.assertEqual(result['error_code'], 'host_check_unavailable')
        self.assertNotIn('/private/diagnostic', json.dumps(result))

    def test_changed_or_missing_apple_app_gets_a_specific_setup_message(self):
        import plistlib
        del self.wizard.host_check
        for data, expected in ((plistlib.dumps({'CFBundleShortVersionString': '2.0', 'CFBundleVersion': '100'}), 'unsupported_app'),
                               (b'invalid plist', 'app_unavailable')):
            with self.subTest(expected=expected), \
                    patch('onboarding.inspect_media_profile', return_value={'compatible': True}), \
                    patch('onboarding.Path.read_bytes', return_value=data):
                with self.assertRaises(SetupIssue) as caught:
                    self.wizard.host_check()
                self.assertEqual(caught.exception.code, expected)

    def test_missing_signer_retains_authorization_and_can_resume_after_installation(self):
        state = self.authorize_stage()
        self.signer.side_effect = RemoteSetupError('Private path', 'missing_ldid')
        with patch('onboarding.remote', return_value=b''), \
                patch('onboarding.authorize') as authorize:
            with self.assertRaises(SetupIssue) as caught:
                self.wizard.connect(state['id'])
            authorize.assert_not_called()
        result = self.wizard.failure(state['id'], 'connect', caught.exception)
        self.assertEqual(result['phase'], 'authorize')
        self.assertEqual(result['error_code'], 'missing_ldid')
        self.assertIn('SSH access works', result['error'])
        self.assertNotIn('Private path', json.dumps(result))
        self.signer.side_effect = None
        with patch('onboarding.remote', return_value=b''), patch('onboarding.authorize') as authorize:
            resumed = self.wizard.connect(state['id'])
            self.assertEqual(resumed['phase'], 'deploy')
            self.assertIsNone(resumed['error'])
            self.assertNotIn('error_code', resumed)
            authorize.assert_not_called()

    def test_unstructured_remote_failure_does_not_claim_signer_is_missing(self):
        state = self.authorize_stage()
        error = RemoteSetupError('missing_ldid /private/secret')
        self.signer.side_effect = error
        with patch('onboarding.remote', return_value=b''):
            with self.assertRaises(RemoteSetupError):
                self.wizard.connect(state['id'])
        result = self.wizard.failure(state['id'], 'connect', error)
        self.assertNotIn('error_code', result)
        self.assertNotIn('/private/secret', json.dumps(result))
        self.assertNotIn('SSH access works', result['error'])

    def test_missing_keystore_function_has_specific_message_without_remote_output(self):
        state = self.authorize_stage()
        error = RemoteSetupError('aks_remote_session /private/secret', 'required_keystore_symbols_unavailable')
        result = self.wizard.failure(state['id'], 'provision', error)
        self.assertEqual(result['error_code'], 'required_keystore_symbols_unavailable')
        self.assertIn('missing keystore functions', result['error'])
        self.assertNotIn('/private/secret', json.dumps(result))

    def test_roothide_permission_error_explains_root_access_without_advancing_setup(self):
        state = self.authorize_stage()
        saved = self.wizard.load(); saved['phase'] = 'deploy'; self.wizard.save(saved)
        error = RemoteSetupError('Permission denied /private/phone/path', 'roothide_code_directory_unavailable')
        result = self.wizard.failure(state['id'], 'provision', error)
        self.assertEqual(result['phase'], 'deploy')
        self.assertEqual(result['error_code'], 'roothide_code_directory_unavailable')
        self.assertIn('root SSH account', result['error'])
        self.assertNotIn('/private/phone/path', json.dumps(result))

    def test_actual_helper_exit_status_survives_in_private_failure_details(self):
        state = self.authorize_stage()
        error = RemoteSetupError('MIRRA_HELPER exit status 137 token=secret', 'helper_terminated',
                                 {'remote_exit_status': 137, 'helper_stage': 'capabilities'})
        result = self.wizard.failure(state['id'], 'provision', error)
        self.assertEqual(result['error_code'], 'helper_terminated')
        self.assertIn('SSH login worked', result['error'])
        directory, _, _ = self.wizard.paths(state['id'])
        detail = self.wizard.read(directory / 'error.json')
        self.assertEqual(detail['details']['remote_exit_status'], 137)
        self.assertNotIn('token=secret', json.dumps(detail))
        self.assertNotIn('secret', json.dumps(result))

    def test_installed_setup_reports_actual_stopped_and_unknown_states_without_mutation(self):
        self.authorize_stage()
        saved = self.wizard.load(); saved['phase'] = 'installed'; self.wizard.save(saved)
        for code, title in (('stopped', 'Support is stopped'), ('unknown', 'Couldn’t confirm the current connection')):
            observation = {'state': code, 'title': title, 'detail': 'Next step', 'actions': ['refresh']}
            with self.subTest(code=code), patch('readiness.observe', return_value=observation), \
                    patch.object(self.service, 'start') as start, patch.object(self.service, 'stop') as stop:
                result = self.wizard.describe()
                self.assertEqual(result['support_state'], code)
                self.assertEqual(result['support_report'], observation)
                self.assertEqual(result['phase'], 'installed')
                start.assert_not_called(); stop.assert_not_called()

    def test_usb_requires_explicit_selection(self):
        with self.assertRaisesRegex(ValueError, 'Choose the USB bridge'):
            self.wizard.begin({'transport': 'usb', 'user': 'mobile'})
        self.assertFalse(self.wizard.active.exists())

    def test_usb_bridge_remains_selectable_when_lockdown_model_lookup_fails(self):
        device = {'DeviceID': 5, 'Properties': {'SerialNumber': 'EXAMPLE-A11', 'ConnectionType': 'USB'}}
        for response in (None, OSError('Locked')):
            with self.subTest(response=response), patch('onboarding.list_devices', return_value=[device]), \
                    patch('onboarding.product_type', **({'side_effect': response} if isinstance(response, Exception)
                          else {'return_value': response})):
                result = self.wizard.devices()['devices']
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['serial'], 'EXAMPLE-A11')
            self.assertEqual(result[0]['product_type'], '')

    def test_usb_wizard_preserves_ssh_port_without_model_lookup_on_reconnect(self):
        from contextlib import nullcontext
        with patch.object(self.wizard, 'connection', side_effect=lambda config: nullcontext(config)):
            result = self.wizard.begin({'transport': 'usb', 'serial': 'EXAMPLE-A11',
                                        'product_type': 'iPhone10,5', 'port': '44', 'user': 'root'})
        _, access, _ = self.wizard.paths(result['id'])
        config = load_config(access)
        self.assertEqual((config.device_port, config.listen_port), (44, 2222))
        self.assertEqual((config.serial, config.product_type), ('EXAMPLE-A11', ''))

    def test_foreign_setup_identifiers_are_refused(self):
        for value in ('../outside', '/tmp/other', '', None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError): self.wizard.paths(value)


if __name__ == '__main__':
    unittest.main()
