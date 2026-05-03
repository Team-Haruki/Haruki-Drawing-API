from datetime import datetime

from pydantic import BaseModel, field_validator

from src.sekai.base.timezone import TimeZoneRequest, localize_datetime, parse_datetime_utc


class VLiveRewardItem(BaseModel):
    image_path: str
    quantity: int = 1


class VLiveCharacterItem(BaseModel):
    icon_path: str


class VLiveBrief(BaseModel):
    id: int
    name: str
    start_at: datetime
    end_at: datetime
    current_start_at: datetime | None = None
    current_end_at: datetime | None = None
    living: bool = False
    rest_count: int = 0
    banner_path: str | None = None
    rewards: list[VLiveRewardItem] | None = None
    characters: list[VLiveCharacterItem] | None = None

    @field_validator("start_at", "end_at", "current_start_at", "current_end_at", mode="before")
    @classmethod
    def parse_timestamp(cls, value):
        return parse_datetime_utc(value)


class VLiveListRequest(TimeZoneRequest):
    region: str
    lives: list[VLiveBrief]

    def model_post_init(self, __context, /) -> None:
        super().model_post_init(__context)
        for item in self.lives:
            item.start_at = localize_datetime(item.start_at, self.timezone)
            item.end_at = localize_datetime(item.end_at, self.timezone)
            item.current_start_at = localize_datetime(item.current_start_at, self.timezone)
            item.current_end_at = localize_datetime(item.current_end_at, self.timezone)
