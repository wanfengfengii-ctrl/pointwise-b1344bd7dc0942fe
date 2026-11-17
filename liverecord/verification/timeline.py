from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class TimelineCheck:
    ok: bool
    detail: str


def check_timeline(capture):
    position = Fraction(0)
    previous = None
    seen = set()
    for item in capture.items:
        declaration = item.media.declaration
        identity = declaration.identity
        if identity.source_id != capture.source_id or identity in seen:
            return TimelineCheck(False, 'source or duplicate identity')
        if previous is not None and identity <= previous:
            return TimelineCheck(False, 'identity order')
        duration = declaration.duration
        if not isinstance(duration, Fraction) or duration <= 0:
            return TimelineCheck(False, 'duration')
        if item.start != position or item.end != position + duration:
            return TimelineCheck(False, 'position does not equal exact duration prefix')
        seen.add(identity)
        previous = identity
        position += duration
    if capture.total != position:
        return TimelineCheck(False, 'total duration')
    return TimelineCheck(True, 'exact positions and total verified')
