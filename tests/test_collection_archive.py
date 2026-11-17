from fractions import Fraction
from liverecord.archive.catalog import Catalog
from liverecord.archive.publication import Publisher
from liverecord.archive.objects import ObjectStore
from liverecord.acquisition.collector import Collector
from liverecord.acquisition.validation import validate_media
from liverecord.acquisition.ranges import make_range_request
from liverecord.timeline.revisions import RevisionStore
from liverecord.source.http import HttpClient
from liverecord.source.errors import ArchiveConflict
from tests.support import BusinessTest, emit_scenario


class TestCollection(BusinessTest):
    def test_bad_range_retry(self):
        self.h.source.fail_once('/live/blob', 'bytes=4-8', 'short')
        self.h.collector.refresh('river')
        first = self.h.collector.collect('river')
        self.assertEqual([x.success for x in first.items], [True, False, True])
        self.h.freeze('before')
        old = self.h.read('before')
        self.assertEqual([x.body for x in old.items], [b'ABCD', b'JKLMNO'])
        self.assertEqual([x.start for x in old.items], [0, Fraction(1, 8)])
        self.assertEqual(old.total, Fraction(5, 8))
        retry = self.h.collector.collect('river')
        self.assertEqual(len(retry.items), 1)
        self.assertTrue(retry.items[0].success)
        self.h.freeze('after')
        new = self.h.read('after')
        self.assertEqual([x.body for x in new.items], [b'ABCD', b'EFGHI', b'JKLMNO'])
        self.assertEqual([x.start for x in new.items], [0, Fraction(1, 8), Fraction(1, 2)])
        self.assertEqual(new.total, 1)
        self.assertEqual(self.h.read('before'), old)
        self.assertEqual(self.h.collector.collect('river').items, ())
        counts = self.h.source.counts
        self.assertEqual([counts[('/live/blob', 'bytes=0-3')],
                          counts[('/live/blob', 'bytes=4-8')],
                          counts[('/live/tail', None)]], [1, 2, 1])
        attempts = self.h.db.connection.execute(
            'SELECT success FROM attempts ORDER BY id').fetchall()
        self.assertEqual([r[0] for r in attempts], [1, 0, 1, 1])
        emit_scenario('N4', {'resource': 'ABCDEFGHI', 'durations': ['1/8', '3/8', '1/2'],
                            'first_second_range': 'short'},
                      ['ABCD', 'JKLMNO'], [x.body.decode() for x in old.items],
                      {'new_starts': ['0', '1/8', '1/2'], 'total': '1'},
                      {'new_starts': [str(x.start) for x in new.items], 'total': str(new.total)},
                      ['liverecord/acquisition/collector.py', 'liverecord/acquisition/ranges.py',
                       'liverecord/archive/publication.py', 'liverecord/replay/snapshots.py'],
                      'literal byte slices and hand sums 1/8+1/2=5/8; 1/8+3/8+1/2=1')

    def test_range_contract(self):
        self.h.collector.refresh('river')
        for fault in ('wrong-range', 'no-range', 'status200', 'transport'):
            with self.subTest(fault=fault):
                self.h.source.fail_once('/live/blob', 'bytes=4-8', fault)
                receipt = self.h.collector.collect('river')
                failed = [x for x in receipt.items if not x.success]
                self.assertEqual(len(failed), 1)
                self.assertEqual(failed[0].identity.media_sequence, 41)
                self.assertEqual(len(Catalog(self.h.db).list_ready('river')), 2)
        self.assertTrue(self.h.collector.collect('river').items[0].success)
        self.h.freeze('ok')
        self.assertEqual(self.h.read('ok').items[1].body, b'EFGHI')

    def test_digest_retry(self):
        self.h.collector.refresh('river')
        for fault in ('digest', 'bad-digest'):
            self.h.source.fail_once('/live/tail', None, fault)
            receipts = self.h.collector.collect('river')
            self.assertFalse(receipts.items[-1].success)
            self.assertEqual(len(Catalog(self.h.db).list_ready('river')), 2)
        final = self.h.collector.collect('river')
        self.assertEqual(len(final.items), 1)
        self.assertTrue(final.items[0].success)
        self.assertEqual(self.h.source.counts[('/live/blob', 'bytes=0-3')], 1)

    def test_publish_atomicity(self):
        self.h.collector.refresh('river')
        fired = []
        def failure(stage):
            if stage == 'ready_before_commit' and not fired:
                fired.append(stage)
                raise OSError('controlled publication failure')
        collector = Collector(self.h.db, publisher=Publisher(self.h.db, failure))
        receipt = collector.collect('river')
        self.assertEqual([x.success for x in receipt.items], [False, True, True])
        self.assertEqual(len(Catalog(self.h.db).list_ready('river')), 2)
        self.h.freeze('partial')
        self.h.reopen()
        self.assertEqual(len(self.h.read('partial').items), 2)
        receipt = self.h.collector.collect('river')
        self.assertEqual(len(receipt.items), 1)
        self.assertTrue(receipt.items[0].success)
        self.assertEqual(len(Catalog(self.h.db).list_ready('river')), 3)

    def test_idempotent_and_conflict(self):
        self.h.day()
        catalog = Catalog(self.h.db)
        original = catalog.list_ready('river')[0]
        original_bytes = ObjectStore(self.h.db).read_verified(original.object_id, original.length)
        counts = self.h.source.counts.copy()
        self.assertEqual(self.h.collector.collect('river').items, ())
        self.assertEqual(counts, self.h.source.counts)
        revision_id, revision = RevisionStore(self.h.db).current('river')
        declaration = revision.declarations[0]
        client = HttpClient()
        publisher = Publisher(self.h.db)
        response = client.fetch(declaration.uri, make_range_request(declaration.byte_range))
        repeated = publisher.publish_ready(validate_media(declaration, revision_id, response))
        self.assertFalse(repeated.created)
        self.assertEqual(repeated.media, original)
        self.h.source.resources['/live/blob'] = b'xxxxEFGHI'
        response = client.fetch(declaration.uri, make_range_request(declaration.byte_range))
        conflicting = validate_media(declaration, revision_id, response)
        with self.assertRaises(ArchiveConflict):
            publisher.publish_ready(conflicting)
        self.assertEqual(catalog.get_ready(declaration.identity), original)
        self.assertEqual(ObjectStore(self.h.db).read_verified(original.object_id, original.length),
                         original_bytes)
        self.assertEqual(len(catalog.list_ready('river')), 3)

    def test_binding_isolation(self):
        self.h.source.resources['/live/b'] = b'A'
        self.h.source.playlist = b'#EXTM3U\n#EXTINF:0.1,\na\n#EXTINF:0.2,\nb'
        self.h.day()
        a, b = Catalog(self.h.db).list_ready('river')
        self.assertEqual(a.object_id, b.object_id)
        self.assertNotEqual(a.binding, b.binding)
        self.h.freeze('shared')
        view = self.h.read('shared')
        self.assertEqual([x.body for x in view.items], [b'A', b'A'])
        self.assertNotEqual(view.items[0].evidence.response.requested_url,
                            view.items[1].evidence.response.requested_url)

    def test_current_history_and_candidates(self):
        self.h.source.fail_once('/live/blob', 'bytes=4-8', 'short')
        self.h.day()
        self.h.source.playlist = b'#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:43\n#EXTINF:1,\na'
        self.h.collector.refresh('river')
        receipt = self.h.collector.collect('river')
        self.assertEqual([x.identity.media_sequence for x in receipt.items], [43])
        self.assertEqual([x.declaration.identity.media_sequence for x in
                          Catalog(self.h.db).list_ready('river')], [40, 42, 43])
        self.assertEqual(self.h.source.counts[('/live/blob', 'bytes=4-8')], 1)
