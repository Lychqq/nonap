"""Unit tests for FastGuard LoopWatchdog detector."""

import asyncio
import io
import time
import pytest
from rich.console import Console

from nonap.config import NonapConfig, FastGuardConfig
from nonap.detector import LoopWatchdog
from nonap.models import BlockIncident
from nonap.reporter import ConsoleReporter


@pytest.mark.asyncio
async def test_watchdog_lifecycle():
    """Verify watchdog starts and stops cleanly."""
    config = FastGuardConfig(threshold_ms=25.0, check_interval_ms=5.0, rich_output=False)
    watchdog = LoopWatchdog(config=config)

    assert not watchdog.is_running
    watchdog.start()
    assert watchdog.is_running

    watchdog.stop()
    assert not watchdog.is_running


@pytest.mark.asyncio
async def test_watchdog_detects_blocking_sleep():
    """Verify watchdog captures synchronous time.sleep blocking the event loop."""
    config = FastGuardConfig(threshold_ms=30.0, check_interval_ms=5.0, rich_output=False)
    watchdog = LoopWatchdog(config=config)
    watchdog.start()

    try:
        # Give watchdog thread a brief moment to spin up
        await asyncio.sleep(0.02)

        # Trigger synchronous blocking call on event loop
        time.sleep(0.08)

        # Allow watchdog to register pong and process incident
        await asyncio.sleep(0.05)

        incidents = watchdog.incidents
        assert len(incidents) >= 1
        incident = incidents[0]
        assert incident.duration_ms >= 30.0
        assert incident.function_name == "test_watchdog_detects_blocking_sleep"
        assert "time.sleep(0.08)" in incident.code_line or "test_watchdog_detects_blocking_sleep" in incident.function_name
    finally:
        watchdog.stop()


@pytest.mark.asyncio
async def test_watchdog_ignores_async_sleep():
    """Verify non-blocking asyncio.sleep does not trigger false positive blocks."""
    config = FastGuardConfig(threshold_ms=30.0, check_interval_ms=5.0, rich_output=False)
    watchdog = LoopWatchdog(config=config)
    watchdog.start()

    try:
        # Non-blocking async sleep gives control back to the event loop
        await asyncio.sleep(0.08)
        await asyncio.sleep(0.02)

        incidents = watchdog.incidents
        assert len(incidents) == 0
    finally:
        watchdog.stop()


@pytest.mark.asyncio
async def test_watchdog_listener_callback():
    """Verify registered listeners receive incidents."""
    config = FastGuardConfig(threshold_ms=25.0, check_interval_ms=5.0, rich_output=False)
    watchdog = LoopWatchdog(config=config)
    captured: list[BlockIncident] = []

    watchdog.add_listener(captured.append)
    watchdog.start()

    try:
        await asyncio.sleep(0.02)
        time.sleep(0.06)
        await asyncio.sleep(0.05)

        assert len(captured) >= 1
        assert captured[0].duration_ms >= 25.0
    finally:
        watchdog.stop()


@pytest.mark.asyncio
async def test_watchdog_context_manager():
    """Verify async context manager protocol on LoopWatchdog."""
    config = FastGuardConfig(threshold_ms=25.0, check_interval_ms=5.0, rich_output=False)

    with LoopWatchdog(config=config) as wd:
        assert wd.is_running
        await asyncio.sleep(0.02)
        time.sleep(0.06)
        await asyncio.sleep(0.05)
        assert len(wd.incidents) >= 1

    assert not wd.is_running


def test_console_reporter_formatting():
    """Verify ConsoleReporter prints formatted Rich incident card without error."""
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=80)
    reporter = ConsoleReporter(console=console)

    incident = BlockIncident(
        duration_ms=124.5,
        file_name="app/main.py",
        line_number=42,
        function_name="heavy_compute",
        code_line="result = sum(range(10_000_000))",
        stack_trace=['File "app/main.py", line 42, in heavy_compute\n    result = sum(...)'],
        route="/compute",
        request_id="req_test_123",
        client_host="127.0.0.1",
        method="POST",
    )

    reporter.report(incident)
    output = buffer.getvalue()

    assert "nonap: event loop blocked" in output
    assert "124.50 ms" in output
    assert "app/main.py:42" in output
    assert "heavy_compute" in output
    assert "POST /compute" in output
    assert "req_test_123" in output


def test_config_validation_constraints():
    """Verify FastGuardConfig validates positive values and interval order."""
    from pydantic import ValidationError

    # Positive constraints
    with pytest.raises(ValidationError):
        FastGuardConfig(threshold_ms=0.0)

    with pytest.raises(ValidationError):
        FastGuardConfig(check_interval_ms=-5.0)

    # check_interval_ms must be strictly less than threshold_ms
    with pytest.raises(ValidationError) as exc_info:
        FastGuardConfig(threshold_ms=20.0, check_interval_ms=25.0)
    assert "check_interval_ms" in str(exc_info.value)
    assert "must be strictly less than" in str(exc_info.value)


@pytest.mark.asyncio
async def test_watchdog_bounded_incidents_buffer():
    """Verify watchdog incidents buffer does not exceed max_incidents."""
    config = FastGuardConfig(
        threshold_ms=20.0,
        check_interval_ms=5.0,
        max_incidents=2,
        rich_output=False,
    )
    watchdog = LoopWatchdog(config=config)
    assert watchdog._incidents.maxlen == 2
