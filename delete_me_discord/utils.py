# delete_me_discord/utils.py

import argparse
import math
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Mapping, Tuple

from .discord.channel_types import channel_type_name
from .privacy import RedactionConfig, sensitive, sensitive_name


def format_timestamp(dt: datetime) -> str:
    """Return a consistent UTC timestamp like [12/08/25 19:05:14]."""
    return dt.astimezone(timezone.utc).strftime("[%y/%m/%d %H:%M:%S]")


def channel_str(channel: Mapping[str, Any]) -> str:
    """
    Returns a human-readable string representation of a Discord channel.

    Args:
        channel (Mapping[str, Any]): The channel data.

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
