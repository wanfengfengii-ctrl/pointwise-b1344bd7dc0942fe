from dataclasses import dataclass
from pathlib import Path
import json
import os
import shutil
import tempfile
from liverecord.archive.objects import ObjectStore
from liverecord.source.selections import SelectionStore, encode_selection
from liverecord.timeline.revisions import RevisionStore
from liverecord.replay.snapshots import load_capture
from liverecord.source.errors import ExportConflict, IntegrityError


@dataclass(frozen=True)
class ExportManifest:
    capture_id: str
    target: Path
    items: int


def export_capture(db, capture_id, target, checkpoint=None):
    from liverecord.verification.service import Verifier
    target = Path(target)
    if os.path.lexists(target):
        raise ExportConflict(str(target))
    capture = load_capture(db, capture_id)
    if not Verifier(db).check(capture_id).ok:
        raise IntegrityError('capture failed delivery verification')
    staging = Path(tempfile.mkdtemp(prefix='.liverecord-export-', dir=target.parent))
    try:
        manifest = capture.to_dict()
        selections = SelectionStore(db)
        for ordinal, item in enumerate(capture.items):
            prefix = str(ordinal).zfill(6)
            body = ObjectStore(db).read_verified(item.media.object_id, item.media.length)
            revision = RevisionStore(db).load(item.media.binding.revision_id)
            (staging / (prefix + '.media')).write_bytes(body)
            (staging / (prefix + '.playlist')).write_bytes(revision.body)
            (staging / (prefix + '.evidence.json')).write_text(
                json.dumps(dict(binding=item.media.binding.to_dict(),
                                playlist=revision.evidence.to_dict()),
                           sort_keys=True, indent=2), encoding='utf-8')
            if item.first_selection is not None:
                (staging / (prefix + '.selection.json')).write_text(
                    json.dumps(encode_selection(selections.load(item.first_selection)),
                               sort_keys=True, indent=2), encoding='utf-8')
            if checkpoint:
                checkpoint('export_item')
        if capture.selection is not None:
            (staging / 'selection.json').write_text(
                json.dumps(encode_selection(capture.selection), sort_keys=True, indent=2),
                encoding='utf-8')
        (staging / 'manifest.json').write_text(
            json.dumps(manifest, sort_keys=True, indent=2), encoding='utf-8')
        if checkpoint:
            checkpoint('export_before_publish')
        if os.path.lexists(target):
            raise ExportConflict(str(target))
        os.rename(staging, target)
        return ExportManifest(capture_id, target, len(capture.items))
    finally:
        if staging.exists():
            shutil.rmtree(staging)
