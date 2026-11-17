from collections import Counter
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import base64
import hashlib
import json
import re
import tempfile
import threading
import unittest

from liverecord.archive.database import Database
from liverecord.source.registry import Sources
from liverecord.source.responses import make_evidence
from liverecord.timeline.model import SegmentIdentity, SegmentDeclaration, PlaylistRevision
from liverecord.timeline.revisions import RevisionStore
from liverecord.acquisition.collector import Collector
from liverecord.replay.snapshots import Archive
from liverecord.replay.reader import Replay


class LocalSource:
    def __init__(self):
        self.day = json.loads((Path(__file__).resolve().parents[1] /
                               'fixtures/day.json').read_text(encoding='utf-8'))
        self.playlist = self.day['playlist'].encode()
        self.resources = {'/live/blob': self.day['blob'].encode(),
                          '/live/tail': self.day['tail'].encode(),
                          '/live/a': b'A', '/live/b': b'B', '/live/c': b'C'}
        self.faults = {}
        self.counts = Counter()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def log_message(self, *args):
                pass

            def do_GET(self):
                key = (self.path, self.headers.get('Range'))
                owner.counts[key] += 1
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/live/list.m3u8')
                    self.end_headers()
                    return
                queue = owner.faults.get(key, [])
                fault = queue.pop(0) if queue else None
                if fault == 'http':
                    self.send_error(503)
                    return
                if self.path == '/live/list.m3u8':
                    body = owner.playlist
                    status, content_range = 200, None
                elif self.path in owner.resources:
                    complete = owner.resources[self.path]
                    body = complete
                    status, content_range = 200, None
                    if key[1] is not None:
                        match = re.fullmatch(r'bytes=([0-9]+)-([0-9]+)', key[1])
                        if match is None or int(match[2]) >= len(complete):
                            self.send_error(416)
                            return
                        start, end = int(match[1]), int(match[2])
                        body = complete[start:end + 1]
                        status = 206
                        content_range = 'bytes %d-%d/%d' % (start, end, len(complete))
                else:
                    self.send_error(404)
                    return
                expected = hashlib.sha256(body).digest()
                declared_length = len(body)
                if fault == 'short':
                    body = body[:-1]
                    declared_length = len(body)
                elif fault == 'transport':
                    body = body[:-1]
                elif fault == 'wrong-range':
                    content_range = 'bytes 0-%d/99' % (len(body) - 1)
                elif fault == 'no-range':
                    content_range = None
                elif fault == 'status200':
                    status, content_range = 200, None
                elif fault == 'digest':
                    body = b'!' * len(body)
                self.send_response(status)
                self.send_header('Content-Length', str(declared_length))
                self.send_header('Content-Encoding', 'identity')
                if content_range:
                    self.send_header('Content-Range', content_range)
                digest = base64.b64encode(expected).decode()
                self.send_header('Digest', 'sha-256=' + ('???' if fault == 'bad-digest' else digest))
                self.send_header('X-Observation', self.path)
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.02})
        self.thread.start()

    @property
    def url(self):
        return self.base + '/redirect'

    def fail_once(self, path, range_header, fault):
        self.faults.setdefault((path, range_header), []).append(fault)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class Harness:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='liverecord-')
        self.root = Path(self.temporary.name)
        self.source = LocalSource()
        self.db = Database(self.root / 'archive')
        Sources(self.db).register('river', self.source.url)
        self.collector = Collector(self.db)

    def reopen(self):
        self.db.close()
        self.db = Database(self.root / 'archive')
        self.collector = Collector(self.db)
        return self.db

    def close(self):
        self.db.close()
        self.source.close()
        self.temporary.cleanup()

    def day(self):
        self.collector.refresh('river')
        return self.collector.collect('river')

    def read(self, capture):
        return Replay(self.db).read(capture)

    def freeze(self, capture):
        return Archive(self.db).freeze('river', capture)


class BusinessTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()


def domain_revision(harness, durations=(Fraction(1, 3), Fraction(2, 7))):
    # Explicit domain fixture, not HLS syntax: permits arbitrary rational seconds.
    declarations = tuple(
        SegmentDeclaration(SegmentIdentity('river', 0, index),
                           harness.source.base + '/live/' + name, duration)
        for index, (name, duration) in enumerate(zip(('a', 'b', 'c'), durations)))
    body = json.dumps({'fixture_kind': 'complete-segment domain declarations',
                       'durations': [str(x) for x in durations]}, sort_keys=True).encode()
    evidence = make_evidence(body, harness.source.url,
                             headers=(('X-Fixture-Origin', 'domain-rational-v1'),))
    revision = PlaylistRevision('river', body, evidence, declarations)
    return RevisionStore(harness.db).accept(revision)


def emit_scenario(case_id, inputs, expected, actual, downstream_expected, downstream_actual,
                  code_paths, oracle):
    print('POINTWISE_SCENARIO ' + json.dumps(
        dict(case_id=case_id, input=inputs, expected=expected, actual=actual,
             downstream_expected=downstream_expected, downstream_actual=downstream_actual,
             code_paths=code_paths, oracle=oracle), sort_keys=True))
