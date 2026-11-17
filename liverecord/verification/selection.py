from dataclasses import dataclass
from liverecord.source.responses import sha256, FetchedResponse
from liverecord.source.selections import (
    SelectionStore, version_source_id, receipt_identity)
from liverecord.timeline.master import parse_master, select_variant
from liverecord.timeline.revisions import RevisionStore


@dataclass(frozen=True)
class SelectionCheck:
    identity: object
    ok: bool
    detail: str


def receipt_problem(db, receipt, source_id, source_url):
    if receipt['source_id'] != source_id or receipt['source_url'] != source_url:
        return 'selection source mismatch'
    if receipt_identity(receipt) != receipt['selection_id']:
        return 'selection identity mismatch'
    evidence = receipt['master_evidence']
    body = receipt['master_body']
    if (evidence.requested_url != source_url or evidence.request_range is not None
            or evidence.status != 200 or evidence.length != len(body)
            or evidence.sha256 != sha256(body)):
        return 'master evidence mismatch'
    variants = parse_master(source_url, FetchedResponse(body, evidence))
    bandwidth, media_url = select_variant(variants, receipt['max_bandwidth'])
    if bandwidth != receipt['bandwidth'] or media_url != receipt['media_url']:
        return 'recomputed selection differs from receipt'
    revision = RevisionStore(db).load(receipt['revision_id'])
    if (revision.source_id != version_source_id(source_id, media_url)
            or revision.evidence.requested_url != media_url):
        return 'media revision attribution mismatch'
    return None


def check_capture_selection(db, capture):
    if capture.selection is None:
        return SelectionCheck(None, True, 'direct media capture without selection')
    try:
        problem = receipt_problem(db, capture.selection,
                                  capture.source_id, capture.source_url)
    except Exception as exc:
        problem = type(exc).__name__ + ': ' + str(exc)
    if problem is not None:
        return SelectionCheck(None, False, problem)
    return SelectionCheck(None, True, 'fixed selection recomputed from stored evidence')


def check_ready_selection(db, capture, item):
    identity = item.media.declaration.identity
    try:
        store = SelectionStore(db)
        pinned = item.first_selection
        live = store.ready_selection_id(identity.key)
        if capture.selection is None:
            if pinned is not None or live is not None:
                return SelectionCheck(identity, False,
                                      'unexpected selection chain on direct capture')
            return SelectionCheck(identity, True, 'no selection chain')
        if pinned is None:
            return SelectionCheck(identity, False, 'missing first-ready selection')
        if live != pinned:
            return SelectionCheck(identity, False,
                                  'first-ready selection record mismatch')
        receipt = store.load(pinned)
        problem = receipt_problem(db, receipt, capture.source_id, capture.source_url)
        if problem is not None:
            return SelectionCheck(identity, False, problem)
        if (version_source_id(receipt['source_id'], receipt['media_url'])
                != identity.source_id):
            return SelectionCheck(identity, False, 'first selection version mismatch')
        revision = RevisionStore(db).load(receipt['revision_id'])
        if item.media.declaration not in revision.declarations:
            return SelectionCheck(identity, False,
                                  'first selection revision does not declare segment')
        return SelectionCheck(identity, True, 'first-ready selection chain verified')
    except Exception as exc:
        return SelectionCheck(identity, False, type(exc).__name__ + ': ' + str(exc))
