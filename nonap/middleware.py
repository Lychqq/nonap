from __future__ import annotations

import asyncio
from collections import deque
import time
import uuid
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nonap.config import NonapConfig, FastGuardConfig
from nonap.detector import LoopWatchdog
from nonap.models import BlockIncident, RequestMetrics


class NonapMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        config: FastGuardConfig | None = None,
        watchdog: LoopWatchdog | None = None,
    ) -> None:
        self.app = app
        self.config = config or FastGuardConfig()
        self._watchdog = watchdog
        self._metrics_history = deque(maxlen=self.config.max_metrics_history)

    @property
    def watchdog(self) -> LoopWatchdog | None:
        if self._watchdog is None and self.config.enabled:
            self._watchdog = LoopWatchdog(config=self.config)
        return self._watchdog

    @property
    def metrics(self) -> list[RequestMetrics]:
        return list(self._metrics_history)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            async def lifespan_send(message: Message) -> None:
                if message["type"] == "lifespan.startup.complete" and self.config.enabled:
                    wd = self.watchdog
                    if wd and not wd.is_running:
                        wd.start()
                elif message["type"] == "lifespan.shutdown.complete":
                    if self._watchdog and self._watchdog.is_running:
                        self._watchdog.stop()
                await send(message)

            try:
                await self.app(scope, receive, lifespan_send)
            finally:
                if self._watchdog and self._watchdog.is_running:
                    self._watchdog.stop()
            return

        if scope["type"] != "http" or not self.config.enabled:
            await self.app(scope, receive, send)
            return

        wd = self.watchdog
        if wd and not wd.is_running:
            wd.start()

        headers = Headers(scope=scope)
        request_id = headers.get("x-request-id") or headers.get("x-correlation-id") or uuid.uuid4().hex[:8]
        client = scope.get("client")
        client_host = client[0] if client else None
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        start_time = time.perf_counter()

        request_incidents = []

        def _on_incident(incident: BlockIncident) -> None:
            # Bind request details directly to the incident in the request's loop thread
            enriched = incident.model_copy(
                update={
                    "request_id": request_id,
                    "route": path,
                    "method": method,
                    "client_host": client_host,
                }
            )
            request_incidents.append(enriched)

        if wd:
            wd.add_listener(_on_incident)

        def _calc_max_lag() -> float:
            if request_incidents:
                return max(inc.duration_ms for inc in request_incidents)
            return wd.last_lag_ms if wd else 0.0

        try:
            async def send_wrapper(message: Message) -> None:
                if message["type"] == "http.response.start" and self.config.inject_server_timing:
                    resp_headers = MutableHeaders(scope=message)
                    lag = _calc_max_lag()
                    timing = [f"nonap-lag;dur={lag:.2f}"]
                    if request_incidents:
                        timing.append(f'nonap-block;desc="{len(request_incidents)} block incident(s)"')

                    header_val = ", ".join(timing)
                    existing = resp_headers.get("server-timing")
                    resp_headers["server-timing"] = f"{existing}, {header_val}" if existing else header_val

                await send(message)

            await self.app(scope, receive, send_wrapper)
        finally:
            if wd:
                wd.remove_listener(_on_incident)

            total_duration_ms = (time.perf_counter() - start_time) * 1000.0

            self._metrics_history.append(
                RequestMetrics(
                    request_id=request_id,
                    method=method,
                    path=path,
                    total_duration_ms=round(total_duration_ms, 2),
                    max_loop_lag_ms=round(_calc_max_lag(), 2),
                    incidents=list(request_incidents),
                )
            )


# Backward-compatible alias
FastGuardMiddleware = NonapMiddleware
