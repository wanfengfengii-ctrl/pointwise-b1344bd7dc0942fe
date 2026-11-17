from dataclasses import dataclass
from liverecord.source.registry import Sources
from liverecord.source.http import HttpClient
from liverecord.source.errors import LiveRecordError
from liverecord.timeline.parser import parse_playlist
from liverecord.timeline.revisions import RevisionStore
from liverecord.acquisition.planner import plan_missing
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

    def collect(self, source_id):
        plan = plan_missing(self.db, source_id)
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
            receipts.append(receipt)
        return CollectionReceipt(source_id, plan.revision_id, tuple(receipts))
