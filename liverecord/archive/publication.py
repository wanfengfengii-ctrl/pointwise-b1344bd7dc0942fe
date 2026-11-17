from dataclasses import dataclass
from liverecord.archive.objects import ObjectStore
from liverecord.archive.catalog import Catalog, SourceBinding, ReadyMedia
from liverecord.timeline.revisions import RevisionStore
from liverecord.acquisition.validation import validate_media
from liverecord.source.responses import canonical
from liverecord.source.errors import ArchiveConflict, PublicationError


@dataclass(frozen=True)
class PublicationReceipt:
    media: ReadyMedia
    created: bool


class Publisher:
    def __init__(self, db, checkpoint=None):
        self.db = db
        self.objects = ObjectStore(db)
        self.catalog = Catalog(db)
        self.checkpoint = checkpoint

    def publish_ready(self, media):
        validate_media(media.declaration, media.revision_id, media.response)
        revision = RevisionStore(self.db).load(media.revision_id)
        if (revision.source_id != media.declaration.identity.source_id
                or media.declaration not in revision.declarations):
            raise ArchiveConflict('receipt declaration is not in accepted revision')
        if RevisionStore(self.db).declaration(media.declaration.identity) != media.declaration:
            raise ArchiveConflict('receipt declaration differs from history')
        staged = self.objects.stage(media.response.body)
        try:
            with self.db.transaction() as con:
                previous = self.catalog.get_ready(media.declaration.identity)
                if previous is not None:
                    if (previous.declaration != media.declaration
                            or previous.object_id != staged.object_id
                            or previous.length != staged.length):
                        raise ArchiveConflict('immutable ready identity conflict')
                    self.objects.read_verified(previous.object_id, previous.length)
                    return PublicationReceipt(previous, False)
                self.objects.seal(staged)
                if self.checkpoint:
                    self.checkpoint('object_sealed')
                ready = ReadyMedia(SourceBinding(media.declaration, media.revision_id,
                                                media.response.evidence),
                                   staged.object_id, staged.length)
                identity = media.declaration.identity
                con.execute('INSERT INTO ready VALUES (?,?,?,?,?)',
                            (identity.key, identity.source_id,
                             str(identity.discontinuity_sequence), str(identity.media_sequence),
                             canonical(ready.to_dict())))
                con.execute('INSERT INTO current_media VALUES (?,?)',
                            (identity.key, identity.source_id))
                if self.checkpoint:
                    self.checkpoint('ready_before_commit')
            return PublicationReceipt(ready, True)
        except (ArchiveConflict, PublicationError):
            raise
        except OSError as exc:
            raise PublicationError(str(exc)) from exc
        finally:
            self.objects.discard_staged(staged)
