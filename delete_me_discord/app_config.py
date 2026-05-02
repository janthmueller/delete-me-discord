import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

from .auth import DEFAULT_CONFIG_PATH
from .preserve_cache import DEFAULT_PRESERVE_CACHE_PATH
from .privacy import RedactionConfig
from .utils import parse_random_range, parse_time_delta


@dataclass(frozen=True)
class EffectiveCleanSettings:
    token: Optional[str]
    config_path: str
    profile: Optional[str]
    include_ids: list[str]
    exclude_ids: list[str]
    keep_last: int
    keep_last_scope: str
    keep_within: timedelta
    fetch_within: Optional[timedelta]
    max_messages: Optional[int]
    buffer_per_channel: bool
    keep_reactions: bool
    preserve_cache: bool
    preserve_cache_path: str
    max_retries: int
    retry_time_buffer: tuple[float, float]
    fetch_sleep_time: tuple[float, float]
    delete_sleep_time: tuple[float, float]
    dry_run: bool
    quiet: bool
    verbose: int
    json: bool
    redact_sensitive: Optional[RedactionConfig]


CLEAN_ARG_DEFAULTS: dict[str, Any] = {
    "token": None,
    "config_path": DEFAULT_CONFIG_PATH,
    "profile": None,
    "include_ids": [],
    "exclude_ids": [],
    "keep_last": 0,
    "keep_last_scope": "all",
    "keep_within": timedelta(0),
    "fetch_within": None,
    "max_messages": None,
    "buffer_per_channel": False,
    "keep_reactions": False,
    "preserve_cache": False,
    "preserve_cache_path": DEFAULT_PRESERVE_CACHE_PATH,
    "max_retries": 5,
    "retry_time_buffer": [25, 35],
    "fetch_sleep_time": [0.2, 0.4],
    "delete_sleep_time": [1.5, 2],
    "dry_run": False,
    "quiet": False,
    "verbose": 0,
    "json": False,
    "redact_sensitive": None,
}


_PROFILE_FIELD_NAMES = {
    "include_ids",
    "exclude_ids",
    "keep_last",
    "keep_last_scope",
    "keep_within",
    "fetch_within",
    "max_messages",
    "buffer_per_channel",
    "keep_reactions",
    "preserve_cache",
    "preserve_cache_path",
    "max_retries",
    "retry_time_buffer",
    "fetch_sleep_time",
    "delete_sleep_time",
    "dry_run",
    "quiet",
    "verbose",
    "json",
    "redact_sensitive",
}


def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    config_path = path or DEFAULT_CONFIG_PATH
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a JSON object.")
    return raw


def load_profile_names(path: str = DEFAULT_CONFIG_PATH) -> list[str]:
    profiles = _load_profiles_dict(path)
    return sorted(profiles.keys())


def load_profile(path: str, name: str) -> dict[str, Any]:
    profiles = _load_profiles_dict(path)
    if name not in profiles:
        raise ValueError(f"Unknown profile '{name}'.")
    return _validate_profile(name, profiles[name])


def build_clean_defaults(profile_name: Optional[str], profile_defaults: Optional[dict[str, Any]]) -> dict[str, Any]:
    merged = {key: _copy_default(value) for key, value in CLEAN_ARG_DEFAULTS.items()}
    if profile_name:
        merged["profile"] = profile_name
    if profile_defaults:
        merged.update(profile_defaults)
    merged["preserve_cache_path"] = _resolve_profile_default_preserve_cache_path(
        profile_name=profile_name,
        preserve_cache_enabled=bool(merged["preserve_cache"]),
        profile_defaults=profile_defaults,
    )
    return merged


def resolve_effective_clean_settings(args) -> EffectiveCleanSettings:
    return EffectiveCleanSettings(
        token=args.token,
        config_path=args.config_path,
        profile=args.profile,
        include_ids=list(args.include_ids),
        exclude_ids=list(args.exclude_ids),
        keep_last=args.keep_last,
        keep_last_scope=args.keep_last_scope,
        keep_within=args.keep_within,
        fetch_within=args.fetch_within,
        max_messages=args.max_messages,
        buffer_per_channel=args.buffer_per_channel,
        keep_reactions=args.keep_reactions,
        preserve_cache=args.preserve_cache,
        preserve_cache_path=_apply_dry_run_suffix(args.preserve_cache_path, bool(args.dry_run)),
        max_retries=args.max_retries,
        retry_time_buffer=parse_random_range(args.retry_time_buffer, "retry-time-buffer"),
        fetch_sleep_time=parse_random_range(args.fetch_sleep_time, "fetch-sleep-time"),
        delete_sleep_time=parse_random_range(args.delete_sleep_time, "delete-sleep-time"),
        dry_run=args.dry_run,
        quiet=args.quiet,
        verbose=args.verbose,
        json=args.json,
        redact_sensitive=args.redact_sensitive,
    )


def _resolve_profile_default_preserve_cache_path(
    profile_name: Optional[str],
    preserve_cache_enabled: bool,
    profile_defaults: Optional[dict[str, Any]],
) -> str:
    if not preserve_cache_enabled or not profile_name:
        return DEFAULT_PRESERVE_CACHE_PATH if not profile_defaults else profile_defaults.get(
            "preserve_cache_path",
            DEFAULT_PRESERVE_CACHE_PATH,
        )

    if profile_defaults and "preserve_cache_path" in profile_defaults:
        return profile_defaults["preserve_cache_path"]
    return _derive_profile_preserve_cache_path(profile_name)


def _apply_dry_run_suffix(path: str, dry_run: bool) -> str:
    if not dry_run:
        return path
    base, ext = os.path.splitext(path)
    return f"{base}.dryrun{ext or '.json'}"


def _copy_default(value: Any) -> Any:
    if isinstance(value, list):
        return list(value)
    return value


def _derive_profile_preserve_cache_path(profile_name: str) -> str:
    safe_name = _profile_filename(profile_name)
    return os.path.join(
        os.path.expanduser("~"),
        ".config",
        "delete-me-discord",
        "preserve-cache",
        f"{safe_name}.json",
    )


def _load_profiles_dict(path: str) -> dict[str, Any]:
    config = load_config(path)
    profiles = config.get("profiles")
    if profiles is None:
        return {}
    if not isinstance(profiles, dict):
        raise ValueError("Config field 'profiles' must be a JSON object.")
    return profiles


def _validate_profile(name: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"Profile '{name}' must be a JSON object.")

    unknown = sorted(set(raw.keys()) - _PROFILE_FIELD_NAMES)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"Profile '{name}' contains unsupported field(s): {joined}.")

    normalized: dict[str, Any] = {}
    for field, value in raw.items():
        if value is None:
            raise ValueError(f"Profile '{name}' field '{field}' must be omitted instead of null.")

        if field in {"include_ids", "exclude_ids"}:
            normalized[field] = _expect_string_list(name, field, value)
        elif field == "keep_last":
            normalized[field] = _expect_non_negative_int(name, field, value)
        elif field == "keep_last_scope":
            if value not in {"mine", "all"}:
                raise ValueError(f"Profile '{name}' field 'keep_last_scope' must be 'mine' or 'all'.")
            normalized[field] = value
        elif field in {"keep_within", "fetch_within"}:
            normalized[field] = _expect_timedelta(name, field, value)
        elif field == "max_messages":
            normalized[field] = _expect_non_negative_int(name, field, value)
        elif field in {
            "buffer_per_channel",
            "keep_reactions",
            "preserve_cache",
            "dry_run",
            "quiet",
            "json",
        }:
            normalized[field] = _expect_bool(name, field, value)
        elif field == "preserve_cache_path":
            normalized[field] = _expect_str(name, field, value)
        elif field == "max_retries":
            normalized[field] = _expect_non_negative_int(name, field, value)
        elif field in {"retry_time_buffer", "fetch_sleep_time", "delete_sleep_time"}:
            normalized[field] = _expect_random_range(name, field, value)
        elif field == "verbose":
            level = _expect_non_negative_int(name, field, value)
            if level > 3:
                raise ValueError(f"Profile '{name}' field 'verbose' must be between 0 and 3.")
            normalized[field] = level
        elif field == "redact_sensitive":
            normalized[field] = _expect_redaction_config(name, field, value)
        else:  # pragma: no cover - defensive
            raise ValueError(f"Profile '{name}' field '{field}' is not supported.")

    return normalized


def _expect_string_list(profile_name: str, field: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Profile '{profile_name}' field '{field}' must be a list of strings.")
    return list(value)


def _expect_non_negative_int(profile_name: str, field: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Profile '{profile_name}' field '{field}' must be a non-negative integer.")
    if value < 0:
        raise ValueError(f"Profile '{profile_name}' field '{field}' must be a non-negative integer.")
    return value


def _expect_timedelta(profile_name: str, field: str, value: Any) -> Optional[timedelta]:
    try:
        if isinstance(value, (int, float)):
            value = str(value)
        if not isinstance(value, str):
            raise ValueError("must be a string or zero-like number")
        return parse_time_delta(value)
    except argparse.ArgumentTypeError as exc:
        raise ValueError(f"Profile '{profile_name}' field '{field}' is invalid: {exc}") from exc


def _expect_bool(profile_name: str, field: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"Profile '{profile_name}' field '{field}' must be true or false.")
    return value


def _expect_str(profile_name: str, field: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Profile '{profile_name}' field '{field}' must be a non-empty string.")
    return value


def _expect_random_range(profile_name: str, field: str, value: Any) -> list[float]:
    if not isinstance(value, list):
        raise ValueError(f"Profile '{profile_name}' field '{field}' must be a list with one or two numbers.")
    rendered = [str(item) for item in value]
    try:
        min_value, max_value = parse_random_range(rendered, field.replace("_", "-"))
    except argparse.ArgumentTypeError as exc:
        raise ValueError(f"Profile '{profile_name}' field '{field}' is invalid: {exc}") from exc
    if min_value == max_value:
        return [min_value]
    return [min_value, max_value]


def _expect_redaction_config(profile_name: str, field: str, value: Any) -> Optional[RedactionConfig]:
    if isinstance(value, bool):
        return RedactionConfig(enabled=True) if value else None
    if isinstance(value, list) and len(value) == 2 and all(isinstance(item, int) and item >= 0 for item in value):
        return RedactionConfig(enabled=True, prefix=value[0], suffix=value[1])
    raise ValueError(
        f"Profile '{profile_name}' field '{field}' must be true, false, or a two-integer list like [0, 4]."
    )


def _profile_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "profile"
