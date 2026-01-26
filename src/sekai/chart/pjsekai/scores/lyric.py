import dataclasses
import re

from .types import *

__all__ = ['Word', 'Lyric']


@dataclasses.dataclass
class Word:
    bar: float
    text: str


class Lyric:
    def __init__(self):
        self.words: list[Word] = []

    @classmethod
    def load(cls, f):
        self = cls()
        lines: list[str] = f.readlines()

        for line in lines:
            line = line.strip()
            if match := re.match(r'^(\d+): (.*)$', line):
                bar = int(match.group(1))
                texts = match.group(2).split('/')
                for i, text in enumerate(texts):
                    if text:
                        self.words.append(Word(
                            bar=bar + Fraction(i, len(texts)),
                            text=text
                        ))

        return self
