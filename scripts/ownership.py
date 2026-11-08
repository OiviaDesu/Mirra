"""Private journal for recovering an abandoned, app-specific launch adapter."""
import json
import os
from pathlib import Path
import stat
import tempfile
from processes import process_birth


class LaunchOwnerJournal:
    def __init__(self,runtime_root):
        self.runtime_root=Path(runtime_root).resolve()
        self.path=Path.home()/'Library/Caches/Mirra/launch.json'

    def read(self):
        if not self.path.exists():return None
        info=self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise RuntimeError('Launch ownership journal has unexpected permissions')
        data=json.loads(self.path.read_text())
        if not isinstance(data,dict):raise RuntimeError('Invalid launch ownership journal')
        directory=Path(data.get('directory','')).resolve()
        birth=data.get('owner_birth')
        if data.get('schema_version')!=1 or directory.parent!=self.runtime_root or not directory.name.startswith('native-'):
            raise RuntimeError('Launch ownership journal is outside this runtime directory')
        if not isinstance(birth,list) or len(birth)!=4 or any(type(n) is not int for n in birth) or birth[0]<=0 or birth[1]!=os.getuid():
            raise RuntimeError('Launch ownership journal has an invalid process identity')
        return data

    def abandoned(self):
        data=self.read()
        if data is None:return None
        current=process_birth(data['owner_birth'][0])
        if current is not None and list(current)==data['owner_birth']:
            raise RuntimeError('The existing native launch controller is still running')
        return data

    def publish(self,directory,environment):
        if self.path.exists():raise RuntimeError('Previous launch ownership must be resolved first')
        birth=process_birth(os.getpid())
        if birth is None:raise RuntimeError('Cannot record the current controller identity')
        data={'schema_version':1,'owner_birth':list(birth),'directory':str(Path(directory).resolve()),
              'helper_socket':environment.get('MIRRA_BIND_SOCKET')}
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with tempfile.NamedTemporaryFile(mode='w',dir=self.path.parent,prefix='.launch-owner-',delete=False) as stream:
            json.dump(data,stream);stream.write('\n');stream.flush();os.fsync(stream.fileno())
            pending=Path(stream.name)
        os.replace(pending,self.path)

    def clear(self,directory):
        data=self.read()
        if data is not None:
            if Path(data['directory']).resolve()!=Path(directory).resolve():
                raise RuntimeError('Refusing to remove another controller\'s ownership journal')
            self.path.unlink()


def cleanup_abandoned_socket(value):
    """Remove only the validated socket/alias directories created by this code."""
    if not value:return
    address=Path(value);alias=address.parent;short=alias.parent
    container=(Path.home()/'Library/Containers/com.apple.ScreenContinuity/Data/tmp').resolve()
    if address.name!='s' or alias.name!='c' or short.parent.resolve()!=Path('/tmp').resolve() or not short.name.startswith('mb-'):
        raise RuntimeError('Abandoned helper path is not a managed socket')
    if not short.exists():return
    if short.is_symlink() or short.stat().st_uid!=os.getuid():
        raise RuntimeError('Abandoned helper directory ownership changed')
    if not alias.is_symlink():
        if not alias.exists():short.rmdir();return
        raise RuntimeError('Abandoned helper alias is not a symlink')
    private=alias.resolve()
    if private.parent!=container or not private.name.startswith('mb-'):
        raise RuntimeError('Abandoned helper alias points outside the app container')
    if private.exists():
        if private.is_symlink() or private.stat().st_uid!=os.getuid():
            raise RuntimeError('Abandoned helper container ownership changed')
        socket=private/'s'
        if socket.exists():
            info=socket.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid():
                raise RuntimeError('Abandoned helper path is not an owned socket')
            socket.unlink()
        private.rmdir()
    alias.unlink();short.rmdir()
