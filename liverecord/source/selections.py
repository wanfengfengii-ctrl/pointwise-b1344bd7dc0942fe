import json
from liverecord.source.errors import IntegrityError
from liverecord.source.responses import canonical, sha256, ResponseEvidence


def version_source_id(source_id, media_url):
    return source_id + '#selected-' + sha256(media_url.encode('utf-8'))


def _content(receipt):
    return dict(source_id=receipt['source_id'], source_url=receipt['source_url'],
                max_bandwidth=receipt['max_bandwidth'], bandwidth=receipt['bandwidth'],
                media_url=receipt['media_url'], revision_id=receipt['revision_id'],
                master_evidence=receipt['master_evidence'].to_dict())


def receipt_identity(receipt):
    return sha256(canonical(_content(receipt)).encode() + b'\0' + receipt['master_body'])


def build_receipt(source, max_bandwidth, bandwidth, media_url, revision_id,
                  master_response):
    receipt = dict(selection_id=None, source_id=source.source_id, source_url=source.url,
                   max_bandwidth=max_bandwidth, bandwidth=bandwidth,
                   media_url=media_url, revision_id=revision_id,
                   master_body=master_response.body,
                   master_evidence=master_response.evidence)
    receipt['selection_id'] = receipt_identity(receipt)
    return receipt


def encode_selection(receipt):
    value = dict(receipt)
    value['master_body'] = receipt['master_body'].decode('utf-8')
    value['master_evidence'] = receipt['master_evidence'].to_dict()
    return value


class SelectionStore:
    def __init__(self, db):
        self.db = db

    def accept(self, receipt):
        with self.db.transaction() as con:
            con.execute('INSERT OR IGNORE INTO selections VALUES (?,?,?,?)',
                        (receipt['selection_id'], receipt['source_id'],
                         receipt['master_body'], canonical(_content(receipt))))
            con.execute('''INSERT INTO current_selection VALUES (?,?)
                        ON CONFLICT(source) DO UPDATE SET selection=excluded.selection''',
                        (receipt['source_id'], receipt['selection_id']))
        return receipt

    def load(self, selection_id):
        row = self.db.connection.execute('SELECT body,payload FROM selections WHERE id=?',
                                         (selection_id,)).fetchone()
        if row is None:
            raise IntegrityError('missing selection receipt')
        body = bytes(row['body'])
        try:
            content = json.loads(row['payload'])
            receipt = dict(selection_id=selection_id,
                              source_id=content['source_id'],
                              source_url=content['source_url'],
                              max_bandwidth=content['max_bandwidth'],
                              bandwidth=content['bandwidth'],
                              media_url=content['media_url'],
                              revision_id=content['revision_id'],
                              master_body=body,
                              master_evidence=ResponseEvidence.from_dict(
                                  content['master_evidence']))
        except (ValueError, KeyError, TypeError) as exc:
            raise IntegrityError('invalid selection encoding') from exc
        if receipt_identity(receipt) != selection_id:
            raise IntegrityError('selection evidence fingerprint mismatch')
        return receipt

    def current(self, source_id):
        row = self.db.connection.execute(
            'SELECT selection FROM current_selection WHERE source=?', (source_id,)).fetchone()
        return self.load(row['selection']) if row else None

    def mark_ready(self, con, identity_key, selection_id):
        con.execute('INSERT OR IGNORE INTO ready_selection VALUES (?,?)',
                    (identity_key, selection_id))

    def ready_selection_id(self, identity_key):
        row = self.db.connection.execute(
            'SELECT selection FROM ready_selection WHERE k=?', (identity_key,)).fetchone()
        return row['selection'] if row else None

    def ready_selection(self, identity_key):
        selection_id = self.ready_selection_id(identity_key)
        return self.load(selection_id) if selection_id is not None else None
