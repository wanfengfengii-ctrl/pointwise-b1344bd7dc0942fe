import contextlib
import json
from pathlib import Path
import tempfile
import unittest

from liverecord.archive.database import Database
from liverecord.source.registry import Sources
from liverecord.source.errors import (
    InputError, PlaylistError, FetchError, DeclarationConflict, CaptureConflict,
    IntegrityError)
from liverecord.acquisition.collector import Collector
from liverecord.replay.snapshots import Archive
from liverecord.replay.reader import Replay
from liverecord.verification.service import Verifier
from selected_fixture import MASTER, MEDIA, serving
from tests.support import BusinessTest


class SelectedCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='liverecord-selected-test-')
        self.root = Path(self.temporary.name)
        self.stack = contextlib.ExitStack()
        self.source = self.stack.enter_context(serving())
        self.db = Database(self.root / 'archive')
        Sources(self.db).register('river', self.source.url)
        self.collector = Collector(self.db)

    def tearDown(self):
        self.db.close()
        self.stack.close()
        self.temporary.cleanup()

    def select(self, limit=150):
        return self.collector.refresh_selected('river', limit)

    def reopen(self):
        self.db.close()
        self.db = Database(self.root / 'archive')
        self.collector = Collector(self.db)

    def assert_failed_delivery(self, capture_id):
        report = Verifier(self.db).check(capture_id)
        self.assertFalse(report.ok)
        with self.assertRaises(IntegrityError):
            Replay(self.db).read(capture_id)
        target = self.root / ('rejected-' + capture_id)
        with self.assertRaises(IntegrityError):
            Replay(self.db).export(capture_id, target)
        self.assertFalse(target.exists())


class TestSelectionEntry(SelectedCase):
    def test_limit_validation(self):
        for bad in (0, -1, True, False, 1.5, '150', None):
            with self.subTest(bad=bad):
                with self.assertRaises(InputError):
                    self.collector.refresh_selected('river', bad)
        self.assertEqual(self.collector.collect('river').items, ())
        with self.assertRaises(InputError):
            self.collector.refresh_selected('missing', 150)

    def test_policy_and_tie_break(self):
        receipt, _, _ = self.select(150)
        self.assertEqual((receipt['bandwidth'], receipt['media_url']),
                         (100, self.source.base + '/catalog/A.m3u8'))
        self.assertEqual(self.select(200)[0]['bandwidth'], 200)
        self.assertEqual(self.select(250)[0]['bandwidth'], 200)
        with self.assertRaises(PlaylistError):
            self.select(99)
        self.source.master = (b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=150\nB.m3u8\n'
                              b'#EXT-X-STREAM-INF:BANDWIDTH=150\nA.m3u8\n')
        receipt, _, _ = self.select(150)
        self.assertEqual((receipt['bandwidth'], receipt['media_url']),
                         (150, self.source.base + '/catalog/A.m3u8'))

    def test_selection_id_determinism(self):
        self.source.master = b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nA.m3u8\n'
        first, r1, _ = self.select(150)
        again, r2, _ = self.select(150)
        self.assertEqual(first, again)
        self.assertEqual(r1, r2)
        other, r3, _ = self.select(250)
        self.assertNotEqual(first['selection_id'], other['selection_id'])
        self.assertEqual(other['max_bandwidth'], 250)
        self.assertEqual(other['bandwidth'], 100)
        self.assertEqual(r1, r3)

    def test_master_rejections_preserve_selection(self):
        self.select(150)
        self.collector.collect('river')
        Archive(self.db).freeze('river', 'kept')
        kept = Archive(self.db).freeze('river', 'kept')
        invalid = [
            b'',
            b'#EXT-X-STREAM-INF:BANDWIDTH=100\nA.m3u8\n',
            b'#EXTM3U\n\xff\xfe\n',
            b'#EXTM3U\n',
            b'#EXTM3U\n# only a comment\n',
            b'#EXTM3U\nA.m3u8\n',
            b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\n',
            b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\n'
            b'#EXT-X-STREAM-INF:BANDWIDTH=200\nB.m3u8\n',
            b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100,CODECS=x\nA.m3u8\n',
            b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=0\nA.m3u8\n',
            b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1O0\nA.m3u8\n',
            '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=１００\nA.m3u8\n'.encode(),
            b'#EXTM3U\n#EXT-X-STREAM-INF\nA.m3u8\n',
            b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nA.m3u8\n#EXT-X-STREAM-INF:100\nB.m3u8\n',
            b'#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-STREAM-INF:BANDWIDTH=100\nA.m3u8\n',
            b'#EXTM3U\n#EXTINF:0.5,\nA.m3u8\n',
            b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nA.m3u8\n'
            b'#EXT-X-STREAM-INF:BANDWIDTH=200\n./A.m3u8\n',
            b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nftp://example.org/x.m3u8\n',
        ]
        for body in invalid:
            with self.subTest(body=body):
                self.source.master = body
                with self.assertRaises(PlaylistError):
                    self.select(150)
                self.assertEqual(Archive(self.db).freeze('river', 'kept'), kept)
        self.assertTrue(Verifier(self.db).check('kept').ok)

    def test_refresh_stays_direct_only(self):
        with self.assertRaises(PlaylistError):
            self.collector.refresh('river')
        self.assertEqual(self.collector.collect('river').items, ())


class TestSelectedVersions(SelectedCase):
    def test_switch_and_reuse(self):
        a, ra, _ = self.select(150)
        self.assertTrue(all(x.success for x in self.collector.collect('river').items))
        Archive(self.db).freeze('river', 'cap-a')
        b, rb, _ = self.select(250)
        self.assertNotEqual(ra, rb)
        self.assertTrue(all(x.success for x in self.collector.collect('river').items))
        Archive(self.db).freeze('river', 'cap-b')
        back, ra2, _ = self.select(150)
        self.assertEqual(ra, ra2)
        self.assertEqual(self.collector.collect('river').items, ())
        self.assertEqual(self.source.counts[('/media/A/s40', None)], 1)
        self.assertEqual(self.source.counts[('/media/B/s40', None)], 1)
        Archive(self.db).freeze('river', 'cap-a2')
        va = Replay(self.db).read('cap-a')
        vb = Replay(self.db).read('cap-b')
        va2 = Replay(self.db).read('cap-a2')
        self.assertEqual([x.body for x in va.items], [b'A'])
        self.assertEqual([x.body for x in vb.items], [b'B'])
        self.assertEqual([x.body for x in va2.items], [b'A'])
        ia = va.items[0].evidence.declaration.identity
        ib = vb.items[0].evidence.declaration.identity
        self.assertNotEqual(ia, ib)
        self.assertEqual((ia.discontinuity_sequence, ia.media_sequence), (0, 40))
        self.assertEqual((ib.discontinuity_sequence, ib.media_sequence), (0, 40))
        self.assertEqual(va.capture.selection, a)
        self.assertEqual(vb.capture.selection, b)
        self.assertEqual(va2.capture.selection, back)
        self.assertEqual(va2.items[0].selection, a)
        self.assertEqual(va.capture.source_id, 'river')
        self.assertEqual(va.capture.source_url, self.source.url)

    def test_redirects_do_not_merge_versions(self):
        self.source.routes['/catalog/B.m3u8'] = (
            302, b'', (('Location', '/media/A/list.m3u8'),))
        self.select(150)
        self.assertTrue(all(x.success for x in self.collector.collect('river').items))
        Archive(self.db).freeze('river', 'va')
        _, rb, _ = self.select(250)
        self.assertTrue(all(x.success for x in self.collector.collect('river').items))
        Archive(self.db).freeze('river', 'vb')
        self.assertEqual(self.source.counts[('/media/A/s40', None)], 2)
        va = Replay(self.db).read('va')
        vb = Replay(self.db).read('vb')
        self.assertEqual([x.body for x in va.items], [b'A'])
        self.assertEqual([x.body for x in vb.items], [b'A'])
        ia = va.items[0].evidence.declaration.identity
        ib = vb.items[0].evidence.declaration.identity
        self.assertNotEqual(ia.source_id, ib.source_id)
        self.assertEqual((ia.discontinuity_sequence, ia.media_sequence),
                         (ib.discontinuity_sequence, ib.media_sequence))
        self.assertTrue(Verifier(self.db).check('va').ok)
        self.assertTrue(Verifier(self.db).check('vb').ok)

    def test_failed_refresh_preserves_selection_and_candidates(self):
        self.select(150)
        self.collector.collect('river')
        Archive(self.db).freeze('river', 'kept')
        kept = Archive(self.db).freeze('river', 'kept')
        self.source.fail_once('/media/B/list.m3u8')
        with self.assertRaises(FetchError):
            self.select(250)
        self.source.fail_once('/media/B/list.m3u8', 'short')
        with self.assertRaises(PlaylistError):
            self.select(250)
        self.source.fail_once('/media/B/list.m3u8', 'transport')
        with self.assertRaises(FetchError):
            self.select(250)
        self.source.fail_once('/catalog/master.m3u8')
        with self.assertRaises(FetchError):
            self.select(150)
        original = self.source.routes['/media/B/list.m3u8']
        self.source.routes['/media/B/list.m3u8'] = (200, MASTER, ())
        try:
            with self.assertRaises(PlaylistError):
                self.select(250)
        finally:
            self.source.routes['/media/B/list.m3u8'] = original
        self.source.routes['/media/A/list.m3u8'] = (200, MEDIA.replace(b's40', b's4x'), ())
        try:
            with self.assertRaises(DeclarationConflict):
                self.select(150)
        finally:
            self.source.routes['/media/A/list.m3u8'] = (
                200, MEDIA, ())
        self.assertEqual(Archive(self.db).freeze('river', 'kept'), kept)
        self.assertTrue(Verifier(self.db).check('kept').ok)
        self.select(250)
        self.assertTrue(all(x.success for x in self.collector.collect('river').items))

    def test_segment_fault_does_not_rollback_selection(self):
        receipt, _, _ = self.select(150)
        self.source.fail_once('/media/A/s40', 'short')
        results = self.collector.collect('river').items
        self.assertEqual([x.success for x in results], [False])
        Archive(self.db).freeze('river', 'after-failure')
        view = Replay(self.db).read('after-failure')
        self.assertEqual(view.capture.selection, receipt)
        self.assertEqual(view.items, ())
        self.assertTrue(all(x.success for x in self.collector.collect('river').items))
        Archive(self.db).freeze('river', 'after-retry')
        view = Replay(self.db).read('after-retry')
        self.assertEqual(view.items[0].selection, receipt)
        self.assertEqual(view.items[0].body, b'A')

    def test_collect_and_freeze_only_current_version(self):
        self.select(150)
        self.collector.collect('river')
        self.select(250)
        Archive(self.db).freeze('river', 'b-empty')
        self.assertEqual(Replay(self.db).read('b-empty').items, ())
        receipt = self.collector.collect('river')
        self.assertEqual(len(receipt.items), 1)
        self.assertTrue(receipt.items[0].success)
        Archive(self.db).freeze('river', 'b-ready')
        view = Replay(self.db).read('b-ready')
        self.assertEqual([x.body for x in view.items], [b'B'])
        self.assertEqual(self.source.counts[('/media/A/s40', None)], 1)


class TestSelectionEvidence(SelectedCase):
    def test_first_ready_binds_retry_selection(self):
        first, _, _ = self.select(150)
        self.source.fail_once('/media/A/s40')
        results = self.collector.collect('river').items
        self.assertEqual([x.success for x in results], [False])
        self.source.master += b'# retry window\n'
        second, _, _ = self.select(150)
        self.assertNotEqual(first['selection_id'], second['selection_id'])
        self.assertTrue(all(x.success for x in self.collector.collect('river').items))
        Archive(self.db).freeze('river', 'cap')
        view = Replay(self.db).read('cap')
        self.assertEqual(view.capture.selection, second)
        self.assertEqual(view.items[0].selection, second)
        self.assertTrue(Verifier(self.db).check('cap').ok)

    def test_first_chain_not_overwritten(self):
        self.select(150)
        self.source.master = MASTER.replace(b'BANDWIDTH=100', b'BANDWIDTH=90')
        a, _, _ = self.select(150)
        self.collector.collect('river')
        self.source.master += b'#later selection\n'
        later, _, _ = self.select(150)
        self.assertEqual(self.collector.collect('river').items, ())
        Archive(self.db).freeze('river', 'cap')
        view = Replay(self.db).read('cap')
        self.assertEqual(view.capture.selection, later)
        self.assertEqual(view.items[0].selection, a)
        self.assertEqual(self.source.counts[('/media/A/s40', None)], 1)
        self.assertTrue(Verifier(self.db).check('cap').ok)

    def test_capture_conflict_on_selection_change(self):
        self.select(150)
        self.collector.collect('river')
        Archive(self.db).freeze('river', 'cap')
        original = Archive(self.db).freeze('river', 'cap')
        self.source.master += b'# changed\n'
        self.select(150)
        with self.assertRaises(CaptureConflict):
            Archive(self.db).freeze('river', 'cap')
        self.assertEqual(Replay(self.db).read('cap').capture, original)
        self.source.master = MASTER
        self.select(150)
        self.assertEqual(Archive(self.db).freeze('river', 'cap'), original)

    def test_empty_selected_capture(self):
        receipt, _, _ = self.select(150)
        Archive(self.db).freeze('river', 'empty')
        view = Replay(self.db).read('empty')
        self.assertEqual(view.capture.selection, receipt)
        self.assertEqual(view.items, ())
        self.assertEqual(view.total, 0)
        self.assertTrue(Verifier(self.db).check('empty').ok)
        target = self.root / 'empty-export'
        Replay(self.db).export('empty', target)
        self.assertEqual(json.loads((target / 'selection.json').read_text())['selection_id'],
                         receipt['selection_id'])
        self.assertEqual(sorted(p.name for p in target.iterdir()),
                         ['manifest.json', 'selection.json'])

    def test_offline_reopen_after_shutdown(self):
        a, _, _ = self.select(150)
        self.collector.collect('river')
        Archive(self.db).freeze('river', 'cap')
        before = Replay(self.db).read('cap')
        self.reopen()
        self.source.close()
        self.assertEqual(Replay(self.db).read('cap'), before)
        self.assertTrue(Verifier(self.db).check('cap').ok)
        target = self.root / 'offline-export'
        Replay(self.db).export('cap', target)
        self.assertEqual((target / '000000.media').read_bytes(), b'A')
        self.assertEqual(json.loads((target / 'selection.json').read_text())['bandwidth'], 100)
        self.assertEqual(
            json.loads((target / '000000.selection.json').read_text())['selection_id'],
            a['selection_id'])

    def test_selection_corruption_refuses_delivery(self):
        self.select(150)
        self.collector.collect('river')
        Archive(self.db).freeze('river', 'cap')
        row = self.db.connection.execute('SELECT id,body,payload FROM selections').fetchone()
        self.db.connection.execute('UPDATE selections SET body=? WHERE id=?',
                                   (row['body'] + b'\n#tampered', row['id']))
        self.assert_failed_delivery('cap')
        self.db.connection.execute('UPDATE selections SET body=? WHERE id=?',
                                   (row['body'], row['id']))
        self.assertTrue(Verifier(self.db).check('cap').ok)
        payload = json.loads(row['payload'])
        payload['max_bandwidth'] = 1
        self.db.connection.execute('UPDATE selections SET payload=? WHERE id=?',
                                   (json.dumps(payload), row['id']))
        self.assert_failed_delivery('cap')
        self.db.connection.execute('UPDATE selections SET payload=? WHERE id=?',
                                   (row['payload'], row['id']))
        self.assertTrue(Verifier(self.db).check('cap').ok)
        self.source.master += b'# second\n'
        later, _, _ = self.select(150)
        self.db.connection.execute('UPDATE ready_selection SET selection=?',
                                   (later['selection_id'],))
        self.assert_failed_delivery('cap')
        self.db.connection.execute('UPDATE ready_selection SET selection=?',
                                   (row['id'],))
        self.assertTrue(Verifier(self.db).check('cap').ok)
        head_row = self.db.connection.execute(
            'SELECT payload FROM captures WHERE id=?', ('cap',)).fetchone()
        head = json.loads(head_row[0])
        head['selection'] = 'missing-selection-id'
        self.db.connection.execute('UPDATE captures SET payload=? WHERE id=?',
                                   (json.dumps(head), 'cap'))
        self.assert_failed_delivery('cap')


class TestDirectSourceUnchanged(BusinessTest):
    def test_direct_results_have_no_selection(self):
        self.h.day()
        self.h.freeze('direct')
        view = self.h.read('direct')
        self.assertIsNone(view.capture.selection)
        self.assertIsNone(view.capture.to_dict()['selection'])
        self.assertTrue(all(item.selection is None for item in view.items))
        target = self.h.root / 'direct-export'
        Replay(self.h.db).export('direct', target)
        names = [p.name for p in target.iterdir()]
        self.assertNotIn('selection.json', names)
        self.assertFalse(any(name.endswith('.selection.json') for name in names))
        self.assertTrue(Verifier(self.h.db).check('direct').ok)

    def test_pre_upgrade_capture_encoding(self):
        self.h.day()
        self.h.freeze('legacy')
        row = self.h.db.connection.execute(
            'SELECT payload FROM captures WHERE id=?', ('legacy',)).fetchone()
        head = json.loads(row[0])
        del head['selection']
        self.h.db.connection.execute('UPDATE captures SET payload=? WHERE id=?',
                                     (json.dumps(head), 'legacy'))
        item_row = self.h.db.connection.execute(
            'SELECT payload FROM capture_items WHERE capture=? AND ordinal=0',
            ('legacy',)).fetchone()
        payload = json.loads(item_row[0])
        del payload['first_selection']
        self.h.db.connection.execute(
            'UPDATE capture_items SET payload=? WHERE capture=? AND ordinal=0',
            (json.dumps(payload), 'legacy'))
        self.h.reopen()
        view = self.h.read('legacy')
        self.assertIsNone(view.capture.selection)
        self.assertTrue(all(item.selection is None for item in view.items))
        self.assertTrue(Verifier(self.h.db).check('legacy').ok)
