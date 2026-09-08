from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace
from typing import Any, Self

from PIL import Image
import pytest

from src.core import debug, heavy_render_pool as pool_mod
from src.core.heavy_render_pool import (
    HeavyRenderQueueFullError,
    HeavyRenderQueueTimeoutError,
    HeavyRenderTaskExecutionError,
    HeavyRenderTaskTimeoutError,
    HeavyRenderWorkerPool,
    _WorkerResult,
    _WorkerSlot,
    _WorkerTask,
)
from src.core.image_payload import EncodedImagePayload
from src.sekai.skia_renderer import render_stats


class FakeProcess:
    def __init__(self, *, alive: bool = True, stop_on_join: bool = True, pid: int = 42) -> None:
        self.alive = alive
        self.stop_on_join = stop_on_join
        self.pid = pid
        self.join_calls: list[float] = []
        self.kill_calls = 0

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float) -> None:
        self.join_calls.append(timeout)
        if self.stop_on_join:
            self.alive = False

    def kill(self) -> None:
        self.kill_calls += 1
        self.alive = False


class LockedValue:
    def __init__(self, value: float) -> None:
        self.value = value

    def get_lock(self) -> LockedValue:
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class FakeCondition:
    def __init__(self, on_wait: Any = None) -> None:
        self.on_wait = on_wait
        self.wait_calls: list[float | None] = []
        self.notify_calls = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def wait(self, timeout: float | None = None) -> None:
        self.wait_calls.append(timeout)
        if self.on_wait is not None:
            self.on_wait()

    def notify(self) -> None:
        self.notify_calls += 1

    def notify_all(self) -> None:
        self.notify_calls += 1


class FakeQueue:
    def __init__(self, values: list[Any] | None = None, *, fail_put_nowait: bool = False) -> None:
        self.values = deque(values or [])
        self.put_values: list[Any] = []
        self.fail_put_nowait = fail_put_nowait

    def get(self, timeout: float | None = None) -> Any:
        del timeout
        return self.values.popleft()

    def put(self, value: Any) -> None:
        self.put_values.append(value)

    def put_nowait(self, value: Any) -> None:
        if self.fail_put_nowait:
            raise RuntimeError("queue closed")
        self.put_values.append(value)


class StopAfterOneHeartbeat:
    def __init__(self) -> None:
        self.calls = 0

    def wait(self, _interval: float) -> bool:
        self.calls += 1
        return self.calls > 1


def make_pool(**overrides: Any) -> HeavyRenderWorkerPool:
    values = {
        "worker_count": 1,
        "queue_limit": 1,
        "queue_timeout_seconds": 0.1,
        "task_timeout_seconds": 1.0,
        "heartbeat_timeout_seconds": 1.0,
        "result_poll_interval_seconds": 0.1,
    }
    values.update(overrides)
    return HeavyRenderWorkerPool(**values)


def make_task(task_id: str = "task-1") -> _WorkerTask:
    return _WorkerTask(task_id, "deck_recommend", {}, "request-1", "/deck", "POST")


def make_payload(*, backend: str | None = "skia") -> EncodedImagePayload:
    return EncodedImagePayload(b"png", "image/png", "image.png", 1, 1, "RGBA", 0.01, backend=backend)


def test_pool_configuration_is_clamped() -> None:
    pool = HeavyRenderWorkerPool(
        worker_count=0,
        queue_limit=-1,
        queue_timeout_seconds=0,
        task_timeout_seconds=0,
        heartbeat_timeout_seconds=0,
        result_poll_interval_seconds=0,
    )

    assert pool._worker_count == 1
    assert pool._queue_limit == 0
    assert pool._queue_timeout_seconds == 0.1
    assert pool._task_timeout_seconds == 1.0
    assert pool._heartbeat_timeout_seconds == 1.0
    assert pool._result_poll_interval_seconds == 0.1


def test_pool_start_and_shutdown_are_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = make_pool(worker_count=2)
    spawned: list[tuple[str, str]] = []
    stopped: list[tuple[str, str]] = []
    monkeypatch.setattr(pool, "_spawn_worker", lambda slot, *, reason: spawned.append((slot.name, reason)))
    monkeypatch.setattr(pool, "_stop_worker", lambda slot, *, reason: stopped.append((slot.name, reason)))

    asyncio.run(pool.start())
    asyncio.run(pool.start())
    assert pool._started is True
    assert spawned == [("heavy-render-1", "startup"), ("heavy-render-2", "startup")]

    for slot in pool._slots:
        slot.busy = True
        slot.current_task_id = "task"
        slot.current_task_kind = "deck_recommend"
        slot.current_task_started_at = 1.0
    asyncio.run(pool.shutdown())
    asyncio.run(pool.shutdown())
    assert pool._started is False
    assert stopped == [("heavy-render-1", "shutdown"), ("heavy-render-2", "shutdown")]
    assert all(not slot.busy and slot.current_task_id is None for slot in pool._slots)


def test_async_slot_wrappers_delegate_to_sync_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = make_pool()
    slot = pool._slots[0]
    released: list[_WorkerSlot] = []
    monkeypatch.setattr(pool, "_acquire_slot_sync", lambda _kind, _ctx: slot)
    monkeypatch.setattr(pool, "_release_slot_sync", released.append)

    assert asyncio.run(pool._acquire_slot("deck_recommend", {})) is slot
    asyncio.run(pool._release_slot(slot))
    assert released == [slot]


def test_acquire_claims_live_slot_and_revives_dead_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = make_pool(worker_count=2)
    pool._slots[0].busy = True
    pool._slots[1].process = FakeProcess(alive=False)
    spawned: list[str] = []

    def spawn(slot: _WorkerSlot, *, reason: str) -> None:
        spawned.append(reason)
        slot.process = FakeProcess()

    monkeypatch.setattr(pool, "_spawn_worker", spawn)
    slot = pool._acquire_slot_sync("deck_recommend", {"request_id": "r", "path": "/deck"})

    assert slot is pool._slots[1]
    assert slot.busy is True
    assert spawned == ["slot-revive-before-acquire"]


def test_acquire_rejects_full_queue() -> None:
    pool = make_pool(queue_limit=0)
    pool._slots[0].busy = True

    with pytest.raises(HeavyRenderQueueFullError, match="queue is full"):
        pool._acquire_slot_sync("deck_recommend", {})
    assert pool._pending_waiters == 0


def test_acquire_times_out_and_removes_pending_waiter(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = make_pool(queue_timeout_seconds=0.1)
    pool._slots[0].busy = True
    pool._condition = FakeCondition()
    times = iter((0.0, 1.0))
    monkeypatch.setattr(pool_mod.time, "monotonic", lambda: next(times))

    with pytest.raises(HeavyRenderQueueTimeoutError, match="queue timeout"):
        pool._acquire_slot_sync("deck_recommend", {})
    assert pool._pending_waiters == 0


def test_acquire_waits_until_a_slot_is_released(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = make_pool(queue_timeout_seconds=1.0)
    slot = pool._slots[0]
    slot.busy = True
    slot.process = FakeProcess()
    pool._condition = FakeCondition(on_wait=lambda: setattr(slot, "busy", False))
    times = iter((0.0, 0.1, 0.2))
    monkeypatch.setattr(pool_mod.time, "monotonic", lambda: next(times))

    assert pool._acquire_slot_sync("deck_recommend", {}) is slot
    assert pool._pending_waiters == 0
    assert pool._condition.wait_calls == [pytest.approx(0.9)]


def test_release_slot_clears_task_state() -> None:
    pool = make_pool()
    pool._condition = FakeCondition()
    slot = pool._slots[0]
    slot.busy = True
    slot.current_task_id = "task"
    slot.current_task_kind = "deck_recommend"
    slot.current_task_started_at = 1.0

    pool._release_slot_sync(slot)

    assert (slot.busy, slot.current_task_id, slot.current_task_kind, slot.current_task_started_at) == (
        False,
        None,
        None,
        None,
    )
    assert pool._condition.notify_calls == 1


def test_result_wait_state_replaces_dead_or_timed_out_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = make_pool()
    slot = pool._slots[0]
    task = make_task()
    reasons: list[str] = []
    monkeypatch.setattr(pool, "_spawn_worker", lambda _slot, *, reason: reasons.append(reason))

    slot.process = FakeProcess(alive=False)
    with pytest.raises(HeavyRenderTaskExecutionError, match="exited unexpectedly"):
        pool._check_result_wait_state(slot, task, deadline=10.0, now=0.0)
    assert reasons[-1].startswith("worker-died")

    slot.process = FakeProcess()
    with pytest.raises(HeavyRenderTaskTimeoutError, match="task timeout"):
        pool._check_result_wait_state(slot, task, deadline=1.0, now=1.0)
    assert reasons[-1].startswith("task-timeout")


def test_result_wait_state_checks_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = make_pool(heartbeat_timeout_seconds=2.0)
    slot = pool._slots[0]
    slot.process = FakeProcess()
    task = make_task()
    reasons: list[str] = []
    monkeypatch.setattr(pool, "_spawn_worker", lambda _slot, *, reason: reasons.append(reason))

    monkeypatch.setattr(pool, "_heartbeat_age", lambda _slot, _now: None)
    pool._check_result_wait_state(slot, task, deadline=10.0, now=0.0)
    monkeypatch.setattr(pool, "_heartbeat_age", lambda _slot, _now: 1.0)
    pool._check_result_wait_state(slot, task, deadline=10.0, now=0.0)
    monkeypatch.setattr(pool, "_heartbeat_age", lambda _slot, _now: 2.0)
    with pytest.raises(HeavyRenderTaskTimeoutError, match="heartbeat timeout"):
        pool._check_result_wait_state(slot, task, deadline=10.0, now=0.0)
    assert reasons[-1].startswith("heartbeat-timeout")


def test_worker_result_acceptance_handles_stale_failure_and_success() -> None:
    pool = make_pool()
    slot = pool._slots[0]
    slot.process = FakeProcess()
    slot.current_task_started_at = 1.0
    task = make_task()

    assert pool._accept_worker_result(slot, task, _WorkerResult("old", True, make_payload()), 2.0) is None
    with pytest.raises(HeavyRenderTaskExecutionError, match="worker failed"):
        pool._accept_worker_result(
            slot,
            task,
            _WorkerResult(task.task_id, False, error="worker failed", traceback_text="trace"),
            2.0,
        )
    with pytest.raises(HeavyRenderTaskExecutionError, match="heavy render task failed"):
        pool._accept_worker_result(slot, task, _WorkerResult(task.task_id, True), 2.0)

    payload = make_payload()
    assert pool._accept_worker_result(slot, task, _WorkerResult(task.task_id, True, payload), 2.0) is payload


def test_wait_for_result_retries_empty_and_stale_results(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = make_pool()
    slot = pool._slots[0]
    slot.process = FakeProcess()
    task = make_task()
    payload = make_payload()
    results: deque[Any] = deque(
        [
            pool_mod.queue.Empty(),
            _WorkerResult("stale", True, payload),
            _WorkerResult(task.task_id, True, payload),
        ]
    )

    def get_result(_slot: _WorkerSlot, _timeout: float) -> _WorkerResult:
        value = results.popleft()
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(pool, "_get_result", get_result)
    assert asyncio.run(pool._wait_for_result(slot, task)) is payload
    assert not results


def test_task_and_result_queue_helpers() -> None:
    pool = make_pool()
    slot = pool._slots[0]
    task = make_task()

    with pytest.raises(RuntimeError, match="worker queue not initialized"):
        pool._put_task(slot, task)
    with pytest.raises(RuntimeError, match="worker result queue not initialized"):
        pool._get_result(slot, 0.1)

    slot.task_queue = FakeQueue()
    slot.result_queue = FakeQueue([_WorkerResult(task.task_id, True, make_payload())])
    slot.heartbeat_at = LockedValue(0.0)
    pool._put_task(slot, task)
    assert slot.task_queue.put_values == [task]
    assert slot.heartbeat_at.value > 0
    assert pool._get_result(slot, 0.1).task_id == task.task_id


def test_heartbeat_age_handles_missing_and_future_values() -> None:
    pool = make_pool()
    slot = pool._slots[0]
    assert pool._heartbeat_age(slot, 10.0) is None
    slot.heartbeat_at = LockedValue(7.5)
    assert pool._heartbeat_age(slot, 10.0) == 2.5
    slot.heartbeat_at.value = 12.0
    assert pool._heartbeat_age(slot, 10.0) == 0.0


def test_heartbeat_loop_updates_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    heartbeat = LockedValue(0.0)
    monkeypatch.setattr(pool_mod.time, "monotonic", lambda: 12.5)

    pool_mod._heartbeat_loop(
        stop_event=StopAfterOneHeartbeat(),
        heartbeat_at=heartbeat,
        interval_seconds=0.01,
    )

    assert heartbeat.value == 12.5


def test_worker_main_handles_unknown_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    task = make_task()
    heartbeat = LockedValue(0.0)

    unknown_results = FakeQueue()
    pool_mod._heavy_render_worker_main("worker", FakeQueue([object(), None]), unknown_results, heartbeat)
    assert unknown_results.put_values == []

    payload = make_payload()
    monkeypatch.setattr(pool_mod, "_render_heavy_task", lambda _kind, _payload: payload)
    success_results = FakeQueue()
    pool_mod._heavy_render_worker_main("worker", FakeQueue([task, None]), success_results, heartbeat)
    assert success_results.put_values == [_WorkerResult(task.task_id, True, payload)]

    monkeypatch.setattr(
        pool_mod,
        "_render_heavy_task",
        lambda _kind, _payload: (_ for _ in ()).throw(ValueError("render failed")),
    )
    failed_results = FakeQueue()
    pool_mod._heavy_render_worker_main("worker", FakeQueue([task, None]), failed_results, heartbeat)
    result = failed_results.put_values[0]
    assert result.ok is False
    assert result.error == "ValueError: render failed"
    assert "ValueError: render failed" in (result.traceback_text or "")


def test_render_heavy_deck_uses_skia_or_pillow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.sekai.deck import drawer, model

    request = object()
    skia_payload = make_payload()
    pillow_payload = make_payload(backend="pillow")
    monkeypatch.setattr(model.DeckRequest, "model_validate", classmethod(lambda _cls, _payload: request))
    monkeypatch.setattr(pool_mod, "_stamp_skia_backend", lambda payload: payload)

    async def render_skia(_request: object) -> EncodedImagePayload:
        return skia_payload

    monkeypatch.setattr(drawer, "try_render_deck_recommend_payload", render_skia)
    assert pool_mod._render_heavy_task("deck_recommend", {}) is skia_payload

    async def no_skia(_request: object) -> None:
        return None

    async def compose(_request: object) -> object:
        return object()

    monkeypatch.setattr(drawer, "try_render_deck_recommend_payload", no_skia)
    monkeypatch.setattr(drawer, "compose_deck_recommend_image", compose)
    monkeypatch.setattr(pool_mod, "_encode_image_payload", lambda _image: pillow_payload)
    assert pool_mod._render_heavy_task("deck_recommend", {}) is pillow_payload


def test_render_heavy_birthday_uses_skia_or_pillow_and_rejects_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.sekai.misc import drawer, model

    request = object()
    skia_payload = make_payload()
    pillow_payload = make_payload(backend="pillow")
    monkeypatch.setattr(model.CharaBirthdayRequest, "model_validate", classmethod(lambda _cls, _payload: request))
    monkeypatch.setattr(pool_mod, "_stamp_skia_backend", lambda payload: payload)

    async def render_skia(_request: object) -> EncodedImagePayload:
        return skia_payload

    monkeypatch.setattr(drawer, "try_render_chara_birthday_payload", render_skia)
    assert pool_mod._render_heavy_task("chara_birthday", {}) is skia_payload

    async def no_skia(_request: object) -> None:
        return None

    async def compose(_request: object) -> object:
        return object()

    monkeypatch.setattr(drawer, "try_render_chara_birthday_payload", no_skia)
    monkeypatch.setattr(drawer, "compose_chara_birthday_image", compose)
    monkeypatch.setattr(pool_mod, "_encode_image_payload", lambda _image: pillow_payload)
    assert pool_mod._render_heavy_task("chara_birthday", {}) is pillow_payload

    with pytest.raises(ValueError, match="unsupported heavy render task kind"):
        pool_mod._render_heavy_task("unknown", {})  # type: ignore[arg-type]


def test_render_records_backend_and_releases_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = make_pool()
    slot = pool._slots[0]
    payload = make_payload(backend="skia")
    released: list[_WorkerSlot] = []
    backends: list[str | None] = []

    async def acquire(_kind: str, _ctx: dict[str, str]) -> _WorkerSlot:
        return slot

    async def wait(_slot: _WorkerSlot, _task: _WorkerTask) -> EncodedImagePayload:
        return payload

    async def release(released_slot: _WorkerSlot) -> None:
        released.append(released_slot)

    monkeypatch.setattr(debug, "current_request_context", lambda: {"request_id": "r", "path": "/", "method": "POST"})
    monkeypatch.setattr(pool, "_acquire_slot", acquire)
    monkeypatch.setattr(pool, "_put_task", lambda _slot, _task: None)
    monkeypatch.setattr(pool, "_wait_for_result", wait)
    monkeypatch.setattr(pool, "_release_slot", release)
    monkeypatch.setattr(render_stats, "record_worker_payload_backend", lambda *_args: "skia")
    monkeypatch.setattr(debug, "set_render_backend", backends.append)

    assert asyncio.run(pool.render("deck_recommend", {"value": 1})) is payload
    assert released == [slot]
    assert backends == ["skia"]
    assert slot.current_task_kind == "deck_recommend"


@pytest.mark.parametrize("cancelled", [False, True])
def test_render_recycles_worker_after_post_submit_failure(
    cancelled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = make_pool()
    slot = pool._slots[0]
    slot.process = FakeProcess()
    spawned: list[str] = []
    released: list[_WorkerSlot] = []

    async def acquire(_kind: str, _ctx: dict[str, str]) -> _WorkerSlot:
        return slot

    async def wait(_slot: _WorkerSlot, _task: _WorkerTask) -> EncodedImagePayload:
        if cancelled:
            raise asyncio.CancelledError
        raise RuntimeError("parent failure")

    async def release(released_slot: _WorkerSlot) -> None:
        released.append(released_slot)

    monkeypatch.setattr(debug, "current_request_context", lambda: {"request_id": "r", "path": "/", "method": "POST"})
    monkeypatch.setattr(pool, "_acquire_slot", acquire)
    monkeypatch.setattr(pool, "_put_task", lambda _slot, _task: None)
    monkeypatch.setattr(pool, "_wait_for_result", wait)
    monkeypatch.setattr(pool, "_release_slot", release)
    monkeypatch.setattr(pool, "_spawn_worker", lambda _slot, *, reason: spawned.append(reason))

    error = asyncio.CancelledError if cancelled else RuntimeError
    with pytest.raises(error):
        asyncio.run(pool.render("deck_recommend", {}))
    assert len(spawned) == 1
    assert released == [slot]


def test_render_does_not_recycle_before_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = make_pool()
    slot = pool._slots[0]
    spawned: list[str] = []

    async def acquire(_kind: str, _ctx: dict[str, str]) -> _WorkerSlot:
        return slot

    async def release(_slot: _WorkerSlot) -> None:
        return None

    monkeypatch.setattr(debug, "current_request_context", lambda: {"request_id": "r", "path": "/", "method": "POST"})
    monkeypatch.setattr(pool, "_acquire_slot", acquire)
    monkeypatch.setattr(pool, "_put_task", lambda _slot, _task: (_ for _ in ()).throw(RuntimeError("put failed")))
    monkeypatch.setattr(pool, "_release_slot", release)
    monkeypatch.setattr(pool, "_spawn_worker", lambda _slot, *, reason: spawned.append(reason))

    with pytest.raises(RuntimeError, match="put failed"):
        asyncio.run(pool.render("deck_recommend", {}))
    assert spawned == []


@pytest.mark.parametrize(
    ("format_name", "mode", "media_type", "filename"),
    [("png", "RGBA", "image/png", "image.png"), ("jpg", "RGBA", "image/jpeg", "image.jpg")],
)
def test_encode_image_payload(
    format_name: str,
    mode: str,
    media_type: str,
    filename: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pool_mod, "EXPORT_IMAGE_FORMAT", format_name)
    image = Image.new(mode, (2, 3), (255, 0, 0, 128))

    payload = pool_mod._encode_image_payload(image)

    assert payload.image_bytes
    assert (payload.media_type, payload.filename) == (media_type, filename)
    assert (payload.image_width, payload.image_height, payload.image_mode) == (2, 3, mode)


def test_stamp_skia_backend_uses_scoped_pillow_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core import pillow_telemetry

    payload = make_payload(backend=None)
    monkeypatch.setattr(
        pillow_telemetry,
        "get_last_pillow_touch_snapshot",
        lambda: SimpleNamespace(scoped=True, counts={"decode": 2}),
    )
    assert pool_mod._stamp_skia_backend(payload) is payload
    assert payload.backend == render_stats.BACKEND_SKIA
    assert payload.pillow_touch_counts == {"decode": 2}

    payload = make_payload(backend="skia_cache")
    monkeypatch.setattr(
        pillow_telemetry,
        "get_last_pillow_touch_snapshot",
        lambda: SimpleNamespace(scoped=False, counts={}),
    )
    pool_mod._stamp_skia_backend(payload)
    assert payload.backend == "skia_cache"
    assert payload.pillow_touch_counts is None


def test_stop_worker_handles_empty_graceful_and_forced_slots() -> None:
    pool = make_pool()
    empty = _WorkerSlot(0, "empty", task_queue=FakeQueue(), result_queue=FakeQueue(), heartbeat_at=LockedValue(0))
    pool._stop_worker(empty, reason="empty")
    assert (empty.task_queue, empty.result_queue, empty.heartbeat_at) == (None, None, None)

    graceful_process = FakeProcess()
    graceful_queue = FakeQueue()
    graceful = _WorkerSlot(1, "graceful", task_queue=graceful_queue, process=graceful_process)
    pool._stop_worker(graceful, reason="shutdown")
    assert graceful_queue.put_values == [None]
    assert graceful_process.kill_calls == 0

    forced_process = FakeProcess(stop_on_join=False)
    forced = _WorkerSlot(2, "forced", task_queue=FakeQueue(fail_put_nowait=True), process=forced_process)
    pool._stop_worker(forced, reason="replace")
    assert forced_process.kill_calls == 1
    assert forced.process is None

    already_stopped = _WorkerSlot(3, "stopped", task_queue=FakeQueue(), process=FakeProcess(alive=False))
    pool._stop_worker(already_stopped, reason="already-stopped")
    assert already_stopped.process is None


def test_spawn_worker_initializes_process_and_recycle_count() -> None:
    pool = make_pool()
    created: dict[str, Any] = {}

    class SpawnProcess(FakeProcess):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__()
            created.update(kwargs)
            self.start_calls = 0

        def start(self) -> None:
            self.start_calls += 1

    class SpawnContext:
        def Queue(self, *, maxsize: int) -> FakeQueue:
            assert maxsize == 1
            return FakeQueue()

        def Value(self, typecode: str, value: float) -> LockedValue:
            assert typecode == "d"
            return LockedValue(value)

        def Process(self, **kwargs: Any) -> SpawnProcess:
            return SpawnProcess(**kwargs)

    pool._ctx = SpawnContext()
    slot = pool._slots[0]
    pool._spawn_worker(slot, reason="test")

    assert isinstance(slot.process, SpawnProcess)
    assert slot.process.start_calls == 1
    assert slot.recycle_count == 1
    assert created["target"] is pool_mod._heavy_render_worker_main
    assert created["name"] == slot.name


def test_global_pool_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakePool:
        async def start(self) -> None:
            calls.append("start")

        async def shutdown(self) -> None:
            calls.append("shutdown")

    fake = FakePool()
    monkeypatch.setattr(pool_mod, "_heavy_render_pool", None)
    monkeypatch.setattr(pool_mod, "HeavyRenderWorkerPool", lambda **_kwargs: fake)

    assert pool_mod.get_heavy_render_worker_pool() is fake
    assert pool_mod.get_heavy_render_worker_pool() is fake
    asyncio.run(pool_mod.startup_heavy_render_worker_pool())
    asyncio.run(pool_mod.shutdown_heavy_render_worker_pool())
    asyncio.run(pool_mod.shutdown_heavy_render_worker_pool())
    assert calls == ["start", "shutdown"]
    assert pool_mod._heavy_render_pool is None
