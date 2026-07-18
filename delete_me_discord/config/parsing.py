"""Parsing primitives shared by CLI arguments and stored configuration."""

import argparse
import math
import re
from datetime import timedelta


def parse_random_range(
    values: list[str],
    parameter_name: str,
) -> tuple[float, float]:
    """Parse one or two finite, non-negative values into an ordered range."""
    try:
        parsed_values = [float(value) for value in values]
        if any(
            not math.isfinite(value) or value < 0
            for value in parsed_values
        ):
            raise ValueError(
                f"Values for {parameter_name} must be finite and non-negative."
            )
        if len(parsed_values) == 1:
            return (parsed_values[0], parsed_values[0])
        if len(parsed_values) == 2:
            if parsed_values[0] > parsed_values[1]:
                raise ValueError(
                    "The first value must be less than or equal to the second "
                    f"value for {parameter_name}."
                )
            return (parsed_values[0], parsed_values[1])
        raise ValueError(
            f"Expected 1 or 2 values for {parameter_name}, "
            f"got {len(parsed_values)}."
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid format for {parameter_name}. Provide one value or two "
            f"values separated by space. Error: {exc}"
        ) from exc


_COMPACT_DURATION_RE = re.compile(
    r"(?P<value>-?\d+(?:\.\d+)?)(?P<unit>[wdhms])",
    re.IGNORECASE,
)
_COMPACT_UNIT_MAP = {
    "w": "weeks",
    "d": "days",
    "h": "hours",
    "m": "minutes",
    "s": "seconds",
}
_KEY_UNITS = frozenset(_COMPACT_UNIT_MAP.values())


def parse_time_delta(time_str: str) -> timedelta:
    """Parse legacy key/value or compact duration syntax."""
    if not time_str or not time_str.strip():
        raise argparse.ArgumentTypeError("Time delta cannot be empty.")

    raw = time_str.strip()
    if raw in {"0", "0.0"}:
        return timedelta(0)

    if "=" in raw:
        try:
            kwargs: dict[str, float] = {}
            parts = [part for part in raw.split(",") if part.strip()]
            if not parts:
                raise ValueError("No time components provided.")
            for part in parts:
                if "=" not in part:
                    raise ValueError(f"Missing '=' in segment '{part}'.")
                key, value = part.split("=", 1)
                key = key.strip().lower()
                if key not in _KEY_UNITS:
                    raise ValueError(
                        f"Unsupported time unit in segment '{part.strip()}'."
                    )
                try:
                    amount = float(value.strip())
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid number for {key}: '{value.strip()}'"
                    ) from exc
                if amount < 0:
                    raise ValueError("Negative durations are not allowed.")
                if key in kwargs:
                    raise ValueError(
                        f"Duplicate unit '{key}' is not allowed."
                    )
                kwargs[key] = amount
            return timedelta(**kwargs)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Invalid time delta format: '{time_str}'. "
                "Use formats like 'weeks=2,days=3' or '2w3d4h5m6s'. "
                f"Error: {exc}"
            ) from exc

    compact_source = raw.replace(" ", "")
    matches = list(_COMPACT_DURATION_RE.finditer(compact_source))
    matched_len = sum(len(match.group(0)) for match in matches)
    if matches and matched_len == len(compact_source):
        totals: dict[str, float] = {}
        for match in matches:
            unit_key = match.group("unit").lower()
            target_unit = _COMPACT_UNIT_MAP[unit_key]
            amount = float(match.group("value"))
            if amount < 0:
                raise argparse.ArgumentTypeError(
                    "Negative durations are not allowed."
                )
            if target_unit in totals:
                raise argparse.ArgumentTypeError(
                    f"Duplicate unit '{unit_key}' is not allowed."
                )
            totals[target_unit] = amount
        return timedelta(**totals)

    raise argparse.ArgumentTypeError(
        f"Invalid time delta format: '{time_str}'. "
        "Use formats like 'weeks=2,days=3' or '2w3d4h5m6s'."
    )


__all__ = ["parse_random_range", "parse_time_delta"]
