from dataclasses import dataclass
from liverecord.archive.objects import ObjectStore
from liverecord.source.errors import IntegrityError


@dataclass(frozen=True)
class MediaCheck:
    identity: object
    ok: bool
    detail: str


def check_media(db, item):
    media = item.media
    try:
        ObjectStore(db).read_verified(media.object_id, media.length)
        return MediaCheck(media.declaration.identity, True, 'body length and SHA-256 verified')
    except (IntegrityError, OSError, ValueError) as exc:
        return MediaCheck(media.declaration.identity, False, str(exc))
