import re

from .notes import *
from .types import *

from .meta import *


@dataclasses.dataclass
class BpmDefinition:
    id: int
    bpm: Fraction


@dataclasses.dataclass
class BpmReference(BaseNote):
    id: int


@dataclasses.dataclass
class SpeedDefinitionItem:
    bar: int
    tick: int
    speed: float


@dataclasses.dataclass
class SpeedDefinition:
    id: int
    items: list[SpeedDefinitionItem]


@dataclasses.dataclass
class SpeedControl:
    id: int | None


class TicksPerBeat(int):
    ...


class Line:
    type: str
    header: str
    data: str

    def __init__(self, line: str):
        line = line.strip()

        if match := re.match(r'^#(\w+)\s+(.*)$', line):
            self.type = 'meta'
            self.header, self.data = match.groups()

        elif match := re.match(r'^#(\w+):\s*(.*)$', line):
            self.type = 'score'
            self.header, self.data = match.groups()

        else:
            self.type = 'comment'
            self.header, self.data = 'comment', line

    def parse(self):
        match self.type:
            case 'meta': return self.parse_meta()
            case 'score': return self.parse_score()
            case _: return []

    def parse_meta(self):
        meta = Meta()
        if hasattr(meta, self.header.lower()):
            try:
                data = eval(self.data)
            except:
                data = self.data
            setattr(meta, self.header.lower(), data)
            yield meta

        elif self.header == 'REQUEST':
            if match := re.match(r'^"ticks_per_beat\s+(\d+)"$', self.data):
                yield TicksPerBeat(int(match.group(1)))

        elif self.header == 'HISPEED':
            yield SpeedControl(int(self.data, 36))

        elif self.header == 'NOSPEED':
            yield SpeedControl(None)

    def parse_score(self):
        if match := re.match(r'^(\d\d\d)02$', self.header):
            yield Event(bar=int(match.group(1)), bar_length=int(self.data))

        elif match := re.match(r'^BPM(..)$', self.header):
            yield BpmDefinition(id=int(match.group(1), 36), bpm=Fraction(self.data))

        elif match := re.match(r'^(\d\d\d)08$', self.header):
            for beat, data in self.parse_score_data():
                yield BpmReference(bar=int(match.group(1)) + beat, id=int(data, 36))

        elif match := re.match(r'^TIL(..)$', self.header):
            id = int(match.group(1), 36)
            data: str = eval(self.data)
            items = []
            if data:
                for item in data.split(','):
                    match = re.match(r'(\d+)\'(\d+):(\S+)', item.strip())
                    item = SpeedDefinitionItem(
                        bar=int(match.group(1)),
                        tick=int(match.group(2)),
                        speed=float(match.group(3)),
                    )
                    items.append(item)

            yield SpeedDefinition(id=id, items=sorted(items, key=lambda item: (item.bar, item.tick)))

        elif match := re.match(r'^(\d\d\d)1(.)$', self.header):
            for beat, data in self.parse_score_data():
                yield Tap(
                    bar=int(match.group(1)) + beat,
                    lane=int(match.group(2), 36),
                    width=int(data[1], 36),
                    type=int(data[0], 36),
                )

        elif match := re.match(r'^(\d\d\d)3(.)(.)$', self.header):
            for beat, data in self.parse_score_data():
                yield Slide(
                    bar=int(match.group(1)) + beat,
                    lane=int(match.group(2), 36),
                    width=int(data[1], 36),
                    type=int(data[0], 36),
                    channel=int(match.group(3), 36),
                    decoration=False,
                )

        elif match := re.match(r'^(\d\d\d)5(.)$', self.header):
            for beat, data in self.parse_score_data():
                yield Directional(
                    bar=int(match.group(1)) + beat,
                    lane=int(match.group(2), 36),
                    width=int(data[1], 36),
                    type=int(data[0], 36),
                )

        elif match := re.match(r'^(\d\d\d)9(.)(.)$', self.header):
            for beat, data in self.parse_score_data():
                yield Slide(
                    bar=int(match.group(1)) + beat,
                    lane=int(match.group(2), 36),
                    width=int(data[1], 36),
                    type=int(data[0], 36),
                    channel=int(match.group(3), 36),
                    decoration=True,
                )

    def parse_score_data(self):
        for i in range(0, len(self.data), 2):
            if self.data[i: i+2] != '00':
                yield Fraction(i, len(self.data)), self.data[i: i+2]
