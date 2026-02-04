import dataclasses


@dataclasses.dataclass
class Meta:
    title: str | None = None
    subtitle: str | None = None
    artist: str | None = None
    genre: str | None = None
    designer: str | None = None
    difficulty: str | None = None
    playlevel: str | None = None
    songid: str | None = None
    wave: str | None = None
    waveoffset: str | None = None
    jacket: str | None = None
    background: str | None = None
    movie: str | None = None
    movieoffset: float | None = None
    basebpm: float | None = None
    # requests: list = dataclasses.field(default_factory=list)

    def __or__(self, other: "Meta") -> "Meta":
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
