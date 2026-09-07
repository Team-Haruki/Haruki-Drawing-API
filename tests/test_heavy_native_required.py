import importlib

import pytest

from src.core.heavy_render_pool import _render_heavy_task
from src.core.image_payload import NativeRenderRequiredError


@pytest.mark.parametrize(
    ("kind", "module", "model"),
    [("deck_recommend", "deck", "DeckRequest"), ("chara_birthday", "misc", "CharaBirthdayRequest")],
)
def test_heavy_worker_never_recovers_with_pillow(monkeypatch, kind, module, model):
    drawer = importlib.import_module(f"src.sekai.{module}.drawer")
    request_type = getattr(importlib.import_module(f"src.sekai.{module}.model"), model)
    monkeypatch.setattr(request_type, "model_validate", lambda _payload: object())

    async def decline(_request):
        return None

    async def legacy_must_not_run(_request):
        raise AssertionError("the heavy worker reentered Pillow")

    monkeypatch.setattr(drawer, f"try_render_{kind}_payload", decline)
    monkeypatch.setattr(drawer, f"compose_{kind}_image", legacy_must_not_run)
    with pytest.raises(NativeRenderRequiredError):
        _render_heavy_task(kind, {})
