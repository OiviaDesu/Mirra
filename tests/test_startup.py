import errno
import argparse
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from startup import require_container, StartupError


class ContainerTests(unittest.TestCase):
    def test_denied_access_is_distinct_from_missing_container(self):
        for failure, code in ((PermissionError(errno.EACCES, 'denied'), 'container_access_denied'),
                              (PermissionError(errno.EPERM, 'denied'), 'container_access_denied'),
                              (FileNotFoundError(), 'container_missing')):
            path = Mock()
            path.stat.side_effect = failure
            with self.assertRaises(StartupError) as caught:
                require_container(path)
            self.assertEqual(caught.exception.code, code)
            path.stat.assert_called_once()

    def test_existing_container_and_non_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            require_container(Path(folder))
            file = Path(folder) / 'file'; file.touch()
            with self.assertRaises(StartupError) as caught:
                require_container(file)
            self.assertEqual(caught.exception.code, 'container_missing')

    def test_native_cleanup_failure_does_not_look_like_a_requested_stop(self):
        import native
        import threading
        from configuration import load_config
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config.json').write_text('{}')
            config = load_config(root / 'config.json', root=root)
            (root / 'build').mkdir()
            for name in ('sockets.dylib', 'media.dylib', 'keystore.dylib'):
                (root / 'build' / name).write_bytes(b'fixture')
            container = root / 'Library/Containers/com.apple.ScreenContinuity/Data/tmp'
            container.mkdir(parents=True)
            requested_stop = threading.Event()
            args = argparse.Namespace(recover_backend=False, launch_services=False,
                config=str(root / 'config.json'), manage_usb=False, seconds=None,
                until_stopped=True, service_state=str(root / 'service.json'), service_log=None)
            handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
            try:
                with patch('native.ROOT', root), patch('native.Path.home', return_value=root), \
                        patch('native.require_media_profile', return_value={'profile': 'fixture'}), \
                        patch('native.NativeBackend') as backend, \
                        patch('native.subprocess.run', side_effect=lambda args, **kwargs:
                              subprocess.CompletedProcess(args, 1 if args[0] == 'pgrep' else 0)):
                    backend.return_value.start.side_effect = RuntimeError('simulated backend startup failure')
                    self.assertEqual(native.run_session(argparse.ArgumentParser(), args, requested_stop), 1)
                self.assertFalse(requested_stop.is_set())
                self.assertEqual(list(container.iterdir()), [])
                self.assertEqual(json.loads((root / 'service.json').read_text())['phase'], 'stopped')
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)


class StartupProcessTests(unittest.TestCase):
    # Real child Python processes test that failures retain one PID without
    # rerunning the session, and that SIGTERM permits a deliberate fresh start.
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'service.json'
        self.calls = self.root / 'calls'
        self.children = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            child.stdout.close(); child.stderr.close()

    def launch(self, body):
        script = ('import sys\nfrom pathlib import Path\n'
                  f'sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / "scripts")!r})\n'
                  'from startup import run_service, StartupError\n'
                  'from state import write_state\nimport json\n'
                  f'state=Path({str(self.state)!r})\n'
                  'def session(stop):\n'
                  f'    with Path({str(self.calls)!r}).open("a") as f: f.write("attempt\\n")\n'
                  + ''.join('    ' + line + '\n' for line in body.splitlines())
                  + 'raise SystemExit(run_service(session, state))\n')
        child = subprocess.Popen([sys.executable, '-E', '-s', '-B', '-c', script],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(child)
        return child

    def wait_state(self, child, phase):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.state.exists():
                data = json.loads(self.state.read_text())
                if data.get('phase') == phase and data['supervisor_birth'][0] == child.pid:
                    return data
            self.assertIsNone(child.poll(), 'Startup guard exited unexpectedly')
            time.sleep(.01)
        self.fail('Timed out waiting for ' + phase)

    def test_failure_types_do_not_exit_or_retry(self):
        for body, reason in [
                ('raise StartupError("container_access_denied", "denied")', 'container_access_denied'),
                ('raise PermissionError("denied during mkdir")', 'support_access_denied'),
                ('raise SystemExit(2)', 'support_startup_failed'),
                ('raise RuntimeError("later startup failed")', 'support_startup_failed'),
                ('return 1', 'support_startup_failed')]:
            with self.subTest(body=body):
                self.calls.unlink(missing_ok=True)
                child = self.launch(body)
                data = self.wait_state(child, 'startup_failed')
                self.assertEqual(data['startup_failure'], reason)
                with self.assertRaises(subprocess.TimeoutExpired):
                    child.wait(timeout=.15)
                self.assertEqual(self.calls.read_text(), 'attempt\n')
                child.terminate(); self.assertEqual(child.wait(timeout=5), 0)
                stopped = json.loads(self.state.read_text())
                self.assertEqual(stopped['phase'], 'stopped')
                self.assertTrue(stopped['startup_cleanup_verified'])

    def test_killed_waiting_process_does_not_reprompt_on_launchd_restart(self):
        first = self.launch('raise PermissionError("denied")')
        self.wait_state(first, 'startup_failed')
        first.kill(); first.wait(timeout=5)
        second = self.launch('raise PermissionError("denied")')
        data = self.wait_state(second, 'startup_failed')
        self.assertEqual(data['startup_failure'], 'startup_interrupted')
        self.assertEqual(self.calls.read_text(), 'attempt\n')
        second.terminate(); second.wait(timeout=5)
        third = self.launch('raise PermissionError("denied")')
        self.wait_state(third, 'startup_failed')
        self.assertEqual(self.calls.read_text(), 'attempt\nattempt\n')

    def test_crash_during_startup_is_latched_before_the_next_attempt(self):
        first = self.launch('stop.wait()\nreturn 0')
        self.wait_state(first, 'startup_checks')
        # Ensure the first callback entered before simulating an abrupt death.
        deadline = time.monotonic() + 5
        while not self.calls.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        first.kill(); first.wait(timeout=5)
        second = self.launch('raise RuntimeError("must not run")')
        self.wait_state(second, 'startup_failed')
        self.assertEqual(self.calls.read_text(), 'attempt\n')

    def test_managed_session_cleanup_evidence_is_preserved(self):
        child = self.launch('data=json.loads(state.read_text())\n'
                            'data.update(phase="stopped", directory="native-example")\n'
                            'write_state(state,data)\nreturn 1')
        data = self.wait_state(child, 'startup_failed')
        self.assertEqual(data['directory'], 'native-example')
        child.kill(); child.wait(timeout=5)
        replacement = self.launch('raise RuntimeError("must not run")')
        data = self.wait_state(replacement, 'startup_failed')
        self.assertEqual(data['directory'], 'native-example')
        self.assertEqual(self.calls.read_text(), 'attempt\n')
        replacement.terminate(); replacement.wait(timeout=5)
        stopped = json.loads(self.state.read_text())
        self.assertNotIn('startup_cleanup_verified', stopped)

    def test_crash_after_session_preparation_does_not_start_another_session(self):
        child = self.launch('data=json.loads(state.read_text())\n'
                            'data.update(phase="preparing", directory="native-example")\n'
                            'write_state(state,data)\nstop.wait()\nreturn 0')
        self.wait_state(child, 'preparing')
        child.kill(); child.wait(timeout=5)
        replacement = self.launch('raise RuntimeError("must not run")')
        data = self.wait_state(replacement, 'startup_failed')
        self.assertEqual(data['startup_failure'], 'startup_interrupted')
        self.assertEqual(data['directory'], 'native-example')
        self.assertEqual(self.calls.read_text(), 'attempt\n')

    def test_normal_stop_does_not_report_startup_failure(self):
        child = self.launch('stop.wait()\nreturn 0')
        self.wait_state(child, 'startup_checks')
        child.terminate(); self.assertEqual(child.wait(timeout=5), 0)
        data = json.loads(self.state.read_text())
        self.assertEqual(data['phase'], 'stopped')
        self.assertNotIn('startup_failure', data)


if __name__ == '__main__':
    unittest.main()
