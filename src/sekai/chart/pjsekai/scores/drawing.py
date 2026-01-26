import os
import math

import svgwrite
import svgwrite.base
import svgwrite.path
import svgwrite.image
import svgwrite.shapes
import svgwrite.text
import svgwrite.gradients
import svgwrite.masking
import svgwrite.container

from .types import *
from .notes import *

from .score import *
from .lyric import *

from dataclasses import dataclass
from typing import Optional

__all__ = ['Drawing', 'DrawingSentence']


@dataclass
class CoverObject:
    bar_from: Optional[Fraction] = None
    css_class: Optional[str] = None

@dataclass
class CoverRect(CoverObject):
    bar_to: Optional[Fraction] = None

@dataclass
class CoverText(CoverObject):
    text: Optional[str] = None

class Drawing:

    def __init__(
        self,
        score: Score,
        lyric: Lyric = None,
        style_sheet: str = '',
        note_host: str = 'https://asset3.pjsekai.moe/live/note/custom01',
        skill: bool = False,
        music_meta: Optional[dict] = None,
        target_segment_seconds: Optional[float] = None,
        **kwargs,
    ):

        self.score = score
        self.lyric = lyric

        self.n_lanes = 12

        self.note_host = note_host

        ''''widths'''
        self.lane_width = 16
        # self.note_width = 8
        # self.flick_width = 32

        '''heights'''
        self.time_height = 360
        self.note_size = 16
        self.flick_height = 24

        '''paddings'''
        self.lane_padding = 40
        self.time_padding = 32

        # self.padding = 36
        self.slide_path_padding = -1
        self.meta_size = 192
        # self.note_size_ratio = 1.0

        self.tick_length = 24
        self.tick_2_length = 8

        '''skill'''
        self.skill = skill
        self.music_meta = music_meta
        self.special_cover_objects: list[CoverObject] = []
        
        self.target_segment_seconds = target_segment_seconds or 8.0

        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'css/default.css'), encoding='UTF-8') as f:
            self.style_sheet = f.read()
        # 添加技能相关样式
        # if self.skill:
        #     with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'css/skill.css'), encoding='UTF-8') as f:
        #         self.style_sheet += '\n' + f.read() 
        # 添加自定义样式
        self.style_sheet += '\n' + style_sheet

    def __getitem__(self, bar: slice) -> svgwrite.Drawing:
        bar = slice(bar.start or 0, bar.stop or int(self.score.notes[-1].bar + 1))
        sentence = DrawingSentence(self, bar)
        return sentence.svg()

    def svg(self) -> svgwrite.Drawing:
        n_bars = math.ceil(self.score.notes[-1].bar)

        drawings: list[svgwrite.Drawing] = []

        width = 0
        height = 0

        bar = 0
        event = Event(bar=0, bpm=120, bar_length=4, sentence_length=4)

        if self.skill:
            # Add fever cover object
            if self.music_meta:
                for e in self.score.events:
                    if e.text != 'SUPER FEVER!!':
                        continue
                    self.special_cover_objects.append(CoverRect(
                        e.bar, "fever-duration", self.score.get_bar_by_time(self.music_meta["fever_end_time"])
                    ))
                    self.special_cover_objects.append(CoverText(
                        e.bar, "skill-score", "multi+%.2f%%" % (self.music_meta['fever_score'] * 100)
                    ))

            # Add skill cover object
            skill_i = 0
            for e in self.score.events:
                if e.text != "SKILL":
                    continue
                # print(e)
                self.special_cover_objects.append(CoverRect(
                    self.score.get_bar_by_time(self.score.get_time(e.bar) - 5 / 60),
                    "skill-great",
                    self.score.get_bar_by_time(self.score.get_time(e.bar) + 5 + 5 / 60)
                ))
                self.special_cover_objects.append(CoverRect(
                    self.score.get_bar_by_time(self.score.get_time(e.bar) - 2.5 / 60),
                    "skill-perfect",
                    self.score.get_bar_by_time(self.score.get_time(e.bar) + 5 + 2.5 / 60)
                ))
                self.special_cover_objects.append(CoverRect(
                    e.bar,
                    "skill-duration",
                    self.score.get_bar_by_time(self.score.get_time(e.bar) + 5)
                ))
                if self.music_meta:
                    solo_skill_score = "+%.2f%%" % (self.music_meta['skill_score_solo'][skill_i] * 100)
                    multi_skill_score = "+%.2f%%" % (self.music_meta['skill_score_multi'][skill_i] * 100)
                    append_text = solo_skill_score
                    if solo_skill_score != multi_skill_score:
                        append_text = "solo%s multi%s" % (solo_skill_score, multi_skill_score)
                    self.special_cover_objects.append(CoverText(
                        e.bar, "skill-score", append_text
                    ))
                skill_i += 1
            pass

        # 设定一个目标高度。
        # 原逻辑是 120BPM 下 4个小节 (4/4拍)。
        # 计算参考：120 BPM => 0.5秒/拍 => 2秒/小节 => 4小节=8秒。
        # self.time_height 是每单位时间的像素高度（代码中通常对应1秒或1单位归一化时间，需根据get_time_delta的返回值确定）。
        target_pixel_height = self.time_height * self.target_segment_seconds

        for i in range(n_bars + 1):
            e = self.score.get_event(i)
            # 计算从当前段落起始 bar 到现在的 i 之间的高度
            current_segment_height = self.time_height * self.score.get_time_delta(bar, i)
            # 修改判断逻辑
            if bar != i and (
                e.section != event.section or                       # 1. 章节发生变化时，依然强制切分
                current_segment_height >= target_pixel_height or    # 2. 当高度超过目标值时切分
                i == n_bars                                         # 3. 到达末尾
            ):
                d = self[bar: i]

                width += d['width']
                if height < d['height']:
                    height = d['height']

                drawings.append(d)

                bar = i
            
            event |= e

        # # 原逻辑
        # for i in range(n_bars + 1):
        #     e = self.score.get_event(i)

        #     if bar != i and (
        #         e.section != event.section or
        #         e.sentence_length != event.sentence_length or
        #         i == bar + event.sentence_length or
        #         i == n_bars
        #     ):
        #         d = self[bar: i]

        #         width += d['width']
        #         if height < d['height']:
        #             height = d['height']

        #         drawings.append(d)

        #         bar = i

        #     event |= e

        drawing = svgwrite.Drawing(size=(
            width + self.lane_padding * 2,
            height + self.time_padding * 2 + self.meta_size + self.time_padding * 2,
        ))

        drawing.defs.add(drawing.style(self.style_sheet))

        decoration_gradient = svgwrite.gradients.LinearGradient(
            start=(0, 1), end=(0, 0), id='decoration-gradient', debug=False)
        decoration_gradient.add_stop_color(offset=0, color='var(--color-start)')
        decoration_gradient.add_stop_color(offset=1, color='var(--color-stop)')
        drawing.defs.add(decoration_gradient)

        decoration_critical_gradient = svgwrite.gradients.LinearGradient(
            start=(0, 1), end=(0, 0), id='decoration-critical-gradient', debug=False)
        decoration_critical_gradient.add_stop_color(offset=0, color='var(--color-start)')
        decoration_critical_gradient.add_stop_color(offset=1, color='var(--color-stop)')
        drawing.defs.add(decoration_critical_gradient)

        # tap_left = svgwrite.masking.ClipPath(id="tap-left")
        # tap_left.add(svgwrite.shapes.Rect(size=(100, 100)))
        # drawing.defs.add(tap_left)

        note_m_ratio = 1200
        for note_number in range(0, 7):
            symbol = svgwrite.container.Symbol(
                id=f'notes-{note_number}',
                viewBox='0 0 112 56',
            )
            symbol.add(svgwrite.image.Image(
                href=f'{self.note_host}/notes_{note_number}.png',
                insert=(-3, -3),
                size=(118, 62),
            ))
            drawing.defs.add(symbol)

            symbol = svgwrite.container.Symbol(
                id=f'notes-{note_number}-middle',
                viewBox=f'0 0 {112 * note_m_ratio} {56}',
            )
            symbol.add(svgwrite.image.Image(
                href=f'{self.note_host}/notes_{note_number}.png',
                insert=(-(3 + 28) * note_m_ratio, -3), size=(118 * note_m_ratio, 62),
                preserveAspectRatio='none',
            ))
            drawing.defs.add(symbol)

            for i in range(1, self.n_lanes + 1):
                note_height = self.note_size
                note_width = self.lane_width * (i + 1)
                note_inner_width = self.lane_width * i

                note_l_width = note_r_width = note_height / 56 * 32
                note_m_width = note_inner_width - (note_l_width + note_r_width) / 2 - 2
                note_padding_x = (note_width - note_l_width - note_m_width - note_r_width) / 2

                symbol = svgwrite.container.Symbol(
                    id=f'notes-{note_number}-{i}', viewBox=f'0 0 {note_width} {note_height}')

                left_clip_path = svgwrite.masking.ClipPath(id=f'notes-{note_number}-{i}-left')
                left_clip_path.add(svgwrite.shapes.Rect(
                    insert=(0, 0),
                    size=(note_l_width, note_height),
                ))
                symbol.add(left_clip_path)

                middle_clip_path = svgwrite.masking.ClipPath(id=f'notes-{note_number}-{i}-middle')
                middle_clip_path.add(svgwrite.shapes.Rect(
                    insert=(0, 0),
                    size=(note_m_width, note_height),
                ))
                symbol.add(middle_clip_path)

                right_clip_path = svgwrite.masking.ClipPath(id=f'notes-{note_number}-{i}-right')
                right_clip_path.add(svgwrite.shapes.Rect(
                    insert=(note_height / 56 * 80, 0),
                    size=(note_r_width, note_height),
                ))
                symbol.add(right_clip_path)

                symbol.add(svgwrite.container.Use(
                    href=f'#notes-{note_number}',
                    insert=(note_padding_x, 0),
                    size=(note_height * 2, note_height),
                    clip_path=f'url(#notes-{note_number}-{i}-left)',
                ))
                symbol.add(svgwrite.container.Use(
                    href=f'#notes-{note_number}-middle',
                    insert=(note_padding_x + note_l_width, 0),
                    size=(note_height * note_m_ratio * 2, note_height),
                    clip_path=f'url(#notes-{note_number}-{i}-middle)',
                ))
                symbol.add(svgwrite.container.Use(
                    href=f'#notes-{note_number}',
                    insert=(note_padding_x + note_l_width + note_m_width + note_r_width - note_height * 2, 0),
                    size=(note_height * 2, note_height),
                    clip_path=f'url(#notes-{note_number}-{i}-right)',
                ))

                drawing.defs.add(symbol)

        drawing.add(drawing.rect(
            insert=(0, 0),
            size=(
                width + self.lane_padding * 2,
                height + self.time_padding * 2,
            ),
            class_='background',
        ))

        drawing.add(drawing.rect(
            insert=(0, height + self.time_padding * 2),
            size=(
                width + self.lane_padding * 2,
                self.meta_size + self.time_padding * 2,
            ),
            class_='meta',
        ))

        drawing.add(drawing.line(
            start=(
                0,
                height + self.time_padding * 2,
            ),
            end=(
                width + self.lane_padding * 2,
                height + self.time_padding * 2,
            ),
            class_='meta-line',
        ))

        drawing.add(svgwrite.image.Image(
            href=self.score.meta.jacket or 'https://storage.sekai.best/sekai-jp-assets/thumbnail/chara_rip/res009_no021_normal.png',
            insert=(
                self.lane_padding * 2,
                height + self.time_padding * 3,
            ),
            size=(self.meta_size, self.meta_size),
        ))

        drawing.add(svgwrite.text.Text(
            ' - '.join(filter(lambda x: x, [
                self.score.meta.title,
                self.score.meta.artist,
            ])) or 'Untitled',
            insert=(
                self.meta_size + self.lane_padding * 4,
                self.meta_size + height + self.time_padding * 3 - 16,
            ),
            class_='title',
        ))

        drawing.add(svgwrite.text.Text(
            ' '.join(filter(lambda x: x, [
                self.score.meta.difficulty and str(self.score.meta.difficulty).upper(),
                self.score.meta.playlevel,
                'Code by pjsekai.moe, Modified by bilibili @xfl03 (3-3.dev), Generated by LunaBot',
            ])),
            insert=(
                self.meta_size + self.lane_padding * 4,
                self.meta_size * 1/3 + height + self.time_padding * 3 - 8,
            ),
            class_='subtitle',
        ))

        # scale = self.scale()
        # scale['x'] = width - self.meta_size
        # scale['y'] = height + self.padding * 2
        # drawing.add(scale)

        width = 0
        for d in drawings:
            d['x'] = width + self.lane_padding
            d['y'] = height - d['height'] + self.time_padding
            width += d['width']
            drawing.add(d)

        return drawing


class DrawingSentence(Drawing):

    def __init__(self, drawing: Drawing, bar: slice):
        self.bar = bar

        self.slide_paths = []
        self.among_images = []
        self.note_images = []
        self.flick_images = []
        self.tick_texts = []

        for k, v in vars(drawing).items():
            self.__setattr__(k, v)

    def _get_bezier_coordinates(self, slide_0: Slide, slide_1: Slide):
        # Bézier curve:
        # left: from l[0], controlled by l[1] and l[2], to l[3]
        # right: from r[0], controlled by r[1] and r[2], to r[3]

        y_0 = self.time_height * self.score.get_time_delta(slide_0.bar, self.bar.stop) + self.time_padding
        y_1 = self.time_height * self.score.get_time_delta(slide_1.bar, self.bar.stop) + self.time_padding

        ease_in = slide_0.directional and slide_0.directional.type in (
            DirectionalType.DOWN, )
        ease_out = slide_0.directional and slide_0.directional.type in (
            DirectionalType.LOWER_LEFT, DirectionalType.LOWER_RIGHT)

        slide_path_padding = self.slide_path_padding if not slide_0.decoration else 0

        return (
            (
                self.lane_width * (slide_0.lane - 2) + self.lane_padding - slide_path_padding,
                y_0,
            ),
            (
                self.lane_width * (slide_0.lane - 2) + self.lane_padding - slide_path_padding,
                (y_0 + y_1) / 2 if ease_in else y_0,
            ),
            (
                self.lane_width * (slide_1.lane - 2) + self.lane_padding - slide_path_padding,
                (y_0 + y_1) / 2 if ease_out else y_1,
            ),
            (
                self.lane_width * (slide_1.lane - 2) + self.lane_padding - slide_path_padding,
                y_1,
            ),
        ), (
            (
                self.lane_width * (slide_0.lane - 2 + slide_0.width) + self.lane_padding + slide_path_padding,
                y_0,
            ),
            (
                self.lane_width * (slide_0.lane - 2 + slide_0.width) + self.lane_padding + slide_path_padding,
                (y_0 + y_1) / 2 if ease_in else y_0,
            ),
            (
                self.lane_width * (slide_1.lane - 2 + slide_1.width) + self.lane_padding + slide_path_padding,
                (y_0 + y_1) / 2 if ease_out else y_1,
            ),
            (
                self.lane_width * (slide_1.lane - 2 + slide_1.width) + self.lane_padding + slide_path_padding,
                y_1,
            )
        )

    def add_slide_path(self, slide: Slide):
        lefts, rights = [], []
        slide_0: Slide = slide

        while slide_0.type != SlideType.END:
            amongs = []
            slide_1: Slide = slide_0.next
            while True:
                if slide_1.type == SlideType.RELAY:
                    amongs.append(slide_1)

                if slide_1.is_path():
                    break

                slide_1 = slide_1.next

            l, r = self._get_bezier_coordinates(slide_0, slide_1)
            lefts.append(l)
            rights.append(r)

            for among in amongs:
                self.add_among_image(among, l, r)

            slide_0 = slide_1

        d = [
            [
                [
                    ('M', list(map(round, [*l[0]]))) if i == 0 else [],
                    ('C', list(map(round, [*l[1], *l[2], *l[3]])))
                ]
                for i, l in enumerate(lefts)
            ],
            [
                [
                    ('L', list(map(round, [*r[3]]))) if i == 0 else [],
                    ('C', list(map(round, [*r[2], *r[1], *r[0]])))
                ]
                for i, r in enumerate(reversed(rights))
            ],
            ('z'),
        ]

        class_name: str
        if slide.decoration:
            class_name = 'decoration-critical' if slide.is_critical() else 'decoration'
        else:
            class_name = 'slide-critical' if slide.is_critical() else 'slide'

        self.slide_paths.append(
            svgwrite.path.Path(
                d=d,
                class_=class_name,
            ),
        )

    def add_friction_among_image(self, note: Note):
        y = self.time_height * self.score.get_time_delta(note.bar, self.bar.stop) + self.time_padding
        x = self.lane_width * (note.lane + note.width / 2 - 2) + self.lane_padding

        w = self.lane_width * 0.75
        h = self.lane_width * 0.75

        self.among_images.append(svgwrite.image.Image(
            href='%s/notes_friction_among%s.png' % (
                self.note_host,
                '_crtcl' if note.is_critical() else '_flick' if isinstance(note, Directional) else '_long',
            ),
            insert=(
                round(x - w / 2),
                round(y - h / 2),
            ),
            size=(
                round(w),
                round(h),
            ),
        ))

    def add_among_image(self, note: Note, l, r):
        y = self.time_height * self.score.get_time_delta(note.bar, self.bar.stop) + self.time_padding

        x_l = _binary_solution_for_x(y, l)
        x_r = _binary_solution_for_x(y, r)
        x = (x_l + x_r) / 2

        w = self.lane_width
        h = self.lane_width

        self.among_images.append(svgwrite.image.Image(
            href='%s/notes_long_among%s.png' % (
                self.note_host,
                '_crtcl' if note.is_critical() else '',
            ),
            insert=(
                round(x - w / 2),
                round(y - h / 2),
            ),
            size=(
                round(w),
                round(h),
            ),
        ))

    def add_note_images(self, note: Note):
        y = self.time_height * self.score.get_time_delta(note.bar, self.bar.stop) + self.time_padding
        x = self.lane_width * (note.lane - 2.5) + self.lane_padding

        w = self.lane_width * (note.width + 1)
        h = self.lane_width / 64 * 56 * 2

        note_number = 2

        if note.is_none():
            return
        elif note.is_trend():
            self.add_friction_among_image(note)
            if note.is_critical():
                note_number = 5
            elif isinstance(note, Directional):
                note_number = 6
            else:
                note_number = 4
        else:
            if note.is_critical():
                note_number = 0
            elif isinstance(note, Directional):
                note_number = 3
            elif isinstance(note, Slide):
                if note.type == SlideType.END and note.directional:
                    note_number = 3
                else:
                    note_number = 1

        self.note_images.append(svgwrite.container.Use(
            href=f'#notes-{note_number}-{note.width}',
            insert=(round(x), round(y - h / 2)),
            size=(round(w), round(h)),
        ))

    def add_flick_image(self, note: Note):
        src = '%s/notes_flick_arrow%s_0%s%s.png'
        y = self.time_height * self.score.get_time_delta(note.bar, self.bar.stop) + self.time_padding

        if note.is_none():
            return

        type = DirectionalType.UP
        if isinstance(note, Directional):
            if note.type == DirectionalType.UPPER_LEFT:
                type = DirectionalType.UPPER_LEFT
            elif note.type == DirectionalType.UPPER_RIGHT:
                type = DirectionalType.UPPER_RIGHT
        elif isinstance(note, Slide):
            if note.directional.type == DirectionalType.UPPER_LEFT:
                type = DirectionalType.UPPER_LEFT
            elif note.directional.type == DirectionalType.UPPER_RIGHT:
                type = DirectionalType.UPPER_RIGHT
            elif note.directional.type == DirectionalType.UP:
                type = DirectionalType.UP
            else:
                type = None

        if type is None:
            return

        width = note.width if note.width < 6 else 6

        h0 = self.flick_height
        h = h0 * ((width + 3) / 3) ** 0.75
        w = h0 * 1.5 * ((width + 0.5) / 3) ** 0.75
        x = self.lane_width * (note.lane - 2 + note.width / 2) + self.lane_padding
        bias = (
            - self.note_size / 4 if type == DirectionalType.UPPER_LEFT else
            self.note_size / 4 if type == DirectionalType.UPPER_RIGHT else
            0
        )

        self.flick_images.append(svgwrite.image.Image(
            src % (
                self.note_host,
                '_crtcl' if note.is_critical() else '',
                width,
                '_diagonal' if type in (DirectionalType.UPPER_LEFT, DirectionalType.UPPER_RIGHT) else ''
            ),
            size=(
                round(w),
                round(h),
            ),
            insert=(
                round(x - w / 2 + bias),
                round(y + self.note_size / 4 - h),
            ),
            transform_origin=f'{round(x + bias)} 0' if type == DirectionalType.UPPER_RIGHT else None,
            transform='scale(-1, 1)' if type == DirectionalType.UPPER_RIGHT else None,
            debug=False,
        ))

    def add_tick_text(self, note: Note, next: Note | None = None):
        y = self.time_height * self.score.get_time_delta(note.bar, self.bar.stop) + self.time_padding

        if next is None:
            self.tick_texts.append(svgwrite.shapes.Line(
                start=(
                    round(self.lane_padding - self.tick_2_length),
                    round(y),
                ),
                end=(
                    round(self.lane_padding),
                    round(y),
                ),
                class_='tick-line',
            ))
            return

        if (
            next is None or
            next is note or
            next.bar == note.bar or
            next.bar - note.bar > 1 or
            next.bar - note.bar > 0.5 and int(next.bar) != int(note.bar)
        ):
            interval = math.floor(note.bar + 1) - note.bar
        else:
            interval = next.bar - note.bar

        interval = interval * self.score.get_event(note.bar).bar_length / 4
        interval = interval.limit_denominator(100)

        if interval == 0:
            return

        text = '%g/%g' % (interval.numerator, interval.denominator) if interval.numerator != 1 else \
            '/%g' % (interval.denominator,)

        self.tick_texts.append(svgwrite.shapes.Line(
            start=(
                round(self.lane_padding - self.tick_length),
                round(y),
            ),
            end=(
                round(self.lane_padding),
                round(y),
            ),
            class_='tick-line',
        ))
        self.tick_texts.append(svgwrite.text.Text(
            text,
            insert=(
                round(self.lane_padding - 4),
                round(y - 2),
            ),
            class_='tick-text',
        ))
        
    def svg(self):
        for i, note in enumerate(self.score.notes):
            if isinstance(note, Slide):
                slide: Slide = note.head
                before = None
                while slide:
                    while not slide.is_path():
                        slide = slide.next

                    if self.bar.start - 1 <= slide.bar < self.bar.stop + 1:  # in
                        break
                    elif slide.bar < self.bar.start - 1:  # before
                        before = True
                    elif before and self.bar.stop + 1 < slide.bar:  # after
                        break

                    slide = slide.next
                else:
                    continue

            else:
                if not self.bar.start - 1 <= note.bar < self.bar.stop + 1:
                    continue

            if note.is_tick() is not None:
                next_tick: Note
                if note.is_tick():
                    for next_tick in self.score.notes[i:]:
                        if next_tick.is_tick() and next_tick.bar > note.bar:
                            break
                    else:
                        next_tick = note
                else:
                    next_tick = None

                self.add_tick_text(note, next=next_tick)

            if isinstance(note, Tap):
                self.add_note_images(note)

            elif isinstance(note, Directional):
                self.add_flick_image(note)
                self.add_note_images(note)

            elif isinstance(note, Slide) and not note.decoration:
                if note.type == SlideType.START:
                    self.add_slide_path(note)
                    self.add_note_images(note)

                elif note.type == SlideType.END:
                    if note.directional:
                        self.add_flick_image(note)
                    self.add_note_images(note)

                elif note.type == SlideType.RELAY:
                    ...

                elif note.type == SlideType.INVISIBLE:
                    ...

            elif isinstance(note, Slide) and note.decoration:
                if note.type == SlideType.START:
                    self.add_slide_path(note)

                elif note.type == SlideType.END:
                    ...

                elif note.type == SlideType.RELAY:
                    ...

                elif note.type == SlideType.INVISIBLE:
                    ...

                if note.tap:
                    self.add_note_images(note.tap)
                    if note.directional:
                        self.add_flick_image(note)

        height = self.time_height * self.score.get_time_delta(self.bar.start, self.bar.stop)

        drawing = svgwrite.Drawing(
            size=(
                round(self.lane_width * self.n_lanes + self.lane_padding * 2),
                round(height + self.time_padding * 2),
            ),
        )

        drawing.add(drawing.rect(
            insert=(0, 0),
            size=(
                round(self.lane_width * self.n_lanes + self.lane_padding * 2),
                round(height + self.time_padding * 2),
            ),
            class_='background',
        ))

        drawing.add(drawing.rect(
            insert=(self.lane_padding, 0),
            size=(
                round(self.lane_width * self.n_lanes),
                round(height + self.time_padding * 2),
            ),
            class_='lane',
        ))
        # Draw special cover object under notes
        for cover_object in self.special_cover_objects:
            if isinstance(cover_object, CoverText):
                cover_bar_from = cover_object.bar_from
                if cover_bar_from < self.bar.start - 0.2 or cover_bar_from >= self.bar.stop - 0.1:
                    continue
                drawing.add(drawing.text(
                    cover_object.text,
                    insert=(
                        self.lane_width * self.n_lanes + self.lane_padding * 2 -3,
                        round(self.time_height * self.score.get_time_delta(cover_bar_from, self.bar.stop) + self.time_padding)
                    ),
                    transform=f'''rotate(-90, {
                        round(self.lane_width * self.n_lanes + self.lane_padding * 2 - 3)
                    }, {
                        round(self.time_height * self.score.get_time_delta(cover_bar_from, self.bar.stop) + self.time_padding)
                    })''',
                    class_=cover_object.css_class
                ))
            elif isinstance(cover_object, CoverRect):
                cover_bar_from = max(self.bar.start - 0.2, cover_object.bar_from)
                cover_bar_to = min(self.bar.stop + 0.2, cover_object.bar_to)
                if cover_bar_to <= cover_bar_from:
                    continue
                drawing.add(drawing.rect(
                    insert=(
                        self.lane_padding,
                        round(self.time_height * self.score.get_time_delta(cover_bar_to, self.bar.stop) + self.time_padding),
                    ),
                    size=(
                        round(self.lane_width * self.n_lanes),
                        round(self.time_height * self.score.get_time_delta(cover_bar_from, cover_bar_to))
                    ),
                    class_=cover_object.css_class
                ))

        for lane in range(0, self.n_lanes + 1, 2):
            drawing.add(drawing.line(
                start=(
                    round(self.lane_width * lane + self.lane_padding),
                    round(0),
                ),
                end=(
                    round(self.lane_width * lane + self.lane_padding),
                    round(height + self.time_padding * 2),
                ),
                class_='lane-line',
            ))

        for bar in range(self.bar.start, self.bar.stop + 1):
            drawing.add(drawing.line(
                start=(
                    round(self.lane_width * 0 + self.lane_padding),
                    round(self.time_height * self.score.get_time_delta(bar, self.bar.stop) + self.time_padding),
                ),
                end=(
                    round(self.lane_width * self.n_lanes + self.lane_padding),
                    round(self.time_height * self.score.get_time_delta(bar, self.bar.stop) + self.time_padding),
                ),
                class_='bar-line',
            ))

            event = self.score.get_event(bar)
            for i in range(1, math.ceil(event.bar_length)):
                t = self.score.get_time_delta(bar + Fraction(i, event.bar_length), self.bar.stop)
                y = self.time_height * t + self.time_padding

                drawing.add(drawing.line(
                    start=(
                        round(self.lane_width * 0 + self.lane_padding),
                        round(y),
                    ),
                    end=(
                        round(self.lane_width * self.n_lanes + self.lane_padding),
                        round(y),
                    ),
                    class_='beat-line',
                ))

        print_events: list[Event] = []
        for event in sorted([Event(bar=i) for i in range(self.bar.start, self.bar.stop + 1)] + self.score.events):
            if event.speed:
                drawing.add(drawing.line(
                    start=(
                        round(self.lane_width * 0 + self.lane_padding),
                        round(self.time_height * self.score.get_time_delta(event.bar, self.bar.stop) + self.time_padding),
                    ),
                    end=(
                        round(self.lane_width * self.n_lanes + self.lane_padding),
                        round(self.time_height * self.score.get_time_delta(event.bar, self.bar.stop) + self.time_padding),
                    ),
                    class_='speed-line',
                ))

                drawing.add(drawing.text(
                    '%gx' % event.speed,
                    insert=(
                        round(self.lane_width * self.n_lanes + self.lane_padding - 2),
                        round(self.time_height * self.score.get_time_delta(event.bar, self.bar.stop) + self.time_padding - 2),
                    ),
                    class_='speed-text',
                ))

                continue

            if print_events and event.bar - print_events[-1].bar <= 1 / 16:
                print_events[-1] |= event
            else:
                print_events.append(event)

            special = event.bpm or event.bar_length or event.speed or event.section or event.text

            drawing.add(drawing.line(
                start=(
                    round(self.lane_width * 0),
                    round(self.time_height * self.score.get_time_delta(event.bar, self.bar.stop) + self.time_padding),
                ),
                end=(
                    round(self.lane_width * 0 + self.lane_padding),
                    round(self.time_height * self.score.get_time_delta(event.bar, self.bar.stop) + self.time_padding),
                ),
                class_='bar-count-flag' if not special else 'event-flag',
            ))

        for event in print_events:
            if not self.bar.start - 1 <= event.bar < self.bar.stop + 1:
                continue

            text = ', '.join(filter(lambda x: x, [
                '#%g' % event.bar if int(event.bar) == event.bar else None,
                '%g BPM' % event.bpm if event.bpm else None,
                '%g/4' % event.bar_length if event.bar_length else None,
                event.section,
                event.text,
            ]))

            special = event.bpm or event.bar_length or event.speed or event.section or event.text

            if not text:
                continue

            drawing.add(drawing.text(
                text,
                insert=(
                    round(self.lane_padding + 8),
                    round(self.time_height * self.score.get_time_delta(event.bar,
                          self.bar.stop) + self.time_padding - self.lane_width * 1.5),
                ),
                transform=f'''rotate(-90, {
                    round(self.lane_padding)
                }, {
                    round(self.time_height * self.score.get_time_delta(event.bar, self.bar.stop) + self.time_padding)
                })''',
                class_='bar-count-text' if not special else 'event-text',
            ))

        if self.lyric:
            for word in self.lyric.words:
                if not self.bar.start - 1 <= word.bar < self.bar.stop + 1:
                    continue

                drawing.add(drawing.text(
                    word.text,
                    insert=(
                        round(self.lane_width * self.n_lanes + self.lane_padding),
                        round(self.time_height * self.score.get_time_delta(word.bar, self.bar.stop) + self.time_padding + 16),
                    ),
                    transform=f'''rotate(-90, {
                        round(self.lane_width * self.n_lanes + self.lane_padding)
                    }, {
                        round(self.time_height * self.score.get_time_delta(word.bar, self.bar.stop) + self.time_padding)
                    })''',
                    class_='lyric-text',
                ))

        for slide_path in self.slide_paths:
            drawing.add(slide_path)

        for tap_image in self.note_images:
            drawing.add(tap_image)

        for among_image in self.among_images:
            drawing.add(among_image)

        for flick_image in reversed(self.flick_images):
            drawing.add(flick_image)

        for tick_text in self.tick_texts:
            drawing.add(tick_text)

        return drawing


def _binary_solution_for_x(y, curve: list[tuple], s: slice = None, e=0.1):
    if s is None:
        s = slice(0, 1)

    t = (s.start + s.stop) / 2
    p = [(
        curve[0][k] * (1 - t) ** 3 * t ** 0 * 1 +
        curve[1][k] * (1 - t) ** 2 * t ** 1 * 3 +
        curve[2][k] * (1 - t) ** 1 * t ** 2 * 3 +
        curve[3][k] * (1 - t) ** 0 * t ** 3 * 1
    ) for k in range(2)]

    # print(y, s, p)

    if y - e < p[1] < y + e:
        return p[0]
    elif p[1] > y:
        return _binary_solution_for_x(y, curve, slice(t, s.stop))
    elif p[1] < y:
        return _binary_solution_for_x(y, curve, slice(s.start, t))
    else:
        raise NotImplementedError
