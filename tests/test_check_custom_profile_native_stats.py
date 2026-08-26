from __future__ import annotations

from scripts.check_custom_profile_native_stats import validate_custom_profile_stats


def _stats() -> dict:
    return {
        "http_requests": {"server_errors": {"total": 0}},
        "renders": {
            "endpoints": {
                "custom_profile_card": {
                    "skia": 3,
                    "cache_hit": 0,
                    "fallback": 0,
                    "disabled": 0,
                    "error": 0,
                    "total": 3,
                    "native_pure": 3,
                    "native_hybrid": 0,
                    "native_unclassified": 0,
                    "scene_completeness": {
                        "checked": 3,
                        "complete": 3,
                        "incomplete": 0,
                        "visible_elements": 8,
                        "native_elements": 7,
                        "noop_elements": 1,
                        "hybrid_elements": 0,
                        "missing_elements": 0,
                        "unresolved_elements": 0,
                        "mem_images": 0,
                        "mem_bytes": 0,
                        "classifications_by_kind": {
                            "general": {"native": 4},
                            "text": {"native": 3, "noop": 1},
                        },
                    },
                }
            }
        },
    }


def test_custom_profile_native_stats_accept_strict_pure_aggregate() -> None:
    summary, failures = validate_custom_profile_stats(
        _stats(),
        min_requests=3,
        required_kinds={"general", "text"},
    )

    assert failures == []
    assert summary == {
        "total": 3,
        "skia": 3,
        "cache_hit": 0,
        "native_pure": 3,
        "scene_complete": 3,
        "native_elements": 7,
        "noop_elements": 1,
        "native_categories": 2,
    }


def test_custom_profile_native_stats_rejects_outcome_purity_and_scene_drift() -> None:
    document = _stats()
    endpoint = document["renders"]["endpoints"]["custom_profile_card"]
    endpoint.update({"skia": 2, "fallback": 1, "native_pure": 1, "native_hybrid": 1})
    scene = endpoint["scene_completeness"]
    scene.update({"complete": 2, "incomplete": 1, "hybrid_elements": 1, "mem_images": 1})
    scene["classifications_by_kind"]["text"]["hybrid"] = 1

    _, failures = validate_custom_profile_stats(document, min_requests=3, required_kinds={"stamp"})

    assert "fallback=1" in failures
    assert "native_hybrid=1" in failures
    assert "native_pure=1 does not equal skia+cache_hit=2" in failures
    assert "scene complete=2, checked=3, incomplete=1" in failures
    assert "scene hybrid_elements=1" in failures
    assert "scene mem_images=1" in failures
    assert "category text hybrid=1" in failures
    assert "required native categories not observed: stamp" in failures


def test_custom_profile_native_stats_requires_category_classifications() -> None:
    document = _stats()["renders"]
    del document["endpoints"]["custom_profile_card"]["scene_completeness"]["classifications_by_kind"]

    _, failures = validate_custom_profile_stats(document, min_requests=1)

    assert failures == ["category classifications are missing"]


def test_custom_profile_native_stats_accepts_zero_aggregate_http_5xx() -> None:
    summary, failures = validate_custom_profile_stats(
        _stats(),
        min_requests=3,
        required_kinds={"general", "text"},
        require_zero_http_5xx=True,
    )

    assert failures == []
    assert summary["http_5xx"] == 0


def test_custom_profile_native_stats_rejects_aggregate_http_5xx() -> None:
    document = _stats()
    document["http_requests"]["server_errors"]["total"] = 1

    summary, failures = validate_custom_profile_stats(
        document,
        min_requests=3,
        require_zero_http_5xx=True,
    )

    assert summary["http_5xx"] == 1
    assert "http_5xx=1" in failures
