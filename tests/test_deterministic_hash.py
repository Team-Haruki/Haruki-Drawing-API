from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from src.sekai.base.painter import deterministic_hash


@dataclass
class _Record:
    name: str
    values: tuple[int, ...]


class _Object:
    def __init__(self, value: int, private: int) -> None:
        self.value = value
        self._private = private


class _Slotted:
    __slots__ = ("value",)

    def __init__(self, value: int) -> None:
        self.value = value


class _UnreadableSlotted:
    __slots__ = ()

    @property
    def value(self) -> int:
        raise RuntimeError("unreadable")


def test_deterministic_hash_is_stable_across_unordered_containers() -> None:
    first = {"mapping": {"b": 2, "a": 1}, "set": {3, 1, 2}, "frozen": frozenset({5, 4})}
    second = {"frozen": frozenset({4, 5}), "set": {2, 3, 1}, "mapping": {"a": 1, "b": 2}}

    assert deterministic_hash(first) == deterministic_hash(second)


def test_deterministic_hash_supports_scalar_and_iterable_values() -> None:
    value = [None, True, 42, 3.5, "text", b"bytes", Path("asset.png"), (item for item in (1, 2))]

    assert deterministic_hash(value) == deterministic_hash([*value[:-1], (item for item in (1, 2))])


def test_deterministic_hash_tracks_image_and_array_contents() -> None:
    black = Image.new("RGBA", (2, 1), (0, 0, 0, 255))
    white = Image.new("RGBA", (2, 1), (255, 255, 255, 255))
    zeros = np.zeros((2, 2), dtype=np.uint8)
    ones = np.ones((2, 2), dtype=np.uint8)

    assert deterministic_hash(black) != deterministic_hash(white)
    assert deterministic_hash(zeros) != deterministic_hash(ones)


def test_deterministic_hash_supports_dataclasses_and_public_object_state() -> None:
    assert deterministic_hash(_Record("a", (1, 2))) == deterministic_hash(_Record("a", (1, 2)))
    assert deterministic_hash(_Object(1, private=2)) == deterministic_hash(_Object(1, private=99))
    assert deterministic_hash(_Object(1, private=2)) != deterministic_hash(_Object(2, private=2))


def test_deterministic_hash_supports_reflective_slotted_objects() -> None:
    assert deterministic_hash(_Slotted(1)) == deterministic_hash(_Slotted(1))
    assert deterministic_hash(_Slotted(1)) != deterministic_hash(_Slotted(2))


def test_deterministic_hash_tolerates_unreadable_reflective_attributes() -> None:
    assert deterministic_hash(_UnreadableSlotted()) == deterministic_hash(_UnreadableSlotted())
