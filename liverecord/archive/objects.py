from dataclasses import dataclass
from pathlib import Path
import os
import re
import tempfile
from liverecord.source.responses import sha256
from liverecord.source.errors import IntegrityError


@dataclass(frozen=True)
class StagedObject:
    path: Path
    object_id: str
    length: int


class ObjectStore:
    def __init__(self, db):
        self.directory = db.root / 'objects'
        self.directory.mkdir(exist_ok=True)

    def path(self, object_id):
        if re.fullmatch('[0-9a-f]{64}', object_id) is None:
            raise IntegrityError('invalid object identity')
        return self.directory / object_id

    def stage(self, body):
        fd, name = tempfile.mkstemp(prefix='.stage-', dir=self.directory)
        path = Path(name)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            return StagedObject(path, sha256(body), len(body))
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def seal(self, staged):
        target = self.path(staged.object_id)
        if target.exists():
            self.read_verified(staged.object_id, staged.length)
            staged.path.unlink()
        else:
            os.replace(staged.path, target)
        return staged.object_id

    def read_verified(self, object_id, length):
        try:
            body = self.path(object_id).read_bytes()
        except OSError as exc:
            raise IntegrityError('missing or unreadable object ' + object_id) from exc
        if len(body) != length or sha256(body) != object_id:
            raise IntegrityError('object length or digest mismatch: ' + object_id)
        return body

    def discard_staged(self, staged):
        staged.path.unlink(missing_ok=True)
