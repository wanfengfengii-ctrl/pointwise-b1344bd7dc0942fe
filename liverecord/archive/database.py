from contextlib import contextmanager
from pathlib import Path
import sqlite3
from liverecord.source.errors import InputError, PublicationError

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, url TEXT NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS revisions (
       id TEXT PRIMARY KEY, source TEXT NOT NULL REFERENCES sources(id),
       body BLOB NOT NULL, payload TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS declarations (
       k TEXT PRIMARY KEY, source TEXT NOT NULL REFERENCES sources(id),
       payload TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS current_revision (
       source TEXT PRIMARY KEY REFERENCES sources(id),
       revision TEXT NOT NULL REFERENCES revisions(id))''',
    '''CREATE TABLE IF NOT EXISTS attempts (
       id INTEGER PRIMARY KEY, source TEXT NOT NULL REFERENCES sources(id),
       k TEXT NOT NULL, revision TEXT NOT NULL REFERENCES revisions(id),
       success INTEGER NOT NULL CHECK(success IN (0,1)), detail TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS ready (
       k TEXT PRIMARY KEY REFERENCES declarations(k),
       source TEXT NOT NULL REFERENCES sources(id),
       d TEXT NOT NULL, m TEXT NOT NULL, payload TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS current_media (
       k TEXT PRIMARY KEY REFERENCES ready(k),
       source TEXT NOT NULL REFERENCES sources(id))''',
    '''CREATE TABLE IF NOT EXISTS captures (
       id TEXT PRIMARY KEY, source TEXT NOT NULL REFERENCES sources(id),
       payload TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS capture_items (
       capture TEXT NOT NULL REFERENCES captures(id), ordinal INTEGER NOT NULL,
       payload TEXT NOT NULL, PRIMARY KEY(capture,ordinal))''',
    '''CREATE TABLE IF NOT EXISTS verifications (
       id INTEGER PRIMARY KEY, capture TEXT NOT NULL, payload TEXT NOT NULL)''',
    'CREATE INDEX IF NOT EXISTS ready_order ON ready(source,d,m)',
    'CREATE INDEX IF NOT EXISTS revision_source ON revisions(source)',
)


class Database:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.root / 'archive.sqlite', isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.execute('PRAGMA synchronous=FULL')
        self.initialize()

    def initialize(self):
        version = self.connection.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            raise InputError('unsupported archive schema version')
        if version == 0:
            tables = self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if tables:
                raise InputError('unversioned nonempty database')
        with self.transaction():
            for statement in SCHEMA:
                self.connection.execute(statement)
            self.connection.execute('PRAGMA user_version=1')

    @contextmanager
    def transaction(self):
        if self.connection.in_transaction:
            raise PublicationError('nested archive transaction')
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            yield self.connection
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def close(self):
        self.connection.close()
