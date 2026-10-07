"""Atomic publication helper for provider-paris."""
import os
from pathlib import Path
import tempfile


def atomic_write(path, content):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,name=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.part',dir=path.parent)
    try:
        with os.fdopen(descriptor,'wb') as handle:
            handle.write(content);handle.flush();os.fsync(handle.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)
