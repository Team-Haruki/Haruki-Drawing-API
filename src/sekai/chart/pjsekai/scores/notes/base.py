import dataclasses

from ..types import *


@dataclasses.dataclass
class BaseNote:
    bar: Fraction

    def __hash__(self) -> int:
        return hash(str(self))

    def __gt__(self, other: 'BaseNote') -> bool:
        return self.bar > other.bar

    def __lt__(self, other: 'BaseNote') -> bool:
        return self.bar < other.bar

    def __ge__(self, other: 'BaseNote') -> bool:
        return self.bar >= other.bar

    def __le__(self, other: 'BaseNote') -> bool:
        return self.bar <= other.bar

    def __eq__(self, other: 'BaseNote') -> bool:
        return self.bar == other.bar


bar_event_epsilon = Fraction(1, 10000)
