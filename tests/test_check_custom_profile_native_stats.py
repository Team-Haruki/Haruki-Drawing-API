from __future__ import annotations

from io import StringIO
import json

import pytest

from scripts.check_custom_profile_native_stats import _load_document, validate_custom_profile_stats


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


def test_custom_profile_native_stats_reports_sanitized_error_stages() -> None:
    document = _stats()
    endpoint = document["renders"]["endpoints"]["custom_profile_card"]
    endpoint.update(
        {
            "skia": 2,
            "error": 1,
            "native_pure": 2,
            "errors_by_stage": {"native_render": 1},
        }
    )
    endpoint["scene_completeness"].update({"checked": 2, "complete": 2})

    _, failures = validate_custom_profile_stats(document, min_requests=3)

    assert "error=1" in failures
    assert "error stage native_render=1" in failures
    assert not any("diagnostics are missing" in failure for failure in failures)


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


def test_custom_profile_native_stats_rejects_missing_http_5xx_total() -> None:
    document = _stats()
    del document["http_requests"]["server_errors"]["total"]

    summary, failures = validate_custom_profile_stats(
        document,
        min_requests=3,
        require_zero_http_5xx=True,
    )

    assert "http_5xx" not in summary
    assert "aggregate HTTP server-error total is missing" in failures


def test_custom_profile_native_stats_checks_http_5xx_before_first_render() -> None:
    document = {
        "http_requests": {"server_errors": {"total": 0}},
        "renders": {"endpoints": {}},
    }

    summary, failures = validate_custom_profile_stats(
        document,
        min_requests=1,
        require_zero_http_5xx=True,
    )

    assert summary == {
        "total": 0,
        "skia": 0,
        "cache_hit": 0,
        "native_pure": 0,
        "scene_complete": 0,
        "native_elements": 0,
        "noop_elements": 0,
        "native_categories": 0,
        "http_5xx": 0,
    }
    assert failures == ["total requests 0 is below required 1"]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (True, "total must be an integer"),
        ("invalid", "total must be an integer"),
        (-1, "total must not be negative"),
    ],
)
def test_custom_profile_native_stats_rejects_invalid_integer_counts(value: object, message: str) -> None:
    document = _stats()
    document["renders"]["endpoints"]["custom_profile_card"]["total"] = value

    with pytest.raises(ValueError, match=message):
        validate_custom_profile_stats(document, min_requests=1)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"renders": []}, "render statistics root must be an object"),
        ({"renders": {}}, "render statistics do not contain endpoints"),
        (
            {"renders": {"endpoints": {"custom_profile_card": []}}},
            "render statistics do not contain custom_profile_card",
        ),
    ],
)
def test_custom_profile_native_stats_rejects_malformed_endpoint_documents(document: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_custom_profile_stats(document, min_requests=1)


@pytest.mark.parametrize(
    ("diagnostics", "expected"),
    [
        (None, "error stage diagnostics are missing"),
        ([], "error stage diagnostics are malformed"),
        ({"unexpected": 1}, "error stage diagnostics contain an unknown category"),
        ({"native_render": 0}, "error stage total=0 does not equal error=1"),
    ],
)
def test_custom_profile_native_stats_rejects_invalid_error_diagnostics(diagnostics: object, expected: str) -> None:
    document = _stats()
    endpoint = document["renders"]["endpoints"]["custom_profile_card"]
    endpoint.update({"skia": 2, "error": 1, "native_pure": 2})
    endpoint["scene_completeness"].update({"checked": 2, "complete": 2})
    if diagnostics is not None:
        endpoint["errors_by_stage"] = diagnostics

    _, failures = validate_custom_profile_stats(document, min_requests=3)

    assert expected in failures


def test_custom_profile_native_stats_rejects_missing_or_inconsistent_scene_counts() -> None:
    document = _stats()
    del document["renders"]["endpoints"]["custom_profile_card"]["scene_completeness"]

    _, failures = validate_custom_profile_stats(document, min_requests=3)

    assert "scene completeness is missing" in failures
    assert "scene checked=0 does not equal total=3" in failures
    assert "category classifications are missing" in failures

    document = _stats()
    document["renders"]["endpoints"]["custom_profile_card"]["scene_completeness"]["visible_elements"] = 9

    _, failures = validate_custom_profile_stats(document, min_requests=3)

    assert "visible_elements=9 does not equal native+noop=8" in failures


def test_custom_profile_native_stats_rejects_malformed_and_unsafe_classifications() -> None:
    document = _stats()
    classifications = document["renders"]["endpoints"]["custom_profile_card"]["scene_completeness"][
        "classifications_by_kind"
    ]
    classifications[""] = {"native": 1}
    classifications["shape"] = []
    classifications["general"].update({"missing": 1, "unresolved": 2})

    _, failures = validate_custom_profile_stats(document, min_requests=3)

    assert failures.count("category classifications are malformed") == 2
    assert "category general missing=1" in failures
    assert "category general unresolved=2" in failures


@pytest.mark.parametrize(
    ("http_requests", "expected"),
    [
        (None, "aggregate HTTP request statistics are missing"),
        ({}, "aggregate HTTP server-error statistics are missing"),
    ],
)
def test_custom_profile_native_stats_rejects_missing_http_aggregates(http_requests: object, expected: str) -> None:
    document = _stats()
    if http_requests is None:
        del document["http_requests"]
    else:
        document["http_requests"] = http_requests

    _, failures = validate_custom_profile_stats(document, min_requests=3, require_zero_http_5xx=True)

    assert expected in failures


def test_load_document_supports_stdin_and_safe_files(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    document = _stats()
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(document)))
    assert _load_document("-") == document

    source = tmp_path / "stats.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    assert _load_document(str(source)) == document


def test_load_document_rejects_non_object_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", StringIO("[]"))

    with pytest.raises(ValueError, match="render statistics document must be an object"):
        _load_document("-")
