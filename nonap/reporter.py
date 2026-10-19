from __future__ import annotations

import sys
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from nonap.config import NonapConfig, FastGuardConfig
from nonap.models import BlockIncident


class ConsoleReporter:
    def __init__(
        self,
        console: Console | None = None,
        config: FastGuardConfig | None = None,
    ) -> None:
        self.config = config or FastGuardConfig()
        self.console = console or Console(stderr=True, highlight=False)

    def report(self, incident: BlockIncident) -> None:
        self.report_incident(incident)

    def report_incident(self, incident: BlockIncident) -> None:
        content = Text()
        content.append("Duration:  ", style="bold")
        content.append(f"{incident.duration_ms:.2f} ms", style="bold red")
        content.append(f" (threshold: {self.config.threshold_ms:.1f} ms)\n", style="dim")

        content.append("Location:  ", style="bold")
        content.append(f"{incident.file_name}:{incident.line_number}", style="cyan underline")
        content.append(f" in {incident.function_name}()\n", style="bold green")

        if incident.code_line:
            content.append("Code:      ", style="bold")
            content.append(f"{incident.code_line}\n", style="bold yellow")

        if incident.route or incident.method:
            content.append("Request:   ", style="bold")
            method = f"{incident.method} " if incident.method else ""
            content.append(f"{method}{incident.route or 'unknown'}", style="magenta")
            if incident.request_id:
                content.append(f" [ID: {incident.request_id}]", style="dim")
            content.append("\n")

        if incident.client_host:
            content.append("Client:    ", style="bold")
            content.append(f"{incident.client_host}\n", style="blue")

        if incident.stack_trace:
            content.append("\nTraceback (most recent calls):\n", style="bold dim")
            depth = min(len(incident.stack_trace), self.config.rich_stack_depth)
            for frame_line in incident.stack_trace[-depth:]:
                content.append(f"  {frame_line.rstrip()}\n", style="dim")

        self.console.print(Panel(
            content,
            title="[bold red]nonap: event loop blocked[/bold red]",
            subtitle="[dim]nonap[/dim]",
            border_style="red",
            expand=False,
            padding=(0, 1),
        ))
