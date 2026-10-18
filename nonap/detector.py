from __future__ import annotations

import asyncio
from collections import deque
import linecache
import sys
import threading
import time
import traceback
import types
from typing import Any, Callable

from nonap.config import NonapConfig, FastGuardConfig
from nonap.models import BlockIncident
from nonap.reporter import ConsoleReporter


class LoopWatchdog:
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
        config: FastGuardConfig | None = None,
        target_thread_id: int | None = None,
        reporter: ConsoleReporter | None = None,
    ) -> None:
        self.config = config or FastGuardConfig()
        self._loop = loop
        self.target_thread_id = target_thread_id
        self._reporter = reporter or (ConsoleReporter(config=self.config) if self.config.rich_output else None)

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._lock = threading.Lock()
        self._incidents = deque(maxlen=self.config.max_incidents)
        self._listeners = []
        self._last_lag_ms = 0.0
        self._max_lag_ms = 0.0

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            self._loop = self._resolve_loop()
        return self._loop

    def _resolve_loop(self) -> asyncio.AbstractEventLoop:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.get_event_loop()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def incidents(self) -> list[BlockIncident]:
        with self._lock:
            return list(self._incidents)

    @property
    def last_lag_ms(self) -> float:
        return self._last_lag_ms

    @property
    def max_lag_ms(self) -> float:
        return self._max_lag_ms

    def add_listener(self, listener: Callable[[BlockIncident], None]) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[BlockIncident], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def reset(self) -> None:
        with self._lock:
            self._incidents.clear()
        self._last_lag_ms = 0.0
        self._max_lag_ms = 0.0

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> LoopWatchdog:
        if not self.config.enabled:
            return self

        with self._lock:
            if self.is_running:
                return self

            self._loop = loop or self._loop or self._resolve_loop()
            if self.target_thread_id is None:
                self.target_thread_id = threading.get_ident()

            self._stop_event.clear()
            self._ready_event.clear()
            self._thread = threading.Thread(
                target=self._watchdog_loop,
                daemon=True,
                name="FastGuard-Watchdog",
            )
            self._thread.start()
            self._ready_event.wait(timeout=0.2)
            return self

    def stop(self, timeout: float = 1.0) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def _update_lag(self, lag_ms: float) -> None:
        self._last_lag_ms = lag_ms
        if lag_ms > self._max_lag_ms:
            self._max_lag_ms = lag_ms

    def _notify(self, incident: BlockIncident) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(incident)
            except Exception:
                pass

    def _watchdog_loop(self) -> None:
        threshold_sec = max(0.001, self.config.threshold_ms / 1000.0)
        check_interval_sec = max(0.001, self.config.check_interval_ms / 1000.0)
        self._ready_event.set()

        while not self._stop_event.is_set():
            if self.loop.is_closed():
                break

            pong_event = threading.Event()
            ping_time = time.perf_counter()
            pong_time = ping_time

            def _pong() -> None:
                nonlocal pong_time
                self.target_thread_id = threading.get_ident()
                pong_time = time.perf_counter()
                pong_event.set()

            try:
                self.loop.call_soon_threadsafe(_pong)
            except RuntimeError:
                break

            responded = pong_event.wait(timeout=threshold_sec)
            now = time.perf_counter()

            if not responded:
                snapshot = self._snapshot_blocked_thread()
                stall_ms = (now - ping_time) * 1000.0

                self._update_lag(stall_ms)
                incident = self._build_incident(snapshot, stall_ms)
                with self._lock:
                    self._incidents.append(incident)
                if self._reporter:
                    self._reporter.report(incident)
                self._notify(incident)

                pong_event.wait(timeout=5.0)
                duration_ms = (time.perf_counter() - ping_time) * 1000.0
                self._update_lag(duration_ms)
            else:
                self._update_lag((pong_time - ping_time) * 1000.0)

            self._stop_event.wait(timeout=check_interval_sec)

    def _snapshot_blocked_thread(self) -> tuple[str, int, str, str, list[str]]:
        if not self.target_thread_id:
            return ("<unknown>", 0, "<unknown>", "", [])
        frame = sys._current_frames().get(self.target_thread_id)
        if frame is None:
            return ("<unknown>", 0, "<unknown>", "", [])

        tb = traceback.extract_stack(frame)
        formatted_stack = [
            f'File "{f.filename}", line {f.lineno}, in {f.name}\n    {f.line}'
            for f in tb
        ]

        best_frame = frame
        curr = frame
        while curr is not None:
            if not any(ignored in curr.f_code.co_filename for ignored in self.config.ignored_paths):
                best_frame = curr
                break
            curr = curr.f_back

        code = best_frame.f_code
        linecache.checkcache(code.co_filename)
        code_line = linecache.getline(code.co_filename, best_frame.f_lineno).strip()
        return (code.co_filename, best_frame.f_lineno, code.co_name, code_line, formatted_stack)

    def _build_incident(
        self,
        snapshot: tuple[str, int, str, str, list[str]],
        duration_ms: float,
    ) -> BlockIncident:
        filename, lineno, funcname, code_line, stack_trace = snapshot
        return BlockIncident(
            duration_ms=round(duration_ms, 2),
            file_name=filename,
            line_number=lineno,
            function_name=funcname,
            code_line=code_line,
            stack_trace=stack_trace,
        )

    def __enter__(self) -> LoopWatchdog:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        self.stop()
