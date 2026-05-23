import asyncio
import faulthandler
import logging
import signal
import sys
import threading
import traceback
from typing import Any

logger = logging.getLogger("src.core.diagnostics")

_config_lock = threading.Lock()
_configured = False


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except Exception:
        return f"SIG{signum}"


def _safe_snapshot_metrics() -> dict[str, Any]:
    try:
        from src.core.debug import snapshot_process_metrics

        return snapshot_process_metrics(include_asyncio=True)
    except Exception as exc:
        return {"snapshot_error": str(exc)}


def _dump_thread_frames(reason: str) -> None:
    frames = sys._current_frames()
    threads = threading.enumerate()
    logger.warning("thread dump begin: reason=%s threads=%d", reason, len(threads))
    for thread in threads:
        logger.warning(
            "thread dump: reason=%s name=%s ident=%s daemon=%s alive=%s",
            reason,
            thread.name,
            thread.ident,
            thread.daemon,
            thread.is_alive(),
        )
        frame = frames.get(thread.ident)
        if frame is None:
            logger.warning("thread dump: reason=%s name=%s stack=unavailable", reason, thread.name)
            continue
        logger.warning(
            "thread stack: reason=%s name=%s\n%s",
            reason,
            thread.name,
            "".join(traceback.format_stack(frame)).rstrip(),
        )
    logger.warning("thread dump end: reason=%s", reason)


def _dump_asyncio_tasks(reason: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("asyncio task dump skipped: reason=%s no-running-loop", reason)
        return

    tasks = list(asyncio.all_tasks(loop))
    logger.warning("asyncio task dump begin: reason=%s tasks=%d", reason, len(tasks))
    for task in tasks:
        coro = getattr(task, "get_coro", lambda: None)()
        coro_name = getattr(coro, "__qualname__", repr(coro))
        logger.warning(
            "asyncio task: reason=%s name=%s done=%s cancelled=%s coro=%s",
            reason,
            task.get_name(),
            task.done(),
            task.cancelled(),
            coro_name,
        )
        stack = task.get_stack(limit=64)
        if not stack:
            logger.warning("asyncio task stack: reason=%s name=%s stack=empty", reason, task.get_name())
            continue
        formatted = "".join("".join(traceback.format_stack(frame)).rstrip() + "\n" for frame in stack).rstrip()
        logger.warning(
            "asyncio task stack: reason=%s name=%s\n%s",
            reason,
            task.get_name(),
            formatted,
        )
    logger.warning("asyncio task dump end: reason=%s", reason)


def dump_runtime_diagnostics(reason: str) -> None:
    metrics = _safe_snapshot_metrics()
    logger.warning("runtime diagnostics begin: reason=%s metrics=%s", reason, metrics)
    _dump_asyncio_tasks(reason)
    _dump_thread_frames(reason)
    logger.warning("runtime diagnostics end: reason=%s", reason)


def configure_runtime_diagnostics() -> None:
    global _configured
    with _config_lock:
        if _configured:
            return

        try:
            faulthandler.enable(file=sys.stderr, all_threads=True)
        except Exception:
            logger.warning("failed to enable faulthandler", exc_info=True)

        manual_dump_signal = getattr(signal, "SIGUSR1", None)
        if manual_dump_signal is not None:
            try:
                faulthandler.register(manual_dump_signal, file=sys.stderr, all_threads=True, chain=False)
                logger.info("faulthandler manual dump signal registered: %s", _signal_name(manual_dump_signal))
            except Exception:
                logger.warning(
                    "failed to register manual dump signal: %s",
                    _signal_name(manual_dump_signal),
                    exc_info=True,
                )

        termination_signal = getattr(signal, "SIGTERM", None)
        if termination_signal is not None:
            try:
                faulthandler.register(termination_signal, file=sys.stderr, all_threads=True, chain=True)
                logger.info("termination traceback dump registered: %s", _signal_name(termination_signal))
            except Exception:
                logger.warning(
                    "failed to register termination traceback dump: %s",
                    _signal_name(termination_signal),
                    exc_info=True,
                )

        _configured = True
