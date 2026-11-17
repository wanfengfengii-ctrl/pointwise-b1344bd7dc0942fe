from fractions import Fraction
from liverecord.source.registry import Sources
from liverecord.source.errors import (
    InputError, SourceConflict, FetchError, PlaylistError, DeclarationConflict)
from liverecord.source.responses import FetchedResponse, make_evidence
from liverecord.timeline.parser import parse_playlist, parse_duration
from liverecord.timeline.revisions import RevisionStore
from liverecord.acquisition.planner import plan_missing
from liverecord.replay.snapshots import Archive
from tests.support import BusinessTest


class TestSources(BusinessTest):
    def test_registration(self):
        sources = Sources(self.h.db)
        original = sources.get('river')
        self.assertEqual(original, sources.register('river', original.url))
        with self.assertRaises(SourceConflict):
            sources.register('river', self.h.source.base + '/other')
        self.h.reopen()
        self.assertEqual(original, Sources(self.h.db).get('river'))
        self.h.collector.refresh('river')
        self.assertEqual(self.h.source.counts[('/redirect', None)], 1)

    def test_invalid_input(self):
        for identity, url in (('', self.h.source.url), ('x', 'http:///bad'),
                              ('x', 'file:///bad'), ('x', 'https://')):
            with self.subTest(identity=identity, url=url):
                with self.assertRaises(InputError):
                    Sources(self.h.db).register(identity, url)
        self.assertEqual(self.h.db.connection.execute('SELECT COUNT(*) FROM sources').fetchone()[0], 1)

    def test_registration_persistence(self):
        self.h.db.initialize()
        self.h.db.initialize()
        self.h.day()
        before = self.h.freeze('daily').to_dict()
        Sources(self.h.db).register('user-station', self.h.source.base + '/user')
        self.h.reopen()
        self.h.db.initialize()
        self.assertEqual(Sources(self.h.db).get('user-station').url,
                         self.h.source.base + '/user')
        self.assertEqual(self.h.read('daily').capture.to_dict(), before)
        self.h.db.connection.execute('PRAGMA user_version=99')
        with self.assertRaises(InputError):
            self.h.db.initialize()
        self.h.db.connection.execute('PRAGMA user_version=1')


class TestTimeline(BusinessTest):
    def parse(self, text):
        body = text.encode()
        response = FetchedResponse(body, make_evidence(body, self.h.source.url,
                                  self.h.source.base + '/live/list.m3u8'))
        return parse_playlist('river', response)

    def test_context_and_discontinuity(self):
        _, revision = self.h.collector.refresh('river')
        a, b, c = revision.declarations
        self.assertEqual(a.uri, self.h.source.base + '/live/blob')
        self.assertEqual(a.byte_range.to_list(), [0, 4])
        self.assertEqual(b.byte_range.to_list(), [4, 5])
        self.assertEqual([x.duration for x in (a, b, c)],
                         [Fraction(1, 8), Fraction(3, 8), Fraction(1, 2)])
        first = '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:5\n#EXT-X-DISCONTINUITY-SEQUENCE:8\n#EXTINF:1,\na'
        second = first.replace('SEQUENCE:8', 'SEQUENCE:9').replace('\na', '\nb')
        for text in (first, second, first):
            self.h.source.playlist = text.encode()
            self.h.collector.refresh('river')
        store = RevisionStore(self.h.db)
        identities = [self.parse(t).declarations[0].identity for t in (first, second)]
        self.assertNotEqual(identities[0], identities[1])
        self.assertNotEqual(store.declaration(identities[0]).uri,
                            store.declaration(identities[1]).uri)
        revision = self.parse('#EXTM3U\n#EXTINF:1,\na?token=x%2Fy\n#EXT-X-DISCONTINUITY\n#EXTINF:2,\nb')
        self.assertEqual(revision.declarations[1].identity.discontinuity_sequence, 1)
        self.assertTrue(revision.declarations[0].uri.endswith('a?token=x%2Fy'))

    def test_range_scope(self):
        valid = self.parse('#EXTM3U\n#EXTINF:1,\n#EXT-X-BYTERANGE:2@3\nblob\n'
                           '#EXTINF:1,\n#EXT-X-BYTERANGE:1\nblob\n'
                           '#EXTINF:1,\n#EXT-X-BYTERANGE:1@0\nblob')
        self.assertEqual([d.byte_range.offset for d in valid.declarations], [3, 5, 0])
        for middle, last in (('#EXTINF:1,\ntail\n', 'blob'), ('', 'tail')):
            invalid = ('#EXTM3U\n#EXTINF:1,\n#EXT-X-BYTERANGE:2@0\nblob\n' +
                       middle + '#EXTINF:1,\n#EXT-X-BYTERANGE:2\n' + last)
            with self.assertRaises(PlaylistError):
                self.parse(invalid)
        self.assertEqual(parse_duration('0.100000000000000001'),
                         Fraction(100000000000000001, 10**18))
        for bad in ('0', '-1', 'nan', '1e-3', '1/3'):
            with self.assertRaises(PlaylistError):
                parse_duration(bad)

    def test_rejected_revision(self):
        self.h.collector.refresh('river')
        store = RevisionStore(self.h.db)
        before = store.current('river')[0]
        plan = plan_missing(self.h.db, 'river')
        count = self.h.db.connection.execute('SELECT COUNT(*) FROM declarations').fetchone()[0]
        legal = self.h.source.playlist
        invalid = [
            b'#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:99\n#EXTINF:1,\na\n#EXTINF:2,',
            legal.replace(b'0.125', b'0.126'),
            legal.replace(b'4@0', b'4@1'),
            legal.replace(b'blob', b'another'),
            b'#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:39\n#EXTINF:1,\na\n'
            b'#EXTINF:0.126,\n#EXT-X-BYTERANGE:4@0\nblob\n',
            b'#EXTM3U\n#EXT-X-PART:DURATION=0.1,URI="a"',
            b'#EXTM3U\n#EXT-X-SKIP:SKIPPED-SEGMENTS=1',
            b'#EXTM3U\n#EXT-X-MAP:URI="a"',
        ]
        for body in invalid:
            with self.subTest(body=body):
                self.h.source.playlist = body
                with self.assertRaises(PlaylistError):
                    self.h.collector.refresh('river')
                self.assertEqual(store.current('river')[0], before)
                self.assertEqual(plan_missing(self.h.db, 'river'), plan)
                self.assertEqual(self.h.db.connection.execute(
                    'SELECT COUNT(*) FROM declarations').fetchone()[0], count)
        self.h.reopen()
        self.assertEqual(RevisionStore(self.h.db).current('river')[0], before)

    def test_transport_rejection(self):
        self.h.collector.refresh('river')
        before = RevisionStore(self.h.db).current('river')[0]
        for fault in ('transport', 'http'):
            self.h.source.fail_once('/live/list.m3u8', None, fault)
            with self.assertRaises(FetchError):
                self.h.collector.refresh('river')
            self.assertEqual(RevisionStore(self.h.db).current('river')[0], before)
        self.h.source.playlist = b'#EXTM3U'
        self.h.collector.refresh('river')
        self.assertEqual(plan_missing(self.h.db, 'river').items, ())
        self.assertEqual(Archive(self.h.db).freeze('river', 'empty').total, 0)
