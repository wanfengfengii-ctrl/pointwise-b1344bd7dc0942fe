from dataclasses import dataclass
import json
from liverecord.timeline.model import SegmentDeclaration
from liverecord.source.responses import ResponseEvidence
from liverecord.source.errors import IntegrityError


@dataclass(frozen=True)
class SourceBinding:
    declaration: SegmentDeclaration
    revision_id: str
    response: ResponseEvidence

    def to_dict(self):
        return dict(declaration=self.declaration.to_dict(), revision_id=self.revision_id,
                    response=self.response.to_dict())

    @classmethod
    def from_dict(cls, value):
        return cls(SegmentDeclaration.from_dict(value['declaration']), value['revision_id'],
                   ResponseEvidence.from_dict(value['response']))


@dataclass(frozen=True)
class ReadyMedia:
    binding: SourceBinding
    object_id: str
    length: int

    @property
    def declaration(self):
        return self.binding.declaration

    def to_dict(self):
        return dict(binding=self.binding.to_dict(), object_id=self.object_id, length=self.length)

    @classmethod
    def from_dict(cls, value):
        return cls(SourceBinding.from_dict(value['binding']), value['object_id'], value['length'])


class Catalog:
    def __init__(self, db):
        self.db = db

    def get_ready(self, identity):
        row = self.db.connection.execute('SELECT payload FROM ready WHERE k=?',
                                         (identity.key,)).fetchone()
        return ReadyMedia.from_dict(json.loads(row['payload'])) if row else None

    def list_ready(self, source_id):
        rows = self.db.connection.execute('''SELECT r.payload FROM ready r
            JOIN current_media c ON c.k=r.k WHERE c.source=?''', (source_id,)).fetchall()
        return tuple(sorted((ReadyMedia.from_dict(json.loads(r['payload'])) for r in rows),
                            key=lambda item: item.declaration.identity))

    def binding(self, identity):
        ready = self.get_ready(identity)
        if ready is None:
            raise IntegrityError('missing ready binding')
        return ready.binding
