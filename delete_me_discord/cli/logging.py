"""CLI logging configuration and output formatting."""

import json
import logging
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from ..logging import (
    DETAIL_LEVEL,
    DIAGNOSTIC_LEVEL,
    EVENT_LEVEL,
    PROGRESS_LEVEL,
)
from ..privacy import RedactionConfig, set_redaction_config


class JsonLogFormatter(logging.Formatter):
    """Format logs as JSON lines for sidecar integrations."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event_name = getattr(record, "dmd_event", None)
        event_data = getattr(record, "dmd_event_data", None)
        if isinstance(event_name, str) and event_name:
            payload["event"] = event_name
            if isinstance(event_data, Mapping):
                payload["data"] = dict(event_data)
        return json.dumps(payload, ensure_ascii=True)


RICH_CONSOLE = Console()


class CliIndentFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.cli_indent = max(0, int(getattr(record, "cli_indent", 0) or 0))
        record.cli_no_wrap = bool(getattr(record, "cli_no_wrap", False))
        return True


class CliRichHandler(RichHandler):
    """Rich handler that indents full rendered message blocks."""

    def render_message(self, record: logging.LogRecord, message: str):
        renderable = super().render_message(record, message)
        indent = max(0, int(getattr(record, "cli_indent", 0) or 0))
        prefix = str(getattr(record, "cli_prefix", "") or "")
        no_wrap = bool(getattr(record, "cli_no_wrap", False))
        if no_wrap and isinstance(renderable, Text):
            renderable.no_wrap = True
            renderable.overflow = "ellipsis"
        if prefix:
            table = Table.grid(padding=0)
            table.add_column(no_wrap=True)
            table.add_column(
                no_wrap=no_wrap,
                overflow="ellipsis" if no_wrap else "fold",
            )
            indent_text = "  " * indent
            prefix_cell = f"{indent_text}{prefix} "
            table.add_row(prefix_cell, renderable)
            return table
        if indent:
            return Padding(renderable, (0, 0, 0, indent * 2))
        return renderable


def setup_logging(
    verbosity: int = 0,
    quiet: bool = False,
    json_output: bool = False,
    redaction_config: Optional[RedactionConfig] = None,
) -> None:
    """Configure CLI verbosity, rendering, and sensitive-value redaction."""
    set_redaction_config(redaction_config)
    if quiet:
        numeric_level = logging.WARNING
    elif verbosity <= 0:
        numeric_level = PROGRESS_LEVEL
    elif verbosity == 1:
        numeric_level = EVENT_LEVEL
    elif verbosity == 2:
        numeric_level = DETAIL_LEVEL
    else:
        numeric_level = DIAGNOSTIC_LEVEL

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(numeric_level)
    if json_output:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(JsonLogFormatter())
        root_logger.addHandler(handler)
        return

    handler = CliRichHandler(
        console=RICH_CONSOLE,
        show_time=False,
        show_level=False,
        show_path=False,
        omit_repeated_times=False,
    )
    handler.addFilter(CliIndentFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(handler)


__all__ = ["JsonLogFormatter", "setup_logging"]
