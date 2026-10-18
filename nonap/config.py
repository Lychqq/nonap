from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class NonapConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NONAP_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        env_file_encoding="utf-8",
    )

    threshold_ms: float = Field(default=50.0, gt=0.0)
    check_interval_ms: float = Field(default=10.0, gt=0.0)
    inject_server_timing: bool = True
    fail_under_test: bool = True
    rich_output: bool = True
    rich_stack_depth: int = 12
    ignored_paths: list[str] = Field(default_factory=lambda: ["asyncio/", "uvicorn/"])
    max_metrics_history: int = Field(default=1000, gt=0)
    max_incidents: int = Field(default=1000, gt=0)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_intervals(self) -> NonapConfig:
        if self.check_interval_ms >= self.threshold_ms:
            raise ValueError(
                f"check_interval_ms ({self.check_interval_ms}) must be strictly less than threshold_ms ({self.threshold_ms})"
            )
        return self


# Backward-compatible alias
FastGuardConfig = NonapConfig
