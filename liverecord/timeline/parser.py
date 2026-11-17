from fractions import Fraction
import re
from liverecord.source.errors import PlaylistError
from liverecord.timeline.context import TimelineContext
from liverecord.timeline.model import SegmentIdentity, SegmentDeclaration, PlaylistRevision


def parse_duration(value):
    if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value) is None:
        raise PlaylistError('EXTINF must be finite decimal seconds')
    result = Fraction(value)
    if result <= 0:
        raise PlaylistError('nonpositive EXTINF')
    return result


def parse_playlist(source_id, response):
    try:
        text = response.body.decode('utf-8')
    except UnicodeError as exc:
        raise PlaylistError('playlist is not UTF-8') from exc
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != '#EXTM3U':
        raise PlaylistError('missing EXTM3U')
    context = TimelineContext(response.evidence.final_url)
    duration = byte_range = None
    declarations = []
    seen = set()
    ended = False
    media_started = False
    for line in lines[1:]:
        if ended:
            if not line.startswith('#') or line.startswith('#EXT'):
                raise PlaylistError('declaration after ENDLIST')
            continue
        if line.startswith('#EXT'):
            tag, separator, value = line.partition(':')
            if tag in ('#EXT-X-MEDIA-SEQUENCE', '#EXT-X-DISCONTINUITY-SEQUENCE'):
                if (not separator or not value.isdigit() or tag in seen
                        or media_started or duration is not None or byte_range is not None):
                    raise PlaylistError('invalid initial sequence')
                seen.add(tag)
                if tag == '#EXT-X-MEDIA-SEQUENCE':
                    context.media = int(value)
                else:
                    context.discontinuity = int(value)
            elif tag == '#EXTINF':
                if not separator or ',' not in value or duration is not None:
                    raise PlaylistError('invalid EXTINF')
                duration = parse_duration(value.split(',', 1)[0])
                media_started = True
            elif tag == '#EXT-X-BYTERANGE':
                if not separator or byte_range is not None:
                    raise PlaylistError('invalid BYTERANGE placement')
                byte_range = value
                media_started = True
            elif tag == '#EXT-X-DISCONTINUITY':
                if separator or duration is not None or byte_range is not None:
                    raise PlaylistError('invalid discontinuity placement')
                context.advance_discontinuity()
                media_started = True
            elif tag in ('#EXT-X-VERSION', '#EXT-X-TARGETDURATION'):
                if not separator or not value.isdigit() or int(value) <= 0 or tag in seen:
                    raise PlaylistError('invalid metadata')
                seen.add(tag)
            elif tag == '#EXT-X-ENDLIST':
                if separator or duration is not None or byte_range is not None:
                    raise PlaylistError('dangling declaration')
                ended = True
            else:
                raise PlaylistError('unsupported HLS declaration: ' + tag)
        elif line.startswith('#'):
            continue
        else:
            if duration is None:
                raise PlaylistError('URI without EXTINF')
            uri = context.resolve_uri(line)
            span = context.resolve_range(byte_range, uri)
            declarations.append(SegmentDeclaration(
                SegmentIdentity(source_id, context.discontinuity, context.media),
                uri, duration, span))
            context.media += 1
            duration = byte_range = None
    if duration is not None or byte_range is not None:
        raise PlaylistError('dangling declaration')
    return PlaylistRevision(source_id, response.body, response.evidence, tuple(declarations))
