import typing
import dataclasses


@dataclasses.dataclass
class Meta:
    title: typing.Optional[str] = None
    subtitle: typing.Optional[str] = None
    artist: typing.Optional[str] = None
    genre: typing.Optional[str] = None
    designer: typing.Optional[str] = None
    difficulty: typing.Optional[str] = None
    playlevel: typing.Optional[str] = None
    songid: typing.Optional[str] = None
    wave: typing.Optional[str] = None
    waveoffset: typing.Optional[str] = None
    jacket: typing.Optional[str] = None
    background: typing.Optional[str] = None
    movie: typing.Optional[str] = None
    movieoffset: typing.Optional[float] = None
    basebpm: typing.Optional[float] = None
    # requests: list = dataclasses.field(default_factory=list)

    def __or__(self, other: 'Meta') -> 'Meta':
        return Meta(
            title=self.title or other.title,
            subtitle=self.subtitle or other.subtitle,
            artist=self.artist or other.artist,
            genre=self.genre or other.genre,
            designer=self.designer or other.designer,
            difficulty=self.difficulty or other.difficulty,
            playlevel=self.playlevel or other.playlevel,
            songid=self.songid or other.songid,
            wave=self.wave or other.wave,
            waveoffset=self.waveoffset or other.waveoffset,
            jacket=self.jacket or other.jacket,
            background=self.background or other.background,
            movie=self.movie or other.movie,
            movieoffset=self.movieoffset or other.movieoffset,
            basebpm=self.basebpm or other.basebpm,
        )
