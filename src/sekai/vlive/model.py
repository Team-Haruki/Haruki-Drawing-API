from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from src.sekai.base.asset_key import AssetKey
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
    banner_path: AssetKey | None = None
    rewards: list[VLiveRewardItem] | None = None
    characters: list[VLiveCharacterItem] | None = None
    # JP 7.0.0: optional grouping. Cloud collapses the per-character lives of one solo virtual
    # live group into ONE brief; every field below is absent for ordinary lives and older regions.
    virtual_live_type: str | None = None
    group_id: int | None = None
    group_name: str | None = None
    group_count: int | None = None

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


class VLiveDetailLive(BaseModel):
    """One member live of a (solo) virtual live group, e.g. one character's solo live."""

    id: int
    name: str | None = None
    character_icon_path: AssetKey | None = None
    current_start_at: datetime | None = None
    current_end_at: datetime | None = None
    living: bool = False
    rest_count: int = 0
    schedule_count: int | None = None

    @field_validator("current_start_at", "current_end_at", mode="before")
    @classmethod
    def parse_timestamp(cls, value):
        return parse_datetime_utc(value)


class VLiveTotalCheerPointReward(BaseModel):
    """``virtualLiveTotalCheerPointRewards``: a reward box unlocked at a total cheer-point threshold."""

    threshold: int
    rewards: list[VLiveRewardItem] = Field(default_factory=list)
    received: bool = False


class VLiveSurplusReward(BaseModel):
    """``virtualLiveTotalCheerPointSurplusReward``: repeated reward every ``base_point`` past the last threshold."""

    base_point: int
    rewards: list[VLiveRewardItem] = Field(default_factory=list)
    received_count: int | None = None


class VLiveOverrideCost(BaseModel):
    """``virtualLiveVirtualItemOverrideCost``: the resource virtual items cost in this live."""

    image_path: AssetKey
    name: str | None = None
    resource_type: str | None = None
    resource_id: int | None = None
    have_quantity: int | None = None


class VLiveDetailRequest(TimeZoneRequest):
    """Detail page of one virtual live or one collapsed solo virtual live group (JP 7.0.0)."""

    region: str
    id: int
    title: str
    virtual_live_type: str | None = None
    banner_path: AssetKey | None = None
    start_at: datetime
    end_at: datetime
    lives: list[VLiveDetailLive] = Field(default_factory=list)
    total_cheer_point: int | None = None
    total_cheer_point_rewards: list[VLiveTotalCheerPointReward] | None = None
    surplus_reward: VLiveSurplusReward | None = None
    override_cost: VLiveOverrideCost | None = None

    @field_validator("start_at", "end_at", mode="before")
    @classmethod
    def parse_timestamp(cls, value):
        return parse_datetime_utc(value)

    def model_post_init(self, __context, /) -> None:
        super().model_post_init(__context)
        self.start_at = localize_datetime(self.start_at, self.timezone)
        self.end_at = localize_datetime(self.end_at, self.timezone)
        for item in self.lives:
            item.current_start_at = localize_datetime(item.current_start_at, self.timezone)
            item.current_end_at = localize_datetime(item.current_end_at, self.timezone)
