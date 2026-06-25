from pydantic import BaseModel

from src.sekai.base.timezone import TimeZoneRequest
from src.sekai.profile.model import DetailedProfileCardRequest


class InventoryItem(BaseModel):
    id: int
    name: str
    description: str = ""
    category: str
    resource_type: str
    icon_path: str
    quantity: int
    seq: int
    recovery_value: int | None = None


class InventorySection(BaseModel):
    key: str
    title: str
    items: list[InventoryItem]


class InventoryListRequest(TimeZoneRequest):
    profile: DetailedProfileCardRequest
    sections: list[InventorySection]
    total_items: int = 0

    def model_post_init(self, __context, /) -> None:
        super().model_post_init(__context)
        self.apply_timezone(self.profile)
