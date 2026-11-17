from dataclasses import dataclass
import re
from liverecord.source.responses import header_values
from liverecord.source.errors import IntegrityError


@dataclass(frozen=True)
class RangeRequest:
    offset: int
    length: int

    @property
    def header(self):
        return 'bytes=' + str(self.offset) + '-' + str(self.offset + self.length - 1)


def make_range_request(byte_range):
    if byte_range is None:
        return None
    return RangeRequest(byte_range.offset, byte_range.length)


def validate_range_response(byte_range, evidence):
    request = make_range_request(byte_range)
    if request is None:
        if evidence.status != 200 or evidence.request_range is not None:
            raise IntegrityError('complete media requires a complete 200 response')
        if header_values(evidence, 'Content-Range'):
            raise IntegrityError('unexpected Content-Range')
        return
    values = header_values(evidence, 'Content-Range')
    if evidence.status != 206 or evidence.request_range != request.header or len(values) != 1:
        raise IntegrityError('range requires 206 and unambiguous Content-Range')
    match = re.fullmatch(r'bytes ([0-9]+)-([0-9]+)/([0-9]+|\*)', values[0])
    last = request.offset + request.length - 1
    if (match is None or int(match[1]) != request.offset or int(match[2]) != last
            or (match[3] != '*' and int(match[3]) <= last)):
        raise IntegrityError('response range does not match request')
    if evidence.length != request.length:
        raise IntegrityError('range body length mismatch')
