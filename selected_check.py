"""Public joint scenario. This requires the new functionality; it is not a V1 regression."""
from pathlib import Path
import json
import tempfile
from liverecord.archive.database import Database
from liverecord.source.registry import Sources
from liverecord.source.errors import PlaylistError, FetchError, PublicationError
from liverecord.acquisition.collector import Collector
from liverecord.replay.snapshots import Archive
from liverecord.replay.reader import Replay
from liverecord.verification.service import Verifier
from selected_fixture import MASTER, MEDIA, serving


def select(collector, source, limit, name, bandwidth):
    receipt, revision_id, revision = collector.refresh_selected("river", limit)
    assert receipt["source_id"] == "river"
    assert receipt["max_bandwidth"] == limit
    assert receipt["bandwidth"] == bandwidth
    assert receipt["media_url"] == source.base + "/catalog/" + name + ".m3u8"
    assert receipt["revision_id"] == revision_id
    assert receipt["master_body"] == source.master
    assert receipt["master_evidence"].requested_url == source.url
    assert receipt["master_evidence"].final_url == source.base + "/catalog/master.m3u8"
    assert isinstance(receipt["selection_id"], str) and receipt["selection_id"]
    return receipt, revision


def frozen(db, capture_id, body, current, first):
    Archive(db).freeze("river", capture_id)
    view = Replay(db).read(capture_id)
    assert view.capture.source_id == "river"
    assert view.capture.selection == current
    assert [item.body for item in view.items] == [body]
    assert view.items[0].selection == first
    assert str(view.total) == "1/2"
    assert Verifier(db).check(capture_id).ok
    return view


def encoded(receipt):
    value = dict(receipt)
    value["master_body"] = value["master_body"].decode("utf-8")
    value["master_evidence"] = value["master_evidence"].to_dict()
    return value


def run():
    with tempfile.TemporaryDirectory(prefix="selected-scenario-") as temporary:
        with serving() as source:
            root = Path(temporary)
            db = Database(root / "archive")
            try:
                Sources(db).register("river", source.url)
                collector = Collector(db)
                early, r0 = select(collector, source, 150, "A", 100)
                source.master = MASTER.replace(b"BANDWIDTH=100", b"BANDWIDTH=90")
                a, r1 = select(collector, source, 150, "A", 90)
                assert early != a
                assert r0.body == r1.body and r0.evidence == r1.evidence
                assert all(item.success for item in collector.collect("river").items)
                va = frozen(db, "A", b"A", a, a)
                b, _ = select(collector, source, 250, "B", 200)
                assert all(item.success for item in collector.collect("river").items)
                vb = frozen(db, "B", b"B", b, b)
                source.master += b"#later selection\n"
                later, _ = select(collector, source, 150, "A", 90)
                assert later != a
                before = source.counts.copy()
                assert collector.collect("river").items == ()
                assert before == source.counts
                vlater = frozen(db, "A-later", b"A", later, a)
                assert source.counts[("/media/A/s40", None)] == 1
                assert source.counts[("/media/B/s40", None)] == 1

                # Failed selection must leave the current fixed input unchanged.
                old_master = source.master
                for invalid in (
                    b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=90,CODECS=x\nA.m3u8\n",
                    b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=90\nA.m3u8\n"
                    b"#EXT-X-STREAM-INF:BANDWIDTH=100\n./A.m3u8\n",
                ):
                    source.master = invalid
                    try:
                        collector.refresh_selected("river", 150)
                    except PlaylistError:
                        pass
                    else:
                        raise AssertionError("invalid master accepted")
                    assert Archive(db).freeze("river", "A-later") == vlater.capture
                source.master = old_master
                try:
                    collector.refresh_selected("river", 1)
                except PlaylistError:
                    pass
                else:
                    raise AssertionError("empty eligible set accepted")
                source.fail_once("/media/B/list.m3u8")
                try:
                    collector.refresh_selected("river", 250)
                except FetchError:
                    pass
                else:
                    raise AssertionError("failed media fetch accepted")
                assert Archive(db).freeze("river", "A-later") == vlater.capture
                path = "/media/B/list.m3u8"
                original = source.routes[path]
                source.routes[path] = (200, MASTER, ())
                try:
                    collector.refresh_selected("river", 250)
                except PlaylistError:
                    pass
                else:
                    raise AssertionError("nested master accepted")
                finally:
                    source.routes[path] = original
                assert Archive(db).freeze("river", "A-later") == vlater.capture

                def fail(stage):
                    if stage == "capture_before_commit":
                        raise OSError("controlled selected freeze failure")
                try:
                    Archive(db, fail).freeze("river", "retry")
                except PublicationError:
                    pass
                else:
                    raise AssertionError("freeze failure swallowed")
                assert Archive(db).freeze("river", "retry").selection == later
                db.close()
                db = None
            finally:
                if db is not None:
                    db.close()
        # The source is now stopped: all three fixed inputs must be self-contained.
        db = Database(root / "archive")
        try:
            for capture_id, expected in (("A", va), ("B", vb), ("A-later", vlater)):
                assert Replay(db).read(capture_id) == expected
                assert Verifier(db).check(capture_id).ok
            target = root / "delivery"
            Replay(db).export("A-later", target)
            assert (target / "000000.media").read_bytes() == b"A"
            assert json.loads((target / "selection.json").read_text()) == encoded(later)
            assert json.loads((target / "000000.selection.json").read_text()) == encoded(a)
            print("SELECTED_JOINT_SCENARIO " + json.dumps({
                "captures": ["A", "B", "A-later"],
                "bodies": ["A", "B", "A"],
                "A_body_requests": 1,
                "B_body_requests": 1,
                "current_selection": later["selection_id"],
                "A_first_selection": a["selection_id"],
                "offline_verified": True,
            }, sort_keys=True))
        finally:
            db.close()


if __name__ == "__main__":
    run()
