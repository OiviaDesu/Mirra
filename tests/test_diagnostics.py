import json
from datetime import date, timedelta
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from diagnostics import collect, PrivateReader, project_line, MAX_ENTRIES
from logstream import project, predicate, NOISY_FRAMEWORKS
from activity import FileLog


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.run = self.root/'current/runtime/native-20261108-110000'
        self.write('service.json', {'directory':str(self.run),'phase':'running'})
        self.write('current/runtime/native-20261108-110000/backend-status.json',
                   {'phase':'ready','attempt':1,'ready':True,'directory':'backend-000001'})

    def file(self, name, data):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        for parent in path.parents:
            if parent == self.root.parent:
                break
            parent.chmod(0o700)
        path.write_bytes(data)
        path.chmod(0o600)
        return path

    def write(self, name, value):
        return self.file(name, json.dumps(value).encode())

    def test_live_setup_output_is_read_without_repeating_unchanged_lines(self):
        path = self.file('logs/setup.log', b'{"source":"SSH stderr","message":"debug1: Authentication succeeded (publickey).","timestamp":"2026-11-08T04:00:00.000000+00:00","level":"debug"}\n')
        first = collect(self.root)
        self.assertTrue(any('Authentication succeeded (publickey).' in e['message'] for e in first['entries']))
        self.assertEqual(collect(self.root, first['cursor'])['entries'], [])
        with path.open('ab') as output:
            output.write(b'{"source":"Certificate","message":"--issue-certificate begin","fields":{"path":"/var/mobile/certificate.der"}}\n')
        second = collect(self.root, first['cursor'])
        self.assertEqual(len(second['entries']), 1)
        self.assertIn('/var/mobile/certificate.der', second['entries'][0]['message'])

    def test_unknown_device_errors_are_preserved_instead_of_mapped_to_constants(self):
        message = 'RuntimeError: A11 custom failure 0xe00002bc; aks_new_function unavailable'
        rows = project_line(message.encode(), 'SSH')
        self.assertEqual(rows[0]['message'], message)
        self.assertNotIn('Check the phone', rows[0]['message'])

    def test_recent_timestamped_setup_output_is_retained_over_old_undated_tails(self):
        self.file('logs/setup.log', b'{"source":"SSH stderr","message":"real current SSH result","timestamp":"2026-11-08T04:00:00.000000+00:00"}\n')
        self.file('logs/service.log', b''.join(f'old undated line {i}\n'.encode() for i in range(300)))
        result = collect(self.root)
        self.assertTrue(any(e['message'] == 'real current SSH result' for e in result['entries']))
        self.assertLessEqual(len(result['entries']), MAX_ENTRIES)

    def test_partial_line_waits_and_copy_truncate_regrowth_is_detected(self):
        path = self.file('logs/setup.log', b'first real line\npartial')
        first = collect(self.root)
        self.assertTrue(any(e['message'] == 'first real line' for e in first['entries']))
        self.assertFalse(any(e['message'] == 'partial' for e in first['entries']))
        with path.open('ab') as output:
            output.write(b' finished\n')
        second = collect(self.root, first['cursor'])
        self.assertEqual(second['entries'][0]['message'], 'partial finished')
        path.write_bytes(b'new after rotation, longer than the old offset\n')
        third = collect(self.root, second['cursor'])
        self.assertEqual(third['entries'][0]['message'], 'new after rotation, longer than the old offset')

    def test_symlink_and_nonprivate_logs_are_not_followed(self):
        outside = self.root/'private-target'; outside.write_text('not a log secret')
        logs = self.root/'logs'; logs.mkdir(mode=0o700)
        (logs/'setup.log').symlink_to(outside)
        result = collect(self.root)
        self.assertNotIn('not a log secret', json.dumps(result))
        self.assertTrue(any(e['source'] == 'Logs' for e in result['entries']))
        (logs/'setup.log').unlink()
        path = logs/'setup.log'; path.write_text('public mode secret\n'); path.chmod(0o644)
        self.assertNotIn('public mode secret', json.dumps(collect(self.root)))

    def test_arbitrary_runtime_path_and_cursor_do_not_authorize_reads(self):
        outside = self.root/'outside'
        self.write('service.json', {'directory':str(outside/'native-20261108-110000')})
        self.file('outside/native-20261108-110000/app.log', b'outside secret\n')
        result = collect(self.root, {'files':{'../../private':{'offset':0}}})
        self.assertNotIn('outside secret', json.dumps(result))
        self.assertNotIn('../../private', json.dumps(result['cursor']))

    def test_tail_is_bounded_and_never_changes_protocol_or_state(self):
        path = self.file('logs/setup.log', b''.join(f'actual line {i}\n'.encode() for i in range(5000)))
        before = path.read_bytes()
        states = (self.root/'service.json').read_bytes()
        result = collect(self.root)
        self.assertLessEqual(len(result['entries']), MAX_ENTRIES)
        self.assertTrue(any('actual line 4999' in e['message'] for e in result['entries']))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((self.root/'service.json').read_bytes(), states)

    def test_native_mirroring_message_keeps_real_text_and_masks_tokens(self):
        value = {'eventMessage':'Connection failed with error 42 token=private-token',
                 'messageType':'Error','processID':123,'subsystem':'com.apple.ScreenContinuity',
                 'category':'connection','timestamp':'2026-11-08 11:00:00.123+0700'}
        row = project(json.dumps(value).encode())
        self.assertIn('Connection failed with error 42', row['message'])
        self.assertIn('pid=123', row['message'])
        self.assertNotIn('private-token', row['message'])
        self.assertEqual(row['level'], 'error')

    def test_short_credentials_in_json_do_not_enter_the_viewer(self):
        path = self.file('logs/setup.log', b'{"operation":"generate","status":0,"data":"small-secret","_auth":"auth-secret"}\n')
        text = json.dumps(collect(self.root))
        self.assertNotIn('small-secret', text)
        self.assertNotIn('auth-secret', text)
        self.assertIn('generate', text)

    def test_verbose_hides_only_routine_framework_debug_messages(self):
        for subsystem in NOISY_FRAMEWORKS:
            value = {'eventMessage':'actual framework event','messageType':'Debug','subsystem':subsystem}
            self.assertIsNone(project(json.dumps(value)))
            self.assertIn('actual framework event', project(json.dumps(value), verbose=True)['message'])
            value['messageType'] = 'Error'
            self.assertEqual(project(json.dumps(value))['level'], 'error')
        value = {'eventMessage':'actual connection negotiation','messageType':'Debug','subsystem':'com.apple.ScreenContinuity'}
        self.assertIn('actual connection negotiation', project(json.dumps(value))['message'])
        self.assertIn('logType == "debug"', predicate())
        self.assertNotIn('logType == "debug"', predicate(verbose=True))

    def test_dated_files_are_followed_across_midnight_without_repeating_output(self):
        today=date.today();day=[today-timedelta(days=1)]
        log=FileLog(self.root/'logs',clock=lambda:day[0])
        log({'source':'SSH stderr','message':'real SSH output before midnight'})
        first=collect(self.root)
        self.assertTrue(any(e['message']=='real SSH output before midnight' for e in first['entries']))
        day[0]=today
        log({'source':'SSH stderr','message':'real SSH output after midnight'})
        second=collect(self.root,first['cursor'])
        self.assertEqual([e['message'] for e in second['entries']],['real SSH output after midnight'])
        self.assertEqual(collect(self.root,second['cursor'])['entries'],[])
