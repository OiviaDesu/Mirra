#!/usr/bin/env python3
"""Bounded loopback test in the original app, exiting before its UI starts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
from configuration import ROOT,load_config
from sockets import BindHelper


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    args=parser.parse_args()
    config=load_config(args.config)
    app=config.system_app
    if app!=Path('/System/Applications/iPhone Mirroring.app'):
        parser.error('This probe is scoped to the original installed system app')
    executable=app/'Contents/MacOS/iPhone Mirroring'
    if subprocess.run(['pgrep','-x','iPhone Mirroring'],capture_output=True).returncode==0:
        parser.error('Close the existing Mirroring app before this bounded probe')
    container=Path.home()/'Library/Containers/com.apple.ScreenContinuity/Data/tmp'
    if not container.is_dir():
        parser.error('The system app container has not been initialized')
    dylib=ROOT/'build/native-bind-probe.dylib';dylib.parent.mkdir(exist_ok=True)
    subprocess.run(['xcrun','clang','-dynamiclib','-fobjc-arc','-fblocks','-Wall','-Wextra','-Werror',
        '-framework','Foundation','-framework','Network',str(ROOT/'src/mirroring/sockets.c'),
        str(ROOT/'tests/sockets.m'),'-o',str(dylib)],check=True)
    subprocess.run(['codesign','--force','--sign','-',str(dylib)],capture_output=True,check=True)
    subprocess.run(['codesign','--verify','--strict',str(app)],capture_output=True,check=True)
    before=hashlib.sha256(executable.read_bytes()).hexdigest()
    private=Path(tempfile.mkdtemp(prefix='bind-',dir=container));private.chmod(0o700)
    short=Path(tempfile.mkdtemp(prefix='mb-',dir='/tmp'));short.chmod(0o700)
    link=short/'c';link.symlink_to(private,target_is_directory=True)
    address=link/'s';actual=private/'s'
    helper_socket=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    peer=socket.socket(socket.AF_INET6,socket.SOCK_DGRAM)
    stop=threading.Event();helper=BindHelper(executable);errors=[];process=None
    def bindings():
        while not stop.is_set():
            try:
                connection,_=helper_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                try:errors.append(helper.handle(connection))
                except OSError as error:errors.append(error.errno)
    def echo():
        while not stop.is_set():
            try:
                data,source=peer.recvfrom(64)
                if data==b'bind-probe':peer.sendto(b'ack-v1',source)
            except socket.timeout:continue
            except OSError:return
    try:
        helper_socket.bind(str(address));actual.chmod(0o600);helper_socket.listen(4);helper_socket.settimeout(.2)
        peer.bind(('::1',0));peer.settimeout(.2)
        threading.Thread(target=bindings,daemon=True).start()
        threading.Thread(target=echo,daemon=True).start()
        env=dict(os.environ,DYLD_INSERT_LIBRARIES=str(dylib),MIRRA_BIND_PROBE='exit-before-main-v1',
            MIRRA_EXPECTED_EXECUTABLE=str(executable),MIRRA_BIND_SOCKET=str(address),
            MIRRA_BIND_PEER_PORT=str(peer.getsockname()[1]))
        process=subprocess.Popen([str(executable)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        timeout=False
        try:out,err=process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            timeout=True;process.terminate()
            try:out,err=process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill();out,err=process.communicate(timeout=3)
        events=[]
        for line in out.decode(errors='replace').splitlines():
            try:events.append(json.loads(line))
            except ValueError:pass
        signature=subprocess.run(['codesign','--verify','--strict',str(app)],capture_output=True).returncode==0
        report=dict(scope='Loopback delegated bind and Network listener in the unchanged system app; no live Mirroring',
            executable_sha256=before,executable_unchanged=hashlib.sha256(executable.read_bytes()).hexdigest()==before,
            signature_verified_after=signature,exit_code=process.returncode,timed_out=timeout,
            events=events,helper_bind_count=helper.bound_count,helper_result_codes=errors,
            stderr=err.decode(errors='replace')[:3000],native_mirroring_verified=False)
        config.runtime_directory.mkdir(parents=True,exist_ok=True)
        output=config.runtime_directory/'native-bind-probe.json'
        output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
        return 0 if process.returncode==0 and not timeout and signature and report['executable_unchanged'] else 1
    finally:
        stop.set();helper_socket.close();peer.close()
        if process and process.poll() is None:process.kill();process.wait(timeout=3)
        actual.unlink(missing_ok=True);link.unlink();private.rmdir();short.rmdir()


if __name__=='__main__':
    raise SystemExit(main())
