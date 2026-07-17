# delete_me_discord/utils.py

import argparse
import json
import logging
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Tuple, Set, Optional
from rich.console import Console
from rich.logging import RichHandler
from rich.padding import Padding

from .channel_types import channel_type_name
from rich.table import Table
from rich.text import Text

from .privacy import RedactionConfig, sensitive, sensitive_name, set_redaction_config
from .scope_filter import ScopeFilter
from .scope_rules import ScopeRules


PROGRESS_LEVEL = 15
EVENT_LEVEL = 14
DETAIL_LEVEL = 13
DIAGNOSTIC_LEVEL = 12

logging.addLevelName(PROGRESS_LEVEL, "PROGRESS")
logging.addLevelName(EVENT_LEVEL, "EVENT")
logging.addLevelName(DETAIL_LEVEL, "DETAIL")
logging.addLevelName(DIAGNOSTIC_LEVEL, "DIAGNOSTIC")


def _log_at_level(level: int):
    def _inner(self, message, *args, indent: int = 0, prefix: str = "", no_wrap: bool = False, **kwargs):
        if self.isEnabledFor(level):
            extra = dict(kwargs.pop("extra", {}) or {})
            extra["cli_indent"] = indent
            extra["cli_prefix"] = prefix
            extra["cli_no_wrap"] = no_wrap
            self._log(level, message, args, extra=extra, **kwargs)
    return _inner


if not hasattr(logging.Logger, "progress"):
    logging.Logger.progress = _log_at_level(PROGRESS_LEVEL)

if not hasattr(logging.Logger, "event"):
    logging.Logger.event = _log_at_level(EVENT_LEVEL)

if not hasattr(logging.Logger, "detail"):
    logging.Logger.detail = _log_at_level(DETAIL_LEVEL)

if not hasattr(logging.Logger, "diagnostic"):
    logging.Logger.diagnostic = _log_at_level(DIAGNOSTIC_LEVEL)


class AuthenticationError(Exception):
    """Custom exception for authentication errors (e.g., 401)."""

class ReachedMaxRetries(Exception):
    """Custom exception for reaching maximum retries."""

class ResourceUnavailable(Exception):
    """Custom exception for unavailable resources."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

class UnexpectedStatus(Exception):
    """Custom exception for unexpected/unhandled status codes."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        discord_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.discord_code = discord_code

class JsonLogFormatter(logging.Formatter):
    """Format logs as JSON lines for sidecar integrations."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
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
            table.add_column(no_wrap=no_wrap, overflow="ellipsis" if no_wrap else "fold")
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
    """
    Configures the logging settings.

    Args:
        verbosity (int): User-facing verbosity level from -v repetitions.
        quiet (bool): Reduce output to warnings and errors only.
        json_output (bool): Emit JSON logs when True.
        redaction_config (Optional[RedactionConfig]): Sensitive-value redaction settings.
    """
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
    else:
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

def format_timestamp(dt: datetime) -> str:
    """Return a consistent UTC timestamp like [12/08/25 19:05:14]."""
    return dt.astimezone(timezone.utc).strftime("[%y/%m/%d %H:%M:%S]")


def channel_str(channel: Dict[str, Any]) -> str:
    """
    Returns a human-readable string representation of a Discord channel.

    Args:
        channel (Dict[str, Any]): The channel data.

    Returns:
        str: A formatted string representing the channel.
    """
    channel_type = channel_type_name(channel.get("type"))
    channel_name = channel.get("name") or ', '.join(
        [recipient.get("username", "Unknown") for recipient in channel.get("recipients", [])]
    )
    return f"{channel_type} {sensitive_name(channel_name)} (ID: {sensitive(channel.get('id', 'unknown'))})"


def parse_redaction_spec(values: List[str]) -> RedactionConfig:
    """
    Parse redaction args in space-separated form.

    Examples:
    - [] fully masks sensitive values
    - ["4"] keeps the last 4 characters
    - ["0", "4"] keeps the last 4 characters
    - ["4", "4"] keeps the first and last 4 characters
    """
    if values == []:
        return RedactionConfig(enabled=True)

    parts = [part.strip() for part in values if part.strip()]
    if len(parts) not in {1, 2}:
        raise argparse.ArgumentTypeError(
            "Invalid redact-sensitive format. Use '--redact-sensitive' for full masking, one suffix integer like '4', or two integers like '0 4'."
        )

    try:
        if len(parts) == 1:
            prefix = 0
            suffix = int(parts[0])
        else:
            prefix = int(parts[0])
            suffix = int(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Invalid redact-sensitive format. Prefix and suffix must be integers."
        ) from exc

    if prefix < 0 or suffix < 0:
        raise argparse.ArgumentTypeError("Redaction prefix and suffix must be non-negative integers.")

    return RedactionConfig(enabled=True, prefix=prefix, suffix=suffix)

def should_include_channel(
    channel: Dict[str, Any],
    include_ids: Set[str],
    exclude_ids: Set[str],
    scope_filter: ScopeFilter | None = None,
) -> bool:
    """
    Decide whether a channel should be included based on include/exclude IDs.

    Type and thread-state exclusions always win. ID scope uses nearest-target precedence.

    Returns:
        bool: True if the channel should be included, False otherwise.
    """
    if scope_filter is not None and not scope_filter.includes_channel(channel):
        return False

    return ScopeRules.from_values(include_ids, exclude_ids).includes(channel)


def parse_random_range(arg: List[str], parameter_name: str) -> Tuple[float, float]:
    """
    Parses command-line arguments that can accept either one or two float values.
    If two values are provided, ensures the first is less than or equal to the second.

    Args:
        arg (List[str]): List of string arguments.
        parameter_name (str): Name of the parameter (for error messages).

    Returns:
        Tuple[float, float]: A tuple representing the range.
                             If one value is provided, both elements are the same.
                             If two values are provided, they represent the range.

    Raises:
        argparse.ArgumentTypeError: If the input format is incorrect.
    """
    try:
        values = [float(value) for value in arg]
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError(f"Values for {parameter_name} must be finite and non-negative.")
        if len(values) == 1:
            return (values[0], values[0])
        elif len(values) == 2:
            if values[0] > values[1]:
                raise ValueError(f"The first value must be less than or equal to the second value for {parameter_name}.")
            return (values[0], values[1])
        else:
            raise ValueError(f"Expected 1 or 2 values for {parameter_name}, got {len(values)}.")
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"Invalid format for {parameter_name}. Provide one value or two values separated by space. Error: {e}"
        ) from e


_COMPACT_DURATION_RE = re.compile(r"(?P<value>-?\d+(?:\.\d+)?)(?P<unit>[wdhms])", re.IGNORECASE)
_COMPACT_UNIT_MAP: Dict[str, str] = {
    "w": "weeks",
    "d": "days",
    "h": "hours",
    "m": "minutes",
    "s": "seconds",
}
_KEY_UNITS = {"weeks", "days", "hours", "minutes", "seconds"}


def parse_time_delta(time_str: str) -> timedelta:
    """
    Parse a time delta string into a timedelta.

    Supported formats:
    - Legacy key/value: 'weeks=2,days=3,hours=5'
    - Compact suffix: '2w3d4h5m6s'
    """
    if not time_str or not time_str.strip():
        raise argparse.ArgumentTypeError("Time delta cannot be empty.")

    raw = time_str.strip()

    # Special-case plain zero for convenience.
    if raw in {"0", "0.0"}:
        return timedelta(0)

    # Legacy key/value format takes precedence when '=' is present.
    if "=" in raw:
        try:
            kwargs: Dict[str, float] = {}
            parts = [p for p in raw.split(",") if p.strip()]
            if not parts:
                raise ValueError("No time components provided.")
            for part in parts:
                if "=" not in part:
                    raise ValueError(f"Missing '=' in segment '{part}'.")
                key, value = part.split("=", 1)
                key = key.strip().lower()
                if key not in _KEY_UNITS:
                    raise ValueError(f"Unsupported time unit in segment '{part.strip()}'.")
                try:
                    amount = float(value.strip())
                except ValueError as exc:
                    raise ValueError(f"Invalid number for {key}: '{value.strip()}'") from exc
                if amount < 0:
                    raise ValueError("Negative durations are not allowed.")
                if key in kwargs:
                    raise ValueError(f"Duplicate unit '{key}' is not allowed.")
                kwargs[key] = amount
            return timedelta(**kwargs)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Invalid time delta format: '{time_str}'. "
                "Use formats like 'weeks=2,days=3' or '2w3d4h5m6s'. "
                f"Error: {exc}"
            ) from exc

    # Compact suffix format (e.g., 1y2w3d4h5m6s).
    compact_source = raw.replace(" ", "")
    matches = list(_COMPACT_DURATION_RE.finditer(compact_source))
    matched_len = sum(len(match.group(0)) for match in matches)
    if matches and matched_len == len(compact_source):
        totals: Dict[str, float] = {}
        for match in matches:
            unit_key = match.group("unit").lower()
            if unit_key not in _COMPACT_UNIT_MAP:
                raise argparse.ArgumentTypeError(f"Unsupported time unit: {match.group('unit')}")
            target_unit = _COMPACT_UNIT_MAP[unit_key]
            amount = float(match.group("value"))
            if amount < 0:
                raise argparse.ArgumentTypeError("Negative durations are not allowed.")
            if target_unit in totals:
                raise argparse.ArgumentTypeError(f"Duplicate unit '{unit_key}' is not allowed.")
            totals[target_unit] = amount
        return timedelta(**totals)

    raise argparse.ArgumentTypeError(
        f"Invalid time delta format: '{time_str}'. "
        "Use formats like 'weeks=2,days=3' or '2w3d4h5m6s'."
    )
