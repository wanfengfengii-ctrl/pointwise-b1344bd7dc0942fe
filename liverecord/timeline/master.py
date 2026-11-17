import re
from urllib.parse import urljoin
from liverecord.source.errors import InputError, IntegrityError, PlaylistError
from liverecord.source.registry import validate_url
from liverecord.source.responses import sha256
from liverecord.acquisition.validation import decode_expected_digest

STREAM_INF_VALUE = re.compile(r'BANDWIDTH=([0-9]+)')


def validate_list_response(response):
    evidence = response.evidence
    try:
        expected = decode_expected_digest(evidence)
    except IntegrityError as exc:
        raise PlaylistError(str(exc)) from exc
    if expected is not None and expected != evidence.sha256:
        raise PlaylistError('upstream sha-256 Digest does not match playlist body')


def parse_master(source_url, response):
    validate_list_response(response)
    evidence = response.evidence
    body = response.body
    if (evidence.requested_url != source_url or evidence.request_range is not None
            or evidence.status != 200 or evidence.length != len(body)
            or evidence.sha256 != sha256(body)):
        raise PlaylistError('invalid playlist response evidence')
    try:
        text = body.decode('utf-8')
    except UnicodeError as exc:
        raise PlaylistError('playlist is not UTF-8') from exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != '#EXTM3U':
        raise PlaylistError('missing EXTM3U')
    variants = []
    seen = set()
    pending = None
    for line in lines[1:]:
        if line.startswith('#EXT'):
            tag, separator, value = line.partition(':')
            if tag != '#EXT-X-STREAM-INF' or not separator:
                raise PlaylistError('unsupported master declaration: ' + tag)
            if pending is not None:
                raise PlaylistError('STREAM-INF missing its URI')
            match = STREAM_INF_VALUE.fullmatch(value)
            if match is None or int(match.group(1)) <= 0:
                raise PlaylistError('invalid STREAM-INF bandwidth')
            pending = int(match.group(1))
        elif line.startswith('#'):
            continue
        else:
            if pending is None:
                raise PlaylistError('URI without STREAM-INF')
            try:
                uri = validate_url(urljoin(evidence.final_url, line))
            except InputError as exc:
                raise PlaylistError(str(exc)) from exc
            if uri in seen:
                raise PlaylistError('duplicate variant URI')
            seen.add(uri)
            variants.append((pending, uri))
            pending = None
    if pending is not None:
        raise PlaylistError('dangling STREAM-INF')
    if not variants:
        raise PlaylistError('master playlist has no variants')
    return tuple(variants)


def select_variant(variants, max_bandwidth):
    eligible = [(bandwidth, uri) for bandwidth, uri in variants
                if bandwidth <= max_bandwidth]
    if not eligible:
        raise PlaylistError('no variant within bandwidth limit')
    bandwidth = max(item[0] for item in eligible)
    uri = min(item[1] for item in eligible if item[0] == bandwidth)
    return bandwidth, uri
