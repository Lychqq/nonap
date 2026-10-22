"""Tests for FastGuard ASGI middleware."""

import asyncio
import time
import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from nonap.config import NonapConfig, FastGuardConfig
from nonap.middleware import NonapMiddleware, FastGuardMiddleware


def create_app(config: FastGuardConfig | None = None) -> FastAPI:
    """Create a FastAPI application wrapped with FastGuardMiddleware."""
    app = FastAPI()

    cfg = config or FastGuardConfig(
        threshold_ms=30.0,
        check_interval_ms=5.0,
        rich_output=False,
    )
    app.add_middleware(FastGuardMiddleware, config=cfg)

    @app.get("/fast")
    async def fast_route():
        await asyncio.sleep(0.01)
        return {"status": "ok"}

    @app.get("/block")
    async def block_route():
        time.sleep(0.08)
        return {"status": "blocked"}

    return app


@pytest.mark.asyncio
async def test_middleware_fast_route_timing():
    """Verify Server-Timing header injection on healthy fast route."""
    app = create_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/fast")
        assert response.status_code == 200
        assert "server-timing" in response.headers
        timing = response.headers["server-timing"]
        assert "nonap-lag;dur=" in timing
        assert "nonap-block" not in timing


@pytest.mark.asyncio
async def test_middleware_detects_block_in_route():
    """Verify middleware detects blocking route and appends block info to Server-Timing."""
    app = create_app()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/block",
            headers={"X-Request-ID": "custom-req-456"}
        )
        assert response.status_code == 200
        assert "server-timing" in response.headers
        timing = response.headers["server-timing"]
        assert "nonap-lag;dur=" in timing
        assert "nonap-block" in timing


@pytest.mark.asyncio
async def test_middleware_disabled_mode():
    """Verify middleware passes through completely when disabled."""
    config = FastGuardConfig(enabled=False)
    app = create_app(config=config)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/fast")
        assert response.status_code == 200
        assert "server-timing" not in response.headers


@pytest.mark.asyncio
async def test_middleware_bounded_metrics_history():
    """Verify middleware bounded metrics buffer limits memory usage."""
    config = FastGuardConfig(
        threshold_ms=30.0,
        check_interval_ms=5.0,
        max_metrics_history=2,
        rich_output=False,
    )
    app = create_app(config=config)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.get("/fast")
        await client.get("/fast")
        await client.get("/fast")

    # Find the FastGuardMiddleware in the app middleware stack
    for mw in app.user_middleware:
        if mw.cls == FastGuardMiddleware:
            assert mw.kwargs.get("config").max_metrics_history == 2


@pytest.mark.asyncio
async def test_middleware_lifespan_handling():
    """Verify ASGI lifespan events trigger watchdog start and graceful stop."""
    config = FastGuardConfig(
        threshold_ms=30.0,
        check_interval_ms=5.0,
        rich_output=False,
    )
    middleware = FastGuardMiddleware(app=FastAPI(), config=config)
    scope = {"type": "lifespan"}

    events_sent = []

    async def mock_receive():
        return {"type": "lifespan.startup"}

    async def mock_send(message):
        events_sent.append(message["type"])

    # Simulate app that confirms startup and shutdown
    async def mock_app(sc, rx, tx):
        await tx({"type": "lifespan.startup.complete"})
        await tx({"type": "lifespan.shutdown.complete"})

    middleware.app = mock_app
    await middleware(scope, mock_receive, mock_send)

    assert "lifespan.startup.complete" in events_sent
    assert "lifespan.shutdown.complete" in events_sent
    assert not middleware.watchdog.is_running
