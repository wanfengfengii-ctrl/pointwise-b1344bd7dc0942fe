from dataclasses import dataclass
from fractions import Fraction
from liverecord.source.errors import InputError


@dataclass(frozen=True)
class TimeSpan:
    start: Fraction
    end: Fraction


def build_positions(durations):
    position = Fraction(0)
    spans = []
    for duration in durations:
        if not isinstance(duration, Fraction) or duration <= 0:
            raise InputError('positive Fraction seconds required')
        spans.append(TimeSpan(position, position + duration))
        position += duration
    return tuple(spans), position
