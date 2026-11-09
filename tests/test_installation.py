from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from configuration import load_config
from installation import NativeService, PACKAGE_ADAPTERS, HOST_TOOLS, PACKAGE_SCHEMA
from state import write_state
from test_interpreter import fixture_runtime


class NativeServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        (self.source / 'scripts').mkdir(parents=True)
        (self.source / 'build').mkdir()
        (self.source / 'scripts/native.py').write_text('print("test fixture")\n')
        for name in ('decoder.py', 'decoder_route.py'):
            (self.source / 'scripts' / name).write_text('# test fixture\n')
        for name in PACKAGE_ADAPTERS + HOST_TOOLS:
            (self.source / 'build' / name).write_bytes(b'test fixture only')
        fixture_runtime(self.source / 'python')
        (self.source / 'config.json').write_text('{}')
        self.config = replace(load_config(self.source / 'config.json', root=self.source), transport='usb', product_type='iPhone11,2')
        for name in ['user_file', 'identity_file', 'known_hosts_file', 'certificate_file']:
            path = getattr(self.config, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'private test fixture')
        self.home = patch('installation.Path.home', return_value=self.root)
        self.home.start()
        self.addCleanup(self.home.stop)
        self.service = NativeService(self.root / 'installed', self.source)
        self.profile = patch('installation.require_media_profile', return_value={'compatible': True})
        self.profile.start()
        self.addCleanup(self.profile.stop)

    def prepare(self):
        with patch('installation.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            return self.service.prepare(self.config)

    def test_status_uses_current_decoder_observation_instead_of_startup_ready(self):
        run = self.service.package / 'runtime/native-fixture'
        run.mkdir(parents=True, mode=0o700)
        birth = (42, os.getuid(), 100, 1)
        write_state(self.service.state, {'phase': 'running', 'directory': str(run),
            'supervisor_birth': birth, 'decoder_route': {'enabled': True, 'ready': True}})
        with patch.object(self.service, 'registration', return_value={}), \
                patch('installation.process_birth', return_value=birth):
            self.assertFalse(self.service.status()['native_launch_support_ready'])
            for value, expected in (({'ready': True}, True), ({'ready': False}, False), ([], False)):
                write_state(run / 'decoder-route.json', value)
                self.assertEqual(self.service.status()['native_launch_support_ready'], expected)

    def test_package_uses_private_stable_files_and_excludes_apple_apps(self):
        self.prepare()
        value = self.service.validate_package()
        config = load_config(self.service.package / 'config.json')
        self.assertEqual(config.identity_file, self.service.package / 'private/identity')
        self.assertEqual(config.certificate_file, self.service.package / 'private/certificate.der')
        self.assertEqual(config.system_app, self.config.system_app)
        self.assertFalse(self.service.plist.exists())
        self.assertEqual((self.service.package / 'build/inspector').stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(not name.endswith('.app') for name in value['files']))
        for name in value['files']:
            self.assertEqual((self.service.package / name).stat().st_mode & 0o077, 0)

    def test_bundled_service_uses_an_independent_stable_interpreter(self):
        fixture_runtime(self.source / 'python')
        self.prepare()
        self.assertEqual(self.service.validate_package()['schema_version'], PACKAGE_SCHEMA)
        executable = self.service.package / 'python/bin/python3'
        self.assertEqual(self.service.definition(self.config)['ProgramArguments'][0], str(executable))
        (self.source / 'python/bin/python3').write_bytes(b'changed source')
        self.service.validate_package()

    def test_missing_bundled_interpreter_prevents_service_start(self):
        fixture_runtime(self.source / 'python'); self.prepare()
        (self.service.package / 'python/bin/python3').unlink()
        with self.assertRaises((OSError, RuntimeError)), patch.object(self.service, 'command') as command:
            self.service.start()
        command.assert_not_called()

    def test_runtime_update_keeps_a_stable_service_path(self):
        self.prepare()
        definition = self.service.definition(self.config)
        original = (self.service.package / 'python/bin/python3').read_bytes()
        executable = self.source / 'python/bin/python3'
        executable.write_bytes(original + b'updated')
        manifest = self.source / 'python/manifest.json'
        data = json.loads(manifest.read_text())
        data['files']['bin/python3'] = hashlib.sha256(executable.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(data))
        self.assertEqual((self.service.package / 'python/bin/python3').read_bytes(), original)
        with patch.object(self.service, 'registration', return_value={}), \
                patch('installation.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            self.service.prepare(self.config, replace_package=True)
        self.assertEqual(self.service.definition(self.config), definition)
        self.assertEqual((self.service.package / 'python/bin/python3').read_bytes(), original + b'updated')

    def test_installed_interpreter_survives_gui_python_change(self):
        self.prepare()
        before = self.service.definition(self.config)
        with patch('installation.sys.executable', '/different/app/python'):
            self.assertEqual(self.service.definition(self.config), before)

    def test_changed_file_is_rejected_before_starting(self):
        self.prepare()
        (self.service.package / 'scripts/native.py').write_text('changed')
        with patch.object(self.service, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'file changed'):
                self.service.start()
            command.assert_not_called()

    def test_unknown_package_contract_is_rejected(self):
        self.prepare()
        path = self.service.package / 'manifest.json'
        original = json.loads(path.read_text())
        for changed in ({**original, 'schema_version': 1}, {**original, 'product': 'Other'}):
            write_state(path, changed)
            with self.assertRaisesRegex(RuntimeError, 'Invalid Mirra package'):
                self.service.validate_package()

    def test_replace_preserves_a_registered_package(self):
        self.prepare()
        before = (self.service.package / 'manifest.json').read_bytes()
        with patch.object(self.service, 'registration', return_value={'pid': '42'}):
            with self.assertRaisesRegex(RuntimeError, 'Stop the service'):
                self.service.prepare(self.config, replace_package=True)
        self.assertEqual((self.service.package / 'manifest.json').read_bytes(), before)

    def test_incompatible_host_cannot_stage_a_package(self):
        with patch('installation.require_media_profile', side_effect=RuntimeError('Unsupported host ABI')):
            with self.assertRaisesRegex(RuntimeError, 'Unsupported host ABI'):
                self.prepare()
        self.assertFalse(self.service.package.exists())

    def test_usb_and_ssh_service_definitions_use_appropriate_transport(self):
        usb = self.service.definition(self.config)
        ssh = self.service.definition(replace(self.config, transport='ssh'))
        self.assertIn('--manage-usb', usb['ProgramArguments'])
        self.assertNotIn('--manage-usb', ssh['ProgramArguments'])
        self.assertTrue(usb['KeepAlive'])
        self.assertEqual(usb['LimitLoadToSessionType'], 'Aqua')
        self.assertGreaterEqual(usb['ExitTimeOut'], 100)
        self.assertEqual(usb, plistlib.loads(plistlib.dumps(usb)))

    def install_plist(self, definition):
        self.service.plist.parent.mkdir(parents=True, exist_ok=True)
        self.service.plist.write_bytes(plistlib.dumps(definition))
        self.service.plist.chmod(0o600)
        return self.service.plist.read_bytes()

    def check_transport_migration(self, original, replacement):
        self.config = replace(self.config, transport=original)
        self.prepare()
        old_config = (self.service.package / 'config.json').read_bytes()
        previous = self.install_plist(self.service.definition(self.config))
        with patch.object(self.service, 'registration', return_value={}), \
                patch('installation.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            self.service.prepare(replace(self.config, transport=replacement), replace_package=True)
        # Preparation leaves login configuration untouched until install.
        self.assertEqual(self.service.plist.read_bytes(), previous)
        with patch.object(self.service, 'registration', return_value={}), \
                patch.object(self.service, 'start', side_effect=lambda: {'registered': True}) as start:
            result = self.service.install()
            start.assert_called_once()
            again = self.service.install()
        installed = load_config(self.service.package / 'config.json')
        self.assertEqual(installed.transport, replacement)
        self.assertEqual(plistlib.loads(self.service.plist.read_bytes()), self.service.definition(installed))
        self.assertEqual(self.service.plist.stat().st_mode & 0o777, 0o600)
        backup = Path(result['previous_plist'])
        self.assertEqual(backup.read_bytes(), previous)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list(self.service.base.glob('service.previous.*.plist'))), 1)
        self.assertNotIn('previous_plist', again)
        packages = list(self.service.base.glob('previous-*'))
        self.assertEqual(len(packages), 1)
        self.assertEqual((packages[0] / 'config.json').read_bytes(), old_config)

    def test_stopped_usb_installation_can_switch_to_ssh(self):
        self.check_transport_migration('usb', 'ssh')

    def test_stopped_ssh_installation_can_switch_to_usb(self):
        self.check_transport_migration('ssh', 'usb')

    def test_manifest_cannot_select_an_unrecognized_entrypoint(self):
        self.prepare()
        path = self.service.package / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['files']['scripts/foreign.py'] = manifest['files'].pop('scripts/native.py')
        write_state(path, manifest)
        with self.assertRaisesRegex(RuntimeError, 'missing required files'):
            self.service.validate_package()

    def test_source_update_preserves_installed_wifi_profile_and_credentials(self):
        self.config = replace(self.config, transport='ssh', ssh_host='bridge.local', ssh_port=2200,
                              host_key_alias='verified-bridge')
        self.prepare()
        previous = load_config(self.service.package / 'config.json')
        credentials = {name: getattr(previous, name).read_bytes() for name in
                       ('identity_file', 'user_file', 'known_hosts_file', 'certificate_file')}
        (self.source / 'scripts/native.py').write_text('print("updated fixture")\n')
        with patch.object(self.service, 'registration', return_value={}), \
                patch('installation.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            self.service.prepare(replace_package=True)
        current = load_config(self.service.package / 'config.json')
        self.assertEqual(current, previous)
        for name, data in credentials.items():
            self.assertEqual(getattr(current, name).read_bytes(), data)
        self.assertEqual((self.service.package / 'scripts/native.py').read_text(), 'print("updated fixture")\n')
        self.service.validate_package()

    def legacy_package(self):
        self.prepare()
        path = self.service.package / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['schema_version'] = 4
        manifest.pop('support_revision')
        for name in ('scripts/decoder.py', 'scripts/decoder_route.py', 'build/decoder-route.dylib'):
            manifest['files'].pop(name)
            (self.service.package / name).unlink()
        write_state(path, manifest)
        return manifest

    def test_legacy_upgrade_preserves_roothide_bridge_credentials_and_normal_decoder_setting(self):
        self.config = replace(self.config, transport='ssh', path_mode='roothide',
                              ssh_host='bridge.local', decoder_route='normal')
        legacy = self.legacy_package()
        self.assertEqual(self.service.validate_package(), legacy)
        self.assertTrue(self.service.needs_update())
        previous = load_config(self.service.package / 'config.json')
        credentials = {name: getattr(previous, name).read_bytes() for name in
                       ('identity_file', 'user_file', 'known_hosts_file', 'certificate_file')}
        self.install_plist(self.service.definition(previous))
        with patch.object(self.service, 'registration', return_value={}), \
             patch.object(self.service, 'stop', return_value={}) as stop, \
             patch.object(self.service, 'start', return_value={}), \
             patch('installation.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            self.service.update_saved()
        stop.assert_called_once()
        self.assertFalse(self.service.needs_update())
        current = load_config(self.service.package / 'config.json')
        self.assertEqual(current, previous)
        for name, data in credentials.items():
            self.assertEqual(getattr(current, name).read_bytes(), data)
        self.assertTrue((self.service.package / 'build/decoder-route.dylib').is_file())

    def test_incomplete_update_preserves_running_old_package(self):
        self.legacy_package()
        before = (self.service.package / 'manifest.json').read_bytes()
        (self.source / 'build/decoder-route.dylib').unlink()
        with patch.object(self.service, 'stop') as stop, \
             patch('installation.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            with self.assertRaisesRegex(RuntimeError, 'missing decoder-route'):
                self.service.update_saved()
        stop.assert_not_called()
        self.assertEqual((self.service.package / 'manifest.json').read_bytes(), before)

    def test_upgrade_preserves_a_foreign_login_definition(self):
        self.legacy_package()
        config = load_config(self.service.package / 'config.json')
        foreign = self.service.definition(config)
        foreign['ProgramArguments'][0] = '/another/controller'
        previous = self.install_plist(foreign)
        with patch.object(self.service, 'stop') as stop, \
             patch('installation.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
            with self.assertRaisesRegex(RuntimeError, 'definition differs'):
                self.service.update_saved()
        stop.assert_not_called()
        self.assertEqual(self.service.plist.read_bytes(), previous)

    def test_package_replacement_waits_for_unregistered_supervisor_cleanup(self):
        self.prepare()
        birth = [42, os.getuid(), 100, 1]
        write_state(self.service.state, {'supervisor_birth': birth})
        with patch.object(self.service, 'registration', return_value={}), \
             patch('installation.process_birth', return_value=tuple(birth)):
            with self.assertRaisesRegex(RuntimeError, 'Wait for native support cleanup'):
                self.service.prepare(replace_package=True)

    def test_failed_decoder_restoration_prevents_cleanup_verification(self):
        self.prepare()
        run = self.service.package / 'runtime/native-test'; run.mkdir(parents=True)
        birth = [42, os.getuid(), 100, 1]
        write_state(self.service.state, {'phase': 'stopped', 'directory': str(run), 'supervisor_birth': birth})
        record = {'backend_cleanup': {'cleanup_verified': True}, 'launch_agent_restored': True,
                  'decoder_route': {'enabled': True}, 'decoder_cleanup': {'cleanup_verified': False}}
        write_state(run / 'result.json', record)
        with patch.object(self.service, 'registration', return_value={}), patch('installation.process_birth', return_value=None):
            self.assertFalse(self.service.status()['last_cleanup_verified'])
            record['decoder_cleanup']['cleanup_verified'] = True
            write_state(run / 'result.json', record)
            self.assertTrue(self.service.status()['last_cleanup_verified'])

    def test_stop_waits_for_unregistered_supervisor_before_reporting_cleanup(self):
        self.prepare()
        run = self.service.package / 'runtime/native-test'; run.mkdir(parents=True)
        birth = [42, os.getuid(), 100, 1]
        write_state(self.service.state, {'phase': 'stopped', 'directory': str(run), 'supervisor_birth': birth})
        write_state(run / 'result.json', {'backend_cleanup': {'cleanup_verified': True}, 'launch_agent_restored': True})
        with patch.object(self.service, 'registration', return_value={}), \
             patch('installation.process_birth', side_effect=[tuple(birth), tuple(birth), None, None]), \
             patch('installation.time.sleep') as wait:
            value = self.service.stop()
        wait.assert_called_once()
        self.assertTrue(value['last_cleanup_verified'])

    def test_transport_migration_refuses_a_registered_job(self):
        self.prepare()
        previous = self.install_plist(self.service.definition(replace(self.config, transport='ssh')))
        with patch.object(self.service, 'registration', return_value={'state': 'running'}), \
                patch.object(self.service, 'start') as start:
            with self.assertRaisesRegex(RuntimeError, 'Stop the service'):
                self.service.install()
            start.assert_not_called()
        self.assertEqual(self.service.plist.read_bytes(), previous)
        self.assertFalse(list(self.service.base.glob('service.previous.*.plist')))

    def test_transport_migration_waits_for_an_unregistered_live_supervisor(self):
        self.prepare()
        previous = self.install_plist(self.service.definition(replace(self.config, transport='ssh')))
        birth = [42, os.getuid(), 100, 3]
        write_state(self.service.state, {'supervisor_birth': birth})
        with patch.object(self.service, 'registration', return_value={}), \
                patch('installation.process_birth', return_value=tuple(birth)), \
                patch.object(self.service, 'start') as start:
            with self.assertRaisesRegex(RuntimeError, 'Wait for native support cleanup'):
                self.service.install()
            start.assert_not_called()
        self.assertEqual(self.service.plist.read_bytes(), previous)

    def test_install_refuses_changes_beyond_the_transport_flag(self):
        self.prepare()
        foreign = self.service.definition(replace(self.config, transport='ssh'))
        foreign['ProgramArguments'][0] = '/another/python'
        previous = self.install_plist(foreign)
        with patch.object(self.service, 'registration', return_value={}), \
                patch.object(self.service, 'start') as start:
            with self.assertRaisesRegex(RuntimeError, 'differs'):
                self.service.install()
            start.assert_not_called()
        self.assertEqual(self.service.plist.read_bytes(), previous)

    def test_interrupted_transport_install_preserves_original_and_can_retry(self):
        self.prepare()
        previous = self.install_plist(self.service.definition(replace(self.config, transport='ssh')))
        with patch.object(self.service, 'registration', return_value={}), \
                patch.object(self.service, 'start', return_value={}) as start:
            with patch('installation.os.replace', side_effect=OSError('simulated disk failure')):
                with self.assertRaisesRegex(OSError, 'simulated disk failure'):
                    self.service.install()
            start.assert_not_called()
            self.assertEqual(self.service.plist.read_bytes(), previous)
            backups = list(self.service.base.glob('service.previous.*.plist'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), previous)
            self.assertEqual(list(self.service.plist.parent.iterdir()), [self.service.plist])
            self.service.install()
            start.assert_called_once()
        self.assertEqual(plistlib.loads(self.service.plist.read_bytes()), self.service.definition(self.config))

    def test_transport_install_rechecks_registration_before_replacement(self):
        self.prepare()
        previous = self.install_plist(self.service.definition(replace(self.config, transport='ssh')))
        with patch.object(self.service, 'registration', side_effect=[{}, {'state': 'running'}]), \
                patch.object(self.service, 'start') as start:
            with self.assertRaisesRegex(RuntimeError, 'changed during installation'):
                self.service.install()
            start.assert_not_called()
        self.assertEqual(self.service.plist.read_bytes(), previous)

    def test_first_install_does_not_clobber_a_concurrently_created_plist(self):
        self.prepare()
        with patch.object(self.service, 'start', return_value={}):
            self.service.install()
        previous = self.service.plist.read_bytes()
        with self.assertRaises(FileExistsError):
            self.service.write_definition({'Label': 'different'})
        self.assertEqual(self.service.plist.read_bytes(), previous)
        self.assertEqual(self.service.plist.stat().st_mode & 0o777, 0o600)

    def test_stale_supervisor_state_is_not_readiness(self):
        self.prepare()
        run = self.service.package / 'runtime/native-test'
        run.mkdir(parents=True)
        birth = [42, os.getuid(), 100, 3]
        write_state(self.service.state, {'phase': 'running', 'directory': str(run), 'supervisor_birth': birth})
        write_state(run / 'backend-status.json', {'phase': 'ready', 'attempt': 1, 'ready': True,
            'worker_birth': [43, os.getuid(), 100, 4]})
        with patch.object(self.service, 'registration', return_value={}), \
                patch('installation.process_birth', return_value=(42, os.getuid(), 200, 4)):
            status = self.service.status()
        self.assertFalse(status['native_launch_support_ready'])
        self.assertFalse(status['backend_ready'])

    def test_foreign_service_registration_is_not_stopped(self):
        reply = subprocess.CompletedProcess([], 0, 'path = /someone/else.plist\npid = 42\n', '')
        with patch.object(self.service, 'command', return_value=reply) as command:
            with self.assertRaisesRegex(RuntimeError, 'another configuration'):
                self.service.stop()
            self.assertEqual([c.args[0] for c in command.call_args_list], ['print'])


if __name__ == '__main__':
    unittest.main()
