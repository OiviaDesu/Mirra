"""Atomic private state updates; readers never see a partially written record."""
import json
import os
from pathlib import Path
import tempfile


def write_state(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', prefix='.state-', dir=path.parent,
                                     delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
