from pydantic import BaseModel, Field

from src.sekai.base.asset_key import AssetKey
from src.sekai.base.timezone import TimeZoneRequest

RESOURCE_NAME_MAX_LENGTH = 128


class MysekaiConvertResource(BaseModel):
    name: str = Field(default="", max_length=RESOURCE_NAME_MAX_LENGTH)
    image_path: AssetKey | None = None
    quantity: int | None = None
    resource_type: str = ""
    id: int = 0


class MysekaiConvertRecipe(BaseModel):
    id: int
    name: str = ""
    category: str = ""
    category_key: str = ""
    inputs: list[MysekaiConvertResource] = Field(default_factory=list)
    outputs: list[MysekaiConvertResource] = Field(default_factory=list)
    duration: str = ""
    required_level: int = 0
    limits: str = ""


class MysekaiConvertRecipesRequest(TimeZoneRequest):
    title: str = "烤森合成表"
    total: int | None = Field(default=None, ge=0)
    filter: str = ""
    recipes: list[MysekaiConvertRecipe] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
