import dataclasses

from .base import BaseNote


@dataclasses.dataclass
class Note(BaseNote):
    lane: int
    width: int
    type: int

    # TODO: speed is not binded to notes in pjsekai
    speed: float | None = None

    def __hash__(self) -> int:
        return hash(str(self))

    def is_critical(self):
        return False

    def is_trend(self):
        return False

    def is_none(self):
        return False

    def is_tick(self):
        return True
