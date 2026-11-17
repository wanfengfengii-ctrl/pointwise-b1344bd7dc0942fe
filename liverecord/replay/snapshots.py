from dataclasses import dataclass
from fractions import Fraction
import json
from liverecord.archive.catalog import Catalog, ReadyMedia
from liverecord.source.registry import Sources
from liverecord.source.responses import canonical, sha256
from liverecord.source.errors import (
    InputError, CaptureConflict, CaptureNotFound, IntegrityError, PublicationError)
from liverecord.replay.timing import build_positions


def fraction_pair(value):
    return [value.numerator, value.denominator]


def input_fingerprint(source_id, source_url, media):
    return sha256(canonical(dict(source=source_id, url=source_url,
                                 media=[x.to_dict() for x in media])).encode())


@dataclass(frozen=True)
class CaptureItem:
    media: ReadyMedia
    start: Fraction
    end: Fraction

    def to_dict(self):
        return dict(media=self.media.to_dict(), start=fraction_pair(self.start),
                    end=fraction_pair(self.end))

    @classmethod
    def from_dict(cls, value):
        return cls(ReadyMedia.from_dict(value['media']),
                   Fraction(*value['start']), Fraction(*value['end']))


@dataclass(frozen=True)
class Capture:
    capture_id: str
    source_id: str
    source_url: str
    fingerprint: str
    items: tuple
    total: Fraction

    def to_dict(self):
        return dict(capture_id=self.capture_id, source_id=self.source_id,
                    source_url=self.source_url, fingerprint=self.fingerprint,
                    items=[x.to_dict() for x in self.items], total=fraction_pair(self.total))


def load_capture(db, capture_id):
    row = db.connection.execute('SELECT * FROM captures WHERE id=?', (capture_id,)).fetchone()
    if row is None:
        raise CaptureNotFound(capture_id)
    try:
        head = json.loads(row['payload'])
        rows = db.connection.execute(
            'SELECT ordinal,payload FROM capture_items WHERE capture=? ORDER BY ordinal',
            (capture_id,)).fetchall()
        if [r['ordinal'] for r in rows] != list(range(head['count'])):
            raise IntegrityError('incomplete capture items')
        return Capture(capture_id, row['source'], head['source_url'], head['fingerprint'],
                       tuple(CaptureItem.from_dict(json.loads(r['payload'])) for r in rows),
                       Fraction(*head['total']))
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        raise IntegrityError('invalid capture encoding') from exc


class Archive:
    def __init__(self, db, checkpoint=None):
        self.db = db
        self.checkpoint = checkpoint

    def freeze(self, source_id, capture_id):
        if not isinstance(capture_id, str) or not capture_id.strip():
            raise InputError('capture_id must be nonempty')
        try:
            with self.db.transaction() as con:
                source = Sources(self.db).get(source_id)
                media = Catalog(self.db).list_ready(source_id)
                fingerprint = input_fingerprint(source_id, source.url, media)
                row = con.execute('SELECT id FROM captures WHERE id=?',
                                  (capture_id,)).fetchone()
                if row:
                    previous = load_capture(self.db, capture_id)
                    if previous.source_id != source_id or previous.fingerprint != fingerprint:
                        raise CaptureConflict(capture_id)
                    return previous
                positions, total = build_positions(x.declaration.duration for x in media)
                head = dict(source_url=source.url, fingerprint=fingerprint,
                            total=fraction_pair(total), count=len(media))
                con.execute('INSERT INTO captures VALUES (?,?,?)',
                            (capture_id, source_id, canonical(head)))
                if self.checkpoint:
                    self.checkpoint('capture_header')
                items = []
                for ordinal, (ready, span) in enumerate(zip(media, positions)):
                    item = CaptureItem(ready, span.start, span.end)
                    con.execute('INSERT INTO capture_items VALUES (?,?,?)',
                                (capture_id, ordinal, canonical(item.to_dict())))
                    items.append(item)
                    if self.checkpoint:
                        self.checkpoint('capture_item')
                if self.checkpoint:
                    self.checkpoint('capture_before_commit')
                return Capture(capture_id, source_id, source.url, fingerprint,
                               tuple(items), total)
        except OSError as exc:
            raise PublicationError(str(exc)) from exc
