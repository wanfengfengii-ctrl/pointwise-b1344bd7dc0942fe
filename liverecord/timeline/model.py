from dataclasses import dataclass
from fractions import Fraction
from liverecord.source.errors import InputError
from liverecord.source.responses import canonical, ResponseEvidence


@dataclass(frozen=True, order=True)
class SegmentIdentity:
    source_id: str
    discontinuity_sequence: int
    media_sequence: int

    def __post_init__(self):
        if (not isinstance(self.source_id, str) or not self.source_id.strip()
                or type(self.discontinuity_sequence) is not int
                or type(self.media_sequence) is not int
                or min(self.discontinuity_sequence, self.media_sequence) < 0):
            raise InputError('invalid complete segment identity')

    @property
    def key(self):
        return canonical(self.to_list())

    def to_list(self):
        return [self.source_id, self.discontinuity_sequence, self.media_sequence]


@dataclass(frozen=True)
class ByteRange:
    offset: int
    length: int

    def __post_init__(self):
        if (type(self.offset) is not int or type(self.length) is not int
                or self.offset < 0 or self.length <= 0):
            raise InputError('invalid byte range')

    def to_list(self):
        return [self.offset, self.length]


@dataclass(frozen=True)
class SegmentDeclaration:
    identity: SegmentIdentity
    uri: str
    duration: Fraction
    byte_range: ByteRange | None = None

    def __post_init__(self):
        from liverecord.source.registry import validate_url
        validate_url(self.uri)
        if not isinstance(self.duration, Fraction) or self.duration <= 0:
            raise InputError('duration must be a positive Fraction')

    def to_dict(self):
        return dict(identity=self.identity.to_list(), uri=self.uri,
                    duration=[self.duration.numerator, self.duration.denominator],
                    byte_range=self.byte_range.to_list() if self.byte_range else None)

    @classmethod
    def from_dict(cls, value):
        return cls(SegmentIdentity(*value['identity']), value['uri'],
                   Fraction(*value['duration']),
                   ByteRange(*value['byte_range']) if value['byte_range'] else None)


@dataclass(frozen=True)
class PlaylistRevision:
    source_id: str
    body: bytes
    evidence: ResponseEvidence
    declarations: tuple

    def to_dict(self):
        return dict(source_id=self.source_id, evidence=self.evidence.to_dict(),
                    declarations=[x.to_dict() for x in self.declarations])
