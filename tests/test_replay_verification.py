from fractions import Fraction
from pathlib import Path
import json
import os
from liverecord.source.registry import Sources
from liverecord.source.responses import canonical
from liverecord.source.errors import (
    PublicationError, CaptureConflict, CaptureNotFound, IntegrityError, ExportConflict, InputError)
from liverecord.archive.objects import ObjectStore
from liverecord.archive.catalog import Catalog
from liverecord.replay.snapshots import Archive, load_capture
from liverecord.replay.reader import Replay
from liverecord.replay.export import export_capture
from liverecord.verification.service import Verifier
from tests.support import BusinessTest, domain_revision, emit_scenario


class TestSnapshots(BusinessTest):
    def test_publication_failure(self):
        self.h.day()
        original = self.h.freeze('old')
        old_view = self.h.read('old')
        for stage in ('capture_header', 'capture_item', 'capture_before_commit'):
            with self.subTest(stage=stage):
                def fail(at):
                    if at == stage:
                        raise OSError('controlled snapshot failure')
                with self.assertRaises(PublicationError):
                    Archive(self.h.db, fail).freeze('river', 'retry-' + stage)
                self.h.reopen()
                with self.assertRaises(CaptureNotFound):
                    load_capture(self.h.db, 'retry-' + stage)
                result = self.h.freeze('retry-' + stage)
                self.assertEqual(result.items, original.items)
                self.assertEqual(self.h.read('old'), old_view)

    def test_capture_conflict(self):
        self.h.day()
        original = self.h.freeze('stable')
        self.assertEqual(self.h.freeze('stable'), original)
        Sources(self.h.db).register('empty', self.h.source.base + '/empty')
        with self.assertRaises(CaptureConflict):
            Archive(self.h.db).freeze('empty', 'stable')
        self.h.source.playlist = b'#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:43\n#EXTINF:1,\na'
        self.h.collector.refresh('river')
        self.h.collector.collect('river')
        with self.assertRaises(CaptureConflict):
            self.h.freeze('stable')
        self.assertEqual(self.h.read('stable').capture, original)
        with self.assertRaises(InputError):
            Archive(self.h.db).freeze('missing', 'not-reserved')


class TestReplay(BusinessTest):
    def test_exact_time_and_reopen(self):
        domain_revision(self.h)
        self.assertTrue(all(x.success for x in self.h.collector.collect('river').items))
        self.h.freeze('rational')
        before = self.h.read('rational')
        self.assertEqual([x.start for x in before.items], [Fraction(0), Fraction(1, 3)])
        self.assertEqual(before.total, Fraction(13, 21))
        self.assertIsInstance(before.total, Fraction)
        Sources(self.h.db).register('empty', self.h.source.base + '/empty')
        Archive(self.h.db).freeze('empty', 'empty-capture')
        self.h.reopen()
        domain_revision(self.h, (Fraction(1, 3), Fraction(2, 7), Fraction(1, 5)))
        self.h.collector.collect('river')
        self.h.freeze('extended')
        self.assertEqual(self.h.read('rational'), before)
        self.assertEqual(len(self.h.read('extended').items), 3)
        empty = self.h.read('empty-capture')
        self.assertEqual(empty.items, ())
        self.assertEqual(empty.total, Fraction(0))
        self.assertTrue(Verifier(self.h.db).check('empty-capture').ok)
        head = json.loads(self.h.db.connection.execute(
            'SELECT payload FROM captures WHERE id=?', ('rational',)).fetchone()[0])
        self.assertEqual(head['total'], [13, 21])
        emit_scenario('N7', {'domain_fixture_seconds': ['1/3', '2/7']},
                      {'starts': ['0', '1/3'], 'total': '13/21'},
                      {'starts': [str(x.start) for x in before.items], 'total': str(before.total)},
                      {'old_count': 2, 'new_count': 3, 'empty_total': '0'},
                      {'old_count': len(self.h.read('rational').items),
                       'new_count': len(self.h.read('extended').items),
                       'empty_total': str(empty.total)},
                      ['liverecord/replay/timing.py', 'liverecord/replay/snapshots.py',
                       'liverecord/replay/reader.py', 'liverecord/verification/timeline.py'],
                      'hand fraction sum (7+6)/21, checked through persisted downstream reads')


class TestVerification(BusinessTest):
    def setUp(self):
        super().setUp()
        self.h.day()
        self.h.freeze('delivery')

    def assert_failed_delivery(self, category):
        report = Verifier(self.h.db).check('delivery')
        self.assertFalse(report.ok)
        self.assertTrue(any(d.category == category and not d.ok for d in report.diagnostics))
        with self.assertRaises(IntegrityError):
            self.h.read('delivery')
        target = self.h.root / 'rejected-export'
        with self.assertRaises(IntegrityError):
            Replay(self.h.db).export('delivery', target)
        self.assertFalse(target.exists())

    def test_corruption_and_export(self):
        view = self.h.read('delivery')
        target = self.h.root / 'export'
        Replay(self.h.db).export('delivery', target)
        manifest = json.loads((target / 'manifest.json').read_text())
        self.assertEqual(manifest, view.capture.to_dict())
        for index, item in enumerate(view.items):
            prefix = str(index).zfill(6)
            self.assertEqual((target / (prefix + '.media')).read_bytes(), item.body)
            self.assertEqual((target / (prefix + '.playlist')).read_bytes(), item.playlist_body)
            evidence = json.loads((target / (prefix + '.evidence.json')).read_text())
            self.assertEqual(evidence['binding'], item.evidence.to_dict())
            self.assertEqual(evidence['playlist'], item.playlist_evidence.to_dict())
        with self.assertRaises(ExportConflict):
            Replay(self.h.db).export('delivery', target)
        first = view.items[0].fixed.media
        path = ObjectStore(self.h.db).path(first.object_id)
        original = path.read_bytes()
        path.write_bytes(b'!' * len(original))
        self.assert_failed_delivery('media')
        path.write_bytes(original)
        self.assertTrue(Verifier(self.h.db).check('delivery').ok)
        records = self.h.db.connection.execute(
            'SELECT payload FROM verifications WHERE capture=?', ('delivery',)).fetchall()
        self.assertIn(False, [json.loads(r[0])['ok'] for r in records])
        self.assertTrue(json.loads(records[-1][0])['ok'])
        self.assertEqual(self.h.read('delivery'), view)

    def test_provenance_corruption(self):
        ready = Catalog(self.h.db).list_ready('river')[0]
        payload = ready.to_dict()
        payload['binding']['response']['requested_url'] += '?wrong=1'
        self.h.db.connection.execute('UPDATE ready SET payload=? WHERE k=?',
                                    (canonical(payload), ready.declaration.identity.key))
        self.assert_failed_delivery('provenance')
        self.h.db.connection.execute('UPDATE ready SET payload=? WHERE k=?',
                                    (canonical(ready.to_dict()), ready.declaration.identity.key))
        self.assertTrue(Verifier(self.h.db).check('delivery').ok)
        revision = ready.binding.revision_id
        body = self.h.db.connection.execute('SELECT body FROM revisions WHERE id=?',
                                            (revision,)).fetchone()[0]
        self.h.db.connection.execute('UPDATE revisions SET body=? WHERE id=?',
                                    (body + b'\n#tampered', revision))
        self.assert_failed_delivery('provenance')

    def test_timing_corruption(self):
        row = self.h.db.connection.execute(
            'SELECT payload FROM capture_items WHERE capture=? AND ordinal=1',
            ('delivery',)).fetchone()
        payload = json.loads(row[0])
        payload['start'] = [0, 1]
        self.h.db.connection.execute(
            'UPDATE capture_items SET payload=? WHERE capture=? AND ordinal=1',
            (canonical(payload), 'delivery'))
        self.assert_failed_delivery('timeline')
        self.h.db.connection.execute(
            'UPDATE capture_items SET payload=? WHERE capture=? AND ordinal=1',
            (row[0], 'delivery'))
        head = json.loads(self.h.db.connection.execute(
            'SELECT payload FROM captures WHERE id=?', ('delivery',)).fetchone()[0])
        head['total'] = [2, 1]
        self.h.db.connection.execute('UPDATE captures SET payload=? WHERE id=?',
                                    (canonical(head), 'delivery'))
        self.assert_failed_delivery('timeline')

    def test_export_target(self):
        targets = [self.h.root / 'existing-file', self.h.root / 'existing-dir',
                   self.h.root / 'dangling-link']
        targets[0].write_bytes(b'keep')
        targets[1].mkdir()
        targets[2].symlink_to(self.h.root / 'absent')
        for target in targets:
            with self.assertRaises(ExportConflict):
                Replay(self.h.db).export('delivery', target)
        self.assertEqual(targets[0].read_bytes(), b'keep')
        self.assertTrue(targets[1].is_dir())
        self.assertTrue(targets[2].is_symlink())
        before = set(self.h.root.iterdir())
        def fail(stage):
            if stage == 'export_item':
                raise OSError('controlled export failure')
        with self.assertRaises(OSError):
            export_capture(self.h.db, 'delivery', self.h.root / 'failed', fail)
        self.assertEqual(set(self.h.root.iterdir()), before)
        with self.assertRaises(CaptureNotFound):
            self.h.read('missing')
        self.assertFalse(Verifier(self.h.db).check('missing').ok)
