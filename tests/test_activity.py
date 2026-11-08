import json
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from activity import capture, FileLog, run, poll_errors
from redaction import redact_line, redact_text
from transport import Endpoint
from logfiles import migrate_legacy


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def script(self, text):
        path = self.root / 'child.py'
        path.write_text(text)
        return [sys.executable, '-u', str(path)]

    def test_real_stderr_is_visible_before_the_operation_completes(self):
        gate = self.root / 'continue'
        command = self.script('''import sys,time
from pathlib import Path
print('MIRRA_ENDPOINT stage=MAEIssueDCRTWithCallback_call',file=sys.stderr,flush=True)
end=time.monotonic()+5
while not Path(sys.argv[1]).exists() and time.monotonic()<end: time.sleep(.01)
print('{"certificate_exported":true,"bytes":1234}')
''') + [str(gate)]
        records = []
        first = threading.Event()
        result = []
        log = FileLog(self.root / 'logs')

        def sink(value):
            records.append(value)
            log(value)
            if 'MAEIssueDCRTWithCallback_call' in value['message']:
                first.set()

        def work():
            with capture(sink):
                result.append(run(command, source='SSH', capture_output=True, timeout=6))

        thread = threading.Thread(target=work)
        thread.start()
        try:
            self.assertTrue(first.wait(3), 'stderr was buffered until completion')
            self.assertTrue(thread.is_alive(), 'the intermediate line was not live')
            self.assertFalse(result)
            text = log.path.read_text()
            self.assertIn('MAEIssueDCRTWithCallback_call', text)
            self.assertNotIn('certificate_exported', text)
        finally:
            gate.touch()
            thread.join(7)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0].stdout, b'{"certificate_exported":true,"bytes":1234}\n')
        self.assertTrue(any(r.get('fields', {}).get('exit_code') == 0 for r in records))

    def test_redaction_keeps_actual_failure_prose_and_codes(self):
        text = 'ssh: connect to host 192.0.2.4 port 22: Connection refused; token=private-token'
        clean = redact_text(text)
        self.assertIn('192.0.2.4 port 22: Connection refused', clean)
        self.assertNotIn('private-token', clean)
        value = json.loads(redact_line(json.dumps({'status':-536870212,'error':'new_device_failure',
            'data':'short-secret','reply':{'password':'secret-password','local_ltk':'short-key'}})))
        self.assertEqual(value['status'], -536870212)
        self.assertEqual(value['error'], 'new_device_failure')
        self.assertNotIn('short-secret', json.dumps(value))
        self.assertNotIn('secret-password', json.dumps(value))
        self.assertNotIn('short-key', json.dumps(value))

    def test_input_and_binary_certificate_do_not_enter_logs(self):
        command = self.script('import sys\ndata=sys.stdin.buffer.read()\nsys.stdout.buffer.write(data)\n')
        records = []
        payload = b'\x00private certificate fixture\xff'
        with capture(records.append):
            result = run(command, source='SSH', input=payload, capture_output=True,
                         record_stdout=False, timeout=3)
        self.assertEqual(result.stdout, payload)
        self.assertNotIn('private certificate fixture', json.dumps(records))
        self.assertEqual(records[-1]['fields']['stdout_bytes'], len(payload))

    def test_nonzero_exit_keeps_real_output_and_check_semantics(self):
        command = self.script('import sys\nprint("unsupported_device: aks_ref_key_create is missing",file=sys.stderr)\nsys.exit(17)\n')
        records = []
        with capture(records.append), self.assertRaises(subprocess.CalledProcessError) as failed:
            run(command, source='SSH', capture_output=True, check=True, timeout=3)
        self.assertEqual(failed.exception.returncode, 17)
        self.assertIn(b'aks_ref_key_create', failed.exception.stderr)
        self.assertTrue(any('unsupported_device: aks_ref_key_create is missing' in r['message'] for r in records))
        self.assertEqual(records[-1]['fields']['exit_code'], 17)

    def test_endpoint_logging_retains_real_replies_and_captures_ssh_stderr(self):
        command = self.script('''import json,sys
print('{"ready":true}',flush=True)
print('actual endpoint stderr: helper initialized',file=sys.stderr,flush=True)
for line in sys.stdin:
 request=json.loads(line)
 print(json.dumps({'status':0,'data':'fixture-short-key','identity':'fixture-identity'}),flush=True)
 if request['operation']=='exit': break
''')
        records = []
        stderr_seen = threading.Event()

        def sink(value):
            records.append(value)
            if 'actual endpoint stderr: helper initialized' in value['message']:
                stderr_seen.set()

        with capture(sink), Endpoint(SimpleNamespace(timeout=2), command=command) as endpoint:
            self.assertTrue(stderr_seen.wait(2), 'endpoint stderr did not reach the live logger')
            reply = endpoint.call('generate', local_ltk='fixture-request-key')
            self.assertEqual(reply['data'], 'fixture-short-key')
            self.assertEqual(reply['identity'], 'fixture-identity')
        text = json.dumps(records)
        self.assertNotIn('fixture-short-key', text)
        self.assertNotIn('fixture-request-key', text)
        self.assertNotIn('fixture-identity', text)
        self.assertIn('actual endpoint stderr: helper initialized', text)

    def test_timeout_reaps_only_the_logger_owned_child(self):
        command = self.script('import time\ntime.sleep(20)\n')
        records = []
        with capture(records.append), self.assertRaises(subprocess.TimeoutExpired):
            run(command, timeout=.2)
        pid = next(r['fields']['pid'] for r in records if r['message'] == 'process started')
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(records[-1]['message'], 'process timed out')

    def test_rotation_preserves_current_inode_and_refuses_symlinks(self):
        folder = self.root / 'logs'
        logger = FileLog(folder, limit=2048)
        logger({'message':'first'})
        path = logger.path
        inode = path.stat().st_ino
        for _ in range(20):
            logger({'message':'x'*200})
        self.assertEqual(path.stat().st_ino, inode)
        self.assertLessEqual(path.stat().st_size, 2048)
        self.assertEqual(path.with_name(path.name + '.1').stat().st_mode & 0o777, 0o600)
        other = self.root/'other'; other.write_text('unchanged')
        link = folder/'bad.log'; link.symlink_to(other)
        with self.assertRaises(OSError):
            FileLog(folder, name='bad.log')({'message':'bad'})
        self.assertEqual(other.read_text(), 'unchanged')

    def test_new_installation_creates_private_parent_directories(self):
        folder = self.root / 'new-Mirra/logs'
        previous = os.umask(0o022)
        try:
            log = FileLog(folder)
            log({'source':'SSH stderr','message':'Authentication succeeded (publickey).'})
        finally:
            os.umask(previous)
        self.assertEqual(folder.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual(log.path.stat().st_mode & 0o777, 0o600)

    def test_ui_event_helper_saves_real_message_with_credentials_redacted(self):
        folder = self.root / 'Mirra/logs'
        script = Path(__file__).resolve().parents[1] / 'scripts/event.py'
        result = subprocess.run([sys.executable, '-B', str(script), '--base', str(folder)],
            input=json.dumps({'source':'Mirra','level':'error','message':'real failure 42 token=fixture-token',
                              'password':'fixture-password'}).encode(), capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads((folder / ('app-' + date.today().isoformat() + '.log')).read_text())
        self.assertIn('real failure 42', saved['message'])
        self.assertIn('timestamp', saved)
        self.assertNotIn('fixture-token', json.dumps(saved))
        self.assertNotIn('fixture-password', json.dumps(saved))

    def test_routine_poll_suppresses_success_dump_and_keeps_actual_failure(self):
        records = []
        with capture(poll_errors(records.append)):
            success = run(self.script('print("routine successful status dump")\n'), capture_output=True, timeout=3)
            self.assertEqual(success.stdout, b'routine successful status dump\n')
            self.assertEqual(records, [])
            failure = run(self.script('import sys\nprint("actual launchctl failure 42",file=sys.stderr)\nsys.exit(42)\n'),
                          capture_output=True, timeout=3)
        self.assertEqual(failure.returncode, 42)
        self.assertTrue(any('actual launchctl failure 42' in r['message'] for r in records))
        self.assertEqual(records[-1]['fields']['exit_code'], 42)

    def test_daily_filename_changes_at_midnight_and_preserves_yesterday(self):
        day = [date(2026,11,8)]
        folder = self.root / 'logs'
        log = FileLog(folder, clock=lambda:day[0])
        log({'message':'actual last operation before midnight'})
        yesterday = folder / 'setup-2026-11-08.log'
        self.assertEqual(json.loads(yesterday.read_text())['message'], 'actual last operation before midnight')
        before = yesterday.read_bytes()
        day[0] = date(2026,11,9)
        log({'message':'actual first operation after midnight'})
        self.assertEqual(yesterday.read_bytes(), before)
        self.assertEqual(json.loads((folder / 'setup-2026-11-09.log').read_text())['message'], 'actual first operation after midnight')

    def test_seven_day_retention_removes_only_expired_private_managed_logs(self):
        folder = self.root / 'logs'; folder.mkdir(mode=0o700)
        names = ('setup-2026-11-01.log','native-2026-11-01.log.1','control-2026-11-02.log',
                 'app-2026-11-08.log','setup-2026-11-09.log','setup-2026-02-30.log','notes.txt','service.log')
        for name in names:
            path = folder/name; path.write_text('retention test'); path.chmod(0o600)
        outside = self.root/'outside';outside.write_text('unrelated file')
        (folder/'app-2026-10-31.log').symlink_to(outside)
        public = folder/'control-2026-10-31.log';public.write_text('unmanaged public file');public.chmod(0o644)
        removed = FileLog(folder, clock=lambda:date(2026,11,8)).maintain()
        self.assertCountEqual(removed, ['setup-2026-11-01.log','native-2026-11-01.log.1'])
        for name in names[2:]:
            self.assertTrue((folder/name).exists(), name)
        self.assertTrue((folder/'app-2026-10-31.log').is_symlink())
        self.assertEqual(outside.read_text(), 'unrelated file')
        self.assertEqual(public.read_text(), 'unmanaged public file')

    def test_idle_maintenance_expires_logs_on_the_next_date_change(self):
        day = [date(2026,11,8)]
        folder=self.root/'logs';folder.mkdir(mode=0o700)
        path=folder/'native-2026-11-02.log';path.write_text('six days old');path.chmod(0o600)
        log=FileLog(folder, clock=lambda:day[0])
        self.assertEqual(log.maintain(), [])
        self.assertTrue(path.exists())
        day[0]=date(2026,11,9)
        self.assertEqual(log.maintain(), ['native-2026-11-02.log'])
        self.assertFalse(path.exists())

    def test_app_event_startup_performs_expiration_without_touching_other_files(self):
        folder=self.root/'logs';folder.mkdir(mode=0o700)
        old=folder/('native-' + (date.today()-timedelta(days=8)).isoformat() + '.log')
        old.write_text('expiration fixture');old.chmod(0o600)
        legacy=folder/'setup.log';legacy.write_text('earlier SSH output\n');legacy.chmod(0o600)
        other=folder/'keep.txt';other.write_text('owner file')
        script=Path(__file__).resolve().parents[1]/'scripts/event.py'
        result=subprocess.run([sys.executable,'-B',str(script),'--base',str(folder)],
            input=b'{"source":"Mirra","message":"capture started"}',capture_output=True,timeout=3)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(old.exists())
        self.assertEqual(other.read_text(),'owner file')
        self.assertTrue((folder/('app-' + date.today().isoformat() + '.log')).is_file())
        self.assertEqual((folder/('setup-' + date.today().isoformat() + '.log')).read_text(),'earlier SSH output\n')
        self.assertFalse(legacy.exists())

    def test_legacy_migration_preserves_bytes_inode_and_existing_dated_files(self):
        folder=self.root/'logs';folder.mkdir(mode=0o700)
        path=folder/'setup.log';path.write_text('original SSH output\n');path.chmod(0o600)
        inode=path.stat().st_ino
        stamp=datetime.now().timestamp();os.utime(path,(stamp,stamp))
        old=folder/'control.log';old.write_text('existing legacy');old.chmod(0o600)
        current=folder/('control-' + date.today().isoformat() + '.log');current.write_text('existing dated');current.chmod(0o600)
        renamed=migrate_legacy(folder)
        target=folder/('setup-' + date.today().isoformat() + '.log')
        self.assertEqual(target.read_text(),'original SSH output\n')
        self.assertEqual(target.stat().st_ino,inode)
        self.assertFalse(path.exists())
        self.assertEqual(current.read_text(),'existing dated')
        self.assertEqual(old.read_text(),'existing legacy')
        self.assertEqual(renamed,[{'from':'setup.log','to':target.name}])
