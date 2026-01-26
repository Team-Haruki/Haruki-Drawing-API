import dataclasses

from .base import BaseNote
from ..types import Fraction


@dataclasses.dataclass
class Event(BaseNote):
    bpm: Fraction = None
    bar_length: Fraction = None
    sentence_length: int = None
    speed: float = None

    section: str = None
    text: str = None

    def __post_init__(self):
        if self.bpm is not None:
            self.bpm = Fraction(self.bpm)

        if self.bar_length is not None:
            self.bar_length = Fraction(self.bar_length)

    def __hash__(self) -> int:
        return hash(str(self))

    def __or__(self, other: 'Event'):
        assert self.bar <= other.bar
        return Event(
            bar=other.bar,
            bpm=other.bpm or self.bpm,
            bar_length=other.bar_length or self.bar_length,
            sentence_length=other.sentence_length or self.sentence_length,
            speed=other.speed or self.speed,
            section=other.section or self.section,
            text=other.text or self.text,
        )
