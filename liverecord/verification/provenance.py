from dataclasses import dataclass
from liverecord.archive.catalog import Catalog
from liverecord.timeline.revisions import RevisionStore
from liverecord.source.registry import Sources
from liverecord.source.responses import sha256
from liverecord.acquisition.ranges import validate_range_response
from liverecord.acquisition.validation import decode_expected_digest
from liverecord.source.responses import header_values


@dataclass(frozen=True)
class ProvenanceCheck:
    identity: object
    ok: bool
    detail: str


def check_provenance(db, capture, item):
    media = item.media
    declaration = media.declaration
    identity = declaration.identity
    try:
        if identity.source_id != capture.source_id:
            raise ValueError('capture source mismatch')
        source = Sources(db).get(identity.source_id)
        if source.url != capture.source_url:
            raise ValueError('source registration mismatch')
        store = RevisionStore(db)
        revision = store.load(media.binding.revision_id)
        evidence = revision.evidence
        if (revision.source_id != identity.source_id or declaration not in revision.declarations
                or store.declaration(identity) != declaration):
            raise ValueError('declaration binding mismatch')
        if (evidence.sha256 != sha256(revision.body) or evidence.length != len(revision.body)
                or evidence.requested_url != capture.source_url or evidence.status != 200
                or evidence.request_range is not None):
            raise ValueError('playlist evidence mismatch')
        current = Catalog(db).get_ready(identity)
        if current is None or current.to_dict() != media.to_dict():
            raise ValueError('immutable ready binding differs from fixed binding')
        response = media.binding.response
        if (response.requested_url != declaration.uri or response.length != media.length
                or response.sha256 != media.object_id):
            raise ValueError('media response binding mismatch')
        validate_range_response(declaration.byte_range, response)
        expected = decode_expected_digest(response)
        if expected is not None and expected != media.object_id:
            raise ValueError('upstream digest differs from object')
        encodings = header_values(response, 'Content-Encoding')
        lengths = header_values(response, 'Content-Length')
        if any(x.strip().lower() != 'identity' for x in encodings):
            raise ValueError('encoded media evidence')
        if lengths and (len(lengths) != 1 or not lengths[0].isdigit()
                        or int(lengths[0]) != media.length):
            raise ValueError('response length evidence')
        return ProvenanceCheck(identity, True, 'declaration, playlist and response verified')
    except Exception as exc:
        return ProvenanceCheck(identity, False, type(exc).__name__ + ': ' + str(exc))
