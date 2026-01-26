import bisect
import functools

from .notes import *
from .types import *

from .meta import *
from .line import *

__all__ = ['Score']


class Score:

    def __init__(self):
        self.meta = Meta()
        self.notes: list[Note] = []
        self.events: list[Event] = []

    def _init_by_lines(self, lines: list[Line]):
        self.meta = Meta()
        self.notes = []
        self.events = []

        bpm_definitions: dict[int, Fraction] = {}
        speed_definitions: dict[int, SpeedDefinition] = {}
        speed_control = SpeedControl(None)
        ticks_per_beat = TicksPerBeat(480)

        for line in lines:
            for object in line.parse():
                match object:
                    case Meta():
                        self.meta |= object

                    case TicksPerBeat():
                        self.ticks_per_beat = object

                    case SpeedControl():
                        speed_control = object

                    case SpeedDefinition():
                        speed_definitions[object.id] = object
                        for item in object.items:
                            bar = item.bar + Fraction(item.tick, ticks_per_beat * 4)
                            self.events.append(Event(bar=bar, speed=item.speed))

                    case Event():
                        self.events.append(object)

                    case BpmDefinition():
                        bpm_definitions[object.id] = object.bpm

                    case BpmReference():
                        self.events.append(Event(bar=object.bar, bpm=bpm_definitions[object.id]))

                    case Note():
                        self.notes.append(object)

        self._init_notes()
        self._init_events()

    def _init_notes(self):
        self.notes.sort()

        note_deleted = [False] * len(self.notes)
        note_indexes: dict[Fraction, list[int]] = {}

        for i, note in enumerate(self.notes):
            if not 0 <= note.lane - 2 < 12:
                note_deleted[i] = True
                self.events.append(Event(
                    bar=note.bar,
                    text='SKILL' if note.lane == 0 else 'FEVER CHANCE!' if note.type == 1 else 'SUPER FEVER!!',
                ))
                continue

            if note.bar not in note_indexes:
                note_indexes[note.bar] = []

            note_indexes[note.bar].append(i)

        for i, directional in enumerate(self.notes):
            if note_deleted[i] or not isinstance(directional, Directional):
                continue

            for j in note_indexes[directional.bar]:
                tap = self.notes[j]
                if note_deleted[j] or not isinstance(tap, Tap):
                    continue

                if tap.bar == directional.bar and tap.lane == directional.lane and tap.width == directional.width:
                    note_deleted[j] = True
                    directional.tap = tap

        for i, slide in enumerate(self.notes):
            if note_deleted[i] or not isinstance(slide, Slide):
                continue

            if slide.head is None:
                slide.head = slide

            for j in note_indexes[slide.bar]:
                tap = self.notes[j]
                if note_deleted[j] or not isinstance(tap, Tap):
                    continue

                if tap.bar == slide.bar and tap.lane == slide.lane and tap.width == slide.width:
                    note_deleted[j] = True
                    slide.tap = tap

            for j in note_indexes[slide.bar]:
                directional = self.notes[j]
                if note_deleted[j] or not isinstance(directional, Directional):
                    continue

                if directional.bar == slide.bar and directional.lane == slide.lane and directional.width == slide.width:
                    note_deleted[j] = True
                    slide.directional = directional
                    if directional.tap is not None:
                        slide.tap = directional.tap

            if slide.type != SlideType.END:
                for j in range(i+1, len(self.notes)):
                    next = self.notes[j]
                    if note_deleted[j] or not isinstance(next, Slide) or next.channel != slide.channel or next.decoration != slide.decoration:
                        continue

                    slide.next = next
                    next.head = slide.head
                    break

        self.notes = [note for i, note in enumerate(self.notes) if not note_deleted[i]]

    def _init_events(self):
        self.events.sort()
        events = []

        for event in self.events:
            if len(events) and event == events[-1]:
                events[-1] |= event
            else:
                events.append(event)

        self.events = events

    @classmethod
    def open(cls, file: str, *args, **kwargs):
        self = cls()
        with open(file, *args, **kwargs) as f:
            self._init_by_lines([Line(line) for line in f.readlines()])

        return self

    @functools.cached_property
    def timed_events(self):
        timed_events: list[tuple[Fraction, Event]] = []

        t = 0
        e = Event(bar=0, bpm=120, bar_length=4, sentence_length=4)
        for i, event in enumerate(self.events):
            t += (event.bar - e.bar) * e.bar_length * 60 / e.bpm
            e |= event

            timed_events.append((t, e))

        if not timed_events:
            timed_events.append((0, e))

        return timed_events

    def get_timed_event(self, bar: Fraction) -> tuple[Fraction, Event]:
        t, e = self.timed_events[bisect.bisect(self.timed_events, bar, key=lambda x: x[1].bar) - 1]
        t += e.bar_length * 60 / e.bpm * (bar - e.bar)
        return t, e

    def get_time(self, bar: Fraction) -> Fraction:
        return self.get_timed_event(bar)[0]

    def get_event(self, bar: Fraction) -> Event:
        return self.get_timed_event(bar)[1]

    def get_time_delta(self, bar_from: Fraction, bar_to: Fraction) -> Fraction:
        return self.get_time(bar_to) - self.get_time(bar_from)

    def get_bar_by_time(self, time: float) -> Fraction:
        t = 0.0
        event = Event(bar=0, bpm=120, bar_length=4, sentence_length=4)

        for i in range(len(self.events)):
            event = event | self.events[i]
            if i+1 == len(self.events):
                break

            event_time = event.bar_length * 60 / event.bpm * (self.events[i+1].bar - event.bar)
            if t + event_time > time:
                break
            else:
                t += event_time

        bar = event.bar + (time - t) / (event.bar_length * 60 / event.bpm)

        return Fraction(bar).limit_denominator()

    def print(self, bar_from: int, bar_to: int):
        for note in self.notes:
            if bar_from <= note.bar < bar_to:
                print(note, f'{note.is_trend() = }')
                if hasattr(note, 'tap') and note.tap:
                    print('    tap:', note.tap, f'{note.tap.is_trend() = }')
                if hasattr(note, 'directional') and note.directional:
                    print('    directional:', note.directional, f'{note.directional.is_trend() = }')

                print()
