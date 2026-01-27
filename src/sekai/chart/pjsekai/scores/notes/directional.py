import enum
import dataclasses

from .note import Note


@dataclasses.dataclass
class Directional(Note):
    tap: Note | None = dataclasses.field(default=None, repr=False)

    def __hash__(self) -> int:
        return hash(str(self))

    def is_critical(self):
        if self.tap and self.tap.is_critical():
            return True

        return False

    def is_trend(self):
        if self.tap and self.tap.is_trend():
            return True

        return False

    def is_tick(self):
        if self.is_none():
            return None
        if self.is_trend():
            return False

        return True


class DirectionalType(enum.IntEnum):
    UP = 1
    DOWN = 2
    UPPER_LEFT = 3
    UPPER_RIGHT = 4
    LOWER_LEFT = 5
    LOWER_RIGHT = 6
