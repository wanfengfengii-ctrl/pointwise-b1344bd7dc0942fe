from dataclasses import dataclass
from liverecord.replay.snapshots import load_capture
from liverecord.archive.objects import ObjectStore
from liverecord.timeline.revisions import RevisionStore
from liverecord.verification.service import Verifier
from liverecord.replay.export import export_capture
from liverecord.source.errors import IntegrityError


@dataclass(frozen=True)
class ReplayItem:
    fixed: object
    body: bytes
    playlist_body: bytes
    playlist_evidence: object

    @property
    def start(self):
        return self.fixed.start

    @property
    def end(self):
        return self.fixed.end

    @property
    def evidence(self):
        return self.fixed.media.binding


@dataclass(frozen=True)
class ReplayView:
    capture: object
    items: tuple

    @property
    def total(self):
        return self.capture.total


class Replay:
    def __init__(self, db):
        self.db = db

    def read(self, capture_id):
        capture = load_capture(self.db, capture_id)
        report = Verifier(self.db).check(capture_id)
        if not report.ok:
            raise IntegrityError(canonical_report(report))
        items = []
        for fixed in capture.items:
            revision = RevisionStore(self.db).load(fixed.media.binding.revision_id)
            items.append(ReplayItem(fixed,
                                    ObjectStore(self.db).read_verified(
                                        fixed.media.object_id, fixed.media.length),
                                    revision.body, revision.evidence))
        return ReplayView(capture, tuple(items))

    def export(self, capture_id, target):
        return export_capture(self.db, capture_id, target)


def canonical_report(report):
    return '; '.join(d.category + ': ' + d.detail for d in report.diagnostics if not d.ok)
