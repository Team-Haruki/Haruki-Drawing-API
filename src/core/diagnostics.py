import asyncio
import faulthandler
import logging
import os
import signal
import sys
import threading
import traceback
from typing import Any


logger = logging.getLogger("src.core.diagnostics")

_config_lock = threading.Lock()
_configured = False
_previous_signal_handlers: dict[int, Any] = {}


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


def _write_stderr_line(message: str) -> None:
    try:
        os.write(2, (message.rstrip() + "\n").encode("utf-8", errors="replace"))
    except Exception:
        pass


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


def _chain_previous_signal_handler(signum: int, frame: Any) -> None:
    previous = _previous_signal_handlers.get(signum, signal.SIG_DFL)
    if previous is None or previous == signal.SIG_DFL:
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
        return
    if previous == signal.SIG_IGN:
        return
    if callable(previous):
        previous(signum, frame)


def _handle_termination_signal(signum: int, frame: Any) -> None:
    signame = _signal_name(signum)
    _write_stderr_line(f"=== {signame} received; dumping Python traceback ===")
    try:
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    except Exception:
        _write_stderr_line(f"=== {signame} traceback dump failed ===")
    _chain_previous_signal_handler(signum, frame)


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
                logger.warning("failed to register manual dump signal: %s", _signal_name(manual_dump_signal), exc_info=True)

        termination_signal = getattr(signal, "SIGTERM", None)
        if termination_signal is not None:
            try:
                _previous_signal_handlers[termination_signal] = signal.getsignal(termination_signal)
                signal.signal(termination_signal, _handle_termination_signal)
                logger.info("termination traceback handler installed: %s", _signal_name(termination_signal))
            except Exception:
                logger.warning(
                    "failed to install termination traceback handler: %s",
                    _signal_name(termination_signal),
                    exc_info=True,
                )

        _configured = True
