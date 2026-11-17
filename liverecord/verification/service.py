from dataclasses import dataclass
from liverecord.replay.snapshots import load_capture, input_fingerprint
from liverecord.source.responses import canonical
from liverecord.verification.media import check_media
from liverecord.verification.provenance import check_provenance
from liverecord.verification.selection import check_capture_selection, check_ready_selection
from liverecord.verification.timeline import check_timeline


@dataclass(frozen=True)
class Diagnostic:
    category: str
    identity: object
    ok: bool
    detail: str

    def to_dict(self):
        return dict(category=self.category,
                    identity=self.identity.to_list() if self.identity else None,
                    ok=self.ok, detail=self.detail)


@dataclass(frozen=True)
class VerificationReport:
    capture_id: str
    diagnostics: tuple

    @property
    def ok(self):
        return bool(self.diagnostics) and all(d.ok for d in self.diagnostics)

    def to_dict(self):
        return dict(capture_id=self.capture_id, ok=self.ok,
                    diagnostics=[d.to_dict() for d in self.diagnostics])


class Verifier:
    def __init__(self, db):
        self.db = db

    def check(self, capture_id):
        diagnostics = []
        try:
            capture = load_capture(self.db, capture_id)
            timeline = check_timeline(capture)
            diagnostics.append(Diagnostic('timeline', None, timeline.ok, timeline.detail))
            selection_id = (capture.selection['selection_id']
                            if capture.selection is not None else None)
            matches = (input_fingerprint(capture.source_id, capture.source_url,
                                         [i.media for i in capture.items], selection_id)
                       == capture.fingerprint)
            diagnostics.append(Diagnostic('binding', None, matches, 'fixed input fingerprint'))
            from liverecord.source.registry import Sources
            source_matches = Sources(self.db).get(capture.source_id).url == capture.source_url
            diagnostics.append(Diagnostic('provenance', None, source_matches,
                                          'fixed source registration'))
            fixed = check_capture_selection(self.db, capture)
            diagnostics.append(Diagnostic('selection', None, fixed.ok, fixed.detail))
            for item in capture.items:
                for category, result in (
                        ('media', check_media(self.db, item)),
                        ('provenance', check_provenance(self.db, capture, item)),
                        ('selection', check_ready_selection(self.db, capture, item))):
                    diagnostics.append(Diagnostic(category, result.identity,
                                                  result.ok, result.detail))
        except Exception as exc:
            diagnostics.append(Diagnostic('capture', None, False,
                                          type(exc).__name__ + ': ' + str(exc)))
        report = VerificationReport(capture_id, tuple(diagnostics))
        with self.db.transaction() as con:
            con.execute('INSERT INTO verifications (capture,payload) VALUES (?,?)',
                        (capture_id, canonical(report.to_dict())))
        return report
