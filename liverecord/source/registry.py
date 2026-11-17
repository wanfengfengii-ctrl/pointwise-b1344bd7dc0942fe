from dataclasses import dataclass
from urllib.parse import urlsplit
from liverecord.source.errors import InputError, SourceConflict


def validate_url(url):
    try:
        parsed = urlsplit(url)
        if (not isinstance(url, str) or parsed.scheme not in ('http', 'https')
                or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment
                or any(c.isspace() for c in url)):
            raise ValueError
        parsed.port
    except (ValueError, TypeError, AttributeError):
        raise InputError('expected an absolute HTTP/HTTPS URL') from None
    return url


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    url: str


class Sources:
    def __init__(self, db):
        self.db = db

    def register(self, source_id, url):
        if not isinstance(source_id, str) or not source_id.strip():
            raise InputError('source_id must be nonempty')
        validate_url(url)
        with self.db.transaction() as con:
            row = con.execute('SELECT url FROM sources WHERE id=?', (source_id,)).fetchone()
            if row and row['url'] != url:
                raise SourceConflict(source_id)
            if not row:
                con.execute('INSERT INTO sources VALUES (?,?)', (source_id, url))
        return self.get(source_id)

    def get(self, source_id):
        row = self.db.connection.execute(
            'SELECT url FROM sources WHERE id=?', (source_id,)).fetchone()
        if row is None:
            raise InputError('unknown source: ' + str(source_id))
        return SourceRecord(source_id, row['url'])
