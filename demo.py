from fractions import Fraction
from pathlib import Path
import json
import tempfile
from liverecord.archive.catalog import Catalog
from liverecord.archive.database import Database
from liverecord.source.registry import Sources
from liverecord.acquisition.collector import Collector
from liverecord.replay.snapshots import Archive
from liverecord.replay.reader import Replay
from liverecord.verification.service import Verifier
from selected_fixture import serving
from tests.support import Harness, emit_scenario


def main():
    h = Harness()
    try:
        source = Sources(h.db).get('river')
        assert Sources(h.db).register('river', source.url) == source
        h.source.fail_once('/live/blob', 'bytes=4-8', 'short')
        first = h.day()
        assert [x.success for x in first.items] == [True, False, True]
        h.freeze('morning')
        morning = h.read('morning')
        assert [x.body for x in morning.items] == [b'ABCD', b'JKLMNO']
        assert morning.total == Fraction(5, 8)
        retry = h.collector.collect('river')
        assert len(retry.items) == 1 and retry.items[0].success
        h.freeze('complete')
        complete = h.read('complete')
        assert [x.body for x in complete.items] == [b'ABCD', b'EFGHI', b'JKLMNO']
        assert [x.start for x in complete.items] == [0, Fraction(1, 8), Fraction(1, 2)]
        assert complete.total == 1
        h.reopen()
        assert h.read('morning') == morning
        assert Verifier(h.db).check('complete').ok
        target = h.root / 'research-delivery'
        Replay(h.db).export('complete', target)
        exported = [(target / (str(i).zfill(6) + '.media')).read_bytes() for i in range(3)]
        assert exported == [b'ABCD', b'EFGHI', b'JKLMNO']
        emit_scenario('N4', {'resource': h.source.day['blob'], 'retry': 'explicit',
                            'durations_seconds': ['1/8', '3/8', '1/2']},
                      {'morning_media': ['ABCD', 'JKLMNO'], 'morning_total': '5/8'},
                      {'morning_media': [x.body.decode() for x in morning.items],
                       'morning_total': str(morning.total)},
                      {'ready': 3, 'complete_starts': ['0', '1/8', '1/2'],
                       'complete_total': '1', 'exported': ['ABCD', 'EFGHI', 'JKLMNO']},
                      {'ready': len(Catalog(h.db).list_ready('river')),
                       'complete_starts': [str(x.start) for x in h.read('complete').items],
                       'complete_total': str(h.read('complete').total),
                       'exported': [x.decode() for x in exported]},
                      ['liverecord/source/http.py', 'liverecord/timeline/parser.py',
                       'liverecord/acquisition/collector.py', 'liverecord/archive/publication.py',
                       'liverecord/replay/snapshots.py', 'liverecord/verification/service.py',
                       'liverecord/replay/export.py'],
                      'literal slices ABCD/EFGHI; exact hand sums; reopened and exported bytes')
    finally:
        h.close()
    selected_demo()


def selected_demo():
    with tempfile.TemporaryDirectory(prefix='liverecord-selected-') as temporary:
        with serving() as source:
            root = Path(temporary)
            db = Database(root / 'archive')
            try:
                Sources(db).register('estuary', source.url)
                collector = Collector(db)
                low, _, _ = collector.refresh_selected('estuary', 150)
                assert low['bandwidth'] == 100
                assert all(x.success for x in collector.collect('estuary').items)
                Archive(db).freeze('estuary', 'low-tide')
                high, _, _ = collector.refresh_selected('estuary', 250)
                assert high['bandwidth'] == 200
                assert high['revision_id'] != low['revision_id']
                assert all(x.success for x in collector.collect('estuary').items)
                Archive(db).freeze('estuary', 'high-tide')
                source.master += b'# shifted observation window\n'
                again, _, _ = collector.refresh_selected('estuary', 150)
                assert again['revision_id'] == low['revision_id']
                assert again['selection_id'] != low['selection_id']
                assert collector.collect('estuary').items == ()
                assert source.counts[('/media/A/s40', None)] == 1
                assert source.counts[('/media/B/s40', None)] == 1
                Archive(db).freeze('estuary', 'low-tide-revisited')
                revisited = Replay(db).read('low-tide-revisited')
                assert [x.body for x in revisited.items] == [b'A']
                assert revisited.capture.selection == again
                assert revisited.items[0].selection == low
                db.close()
                db = None
            finally:
                if db is not None:
                    db.close()
        # The source is stopped: fixed results must read, verify and export offline.
        db = Database(root / 'archive')
        try:
            views = {name: Replay(db).read(name)
                     for name in ('low-tide', 'high-tide', 'low-tide-revisited')}
            assert [x.body for x in views['low-tide'].items] == [b'A']
            assert [x.body for x in views['high-tide'].items] == [b'B']
            assert [x.body for x in views['low-tide-revisited'].items] == [b'A']
            assert all(Verifier(db).check(name).ok for name in views)
            target = root / 'selected-delivery'
            Replay(db).export('low-tide-revisited', target)
            assert (target / '000000.media').read_bytes() == b'A'
            manifest = json.loads((target / 'manifest.json').read_text())
            assert manifest['selection']['selection_id'] == again['selection_id']
            current_chain = json.loads((target / 'selection.json').read_text())
            first_chain = json.loads((target / '000000.selection.json').read_text())
            assert current_chain['selection_id'] == again['selection_id']
            assert first_chain['selection_id'] == low['selection_id']
            emit_scenario('S1', {'variants': {'A': 100, 'B': 200},
                                 'limits': [150, 250, 150]},
                          {'low-tide': 'A', 'high-tide': 'B',
                           'low-tide-revisited': 'A reused'},
                          {name: [item.body.decode() for item in view.items]
                           for name, view in views.items()},
                          {'A_segment_requests': 1, 'B_segment_requests': 1,
                           'offline_verified': True, 'exported': 'A',
                           'first_chain_is_low': True},
                          {'A_segment_requests': source.counts[('/media/A/s40', None)],
                           'B_segment_requests': source.counts[('/media/B/s40', None)],
                           'offline_verified': True,
                           'exported': (target / '000000.media').read_text(),
                           'first_chain_is_low':
                               first_chain['selection_id'] == low['selection_id']},
                          ['liverecord/timeline/master.py', 'liverecord/source/selections.py',
                           'liverecord/acquisition/collector.py',
                           'liverecord/replay/snapshots.py',
                           'liverecord/verification/selection.py',
                           'liverecord/replay/export.py'],
                          'fixture bandwidths 100/200 under limits 150/250; reuse proven '
                          'by segment request counts; fixed evidence verified offline')
        finally:
            db.close()


if __name__ == '__main__':
    main()
