from pydantic import BaseModel, Field

from src.sekai.base.timezone import TimeZoneRequest


class CostumeColorVariant(BaseModel):
    costume_id: int
    color_id: int
    color_name: str = ""
    asset_bundle_name: str = ""
    thumbnail_path: str = ""
    preview_image_path: str | None = None
    source_card_ids: list[int] = Field(default_factory=list)


class CostumeBasic(BaseModel):
    costume_id: int
    costume_group_id: int
    name: str
    part_type: str
    part_name: str = ""
    costume_3d_type: str = ""
    character_id: int
    character_name: str
    character_gender: str | None = None
    rarity: str | None = None
    how_to_obtain: str | None = None
    designer: str | None = None
    asset_bundle_name: str | None = None
    color_id: int | None = None
    color_name: str | None = None
    published_at: int | None = None
    archive_published_at: int | None = None
    thumbnail_path: str
    preview_image_path: str | None = None
    source_card_ids: list[int] = Field(default_factory=list)
    variants: list[CostumeColorVariant] = Field(default_factory=list)


class CostumeListRequest(TimeZoneRequest):
    region: str
    title: str | None = None
    page: int = 1
    page_size: int = 240
    total: int = 0
    total_pages: int = 1
    filter_label: str = ""
    costumes: list[CostumeBasic] = Field(default_factory=list)


class CostumeDetailRequest(TimeZoneRequest):
    region: str
    costume: CostumeBasic
    character_icon_path: str | None = None
    unit_logo_path: str | None = None
