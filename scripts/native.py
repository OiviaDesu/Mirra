#!/usr/bin/env python3
"""Experimental session using Apple's installed app and a scoped socket helper."""
import argparse
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from configuration import ROOT,load_config
from sockets import BindHelper
from launcher import NativeLaunchAgent
from processes import NativeAppObserver,process_birth
from supervisor import NativeBackend
from forwarder import USBForwarder
from state import write_state
from lifetime import add_lifetime_arguments,duration_from_arguments,deadline_for,emit_event
from logs import BoundedLogs,open_append
from retention import track,retire,prune
from compatibility import require_media_profile, supported_apple_app
from decoder import DecoderRoute


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    add_lifetime_arguments(parser)
    parser.add_argument('--launch-services',action='store_true',
                        help='Prepare background support; open Apple\'s existing app normally from Launchpad or Spotlight')
    parser.add_argument('--recover-backend',action='store_true',
                        help='Keep native launch support running and retry a failed backend (requires --launch-services)')
    parser.add_argument('--manage-usb',action='store_true',
                        help='Own the configured USB forwarding port; no separate proxy Terminal is required')
    parser.add_argument('--service-log',help=argparse.SUPPRESS)
    parser.add_argument('--service-state',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.recover_backend and not args.launch_services:
        parser.error('--recover-backend requires --launch-services')
    config=load_config(args.config)
    if args.manage_usb and config.transport != 'usb':
        parser.error('--manage-usb requires backend.transport=usb')
    duration=duration_from_arguments(parser,args,config.seconds)
    if config.system_app!=Path('/System/Applications/iPhone Mirroring.app'):
        parser.error('The native path currently targets the installed system app')
    if args.launch_services:
        try:recovered=NativeLaunchAgent.recover_abandoned()
        except (OSError,RuntimeError,ValueError) as error:parser.error(str(error))
        if recovered:print(json.dumps({'ABANDONED_NATIVE_SESSION_RECOVERED':recovered}),flush=True)
    if subprocess.run(['pgrep','-x','iPhone Mirroring'],capture_output=True).returncode==0:
        parser.error('Close the existing Mirroring app before starting a native session')
    info=plistlib.loads((config.system_app/'Contents/Info.plist').read_bytes())
    if not supported_apple_app(info):
        parser.error('This adapter requires Apple’s original iPhone Mirroring 1.x system app')
    executable=config.system_app/'Contents/MacOS/iPhone Mirroring'
    subprocess.run(['codesign','--verify','--strict',str(config.system_app)],capture_output=True,check=True)
    adapters=[ROOT/'build/sockets.dylib',ROOT/'build/media.dylib',ROOT/'build/keystore.dylib']
    if not all(p.is_file() for p in adapters):parser.error('Build the host and native bind adapters first: make build build/sockets.dylib')
    if args.launch_services and not (ROOT/'build/launch.dylib').is_file():
        parser.error('Build the native LaunchServices adapter first: make build/launch.dylib')
    try:media_profile=require_media_profile()
    except (OSError,RuntimeError,subprocess.SubprocessError) as error:parser.error(str(error))
    container=Path.home()/'Library/Containers/com.apple.ScreenContinuity/Data/tmp'
    if not container.is_dir():parser.error('Initialize the system app container before using the native adapter')
    os.umask(0o077)
    run_directory=ROOT/'runtime'/('native-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    run_directory.mkdir(parents=True,exist_ok=False)
    track(run_directory,'native')
    retention=prune(run_directory.parent,'native',protected=(run_directory,))
    if retention['removed'] or retention['deferred']:
        emit_event({'NATIVE_RETENTION':retention})
    staged=replace(config,runtime_directory=run_directory,seconds=duration if duration is not None else config.seconds)
    settings=run_directory/'config.json';settings.write_text(json.dumps(staged.as_dict(),indent=2)+'\n')
    write_state(run_directory/'media-compatibility.json',media_profile)
    staged_adapters=[]
    for adapter in adapters:
        target=run_directory/adapter.name
        shutil.copy2(adapter,target)
        target.chmod(0o600)
        staged_adapters.append(target)
    private=Path(tempfile.mkdtemp(prefix='mb-',dir=container));private.chmod(0o700)
    short=Path(tempfile.mkdtemp(prefix='mb-',dir='/tmp'));short.chmod(0o700)
    link=short/'c';link.symlink_to(private,target_is_directory=True)
    address=link/'s';actual=private/'s'
    listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    helper=BindHelper(executable,allow_awdl=True)
    app_observer=NativeAppObserver(executable,staged_adapters[0])
    stop=threading.Event();backend=app=None;launch_agent=None;usb_proxy=None;decoder=None
    log_limits=BoundedLogs(on_error=stop.set)
    before=hashlib.sha256(executable.read_bytes()).hexdigest()
    record={'run':run_directory.name,'supervisor_pid':os.getpid(),'system_app':str(config.system_app),'executable_sha256_before':before,
            'supervisor_birth':process_birth(os.getpid()),
            'duration_seconds':duration,'native_mirroring_verified':None,'daily_use_verified':False,
            'automatic_backend_recovery':args.recover_backend,
            'media_profile':media_profile['profile'],
            'managed_usb_forwarding':args.manage_usb,
            'launch_method':'ordinary LaunchServices with background support' if args.launch_services else 'custom launch command',
            'validation_scope':'Lifecycle only; video and input require a separate observation',
            'adapter_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in staged_adapters}}
    def signals(*_):stop.set()
    signal.signal(signal.SIGINT,signals);signal.signal(signal.SIGTERM,signals)
    def bindings():
        while not stop.is_set():
            try:connection,_=listener.accept()
            except socket.timeout:continue
            except OSError:return
            with connection:
                try:
                    error=helper.handle(connection)
                    event={'UDP_BIND':error,'bound_count':helper.bound_count}
                    if error:event['reason']=helper.last_failure
                    emit_event(event)
                except OSError:pass
    def stop_process(process,seconds):
        if process is None or process.poll() is not None:return
        process.terminate()
        try:process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            # A restoration controller must retain its opportunity to restore
            # sharingd; only the app can be killed by this launcher.
            if process is app:process.kill();process.wait(timeout=3)
            else:record['backend_cleanup_timeout']=True
    def owned_apps():
        found=app_observer.poll()
        if app_observer.last_errors:
            print(json.dumps({'APP_OBSERVATION_DEFERRED':True,'errors':app_observer.last_errors}),flush=True)
        return found
    def stop_owned_apps():
        app_observer.terminate_owned()
    failure=None
    try:
        if args.service_log:
            for fd in (1,2):
                log_limits.watch_native(Path(args.service_log),os.getpid(),fd)
        if args.manage_usb:
            usb_proxy=USBForwarder(config).start()
            emit_event({'NATIVE_USB_FORWARDING_READY':True,'port':config.listen_port})
        listener.bind(str(address));actual.chmod(0o600);listener.listen(8);listener.settimeout(.2)
        threading.Thread(target=bindings,daemon=True).start()
        with open_append(run_directory/'app.log',binary=True) as app_log:
            log_limits.watch_stream(run_directory/'app.log',app_log)
            backend=NativeBackend(staged,run_directory,recover=args.recover_backend).start()
            if not args.recover_backend:
                deadline=time.monotonic()+30+3*config.timeout
                while not stop.is_set() and time.monotonic()<deadline:
                    state=backend.snapshot()
                    if state['ready']:break
                    if state['phase'] in ('failed','stopped'):
                        raise RuntimeError('Backend ended before readiness; inspect backend-status.json and its attempt log')
                    stop.wait(.2)
                else:raise RuntimeError('Backend readiness interrupted or timed out')
            decoder=DecoderRoute(ROOT,run_directory,mode=config.decoder_route,stop=stop,log_limits=log_limits)
            record['decoder_route']=decoder.decision
            record['decoder_route']=decoder.start()
            emit_event({'NATIVE_DECODER_READY':True,'mode':record['decoder_route']['mode'],
                        'host':record['decoder_route']['host']})
            support_environment={'DYLD_INSERT_LIBRARIES':':'.join(str(p) for p in staged_adapters[:2]),
                                 'MIRRA_BIND_SOCKET':str(address)}
            if args.launch_services:
                launch_agent=NativeLaunchAgent(run_directory,support_environment,stop)
                record['launch_agent']=launch_agent.start()
                for fd,name in [(1,'native-launch-agent.stdout.log'),(2,'native-launch-agent.stderr.log')]:
                    log_limits.watch_native(run_directory/name,int(record['launch_agent']['pid']),fd)
                record['observed_app_pids']=[]
                emit_event({'NATIVE_LAUNCH_SERVICES_READY':True,'run':run_directory.name,
                            'backend_ready':backend.snapshot()['ready'],
                            'action':'Open the original iPhone Mirroring app normally; connection requires backend ready'})
            else:
                app=subprocess.Popen([str(executable)],env=dict(os.environ,**support_environment),stdout=app_log,stderr=subprocess.STDOUT)
                helper.expected_pid=app.pid;record['app_pid']=app.pid
                print(json.dumps({'NATIVE_APP_STARTED':app.pid,'run':run_directory.name}),flush=True)
            write_state(run_directory/'native-session-active.json',record)
            if args.service_state:
                write_state(Path(args.service_state),{'phase':'running','directory':str(run_directory),
                    'supervisor_birth':record['supervisor_birth'],
                    'decoder_route':{'enabled':record['decoder_route']['enabled'],'ready':record['decoder_route']['ready']}})
            deadline=deadline_for(duration)
            while not stop.wait(.5) and time.monotonic()<deadline:
                decoder.poll()
                if app is not None and app.poll() is not None:
                    record['app_exit_code']=app.returncode;break
                if args.launch_services:
                    for pid in owned_apps():
                        if pid not in record['observed_app_pids']:
                            record['observed_app_pids'].append(pid)
                            print(json.dumps({'NATIVE_NORMAL_LAUNCH_OBSERVED':pid}),flush=True)
                state=backend.snapshot()
                if state['phase'] in ('failed','stopped'):
                    record['backend_ended_before_stop']=True
                    break
    except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as error:
        failure=str(error);record['error']=failure
        print(json.dumps({'NATIVE_SESSION_ERROR':failure}),flush=True)
    finally:
        stop.set()
        if launch_agent is not None:
            try:record['launch_agent_restored']=launch_agent.close()
            except (OSError,RuntimeError,subprocess.SubprocessError) as error:
                record['launch_agent_restore_error']=str(error)
            try:stop_owned_apps()
            except (OSError,RuntimeError,subprocess.SubprocessError) as error:
                record['app_cleanup_error']=str(error)
        stop_process(app,8)
        if decoder is not None:
            try:record['decoder_cleanup']=decoder.close()
            except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as error:
                record['decoder_restore_error']=str(error)
        if backend is not None:
            record['backend_cleanup']=backend.close()
        if usb_proxy is not None:
            record['usb_proxy_cleanup']=usb_proxy.close()
        record['log_limits']=log_limits.close()
        listener.close()
        actual.unlink(missing_ok=True);link.unlink();private.rmdir();short.rmdir()
        if launch_agent is not None and not record.get('app_cleanup_error'):
            launch_agent.release_ownership()
        record.update(helper_bind_count=helper.bound_count,helper_last_failure=helper.last_failure,
            app_observation_error_count=app_observer.error_count,
            executable_unchanged=hashlib.sha256(executable.read_bytes()).hexdigest()==before,
            signature_verified_after=subprocess.run(['codesign','--verify','--strict',str(config.system_app)],capture_output=True).returncode==0)
        record['backend_exit_code']=record.get('backend_cleanup',{}).get('exit_code')
        record['app_exit_code']=app.poll() if app is not None else None
        (run_directory/'result.json').write_text(json.dumps(record,indent=2)+'\n')
        if args.service_state:
            write_state(Path(args.service_state),{'phase':'stopped','directory':str(run_directory),
                'supervisor_birth':record['supervisor_birth']})
        cleanup=record.get('backend_cleanup',{})
        if (cleanup.get('cleanup_verified') and not cleanup.get('cleanup_error')
                and not record.get('launch_agent_restore_error') and not record.get('app_cleanup_error')
                and not record.get('decoder_restore_error')
                and (decoder is None or record.get('decoder_cleanup',{}).get('cleanup_verified'))):
            retire(run_directory)
        print(json.dumps({'NATIVE_SESSION_ENDED':True,'run':run_directory.name,'executable_unchanged':record['executable_unchanged'],
            'backend_exit_code':record['backend_exit_code']}),flush=True)
    cleanup=record.get('backend_cleanup',{})
    return 1 if (failure or record.get('backend_ended_before_stop') or cleanup.get('cleanup_error')
                 or not cleanup.get('cleanup_verified') or record.get('launch_agent_restore_error')
                 or record.get('decoder_restore_error')
                 or (decoder is not None and not record.get('decoder_cleanup',{}).get('cleanup_verified'))
                 or record.get('app_cleanup_error') or record['log_limits'].get('error')) else 0


if __name__=='__main__':
    raise SystemExit(main())
