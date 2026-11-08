"""Bound diagnostic append logs, retaining one tail backup per file.

Rotation uses copy-and-truncate on the existing inode. This may lose a line
written during rotation; these files must never carry protocol or state data.
The live file is inspected once per second, so bursts can exceed the threshold
until the next inspection. Native writers must prove O_APPEND before use.
"""
import ctypes
import fcntl
import os
from pathlib import Path
import stat
import tempfile
import threading


def open_append(path, *, binary=False):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_APPEND,0o600)
    return os.fdopen(fd,'ab' if binary else 'a')


def native_fd_is_append(pid,fd):
    # proc_pidfdinfo(PROC_PIDFDVNODEINFO): proc_fileinfo is the first member.
    # The flags field is uint32_t; FAPPEND and O_APPEND share the Darwin bit.
    libc=ctypes.CDLL(None,use_errno=True)
    libc.proc_pidfdinfo.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_void_p,ctypes.c_int]
    buffer=ctypes.create_string_buffer(4096)
    count=libc.proc_pidfdinfo(pid,fd,1,buffer,len(buffer))
    if count<24:
        raise OSError(ctypes.get_errno(),'Cannot inspect native diagnostic descriptor')
    return bool(int.from_bytes(buffer.raw[:4],'little') & os.O_APPEND)


class BoundedLogs:
    def __init__(self,limit=2*1024*1024,on_error=None):
        if limit<1024:raise ValueError('Log limit must be at least 1024 bytes')
        self.limit=limit
        self.on_error=on_error
        self.entries={}
        self.lock=threading.Lock()
        self.stop=threading.Event()
        self.error=None
        self.thread=threading.Thread(target=self._run,name='diagnostic-log-limits',daemon=True)
        self.thread.start()

    def _watch(self,path):
        path=Path(path)
        descriptor=os.open(path,os.O_RDWR|os.O_APPEND|os.O_NOFOLLOW)
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():
            os.close(descriptor)
            raise ValueError('Diagnostic log must be a regular file owned by this user')
        with self.lock:
            if path in self.entries:
                os.close(descriptor)
                return
            self.entries[path]={'fd':descriptor,'inode':info.st_ino,'device':info.st_dev,'rotations':0}

    def watch_stream(self,path,stream):
        if not fcntl.fcntl(stream.fileno(),fcntl.F_GETFL)&os.O_APPEND:
            raise ValueError('Diagnostic writer must use O_APPEND')
        self._watch(path)

    def watch_native(self,path,pid,fd):
        if not native_fd_is_append(pid,fd):
            raise ValueError('Native diagnostic writer does not use O_APPEND')
        self._watch(path)

    def rotate_if_needed(self):
        with self.lock:
            for path,entry in self.entries.items():
                current=os.stat(path,follow_symlinks=False)
                if (current.st_dev,current.st_ino)!=(entry['device'],entry['inode']):
                    raise RuntimeError('Diagnostic path changed during the session')
                size=os.fstat(entry['fd']).st_size
                if size<=self.limit:continue
                tail=os.pread(entry['fd'],self.limit,size-self.limit)
                with tempfile.NamedTemporaryFile(prefix='.log-rotation-',dir=path.parent,delete=False) as temporary:
                    temporary.write(tail)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    replacement=Path(temporary.name)
                os.replace(replacement,path.with_name(path.name+'.1'))
                os.ftruncate(entry['fd'],0)
                entry['rotations']+=1

    def _run(self):
        while not self.stop.wait(1):
            try:self.rotate_if_needed()
            except (OSError,RuntimeError) as error:
                self.error=str(error)
                if self.on_error:self.on_error()
                return

    def close(self):
        self.stop.set()
        self.thread.join(5)
        if self.thread.is_alive():
            self.error='Diagnostic log maintenance did not stop'
            return {'error':self.error}
        try:self.rotate_if_needed()
        except (OSError,RuntimeError) as error:self.error=str(error)
        with self.lock:
            report={'limit_bytes_per_file':self.limit,'backups_per_file':1,
                    'rotations':{p.name:e['rotations'] for p,e in self.entries.items()},'error':self.error}
            for entry in self.entries.values():os.close(entry['fd'])
            self.entries.clear()
        return report
