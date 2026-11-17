from dataclasses import dataclass
import base64
import binascii
from liverecord.acquisition.ranges import validate_range_response
from liverecord.source.responses import header_values, sha256
from liverecord.source.errors import IntegrityError


def decode_expected_digest(evidence):
    expected = None
    for value in header_values(evidence, 'Digest'):
        for component in value.split(','):
            algorithm, separator, encoded = component.strip().partition('=')
            if not separator or not algorithm or not encoded.strip():
                raise IntegrityError('malformed Digest header')
            if algorithm.lower() != 'sha-256':
                continue
            try:
                decoded = base64.b64decode(encoded.strip(), validate=True)
            except (ValueError, binascii.Error):
                raise IntegrityError('malformed sha-256 Digest') from None
            if len(decoded) != 32 or (expected is not None and expected != decoded.hex()):
                raise IntegrityError('invalid or conflicting sha-256 Digest')
            expected = decoded.hex()
    return expected


@dataclass(frozen=True)
class ValidatedMedia:
    declaration: object
    revision_id: str
    response: object


def validate_media(declaration, revision_id, response):
    evidence = response.evidence
    if evidence.requested_url != declaration.uri:
        raise IntegrityError('media request URI differs from declaration')
    if evidence.length != len(response.body) or evidence.sha256 != sha256(response.body):
        raise IntegrityError('response body differs from evidence')
    validate_range_response(declaration.byte_range, evidence)
    encodings = header_values(evidence, 'Content-Encoding')
    if any(value.strip().lower() != 'identity' for value in encodings):
        raise IntegrityError('encoded media is unsupported')
    lengths = header_values(evidence, 'Content-Length')
    if lengths and (len(lengths) != 1 or not lengths[0].isdigit()
                    or int(lengths[0]) != len(response.body)):
        raise IntegrityError('Content-Length mismatch')
    expected = decode_expected_digest(evidence)
    if expected is not None and expected != evidence.sha256:
        raise IntegrityError('upstream sha-256 mismatch')
    return ValidatedMedia(declaration, revision_id, response)
