# delete_me_discord/utils.py

import logging
import re
from datetime import timedelta, datetime, timezone
from typing import List, Dict, Any, Tuple, Set, Optional
from rich.logging import RichHandler
import argparse

class FetchError(Exception):
    """Custom exception for fetch-related errors."""

def setup_logging(log_level: str = "INFO") -> None:
    """
    Configures the logging settings.

    Args:
        log_level (str): The logging level (e.g., 'DEBUG', 'INFO').
    """
    numeric_level = getattr(logging, log_level.upper(), None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO
    logging.basicConfig(
        level=numeric_level,
        format='%(message)s',
        handlers=[
            RichHandler()
        ],
    )

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
    channel_types: Dict[int, str] = {0: "GuildText", 1: "DM", 3: "GroupDM"}
    channel_type = channel_types.get(channel["type"], "Unknown")
    channel_name = channel.get("name") or ', '.join(
        [recipient.get("username", "Unknown") for recipient in channel.get("recipients", [])]
    )
    return f"{channel_type} {channel_name} (ID: {channel.get('id')})"

def should_include_channel(
    channel: Dict[str, Any],
    include_ids: Set[str],
    exclude_ids: Set[str],
) -> bool:
    """
    Decide whether a channel should be included based on include/exclude IDs.

    Exclude takes precedence unless the channel itself is explicitly included.

    Returns:
        bool: True if the channel should be included, False otherwise.
    """
    channel_id = channel.get("id")
    guild_id = channel.get("guild_id")
    parent_id = channel.get("parent_id")

    # Always honor explicit channel exclusion.
    if channel_id in exclude_ids:
        return False

    # Allow channel-level override.
    if channel_id in include_ids:
        return True

    # Allow parent/category include to carve out channels even if the guild/parent is excluded.
    if parent_id and parent_id in include_ids:
        return True

    # Exclude parent/guild if matched.
    if parent_id in exclude_ids:
        return False
    if guild_id in exclude_ids:
        return False

    # If include_ids is provided, require a match on channel/guild/parent.
    if include_ids and not include_ids.intersection({channel_id, guild_id, parent_id}):
        return False

    return True


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
