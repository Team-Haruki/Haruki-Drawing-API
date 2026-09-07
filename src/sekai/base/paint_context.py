"""Coordinate state shared by raster painters and IR recording."""

from typing import Self

from .paint_types import Position, Size


class PaintContext:
    def __init__(self, size: Size):
        self.offset = (0, 0)
        self.size = size
        self.w, self.h = size
        self.region_stack = []

    def set_region(self, pos: Position, size: Size) -> Self:
        assert isinstance(pos[0], int), "Position x must be integer"
        assert isinstance(pos[1], int), "Position y must be integer"
        assert isinstance(size[0], int), "Size width must be integer"
        assert isinstance(size[1], int), "Size height must be integer"
        self.region_stack.append((self.offset, self.size))
        self.offset = pos
        self.size = size
        self.w = size[0]
        self.h = size[1]
        return self

    def shrink_region(self, dlt: Position) -> Self:
        pos = (self.offset[0] + dlt[0], self.offset[1] + dlt[1])
        size = (self.size[0] - dlt[0] * 2, self.size[1] - dlt[1] * 2)
        return self.set_region(pos, size)

    def expand_region(self, dlt: Position) -> Self:
        pos = (self.offset[0] - dlt[0], self.offset[1] - dlt[1])
        size = (self.size[0] + dlt[0] * 2, self.size[1] + dlt[1] * 2)
        return self.set_region(pos, size)

    def move_region(self, dlt: Position, size: Size = None) -> Self:
        offset = (self.offset[0] + dlt[0], self.offset[1] + dlt[1])
        size = size or self.size
        return self.set_region(offset, size)
