from __future__ import annotations

from collections.abc import Mapping

import pytest

from src.sekai.honor.assets import (
    HONOR_ASSET_MANIFEST,
    honor_asset_branch,
    honor_asset_specs,
    resolve_honor_assets,
)
from src.sekai.honor.model import HonorRequest


def _available_resolver(available: Mapping[str, str]):
    def resolve(path: str) -> str | None:
        return available.get(path)

    return resolve


def _source_factory(path: str) -> str:
    return f"source:{path}"


def _spec_map(request: HonorRequest):
    return {spec.image_key: spec for spec in honor_asset_specs(request)}


def test_manifest_is_the_single_request_field_map() -> None:
    assert dict(HONOR_ASSET_MANIFEST) == {
        "honor_img": "honor_img_path",
        "rank_img": "rank_img_path",
        "lv_img": "lv_img_path",
        "lv6_img": "lv6_img_path",
        "empty_honor": "empty_honor_path",
        "scroll_img": "scroll_img_path",
        "word_img": "word_img_path",
        "chara_icon_1": "chara_icon_path",
        "chara_icon_2": "chara_icon_path2",
        "bonds_bg": "bonds_bg_path",
        "bonds_bg2": "bonds_bg_path2",
        "mask_img": "mask_img_path",
        "frame_img": "frame_img_path",
        "frame_degree_level_img": "frame_degree_level_img_path",
    }


def test_branch_specs_only_include_assets_used_by_that_branch() -> None:
    empty = HonorRequest(is_empty=True, empty_honor_path="empty.png", frame_img_path="ignored.png")
    assert honor_asset_branch(empty) == "empty"
    assert tuple(_spec_map(empty)) == ("empty_honor",)
    assert _spec_map(empty)["empty_honor"].required

    normal = HonorRequest(
        honor_type="normal",
        group_type="character",
        honor_img_path="base.png",
        rank_img_path="rank.png",
    )
    normal_specs = _spec_map(normal)
    assert tuple(normal_specs) == ("honor_img", "rank_img", "frame_img", "lv_img", "lv6_img")
    assert normal_specs["honor_img"].required
    assert normal_specs["rank_img"].requirement == "optional"
    assert normal_specs["rank_img"].on_supplied_missing == "ignore"
    assert normal_specs["frame_img"].on_supplied_missing == "hybrid"

    birthday = HonorRequest(
        honor_type="birthday",
        group_type="event",
        honor_img_path="base.png",
        frame_img_path="frame.png",
    )
    assert tuple(_spec_map(birthday)) == (
        "honor_img",
        "rank_img",
        "frame_img",
        "frame_degree_level_img",
        "scroll_img",
    )

    bonds = HonorRequest(
        honor_type="bonds",
        is_main_honor=True,
        chara_icon_path="one.png",
        chara_icon_path2="two.png",
    )
    assert tuple(_spec_map(bonds)) == (
        "bonds_bg",
        "bonds_bg2",
        "chara_icon_1",
        "chara_icon_2",
        "mask_img",
        "frame_img",
        "lv_img",
        "lv6_img",
        "word_img",
    )
    assert _spec_map(bonds)["bonds_bg"].required
    assert _spec_map(bonds)["bonds_bg2"].required

    bonds_sub = HonorRequest(
        honor_type="bonds",
        is_main_honor=False,
        chara_icon_path="one.png",
        chara_icon_path2="two.png",
    )
    assert "word_img" not in _spec_map(bonds_sub)

    bare_bonds = HonorRequest(honor_type="bonds", chara_icon_path="ignored-without-pair.png")
    assert tuple(_spec_map(bare_bonds)) == ("bonds_bg", "bonds_bg2")

    unsupported = HonorRequest(honor_type="future")
    assert honor_asset_branch(unsupported) == "unsupported"
    assert honor_asset_specs(unsupported) == ()


def test_empty_branch_resolves_without_touching_irrelevant_paths() -> None:
    request = HonorRequest(
        is_empty=True,
        empty_honor_path="empty.png",
        honor_img_path="ignored.png",
        frame_img_path="also-ignored.png",
    )
    seen: list[str] = []

    def resolve(path: str) -> str:
        seen.append(path)
        return path

    result = resolve_honor_assets(request, path_resolver=resolve, source_factory=_source_factory)

    assert result.ready
    assert result.branch == "empty"
    assert result.sources == {"empty_honor": "source:empty.png"}
    assert seen == ["empty.png"]


@pytest.mark.parametrize(
    "honor_request",
    [
        HonorRequest(is_empty=True),
        HonorRequest(honor_type="normal"),
        HonorRequest(honor_type="birthday", honor_img_path="missing.png"),
        HonorRequest(honor_type="bonds", bonds_bg_path="left.png"),
    ],
)
def test_missing_required_base_is_unrenderable(honor_request: HonorRequest) -> None:
    result = resolve_honor_assets(
        honor_request,
        path_resolver=_available_resolver({"left.png": "left"}),
        source_factory=_source_factory,
    )

    assert result.status == "unrenderable"
    assert result.sources is None
    assert result.failure is not None
    assert result.failure.image_key in {"empty_honor", "honor_img", "bonds_bg2"}
    assert result.failure.reason in {"path_absent", "path_unresolved"}


def test_rank_is_optional_even_when_a_supplied_path_is_missing() -> None:
    request = HonorRequest(
        honor_type="normal",
        group_type="character",
        honor_img_path="base.png",
        rank_img_path="missing-rank.png",
        lv_img_path="level.png",
    )
    result = resolve_honor_assets(
        request,
        path_resolver=_available_resolver({"base.png": "base", "level.png": "level"}),
        source_factory=_source_factory,
    )

    assert result.ready
    assert result.sources == {
        "honor_img": "source:base",
        "rank_img": None,
        "frame_img": None,
        "lv_img": "source:level",
        "lv6_img": None,
    }


def test_rank_resolver_exception_is_ignored_by_its_optional_policy() -> None:
    request = HonorRequest(
        honor_type="normal",
        honor_img_path="base.png",
        rank_img_path="outside.png",
    )

    def resolve(path: str) -> str:
        if path == "outside.png":
            raise ValueError("outside asset root")
        return path

    result = resolve_honor_assets(request, path_resolver=resolve, source_factory=_source_factory)

    assert result.ready
    assert result.sources == {"honor_img": "source:base.png", "rank_img": None, "frame_img": None}


@pytest.mark.parametrize("field", ["frame_img_path", "scroll_img_path", "frame_degree_level_img_path"])
def test_supplied_overlay_missing_makes_request_hybrid(field: str) -> None:
    kwargs = {
        "honor_type": "birthday",
        "group_type": "event",
        "honor_img_path": "base.png",
        "frame_img_path": "frame.png",
        field: "missing-overlay.png",
    }
    request = HonorRequest.model_validate(kwargs)
    result = resolve_honor_assets(
        request,
        path_resolver=_available_resolver({"base.png": "base", "frame.png": "frame"}),
        source_factory=_source_factory,
    )

    assert result.status == "hybrid"
    assert result.sources is None
    assert result.failure is not None
    assert result.failure.path_field == field
    assert result.failure.raw_path == "missing-overlay.png"


def test_bonds_branch_returns_all_resolved_sources() -> None:
    request = HonorRequest(
        honor_type="bonds",
        is_main_honor=True,
        bonds_bg_path="left.png",
        bonds_bg_path2="right.png",
        chara_icon_path="one.png",
        chara_icon_path2="two.png",
        mask_img_path="mask.png",
        word_img_path="word.png",
    )
    available = {
        path: path.removesuffix(".png")
        for path in ("left.png", "right.png", "one.png", "two.png", "mask.png", "word.png")
    }
    result = resolve_honor_assets(
        request,
        path_resolver=_available_resolver(available),
        source_factory=_source_factory,
    )

    assert result.ready
    assert result.sources == {
        "bonds_bg": "source:left",
        "bonds_bg2": "source:right",
        "chara_icon_1": "source:one",
        "chara_icon_2": "source:two",
        "mask_img": "source:mask",
        "frame_img": None,
        "lv_img": None,
        "lv6_img": None,
        "word_img": "source:word",
    }


def test_source_factory_failure_uses_the_same_missing_policy() -> None:
    request = HonorRequest(
        honor_type="normal",
        honor_img_path="base.png",
        frame_img_path="frame.png",
    )

    def unavailable_source(path: str) -> str:
        if path == "frame":
            raise ValueError("corrupt")
        return f"source:{path}"

    result = resolve_honor_assets(
        request,
        path_resolver=_available_resolver({"base.png": "base", "frame.png": "frame"}),
        source_factory=unavailable_source,
    )

    assert result.status == "hybrid"
    assert result.sources is None
    assert result.failure is not None
    assert result.failure.reason == "source_unavailable"
    assert result.failure.detail == "ValueError: corrupt"


def test_none_from_source_factory_is_classified_as_hybrid() -> None:
    request = HonorRequest(honor_type="normal", honor_img_path="base.png")
    result = resolve_honor_assets(
        request,
        path_resolver=lambda path: path,
        source_factory=lambda _path: None,
    )

    assert result.status == "hybrid"
    assert result.sources is None
    assert result.failure is not None
    assert result.failure.reason == "source_unavailable"
    assert result.failure.raw_path == "base.png"


def test_supplied_required_base_rejected_by_backend_is_hybrid_not_fallthrough() -> None:
    request = HonorRequest(honor_type="normal", honor_img_path="outside.png")

    def rejected_path(_path: str) -> str:
        raise ValueError("outside asset root")

    result = resolve_honor_assets(
        request,
        path_resolver=rejected_path,
        source_factory=_source_factory,
    )

    assert result.status == "hybrid"
    assert result.sources is None
    assert result.failure is not None
    assert result.failure.image_key == "honor_img"
    assert result.failure.reason == "path_unresolved"
    assert result.failure.detail == "ValueError: outside asset root"


def test_unknown_honor_type_is_unrenderable_without_callbacks() -> None:
    request = HonorRequest(honor_type="future")

    def unexpected_path(_path: str) -> str:  # pragma: no cover - regression guard
        raise AssertionError("unsupported branch must not resolve paths")

    result = resolve_honor_assets(
        request,
        path_resolver=unexpected_path,
        source_factory=_source_factory,
    )

    assert result.status == "unrenderable"
    assert result.branch == "unsupported"
    assert result.sources is None
    assert result.failure is not None
    assert result.failure.reason == "unsupported_branch"
