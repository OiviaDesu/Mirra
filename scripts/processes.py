"""Observe owned native apps without DNS lookup or treating timeouts as exits."""
import ctypes
import errno
import os
import subprocess
import signal
import time


class _BSDInfo(ctypes.Structure):
    # Darwin sys/proc_info.h, PROC_PIDTBSDINFO (3), including birth timestamp.
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid',
        'rgid', 'svuid', 'svgid', 'reserved')]
    _fields_ += [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in ('nfiles', 'pgid', 'jobc', 'tdev', 'tpgid', 'nice')]
    _fields_ += [('start_seconds', ctypes.c_uint64), ('start_microseconds', ctypes.c_uint64)]


def process_birth(pid):
    """Identify a process across PID reuse; an observation error is not death."""
    libc=ctypes.CDLL(None,use_errno=True)
    libc.proc_pidinfo.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_int]
    info=_BSDInfo();size=ctypes.sizeof(info);ctypes.set_errno(0)
    if libc.proc_pidinfo(pid,3,0,ctypes.byref(info),size)!=size:
        error=ctypes.get_errno()
        if error==errno.ESRCH:return None
        raise OSError(error,'Cannot determine process lifetime')
    return (info.pid,info.uid,info.start_seconds,info.start_microseconds)


class NativeAppObserver:
    def __init__(self, executable, adapter):
        self.executable = str(executable)
        self.adapter = str(adapter)
        self.verified = {}
        self.last_errors = []
        self.error_count = 0
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        self.libc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]

    def identity(self, pid):
        info = _BSDInfo()
        size = ctypes.sizeof(info)
        if self.libc.proc_pidinfo(pid, 3, 0, ctypes.byref(info), size) != size:
            error = ctypes.get_errno()
            if error == errno.ESRCH:
                return None
            raise OSError(error, 'Cannot observe process identity')
        path = ctypes.create_string_buffer(4096)
        if self.libc.proc_pidpath(pid, path, len(path)) <= 0:
            error = ctypes.get_errno()
            if error == errno.ESRCH:
                return None
            raise OSError(error, 'Cannot observe executable path')
        if info.pid != pid or info.uid != os.getuid() or os.fsdecode(path.value) != self.executable:
            return None
        return (pid, info.start_seconds, info.start_microseconds)

    def poll(self):
        self.last_errors = []
        found = []
        try:
            result = subprocess.run(['pgrep', '-x', 'iPhone Mirroring'], capture_output=True, text=True, timeout=3)
            if result.returncode not in (0, 1):
                raise RuntimeError('Process enumeration failed')
            for value in result.stdout.split():
                if not value.isdecimal():
                    continue
                pid = int(value)
                try:
                    identity = self.identity(pid)
                    if identity is None:
                        self.verified.pop(pid, None)
                        continue
                    if self.verified.get(pid) != identity:
                        self.verified.pop(pid, None)
                        # Text mappings only, numeric addresses and ports: never
                        # wait for a live media socket's reverse DNS lookup.
                        images = subprocess.run(['/usr/sbin/lsof', '-n', '-P', '-a', '-p', value, '-d', 'txt', '-Fn'],
                            capture_output=True, text=True, timeout=3)
                        if images.returncode not in (0, 1):
                            raise RuntimeError('Image observation failed')
                        if 'n' + self.adapter not in images.stdout.splitlines():
                            continue
                        if self.identity(pid) != identity:
                            continue
                        self.verified[pid] = identity
                    found.append(pid)
                except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                    self.last_errors.append(type(error).__name__)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            self.last_errors.append(type(error).__name__)
        self.error_count += len(self.last_errors)
        return found

    def terminate_owned(self,timeout=8):
        def signal_owned(sig):
            for pid in self.poll():
                try:os.kill(pid,sig)
                except ProcessLookupError:pass
        signal_owned(signal.SIGTERM)
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if not self.poll() and not self.last_errors:return
            time.sleep(.2)
        signal_owned(signal.SIGKILL)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if not self.poll() and not self.last_errors:return
            time.sleep(.1)
        # A failed observation must remain a cleanup error, never an exit claim.
        raise RuntimeError('Cannot verify native app cleanup')
