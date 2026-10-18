"""Data models for FastGuard event loop block detection and metrics."""

from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field


class BlockIncident(BaseModel):
    """Detected event loop blockage incident."""

    model_config = ConfigDict(frozen=True)

    duration_ms: float = Field(..., description="Duration in milliseconds of event loop blockage.")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the blockage was detected.",
    )
    file_name: str = Field(..., description="Source file where execution was paused.")
    line_number: int = Field(..., description="Line number where execution was paused.")
    function_name: str = Field(..., description="Function or method executing when blocked.")
    code_line: str = Field(default="", description="Source code line extracted via linecache.")
    stack_trace: list[str] = Field(
        default_factory=list,
        description="Stack trace frames at the moment of block detection.",
    )
    route: str | None = Field(default=None, description="HTTP route/path being handled.")
    request_id: str | None = Field(default=None, description="Correlation ID of the request.")
    client_host: str | None = Field(default=None, description="Client host/IP address.")
    method: str | None = Field(default=None, description="HTTP method of the active request.")


class RequestMetrics(BaseModel):
    """Metrics collected for an HTTP request passing through FastGuard."""

    model_config = ConfigDict(frozen=True)

    request_id: str = Field(..., description="Unique identifier for the request.")
    method: str = Field(..., description="HTTP request method.")
    path: str = Field(..., description="HTTP request path.")
    total_duration_ms: float = Field(..., description="Total request duration in milliseconds.")
    max_loop_lag_ms: float = Field(default=0.0, description="Max event loop lag during request.")
    incidents: list[BlockIncident] = Field(
        default_factory=list,
        description="Blocking incidents detected during this request.",
    )
