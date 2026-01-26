import os
import argparse

from .__init__ import *


class Main:
    def __init__(self):
        self.input: str = None
        self.output: str = None
        self.score: Score = None
        self.rebase: Rebase = None
        self.lyric: Lyric = None
        self.note_host: str = ''
        self.css: str = ''

    @classmethod
    def from_args(cls) -> 'Main':
        parser = argparse.ArgumentParser()
        parser.add_argument('score', metavar='<xxx.sus>', help='the pjsekai score file')
        parser.add_argument('--rebase', metavar='<xxx.json>', help='customized bpm, beats and sections')
        parser.add_argument('--lyric', metavar='<xxx.txt>', help='lyrics')
        parser.add_argument('--css', metavar='<xxx.css>', help='style sheets')
        parser.add_argument('--note-host', dest='note_host', metavar='<url>',
                            default='https://asset3.pjsekai.moe/live/note/custom01',
                            help='the base dir of asset files for notes')

        parser.add_argument('-o', '--output', metavar='<xxx.svg>')
        args = parser.parse_args()

        self = cls()
        self.input = os.path.abspath(args.score)

        if args.output:
            if os.path.isdir(args.output):
                self.output = os.path.join(
                    os.path.dirname(args.output),
                    os.path.splitext(self.input)[0] + '.svg',
                )
            else:
                self.output = str(args.output)
        else:
            self.output = os.path.join(
                os.path.dirname(self.input),
                os.path.splitext(self.input)[0] + '.svg',
            )

        self.score = Score.open(args.score, encoding='UTF-8')

        if args.rebase:
            with open(args.rebase, encoding='UTF-8') as f:
                self.rebase = Rebase.load(f)

        if args.lyric:
            with open(args.lyric, encoding='UTF-8') as f:
                self.lyric = Lyric.load(f)

        if args.css:
            with open(args.css, encoding='UTF-8') as f:
                self.css = f.read()

        self.note_host = args.note_host

        return self

    def __call__(self):
        s = self.score
        if self.rebase:
            s = self.rebase(self.score)

        d = Drawing(score=s, lyric=self.lyric, style_sheet=self.css, note_host=self.note_host)
        d.svg().saveas(self.output)


if __name__ == '__main__':
    Main.from_args()()
