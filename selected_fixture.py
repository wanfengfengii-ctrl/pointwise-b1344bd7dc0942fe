"""Finite HTTP material for the selected-playlist extension; no selector implementation."""
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import hashlib
import sys
import threading
from liverecord.source.http import HttpClient
from liverecord.source.responses import FetchedResponse, make_evidence
from liverecord.timeline.parser import parse_playlist

MASTER = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nA.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=200\nB.m3u8\n"
MEDIA = b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:40\n#EXTINF:0.5,\ns40\n"

class SelectedSource:
    def __init__(self):
        self.master = MASTER
        self.routes = {
            "/entry": (302, b"", (("Location", "/catalog/master.m3u8"),)),
            "/catalog/A.m3u8": (302, b"", (("Location", "/media/A/list.m3u8"),)),
            "/catalog/B.m3u8": (302, b"", (("Location", "/media/B/list.m3u8"),)),
            "/media/A/list.m3u8": (200, MEDIA, ()),
            "/media/B/list.m3u8": (200, MEDIA, ()),
            "/media/A/s40": (200, b"A", ()),
            "/media/B/s40": (200, b"B", ()),
        }
        self.counts = Counter()
        self.faults = {}
        self.server = None
        self.thread = None

    def route(self, path):
        if path == "/catalog/master.m3u8":
            return 200, self.master, ()
        return self.routes.get(path, (404, b"missing", ()))

    def fail_once(self, path, fault="http"):
        if fault not in ("http", "short", "transport"):
            raise ValueError("unknown fixture fault")
        self.faults.setdefault(path, []).append(fault)

    def start(self):
        if self.server is not None:
            raise RuntimeError("already started")
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"
            def date_time_string(self, timestamp=None):
                return "Thu, 01 Jan 1970 00:00:00 GMT"
            def log_message(self, *args):
                pass
            def do_GET(self):
                owner.counts[(self.path, self.headers.get("Range"))] += 1
                queue = owner.faults.get(self.path, [])
                fault = queue.pop(0) if queue else None
                status, body, headers = owner.route(self.path)
                if fault == "http":
                    status, body, headers = 503, b"controlled failure", ()
                original = body
                if fault in ("short", "transport"):
                    body = body[:-1]
                length = len(original) if fault == "transport" else len(body)
                self.send_response(status)
                self.send_header("Content-Length", str(length))
                self.send_header("Content-Encoding", "identity")
                self.send_header("Digest", "sha-256=" + base64.b64encode(
                    hashlib.sha256(original).digest()).decode("ascii"))
                for key, value in headers:
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02})
        self.thread.start()
        return self

    @property
    def url(self):
        return self.base + "/entry"

    def close(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
            self.server = None
            self.thread = None

@contextmanager
def serving():
    source = SelectedSource().start()
    try:
        yield source
    finally:
        source.close()

def check_data():
    source = SelectedSource()
    assert source.route("/entry")[2] == (("Location", "/catalog/master.m3u8"),)
    assert source.route("/catalog/A.m3u8")[2] == (("Location", "/media/A/list.m3u8"),)
    assert source.route("/catalog/B.m3u8")[2] == (("Location", "/media/B/list.m3u8"),)
    assert source.route("/catalog/master.m3u8")[1] == MASTER
    for name in ("A", "B"):
        requested = "https://fixture.test/catalog/" + name + ".m3u8"
        final = "https://fixture.test/media/" + name + "/list.m3u8"
        body = source.route("/media/" + name + "/list.m3u8")[1]
        revision = parse_playlist(name, FetchedResponse(
            body, make_evidence(body, requested, final)))
        item, = revision.declarations
        assert item.identity.to_list() == [name, 0, 40]
        assert str(item.duration) == "1/2"
        assert item.uri == "https://fixture.test/media/" + name + "/s40"
        assert source.route("/media/" + name + "/s40")[1] == name.encode("ascii")
    print("SELECTED_FIXTURE_DATA_OK")

def check_http():
    with serving() as source:
        client = HttpClient()
        master = client.fetch(source.url)
        assert master.body == MASTER
        assert master.evidence.requested_url == source.url
        assert master.evidence.final_url == source.base + "/catalog/master.m3u8"
        for name in ("A", "B"):
            response = client.fetch(source.base + "/catalog/" + name + ".m3u8")
            revision = parse_playlist(name, response)
            body = client.fetch(revision.declarations[0].uri).body
            assert body == name.encode("ascii")
        source.fail_once("/media/A/s40", "http")
        from liverecord.source.errors import FetchError
        try:
            client.fetch(source.base + "/media/A/s40")
        except FetchError:
            pass
        else:
            raise AssertionError("503 must fail")
        assert client.fetch(source.base + "/media/A/s40").body == b"A"
        assert source.counts[("/entry", None)] == 1
        print("SELECTED_FIXTURE_HTTP_OK")

if __name__ == "__main__":
    if sys.argv[1:] == ["--check-data"]:
        check_data()
    elif sys.argv[1:] == ["--http-check"]:
        check_http()
    else:
        raise SystemExit("use --check-data or --http-check")
