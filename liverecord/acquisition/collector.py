from dataclasses import dataclass
from liverecord.source.registry import Sources
from liverecord.source.http import HttpClient
from liverecord.source.errors import InputError, LiveRecordError
from liverecord.source.selections import (
    SelectionStore, build_receipt, version_source_id)
from liverecord.timeline.parser import parse_playlist
from liverecord.timeline.master import parse_master, select_variant, validate_list_response
from liverecord.timeline.revisions import RevisionStore
from liverecord.acquisition.planner import plan_missing, plan_revision
from liverecord.acquisition.ranges import make_range_request
from liverecord.acquisition.validation import validate_media
from liverecord.archive.publication import Publisher


@dataclass(frozen=True)
class ItemReceipt:
    identity: object
    success: bool
    detail: str


@dataclass(frozen=True)
class CollectionReceipt:
    source_id: str
    revision_id: str | None
    items: tuple


class Collector:
    def __init__(self, db, client=None, publisher=None):
        self.db = db
        self.client = client or HttpClient()
        self.publisher = publisher or Publisher(db)

    def refresh(self, source_id):
        source = Sources(self.db).get(source_id)
        response = self.client.fetch(source.url)
        revision = parse_playlist(source_id, response)
        revision_id = RevisionStore(self.db).accept(revision)
        return revision_id, revision

    def refresh_selected(self, source_id, max_bandwidth):
        if type(max_bandwidth) is not int or max_bandwidth <= 0:
            raise InputError('max_bandwidth must be a positive integer')
        source = Sources(self.db).get(source_id)
        master_response = self.client.fetch(source.url)
        variants = parse_master(source.url, master_response)
        bandwidth, media_url = select_variant(variants, max_bandwidth)
        media_response = self.client.fetch(media_url)
        validate_list_response(media_response)
        version_id = version_source_id(source_id, media_url)
        revision = parse_playlist(version_id, media_response)
        Sources(self.db).register(version_id, media_url)
        revision_id = RevisionStore(self.db).accept(revision)
        receipt = build_receipt(source, max_bandwidth, bandwidth, media_url,
                                revision_id, master_response)
        SelectionStore(self.db).accept(receipt)
        return receipt, revision_id, revision

    def collect(self, source_id):
        store = SelectionStore(self.db)
        current = store.current(source_id)
        if current is None:
            plan = plan_missing(self.db, source_id)
            selection_id = None
        else:
            plan = plan_revision(self.db, source_id, current['revision_id'])
            selection_id = current['selection_id']
        receipts = []
        for item in plan.items:
            declaration = item.declaration
            try:
                response = self.client.fetch(declaration.uri,
                                             make_range_request(declaration.byte_range))
                validated = validate_media(declaration, item.revision_id, response)
                self.publisher.publish_ready(validated)
                receipt = ItemReceipt(declaration.identity, True, 'ready')
            except (LiveRecordError, OSError) as exc:
                receipt = ItemReceipt(declaration.identity, False,
                                      type(exc).__name__ + ': ' + str(exc))
            with self.db.transaction() as con:
                con.execute('''INSERT INTO attempts
                    (source,k,revision,success,detail) VALUES (?,?,?,?,?)''',
                            (source_id, declaration.identity.key, item.revision_id,
                             int(receipt.success), receipt.detail))
                if receipt.success and selection_id is not None:
                    store.mark_ready(con, declaration.identity.key, selection_id)
            receipts.append(receipt)
        return CollectionReceipt(source_id, plan.revision_id, tuple(receipts))
