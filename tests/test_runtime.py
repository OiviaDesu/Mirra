import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from transport import Endpoint,EndpointError,MAX_REPLY
from configuration import ConfigError,load_config
from usbmux import select_device


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.path = self.root/'settings.json'

    def config(self,data):
        self.path.write_text(json.dumps(data))
        return load_config(self.path,root=self.root)

    def test_defaults_do_not_select_a_reference_phone(self):
        c = self.config({})
        self.assertEqual((c.transport,c.product_type,c.listen_port,c.device_port,c.seconds),
                         ('ssh','',2222,22,1800))
        self.assertEqual(c.certificate_file,self.root/'private/bridge/certificate.der')

    def test_another_device_and_custom_paths(self):
        c = self.config({'backend':{'usb':{'product_type':'iPhone12,3','serial':'TEST-DEVICE','listen_port':2323},
                                     'ssh':{'identity_file':'private/other/key'},'certificate_file':'private/other/cert.der'}})
        self.assertEqual(c.product_type,'iPhone12,3')
        self.assertEqual(c.serial,'TEST-DEVICE')
        self.assertEqual(c.identity_file,self.root/'private/other/key')

    def test_ssh_remote_command_preserves_literal_paths(self):
        remote = "/var/mobile/path with spaces/$(touch unwanted);'endpoint"
        c = self.config({'backend':{'transport':'ssh','ssh':{'host':'192.0.2.5','port':2223,
            'host_key_alias':'owned-backend','user_file':'user','remote_endpoint':remote}}})
        c.user_file.write_text('mobile\n')
        command = c.ssh_command()
        self.assertIn('StrictHostKeyChecking=yes',command)
        self.assertIn('HostKeyAlias=owned-backend',command)
        self.assertEqual(command[-2],'192.0.2.5')
        self.assertEqual(shlex.split(command[-1]),[remote,c.remote_certificate])

    def test_usb_port_overrides_ssh_port(self):
        c = self.config({'backend':{'transport':'usb','usb':{'product_type':'iPhone11,2','listen_port':2323},'ssh':{'port':8888,'user_file':'user'}}})
        c.user_file.write_text('mobile')
        command = c.ssh_command()
        self.assertEqual(command[command.index('-p')+1],'2323')
        self.assertEqual(command[-2],'127.0.0.1')

    def test_ssh_known_host_path_with_spaces_is_one_configuration_token(self):
        c = self.config({'backend':{'ssh':{'known_hosts_file':'Application Support/backend/known_hosts',
                                         'user_file':'user'}}})
        c.user_file.write_text('mobile')
        option = next(arg for arg in c.ssh_command() if arg.startswith('UserKnownHostsFile='))
        # Protect the nested configuration-value boundary, in addition to argv.
        # The installed-path live SSH test separately verifies OpenSSH behavior.
        self.assertEqual(shlex.split(option.partition('=')[2]),[str(c.known_hosts_file)])

    def test_invalid_settings_fail_before_device_access(self):
        invalid = [[],{'schema_version':True},{'backend':{'usb':{'listen_port':True}}},
            {'backend':{'usb':{'listen_port':65536}}},{'backend':{'tranpsort':'ssh'}},
            {'backend':{'transport':'usb','usb':{'product_type':'','serial':''}}},
            {'backend':{'ssh':{'host':'-oProxyCommand=bad'}}},
            {'backend':{'ssh':{'remote_endpoint':'relative'}}},
            {'backend':{'ssh':{'remote_certificate':'/path\ncommand'}}}]
        for data in invalid:
            with self.subTest(data=data),self.assertRaises(ConfigError):
                self.config(data)

    def test_missing_explicit_config_is_not_defaulted(self):
        with self.assertRaises(ConfigError):
            load_config(self.root/'missing.json',root=self.root)

    def test_session_staging_preserves_device_settings(self):
        original=self.config({'backend':{'transport':'ssh','ssh':{'host':'192.0.2.4','port':2223}},
                              'host':{'runtime_directory':'runtime/staged'}})
        staged=self.config(original.as_dict())
        self.assertEqual(original,staged)

    def test_roothide_profile_round_trip_preserves_mode_and_literal_paths(self):
        original = self.config({'backend': {'ssh': {'path_mode': 'roothide', 'user_file': 'user',
            'remote_endpoint': "/usr/local/libexec/Mirra/endpoint 'with' spaces"}}})
        original.user_file.write_text('root')
        command = original.ssh_command()
        arguments = shlex.split(command[-1])
        self.assertEqual(arguments[-2:], [original.remote_endpoint, original.remote_certificate])
        self.assertIn('jbroot', arguments[2])
        self.assertIn('BatchMode=yes', command)
        self.assertEqual(self.config(original.as_dict()), original)

    def test_native_profile_does_not_add_a_new_setting_or_launcher(self):
        original = self.config({})
        self.assertEqual(original.path_mode, 'native')
        self.assertNotIn('path_mode', original.as_dict()['backend']['ssh'])

    def test_invalid_path_modes_are_rejected_before_access(self):
        for mode in ('guess', '', None, [], True):
            with self.subTest(mode=mode), self.assertRaises(ConfigError):
                self.config({'backend': {'ssh': {'path_mode': mode}}})


class USBSelectionTests(unittest.TestCase):
    @staticmethod
    def device(identifier,serial='BACKEND',transport='USB'):
        return {'DeviceID':identifier,'Properties':{'SerialNumber':serial,'ConnectionType':transport}}

    def test_replug_uses_new_identifier(self):
        first = select_device([self.device(4)],serial='BACKEND')
        next_ = select_device([self.device(17)],serial='BACKEND')
        self.assertEqual(first['DeviceID'],4)
        self.assertEqual(next_['DeviceID'],17)

    def test_same_model_is_ambiguous_without_serial(self):
        devices = [self.device(1,'FIRST'),self.device(2,'SECOND')]
        with self.assertRaises(ConnectionError):
            select_device(devices,product='iPhone11,2',product_reader=lambda _: 'iPhone11,2')
        self.assertEqual(select_device(devices,serial='SECOND')['DeviceID'],2)

    def test_serial_does_not_override_wrong_model(self):
        with self.assertRaises(ConnectionError):
            select_device([self.device(1)],serial='BACKEND',product='iPhone11,2',product_reader=lambda _: 'iPhone14,5')

    def test_network_and_unreadable_devices_are_excluded(self):
        with self.assertRaises(ConnectionError):
            select_device([self.device(1,transport='Network')],serial='BACKEND')
        def unavailable(_):
            raise OSError('disconnected')
        with self.assertRaises(ConnectionError):
            select_device([self.device(1)],product='iPhone11,2',product_reader=unavailable)


class TransportTests(unittest.TestCase):
    def endpoint(self,program,timeout=.3):
        endpoint = Endpoint(command=[sys.executable,'-u','-c',program],timeout=timeout)
        self.addCleanup(endpoint.close)
        return endpoint

    def test_mismatched_certificate_and_unknown_protocol_are_rejected(self):
        greetings = [({'ready': True, 'dcrt_sha256': 'a' * 64}, 'b' * 64),
                     ({'ready': True, 'protocol_version': 2}, None)]
        for greeting, expected in greetings:
            program = 'import time; print(' + repr(json.dumps(greeting)) + ',flush=True); time.sleep(30)'
            with self.subTest(greeting=greeting), self.assertRaises(EndpointError):
                Endpoint(command=[sys.executable,'-u','-c',program], timeout=.3,
                         expected_certificate_sha256=expected)

    def test_missing_fingerprint_does_not_satisfy_explicit_certificate_binding(self):
        with self.assertRaises(EndpointError):
            Endpoint(command=[sys.executable,'-u','-c','import time; print(\'{"ready":true}\',flush=True); time.sleep(30)'],
                     timeout=.3, expected_certificate_sha256='a' * 64)

    def test_multiple_buffered_lines_are_not_lost(self):
        e = self.endpoint('import os,time; os.write(1,b\'{"ready":true}\\n{"status":0}\\n\'); time.sleep(30)')
        self.assertEqual(e.call('validate'),{'status':0})
        e._failed = True

    def test_stderr_flood_does_not_fill_disk_or_block_protocol(self):
        e = self.endpoint('''import os, sys, json
for _ in range(320): os.write(2,b'x'*16384)
print('{"ready":true}',flush=True)
for line in sys.stdin:
 request=json.loads(line)
 for _ in range(320): os.write(2,b'y'*16384)
 print('{"status":0}',flush=True)
 if request['operation']=='exit':break
''', timeout=5)
        self.assertEqual(e.call('validate'), {'status': 0})
        tail, total = e._stderr.snapshot()
        self.assertGreaterEqual(total, 9 * 1024 * 1024)
        self.assertLessEqual(len(tail), 65536)
        self.assertEqual(tail, b'y' * len(tail))
        e.close()
        self.assertEqual(e.process.returncode, 0)
        self.assertFalse(e._stderr.thread.is_alive())

    def test_roundtrip_and_graceful_close(self):
        e = self.endpoint('''import json,sys
print(json.dumps({'ready':True}))
for line in sys.stdin:
 r=json.loads(line); print(json.dumps({'operation':r['operation'],'status':0}),flush=True)
 if r['operation']=='exit':break
''')
        self.assertEqual(e.call('validate')['operation'],'validate')
        e.close()
        self.assertEqual(e.process.returncode,0)

    def test_partial_response_times_out_and_poisoned_session_is_not_reused(self):
        e = self.endpoint('''import sys,time
print('{"ready":true}',flush=True)
sys.stdin.readline(); sys.stdout.write('{"status":');sys.stdout.flush();time.sleep(30)
''')
        started = time.monotonic()
        with self.assertRaisesRegex(EndpointError,'timed out'):
            e.call('generate')
        self.assertLess(time.monotonic()-started,2)
        with self.assertRaisesRegex(EndpointError,'unavailable'):
            e.call('validate')
        e.close()
        self.assertIsNotNone(e.process.poll())

    def test_invalid_and_oversized_replies_are_rejected(self):
        for reply in ['[]','invalid','x'*(MAX_REPLY+1)]:
            with self.subTest(length=len(reply)):
                program="import sys,time; print('{\"ready\":true}',flush=True); sys.stdin.readline(); print("+repr(reply)+",flush=True); time.sleep(30)"
                # Keep the huge sample out of argv limits.
                if len(reply)>MAX_REPLY:
                    program="import sys,time; print('{\"ready\":true}',flush=True); sys.stdin.readline(); print('x'*262145,flush=True); time.sleep(30)"
                e = self.endpoint(program)
                with self.assertRaises(EndpointError):e.call('validate')
                e.close()
                self.assertIsNotNone(e.process.poll())

    def test_failed_start_reaps_child(self):
        children=[]
        original=subprocess.Popen
        def spawn(*a,**kw):
            process=original(*a,**kw);children.append(process);return process
        with patch('transport.subprocess.Popen',side_effect=spawn):
            with self.assertRaises(EndpointError):
                Endpoint(command=[sys.executable,'-u','-c',"import time;print('{\"ready\":false}',flush=True);time.sleep(30)"],timeout=.3)
        self.assertEqual(len(children),1)
        self.assertIsNotNone(children[0].poll())

    def test_close_interrupts_an_inflight_request(self):
        e=self.endpoint('''import sys,time
print('{"ready":true}',flush=True)
sys.stdin.readline();time.sleep(30)
''',timeout=20)
        errors=[]
        def pending():
            try:e.call('validate')
            except EndpointError as error:errors.append(str(error))
        thread=threading.Thread(target=pending);thread.start()
        deadline=time.monotonic()+2
        while not e._lock.locked() and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(e._lock.locked())
        start=time.monotonic();e.close();thread.join(timeout=2)
        self.assertLess(time.monotonic()-start,5)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)


if __name__=='__main__':
    unittest.main()
