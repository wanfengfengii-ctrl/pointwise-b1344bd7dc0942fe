import re
from urllib.parse import urljoin
from liverecord.timeline.model import ByteRange
from liverecord.source.registry import validate_url
from liverecord.source.errors import PlaylistError, InputError


class TimelineContext:
    def __init__(self, base_url, media=0, discontinuity=0):
        self.base_url = base_url
        self.media = media
        self.discontinuity = discontinuity
        self.previous_uri = None
        self.previous_range = None

    def resolve_uri(self, value):
        try:
            return validate_url(urljoin(self.base_url, value))
        except InputError as exc:
            raise PlaylistError(str(exc)) from exc

    def resolve_range(self, value, uri):
        if value is None:
            result = None
        else:
            match = re.fullmatch(r'([0-9]+)(?:@([0-9]+))?', value)
            if match is None or int(match[1]) <= 0:
                raise PlaylistError('invalid BYTERANGE')
            if match[2] is None:
                if self.previous_uri != uri or self.previous_range is None:
                    raise PlaylistError('implicit range requires adjacent same resource')
                offset = self.previous_range.offset + self.previous_range.length
            else:
                offset = int(match[2])
            result = ByteRange(offset, int(match[1]))
        self.previous_uri, self.previous_range = uri, result
        return result

    def advance_discontinuity(self):
        self.discontinuity += 1
