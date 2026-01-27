import json
import dataclasses

from .notes import *
from .types import *

from .score import *
from .meta import *

__all__ = ['Rebase']


@dataclasses.dataclass
class Rebase:
    offset: float
    events: list[Event]
    meta: Meta

    @classmethod
    def load(cls, a) -> 'Rebase':
        if isinstance(a, dict):
            return cls.load_from_dict(a)

        a = json.load(a)
        return cls.load_from_dict(a)

    @classmethod
    def load_from_dict(cls, d: dict) -> 'Rebase':
        return Rebase(
            offset=d.get('offset', 0),
            events=[
                Event(
                    bar=event.get('bar'),
                    bpm=event.get('bpm'),
                    bar_length=event.get('barLength'),
                    sentence_length=event.get('sentenceLength'),
                    section=event.get('section'),
                    text=event.get('text'),
                )
                for event in d.get('events', [])
            ],
            meta=Meta(**d.get('meta', {})),
        )

    def __call__(rebase, self: Score) -> Score:
        score = Score()
        score.meta = self.meta | rebase.meta
        score.events = rebase.events

        def rebase_note(note_0: Note):
            return dataclasses.replace(
                note_0,
                bar=score.get_bar_by_time(self.get_time(note_0.bar) - rebase.offset),
                **{
                    key: None
                    for key in {
                        Tap: [],
                        Directional: ['tap'],
                        Slide: ['tap', 'directional', 'next', 'head'],
                    }[type(note_0)]
                },
            )

        for note_0 in self.notes:
            if isinstance(note_0, Tap):
                score.notes.append(rebase_note(note_0))

            elif isinstance(note_0, Directional):
                score.notes.append(rebase_note(note_0))
                if note_0.tap:
                    score.notes.append(rebase_note(note_0.tap))

            elif isinstance(note_0, Slide):
                score.notes.append(rebase_note(note_0))
                if note_0.tap:
                    score.notes.append(rebase_note(note_0.tap))
                if note_0.directional:
                    score.notes.append(rebase_note(note_0.directional))
                    if note_0.directional.tap and note_0.directional.tap is not note_0.tap:
                        score.notes.append(rebase_note(note_0.directional.tap))

        score.events = sorted(score.events + [
            dataclasses.replace(event, bar=score.get_bar_by_time(self.get_time(event.bar) - rebase.offset))
            for event in self.events
            if event.speed or event.text
        ])

        score.notes.sort(key=lambda note: note.bar)
        score._init_notes()
        score._init_events()
        return score

    def rebase(rebase, self: Score) -> Score:
        return rebase(self)
