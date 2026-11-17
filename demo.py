from fractions import Fraction
from liverecord.archive.catalog import Catalog
from liverecord.source.registry import Sources
from liverecord.replay.reader import Replay
from liverecord.verification.service import Verifier
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


if __name__ == '__main__':
    main()
