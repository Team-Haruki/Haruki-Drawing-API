from __future__ import annotations

import asyncio
from dataclasses import dataclass
import io
import logging
import multiprocessing
from multiprocessing import get_context
import os
import queue
import threading
import time
import traceback
from typing import Any, Literal
from uuid import uuid4

from PIL import Image

from src.settings import (
    EXPORT_IMAGE_FORMAT,
    ISOLATED_WORKER_POOL_SIZE,
    ISOLATED_WORKER_QUEUE_LIMIT,
    ISOLATED_WORKER_QUEUE_TIMEOUT_SECONDS,
    JPG_QUALITY,
    REQUEST_HARD_TIMEOUT_SECONDS,
    settings,
)

logger = logging.getLogger("src.core.heavy_render_pool")

HeavyTaskKind = Literal["deck_recommend", "chara_birthday"]
_HEARTBEAT_INTERVAL_SECONDS = 1.0
_HEARTBEAT_TIMEOUT_SECONDS = 20.0
_RESULT_POLL_INTERVAL_SECONDS = 1.0
_WORKER_SHUTDOWN_GRACE_SECONDS = 3.0

_heavy_pool_ctx = get_context("spawn")
_heavy_render_pool: HeavyRenderWorkerPool | None = None
_heavy_render_pool_lock = threading.Lock()


@dataclass(slots=True)
class EncodedImagePayload:
    image_bytes: bytes
    media_type: str
    filename: str
    image_width: int | None
    image_height: int | None
    image_mode: str | None
    encode_elapsed: float


@dataclass(slots=True)
class _WorkerTask:
    task_id: str
    kind: HeavyTaskKind
    payload: dict[str, Any]
    request_id: str
    request_path: str
    request_method: str


@dataclass(slots=True)
class _WorkerResult:
    task_id: str
    ok: bool
    payload: EncodedImagePayload | None = None
    error: str | None = None
    traceback_text: str | None = None


class HeavyRenderTaskTimeoutError(TimeoutError):
    pass


class HeavyRenderTaskExecutionError(RuntimeError):
    pass


class HeavyRenderQueueFullError(RuntimeError):
    pass


class HeavyRenderQueueTimeoutError(TimeoutError):
    pass


def _encode_image_payload(image: Image.Image) -> EncodedImagePayload:
    image_width = getattr(image, "width", None)
    image_height = getattr(image, "height", None)
    image_mode = getattr(image, "mode", None)
    started = time.perf_counter()
    buffer = io.BytesIO()
    try:
        if EXPORT_IMAGE_FORMAT == "jpg":
            if image.mode in ("RGBA", "LA", "PA"):
                rgb = image.convert("RGB")
                image.close()
                image = rgb
            image.save(buffer, format="JPEG", quality=JPG_QUALITY)
            media_type = "image/jpeg"
            filename = "image.jpg"
        else:
            image.save(buffer, format="PNG")
            media_type = "image/png"
            filename = "image.png"
    finally:
        close = getattr(image, "close", None)
        if callable(close):
            close()
    return EncodedImagePayload(
        image_bytes=buffer.getvalue(),
        media_type=media_type,
        filename=filename,
        image_width=image_width,
        image_height=image_height,
        image_mode=image_mode,
        encode_elapsed=time.perf_counter() - started,
    )


def _configure_worker_render_environment() -> None:
    # Heavy requests already run inside isolated worker processes.
    # Disable nested painter process-pool fanout there to avoid spawning
    # grandchildren from worker processes.
    settings.drawing.use_process_pool = False


def _render_heavy_task(kind: HeavyTaskKind, payload: dict[str, Any]) -> EncodedImagePayload:
    _configure_worker_render_environment()
    if kind == "deck_recommend":
        from src.sekai.deck.drawer import compose_deck_recommend_image
        from src.sekai.deck.model import DeckRequest

        request = DeckRequest.model_validate(payload)
        image = asyncio.run(compose_deck_recommend_image(request))
        return _encode_image_payload(image)

    if kind == "chara_birthday":
        from src.sekai.misc.drawer import compose_chara_birthday_image
        from src.sekai.misc.model import CharaBirthdayRequest

        request = CharaBirthdayRequest.model_validate(payload)
        image = asyncio.run(compose_chara_birthday_image(request))
        return _encode_image_payload(image)

    raise ValueError(f"unsupported heavy render task kind: {kind}")


def _heartbeat_loop(
    *,
    stop_event: threading.Event,
    heartbeat_at: multiprocessing.sharedctypes.Synchronized,
    interval_seconds: float,
) -> None:
    while not stop_event.wait(interval_seconds):
        with heartbeat_at.get_lock():
            heartbeat_at.value = time.monotonic()


def _heavy_render_worker_main(
    worker_name: str,
    task_queue: multiprocessing.queues.Queue,
    result_queue: multiprocessing.queues.Queue,
    heartbeat_at: multiprocessing.sharedctypes.Synchronized,
) -> None:
    logger.info("heavy render worker started: name=%s pid=%s", worker_name, os.getpid())
    while True:
        task = task_queue.get()
        if task is None:
            logger.info("heavy render worker stopping: name=%s pid=%s", worker_name, os.getpid())
            return

        if not isinstance(task, _WorkerTask):
            logger.warning("heavy render worker got unknown task payload: name=%s type=%s", worker_name, type(task))
            continue

        from src.core.debug import pop_request_context, push_request_context, set_request_stage

        tokens = push_request_context(task.request_id, task.request_path, task.request_method)
        with heartbeat_at.get_lock():
            heartbeat_at.value = time.monotonic()

        stop_event = threading.Event()
        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            name=f"{worker_name}-heartbeat",
            kwargs={
                "stop_event": stop_event,
                "heartbeat_at": heartbeat_at,
                "interval_seconds": _HEARTBEAT_INTERVAL_SECONDS,
            },
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            set_request_stage(f"worker:{task.kind}:compose_image")
            payload = _render_heavy_task(task.kind, task.payload)
            result_queue.put(_WorkerResult(task_id=task.task_id, ok=True, payload=payload))
        except BaseException as exc:
            result_queue.put(
                _WorkerResult(
                    task_id=task.task_id,
                    ok=False,
                    error=f"{exc.__class__.__name__}: {exc}",
                    traceback_text=traceback.format_exc(),
                )
            )
        finally:
            stop_event.set()
            heartbeat_thread.join(timeout=1.0)
            with heartbeat_at.get_lock():
                heartbeat_at.value = time.monotonic()
            pop_request_context(tokens)


@dataclass(slots=True)
class _WorkerSlot:
    index: int
    name: str
    task_queue: multiprocessing.queues.Queue | None = None
    result_queue: multiprocessing.queues.Queue | None = None
    heartbeat_at: multiprocessing.sharedctypes.Synchronized | None = None
    process: multiprocessing.Process | None = None
    busy: bool = False
    current_task_id: str | None = None
    current_task_kind: HeavyTaskKind | None = None
    current_task_started_at: float | None = None
    recycle_count: int = 0


class HeavyRenderWorkerPool:
    def __init__(
        self,
        *,
        worker_count: int,
        queue_limit: int,
        queue_timeout_seconds: float,
        task_timeout_seconds: float,
        heartbeat_timeout_seconds: float = _HEARTBEAT_TIMEOUT_SECONDS,
        result_poll_interval_seconds: float = _RESULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._ctx = _heavy_pool_ctx
        self._worker_count = max(1, worker_count)
        self._queue_limit = max(0, queue_limit)
        self._queue_timeout_seconds = max(0.1, float(queue_timeout_seconds))
        self._task_timeout_seconds = max(1.0, float(task_timeout_seconds))
        self._heartbeat_timeout_seconds = max(1.0, float(heartbeat_timeout_seconds))
        self._result_poll_interval_seconds = max(0.1, float(result_poll_interval_seconds))
        self._slots = [_WorkerSlot(index=i, name=f"heavy-render-{i + 1}") for i in range(self._worker_count)]
        self._condition = threading.Condition()
        self._pending_waiters = 0
        self._started = False

    async def start(self) -> None:
        with self._condition:
            if self._started:
                return
            for slot in self._slots:
                self._spawn_worker(slot, reason="startup")
            self._started = True
        logger.info(
            "heavy render worker pool started: workers=%d queue_limit=%d queue_timeout=%.0fs "
            "timeout=%.0fs heartbeat_timeout=%.0fs",
            self._worker_count,
            self._queue_limit,
            self._queue_timeout_seconds,
            self._task_timeout_seconds,
            self._heartbeat_timeout_seconds,
        )

    async def shutdown(self) -> None:
        with self._condition:
            if not self._started:
                return
            for slot in self._slots:
                self._stop_worker(slot, reason="shutdown")
                slot.busy = False
                slot.current_task_id = None
                slot.current_task_kind = None
                slot.current_task_started_at = None
            self._started = False
            self._condition.notify_all()
        logger.info("heavy render worker pool stopped")

    async def render(self, kind: HeavyTaskKind, payload: dict[str, Any]) -> EncodedImagePayload:
        from src.core.debug import current_request_context

        request_ctx = current_request_context()
        slot = await self._acquire_slot(kind, request_ctx)
        task = _WorkerTask(
            task_id=uuid4().hex[:12],
            kind=kind,
            payload=payload,
            request_id=request_ctx["request_id"],
            request_path=request_ctx["path"],
            request_method=request_ctx["method"],
        )
        slot.current_task_id = task.task_id
        slot.current_task_kind = kind
        slot.current_task_started_at = time.monotonic()
        task_submitted = False

        try:
            await asyncio.to_thread(self._put_task, slot, task)
            task_submitted = True
            return await self._wait_for_result(slot, task)
        except asyncio.CancelledError:
            if task_submitted:
                self._spawn_worker(
                    slot,
                    reason=f"caller-cancelled task_id={task.task_id} kind={task.kind}",
                )
            raise
        except Exception:
            if task_submitted and slot.current_task_id == task.task_id and self._is_worker_alive(slot):
                self._spawn_worker(
                    slot,
                    reason=f"main-process-error task_id={task.task_id} kind={task.kind}",
                )
            raise
        finally:
            await self._release_slot(slot)

    async def _acquire_slot(self, kind: HeavyTaskKind, request_ctx: dict[str, str]) -> _WorkerSlot:
        return await asyncio.to_thread(self._acquire_slot_sync, kind, request_ctx)

    async def _release_slot(self, slot: _WorkerSlot) -> None:
        await asyncio.to_thread(self._release_slot_sync, slot)

    def _busy_count_unlocked(self) -> int:
        return sum(1 for slot in self._slots if slot.busy)

    def _acquire_slot_sync(self, kind: HeavyTaskKind, request_ctx: dict[str, str]) -> _WorkerSlot:
        wait_started = time.monotonic()
        queued = False
        with self._condition:
            try:
                while True:
                    busy_count = self._busy_count_unlocked()
                    for slot in self._slots:
                        if slot.busy:
                            continue
                        if not self._is_worker_alive(slot):
                            self._spawn_worker(slot, reason="slot-revive-before-acquire")
                        slot.busy = True
                        wait_ms = (time.monotonic() - wait_started) * 1000
                        logger.info(
                            "heavy render slot acquired: worker=%s kind=%s recycle_count=%d pid=%s "
                            "busy=%d pending=%d wait_ms=%.1f request_id=%s path=%s",
                            slot.name,
                            kind,
                            slot.recycle_count,
                            getattr(slot.process, "pid", None),
                            busy_count + 1,
                            self._pending_waiters,
                            wait_ms,
                            request_ctx.get("request_id", "-"),
                            request_ctx.get("path", "-"),
                        )
                        return slot

                    if not queued:
                        if self._pending_waiters >= self._queue_limit:
                            logger.warning(
                                "heavy render queue full: kind=%s busy=%d pending=%d workers=%d "
                                "queue_limit=%d request_id=%s path=%s",
                                kind,
                                busy_count,
                                self._pending_waiters,
                                self._worker_count,
                                self._queue_limit,
                                request_ctx.get("request_id", "-"),
                                request_ctx.get("path", "-"),
                            )
                            raise HeavyRenderQueueFullError(
                                f"heavy render queue is full: kind={kind} pending={self._pending_waiters} "
                                f"limit={self._queue_limit}"
                            )
                        self._pending_waiters += 1
                        queued = True
                        logger.warning(
                            "heavy render queued: kind=%s busy=%d pending=%d workers=%d queue_limit=%d "
                            "queue_timeout=%.0fs request_id=%s path=%s",
                            kind,
                            busy_count,
                            self._pending_waiters,
                            self._worker_count,
                            self._queue_limit,
                            self._queue_timeout_seconds,
                            request_ctx.get("request_id", "-"),
                            request_ctx.get("path", "-"),
                        )

                    remaining = self._queue_timeout_seconds - (time.monotonic() - wait_started)
                    if remaining <= 0:
                        logger.warning(
                            "heavy render queue timeout: kind=%s busy=%d pending=%d workers=%d "
                            "queue_limit=%d wait_ms=%.1f request_id=%s path=%s",
                            kind,
                            self._busy_count_unlocked(),
                            self._pending_waiters,
                            self._worker_count,
                            self._queue_limit,
                            (time.monotonic() - wait_started) * 1000,
                            request_ctx.get("request_id", "-"),
                            request_ctx.get("path", "-"),
                        )
                        raise HeavyRenderQueueTimeoutError(
                            f"heavy render queue timeout after {self._queue_timeout_seconds:.0f}s: kind={kind}"
                        )
                    self._condition.wait(timeout=remaining)
            finally:
                if queued:
                    self._pending_waiters = max(0, self._pending_waiters - 1)

    def _release_slot_sync(self, slot: _WorkerSlot) -> None:
        with self._condition:
            slot.busy = False
            slot.current_task_id = None
            slot.current_task_kind = None
            slot.current_task_started_at = None
            self._condition.notify()

    async def _wait_for_result(self, slot: _WorkerSlot, task: _WorkerTask) -> EncodedImagePayload:
        deadline = time.monotonic() + self._task_timeout_seconds
        while True:
            if not self._is_worker_alive(slot):
                self._spawn_worker(slot, reason=f"worker-died task_id={task.task_id} kind={task.kind}")
                raise HeavyRenderTaskExecutionError(
                    f"heavy render worker exited unexpectedly: worker={slot.name} task={task.kind}"
                )

            now = time.monotonic()
            heartbeat_age = self._heartbeat_age(slot, now)
            if now >= deadline:
                self._spawn_worker(
                    slot,
                    reason=(
                        f"task-timeout task_id={task.task_id} kind={task.kind} "
                        f"elapsed={self._task_timeout_seconds:.1f}s worker={slot.name}"
                    ),
                )
                raise HeavyRenderTaskTimeoutError(
                    f"heavy render task timeout: worker={slot.name} kind={task.kind} "
                    f"timeout={self._task_timeout_seconds:.0f}s"
                )
            if heartbeat_age is not None and heartbeat_age >= self._heartbeat_timeout_seconds:
                self._spawn_worker(
                    slot,
                    reason=(
                        f"heartbeat-timeout task_id={task.task_id} kind={task.kind} "
                        f"heartbeat_age={heartbeat_age:.1f}s worker={slot.name}"
                    ),
                )
                raise HeavyRenderTaskTimeoutError(
                    f"heavy render worker heartbeat timeout: worker={slot.name} kind={task.kind}"
                )

            timeout_seconds = min(self._result_poll_interval_seconds, max(0.1, deadline - now))
            try:
                result = await asyncio.to_thread(self._get_result, slot, timeout_seconds)
            except queue.Empty:
                continue

            if result.task_id != task.task_id:
                logger.warning(
                    "heavy render worker returned stale result: worker=%s expected=%s actual=%s",
                    slot.name,
                    task.task_id,
                    result.task_id,
                )
                continue

            if not result.ok or result.payload is None:
                logger.error(
                    "heavy render task failed: worker=%s kind=%s task_id=%s error=%s\n%s",
                    slot.name,
                    task.kind,
                    task.task_id,
                    result.error,
                    (result.traceback_text or "").rstrip(),
                )
                raise HeavyRenderTaskExecutionError(result.error or f"heavy render task failed: {task.kind}")

            logger.info(
                "heavy render task completed: worker=%s kind=%s task_id=%s elapsed=%.3fs pid=%s",
                slot.name,
                task.kind,
                task.task_id,
                time.monotonic() - (slot.current_task_started_at or now),
                getattr(slot.process, "pid", None),
            )
            return result.payload

    def _put_task(self, slot: _WorkerSlot, task: _WorkerTask) -> None:
        if slot.task_queue is None:
            raise RuntimeError(f"worker queue not initialized: {slot.name}")
        if slot.heartbeat_at is not None:
            with slot.heartbeat_at.get_lock():
                slot.heartbeat_at.value = time.monotonic()
        slot.task_queue.put(task)

    def _get_result(self, slot: _WorkerSlot, timeout_seconds: float) -> _WorkerResult:
        if slot.result_queue is None:
            raise RuntimeError(f"worker result queue not initialized: {slot.name}")
        return slot.result_queue.get(timeout=timeout_seconds)

    def _heartbeat_age(self, slot: _WorkerSlot, now: float) -> float | None:
        heartbeat_at = slot.heartbeat_at
        if heartbeat_at is None:
            return None
        with heartbeat_at.get_lock():
            last = heartbeat_at.value
        return max(0.0, now - last)

    def _spawn_worker(self, slot: _WorkerSlot, *, reason: str) -> None:
        self._stop_worker(slot, reason=f"replace-before-spawn:{reason}")
        slot.task_queue = self._ctx.Queue(maxsize=1)
        slot.result_queue = self._ctx.Queue(maxsize=1)
        slot.heartbeat_at = self._ctx.Value("d", time.monotonic())
        slot.process = self._ctx.Process(
            target=_heavy_render_worker_main,
            name=slot.name,
            kwargs={
                "worker_name": slot.name,
                "task_queue": slot.task_queue,
                "result_queue": slot.result_queue,
                "heartbeat_at": slot.heartbeat_at,
            },
            daemon=False,
        )
        slot.process.start()
        slot.recycle_count += 1
        logger.warning(
            "heavy render worker spawned: worker=%s pid=%s reason=%s recycle_count=%d",
            slot.name,
            slot.process.pid,
            reason,
            slot.recycle_count,
        )

    def _stop_worker(self, slot: _WorkerSlot, *, reason: str) -> None:
        process = slot.process
        task_queue = slot.task_queue
        if process is None:
            slot.task_queue = None
            slot.result_queue = None
            slot.heartbeat_at = None
            return

        logger.warning(
            "heavy render worker stopping: worker=%s pid=%s reason=%s busy=%s task_id=%s kind=%s",
            slot.name,
            process.pid,
            reason,
            slot.busy,
            slot.current_task_id,
            slot.current_task_kind,
        )
        try:
            if process.is_alive() and task_queue is not None:
                try:
                    task_queue.put_nowait(None)
                except Exception:
                    pass
                process.join(timeout=_WORKER_SHUTDOWN_GRACE_SECONDS)
            if process.is_alive():
                process.kill()
                process.join(timeout=_WORKER_SHUTDOWN_GRACE_SECONDS)
        finally:
            slot.process = None
            slot.task_queue = None
            slot.result_queue = None
            slot.heartbeat_at = None

    def _is_worker_alive(self, slot: _WorkerSlot) -> bool:
        return slot.process is not None and slot.process.is_alive()


def get_heavy_render_worker_pool() -> HeavyRenderWorkerPool:
    global _heavy_render_pool
    if _heavy_render_pool is not None:
        return _heavy_render_pool

    with _heavy_render_pool_lock:
        if _heavy_render_pool is None:
            _heavy_render_pool = HeavyRenderWorkerPool(
                worker_count=max(1, ISOLATED_WORKER_POOL_SIZE),
                queue_limit=max(0, ISOLATED_WORKER_QUEUE_LIMIT),
                queue_timeout_seconds=float(ISOLATED_WORKER_QUEUE_TIMEOUT_SECONDS),
                task_timeout_seconds=float(REQUEST_HARD_TIMEOUT_SECONDS),
            )
    return _heavy_render_pool


async def startup_heavy_render_worker_pool() -> None:
    await get_heavy_render_worker_pool().start()


async def shutdown_heavy_render_worker_pool() -> None:
    global _heavy_render_pool
    pool = _heavy_render_pool
    if pool is None:
        return
    await pool.shutdown()
    _heavy_render_pool = None
