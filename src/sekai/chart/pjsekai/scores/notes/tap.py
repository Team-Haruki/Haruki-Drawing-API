import enum
import dataclasses

from .note import Note


@dataclasses.dataclass
class Tap(Note):

    def __hash__(self) -> int:
        return hash(str(self))

    def is_critical(self):
        if self.type in (TapType.CRITICAL, TapType.CRITICAL_TREND, TapType.CRITICAL_CANCEL):
            return True

        return False

    def is_trend(self):
        if self.type in (TapType.TREND, TapType.CRITICAL_TREND):
            return True

        return False

    def is_none(self):
        if self.type in (TapType.CANCEL, TapType.CRITICAL_CANCEL):
            return True

        return False

    def is_tick(self):
        if self.is_none():
            return None
        if self.is_trend():
            return False

        return True


class TapType(enum.IntEnum):
    TAP = 1
    CRITICAL = 2
    FLICK = 3
    DAMAGE = 4
    TREND = 5
    CRITICAL_TREND = 6
    CANCEL = 7
    CRITICAL_CANCEL = 8
