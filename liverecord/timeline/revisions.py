import json
from liverecord.timeline.model import PlaylistRevision, SegmentDeclaration
from liverecord.source.responses import canonical, sha256, ResponseEvidence
from liverecord.source.registry import Sources
from liverecord.source.errors import DeclarationConflict, IntegrityError, PlaylistError


class RevisionStore:
    def __init__(self, db):
        self.db = db

    def accept(self, revision):
        source = Sources(self.db).get(revision.source_id)
        evidence = revision.evidence
        if (evidence.requested_url != source.url or evidence.request_range is not None
                or evidence.status != 200 or evidence.length != len(revision.body)
                or evidence.sha256 != sha256(revision.body)):
            raise PlaylistError('invalid playlist response evidence')
        keys = [d.identity.key for d in revision.declarations]
        if len(set(keys)) != len(keys):
            raise DeclarationConflict('duplicate identity in revision')
        payload = canonical(revision.to_dict())
        revision_id = sha256(payload.encode() + b'\0' + revision.body)
        with self.db.transaction() as con:
            for declaration in revision.declarations:
                if declaration.identity.source_id != revision.source_id:
                    raise DeclarationConflict('source mismatch')
                row = con.execute('SELECT payload FROM declarations WHERE k=?',
                                  (declaration.identity.key,)).fetchone()
                if row and row['payload'] != canonical(declaration.to_dict()):
                    raise DeclarationConflict(declaration.identity.key)
            con.execute('INSERT OR IGNORE INTO revisions VALUES (?,?,?,?)',
                        (revision_id, revision.source_id, revision.body, payload))
            for declaration in revision.declarations:
                con.execute('INSERT OR IGNORE INTO declarations VALUES (?,?,?)',
                            (declaration.identity.key, revision.source_id,
                             canonical(declaration.to_dict())))
            con.execute('''INSERT INTO current_revision VALUES (?,?)
                        ON CONFLICT(source) DO UPDATE SET revision=excluded.revision''',
                        (revision.source_id, revision_id))
        return revision_id

    def load(self, revision_id):
        row = self.db.connection.execute('SELECT * FROM revisions WHERE id=?',
                                         (revision_id,)).fetchone()
        if row is None:
            raise IntegrityError('missing playlist revision')
        payload = json.loads(row['payload'])
        body = bytes(row['body'])
        if sha256(canonical(payload).encode() + b'\0' + body) != revision_id:
            raise IntegrityError('revision evidence fingerprint mismatch')
        return PlaylistRevision(payload['source_id'], body,
                                ResponseEvidence.from_dict(payload['evidence']),
                                tuple(SegmentDeclaration.from_dict(x)
                                      for x in payload['declarations']))

    def current(self, source_id):
        Sources(self.db).get(source_id)
        row = self.db.connection.execute(
            'SELECT revision FROM current_revision WHERE source=?', (source_id,)).fetchone()
        return (row['revision'], self.load(row['revision'])) if row else (None, None)

    def declaration(self, identity):
        row = self.db.connection.execute('SELECT payload FROM declarations WHERE k=?',
                                         (identity.key,)).fetchone()
        if row is None:
            raise IntegrityError('missing declaration')
        return SegmentDeclaration.from_dict(json.loads(row['payload']))

    def evidence(self, revision_id):
        return self.load(revision_id).evidence
