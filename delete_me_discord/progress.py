from contextlib import nullcontext
from typing import Optional

from rich.live import Live
from rich.progress import Progress, TaskID, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.spinner import Spinner
from rich.table import Table

from .utils import RICH_CONSOLE


BUFFERING_INDENT = 29
ACTION_PROGRESS_INDENT = 28


class CleanerProgress:
    """Rich-based progress helpers for buffered fetch and action execution."""

    def buffering_context(self, enabled: bool):
        """Return a live buffering renderer when enabled, otherwise a no-op context."""
        if not enabled:
            return nullcontext(None)
        return Live(
            self.render_buffering_status(0),
            console=RICH_CONSOLE,
            refresh_per_second=12,
            transient=True,
        )

    def update_buffering(self, live: Optional[Live], buffered_count: int) -> None:
        """Refresh the live buffering status with the current buffered message count."""
        if live is None:
            return
        live.update(self.render_buffering_status(buffered_count))

    def render_buffering_status(self, buffered_count: int) -> Table:
        """Render the one-line buffering spinner aligned with the log body."""
        row = Table.grid(padding=0)
        row.add_column(width=BUFFERING_INDENT)
        row.add_column(width=2)
        row.add_column()
        row.add_row("", Spinner("dots", style="cyan"), f"Buffering: {buffered_count} messages")
        return row

    def action_progress(self, enabled: bool, total_actions: int, description: str):
        """Return a context manager for transient per-channel action progress."""
        if not enabled or total_actions <= 0:
            return nullcontext((None, None))

        progress = Progress(
            TextColumn(" " * ACTION_PROGRESS_INDENT),
            TextColumn("[progress.description]{task.description}"),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=RICH_CONSOLE,
            transient=True,
        )
        progress.start()
        task_id = progress.add_task(description, total=total_actions)

        class _Manager:
            def __enter__(self):
                return progress, task_id

            def __exit__(self, exc_type, exc, tb):
                progress.stop()
                return False

        return _Manager()
