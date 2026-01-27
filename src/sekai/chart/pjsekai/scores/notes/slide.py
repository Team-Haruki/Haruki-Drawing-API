import enum
import dataclasses

from .note import Note


@dataclasses.dataclass
class Slide(Note):
    channel: int = 0
    decoration: bool = False

    tap: Note | None = dataclasses.field(default=None, repr=False)
    directional: Note | None = dataclasses.field(default=None, repr=False)
    next: Note | None = dataclasses.field(default=None, repr=False)
    head: Note | None = dataclasses.field(default=None, repr=False)

    def __hash__(self) -> int:
        return hash(str(self))

    def is_path(self):
        if self.type == 0:
            return False

        if self.type != 3:
            return True

        if self.directional:
            return True

        if self.tap is None and self.directional is None:
            return True

        return False

    def is_critical(self):
        if self.tap and self.tap.is_critical():
            return True
        if self.directional and self.directional.is_critical():
            return True
        if self.head is not self and self.head.is_critical():
            return True

        return False

    def is_trend(self):
        if self.tap and self.tap.is_trend():
            return True
        if self.directional and self.directional.is_trend():
            return True

        return False

    def is_none(self):
        if self.tap and self.tap.is_none():
            return True
        if self.directional and self.directional.is_none():
            return True

        return False

    def is_tick(self):
        if self.is_none():
            return None
        if self.decoration:
            if tap_tick := self.tap and self.tap.is_tick():
                return tap_tick
            if directional_tick := self.directional and self.directional.is_tick():
                return directional_tick
            if tap_tick is not None or directional_tick is not None:
                return False
            return None

        if self.type == SlideType.INVISIBLE:
            return None
        if self.is_trend():
            return False
        if self.type == SlideType.RELAY:
            return False

        return True


class SlideType(enum.IntEnum):
    START = 1
    END = 2
    RELAY = 3
    INVISIBLE = 5
