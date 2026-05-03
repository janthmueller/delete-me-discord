import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, Optional

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
    redact_names: bool


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
    "redact_names": True,
}


ProfileValueMode = Literal["cli-set", "stored", "runtime"]


PROFILE_FIELD_SPECS: list[dict[str, Any]] = [
    {"name": "include_ids", "type": "string list", "parser": "string_list", "nullable": False, "description": "Restrict cleanup to matching IDs."},
    {"name": "exclude_ids", "type": "string list", "parser": "string_list", "nullable": False, "description": "Exclude matching IDs from cleanup."},
    {"name": "keep_last", "type": "non-negative integer", "parser": "int", "nullable": False, "description": "Keep the last N messages in each channel."},
    {"name": "keep_last_scope", "type": "mine|all", "parser": "enum", "choices": ("mine", "all"), "nullable": False, "description": "Count keep_last against your messages or all recent messages."},
    {"name": "keep_within", "type": "time delta string", "parser": "time_delta", "nullable": False, "description": "Keep messages and reactions newer than this window."},
    {"name": "fetch_within", "type": "time delta string", "parser": "time_delta", "nullable": True, "description": "Only fetch messages newer than this window."},
    {"name": "max_messages", "type": "non-negative integer", "parser": "int", "nullable": True, "description": "Maximum messages to fetch per channel."},
    {"name": "buffer_per_channel", "type": "true|false", "parser": "bool", "nullable": False, "description": "Buffer one channel at a time before evaluation."},
    {"name": "keep_reactions", "type": "true|false", "parser": "bool", "nullable": False, "description": "Keep your reactions instead of removing them."},
    {"name": "preserve_cache", "type": "true|false", "parser": "bool", "nullable": False, "description": "Enable preserve cache between runs."},
    {"name": "preserve_cache_path", "type": "string path", "parser": "non_empty_string", "nullable": False, "description": "Override the preserve cache path."},
    {"name": "max_retries", "type": "non-negative integer", "parser": "int", "nullable": False, "description": "Maximum retry attempts for retryable API requests."},
    {"name": "retry_time_buffer", "type": "number list", "parser": "number_list", "nullable": False, "description": "One or two numbers added after rate limit waits."},
    {"name": "fetch_sleep_time", "type": "number list", "parser": "number_list", "nullable": False, "description": "One or two numbers for sleep between fetch requests."},
    {"name": "delete_sleep_time", "type": "number list", "parser": "number_list", "nullable": False, "description": "One or two numbers for sleep between delete actions."},
    {"name": "dry_run", "type": "true|false", "parser": "bool", "nullable": False, "description": "Simulate deletions without making changes."},
    {"name": "quiet", "type": "true|false", "parser": "bool", "nullable": False, "description": "Only show warnings and errors for runs using this profile."},
    {"name": "verbose", "type": "0..3 integer", "parser": "int", "nullable": False, "description": "Default verbosity level for runs using this profile."},
    {"name": "json", "type": "true|false", "parser": "bool", "nullable": False, "description": "Emit JSON output for runs using this profile."},
    {"name": "redact_sensitive", "type": "true|false, suffix, or prefix,suffix", "parser": "redact_sensitive", "nullable": False, "description": "Redact sensitive values in logs and discovery output."},
    {"name": "redact_names", "type": "true|false", "parser": "bool", "nullable": False, "description": "Redact human-readable names when sensitive redaction is enabled."},
]


_PROFILE_FIELD_NAMES = {spec["name"] for spec in PROFILE_FIELD_SPECS}
_PROFILE_FIELD_SPEC_BY_NAME = {spec["name"]: spec for spec in PROFILE_FIELD_SPECS}


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


def get_profile_field_specs() -> list[dict[str, Any]]:
    return [dict(spec) for spec in PROFILE_FIELD_SPECS]


def load_raw_profile(path: str, name: str) -> Any:
    profiles = _load_profiles_dict(path)
    if name not in profiles:
        raise ValueError(f"Unknown profile '{name}'.")
    return profiles[name]


def profile_requests_json_output(path: str, name: str) -> bool:
    profiles = _load_profiles_dict(path)
    raw = profiles.get(name)
    if not isinstance(raw, dict):
        return False
    if "json" not in raw:
        return False
    try:
        return _normalize_profile_value(f"Profile '{name}'", "json", raw["json"], mode="runtime") is True
    except ValueError:
        return False


def load_profile(path: str, name: str) -> dict[str, Any]:
    profiles = _load_profiles_dict(path)
    if name not in profiles:
        raise ValueError(f"Unknown profile '{name}'.")
    return _normalize_profile_data(f"Profile '{name}'", profiles[name], mode="runtime")


def parse_profile_set_assignments(assignments: list[str]) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for assignment in assignments:
        if "=" not in assignment:
            raise ValueError(
                f"Invalid --set value '{assignment}'. Expected key=value."
            )
        field, raw_value = assignment.split("=", 1)
        field = field.strip()
        if not field:
            raise ValueError(
                f"Invalid --set value '{assignment}'. Field name must not be empty."
            )
        if field in raw:
            raise ValueError(f"Duplicate --set field '{field}'.")
        raw[field] = _normalize_profile_value(
            "Profile input",
            field,
            raw_value,
            mode="cli-set",
        )
    return raw


def validate_profile_unset_fields(fields: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for field in fields:
        field = field.strip()
        if not field:
            raise ValueError("Profile unset field names must not be empty.")
        if field not in _PROFILE_FIELD_NAMES:
            raise ValueError(f"Unsupported profile field '{field}'.")
        if field not in seen:
            deduped.append(field)
            seen.add(field)
    return deduped


def add_profile(path: str, name: str, profile_data: dict[str, Any]) -> None:
    config = load_config(path)
    profiles = _mutable_profiles(config)
    if name in profiles:
        raise ValueError(f"Profile '{name}' already exists.")
    profiles[name] = _normalize_profile_data(f"Profile '{name}'", profile_data, mode="stored")
    _write_config(path, config)


def update_profile(
    path: str,
    name: str,
    profile_updates: dict[str, Any],
    unset_fields: list[str],
) -> None:
    config = load_config(path)
    profiles = _mutable_profiles(config)
    if name not in profiles:
        raise ValueError(f"Unknown profile '{name}'.")
    current_raw = profiles[name]
    if not isinstance(current_raw, dict):
        raise ValueError(f"Profile '{name}' must be a JSON object.")

    current = dict(current_raw)
    missing_unset_fields = [field for field in unset_fields if field not in current]
    if missing_unset_fields:
        joined = ", ".join(sorted(missing_unset_fields))
        raise ValueError(
            f"Profile '{name}' does not currently define field(s): {joined}."
        )
    for field in unset_fields:
        current.pop(field, None)
    current.update(profile_updates)
    profiles[name] = _normalize_profile_data(f"Profile '{name}'", current, mode="stored")
    _write_config(path, config)


def remove_profile(path: str, name: str) -> None:
    config = load_config(path)
    profiles = _mutable_profiles(config)
    if name not in profiles:
        raise ValueError(f"Unknown profile '{name}'.")
    del profiles[name]
    if not profiles:
        config.pop("profiles", None)
    _write_config(path, config)


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
        redact_names=args.redact_names,
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


def _normalize_profile_data(source_label: str, raw: Any, *, mode: ProfileValueMode) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{source_label} must be a JSON object.")

    unknown = sorted(set(raw.keys()) - _PROFILE_FIELD_NAMES)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"{source_label} contains unsupported field(s): {joined}.")

    normalized: dict[str, Any] = {}
    for field, value in raw.items():
        if value is None:
            raise ValueError(f"{source_label} field '{field}' must be omitted instead of null.")
        normalized[field] = _normalize_profile_value(source_label, field, value, mode=mode)

    return normalized


def _normalize_profile_value(source_label: str, field: str, value: Any, *, mode: ProfileValueMode) -> Any:
    spec = _PROFILE_FIELD_SPEC_BY_NAME.get(field)
    if spec is None:
        raise ValueError(f"Unsupported profile field '{field}'.")
    if mode == "cli-set" and spec["nullable"] and isinstance(value, str) and value.lower() == "none":
        return None
    if mode not in {"cli-set", "stored", "runtime"}:
        raise ValueError(f"Unsupported profile value mode '{mode}'.")

    parser_name = spec["parser"]
    if parser_name == "string_list":
        return _expect_string_list(source_label, field, _coerce_string_list(source_label, field, value))
    if parser_name == "number_list":
        return _expect_random_range(source_label, field, _coerce_number_list(source_label, field, value))
    if parser_name == "int":
        return _normalize_profile_int(source_label, field, value)
    if parser_name == "bool":
        return _normalize_profile_bool(source_label, field, value)
    if parser_name == "enum":
        return _normalize_profile_enum(source_label, field, value, spec["choices"])
    if parser_name == "time_delta":
        return _expect_timedelta(source_label, field, value) if mode == "runtime" else _expect_stored_timedelta(source_label, field, value)
    if parser_name == "non_empty_string":
        return _expect_str(source_label, field, value)
    if parser_name == "redact_sensitive":
        return _normalize_redaction_config(source_label, field, value, mode=mode)
    if parser_name == "string":
        return _expect_str(source_label, field, value)
    raise ValueError(f"Unsupported profile parser '{parser_name}' for field '{field}'.")


def _normalize_profile_int(source_label: str, field: str, value: Any) -> int:
    if isinstance(value, str):
        value = _parse_cli_int(value)
    level = _expect_non_negative_int(source_label, field, value)
    if field == "verbose" and level > 3:
        raise ValueError(f"{source_label} field 'verbose' must be between 0 and 3.")
    return level


def _normalize_profile_bool(source_label: str, field: str, value: Any) -> bool:
    if isinstance(value, str):
        value = _parse_cli_bool(value)
    return _expect_bool(source_label, field, value)


def _normalize_profile_enum(source_label: str, field: str, value: Any, choices: tuple[str, ...]) -> str:
    value = _expect_str(source_label, field, value)
    if value not in choices:
        rendered = " or ".join(f"'{choice}'" for choice in choices)
        raise ValueError(f"{source_label} field '{field}' must be {rendered}.")
    return value


def _expect_string_list(source_label: str, field: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{source_label} field '{field}' must be a list of strings.")
    return list(value)


def _expect_non_negative_int(source_label: str, field: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{source_label} field '{field}' must be a non-negative integer.")
    if value < 0:
        raise ValueError(f"{source_label} field '{field}' must be a non-negative integer.")
    return value


def _expect_timedelta(source_label: str, field: str, value: Any) -> Optional[timedelta]:
    try:
        if isinstance(value, (int, float)):
            value = str(value)
        if not isinstance(value, str):
            raise ValueError(f"{source_label} field '{field}' must be a string or zero-like number.")
        return parse_time_delta(value)
    except argparse.ArgumentTypeError as exc:
        raise ValueError(f"{source_label} field '{field}' is invalid: {exc}") from exc


def _expect_stored_timedelta(source_label: str, field: str, value: Any) -> str:
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        raise ValueError(f"{source_label} field '{field}' must be a string or zero-like number.")
    _expect_timedelta(source_label, field, value)
    return value


def _expect_bool(source_label: str, field: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{source_label} field '{field}' must be true or false.")
    return value


def _expect_str(source_label: str, field: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source_label} field '{field}' must be a non-empty string.")
    return value


def _expect_random_range(source_label: str, field: str, value: Any) -> list[float]:
    if not isinstance(value, list):
        raise ValueError(f"{source_label} field '{field}' must be a list with one or two numbers.")
    rendered = [str(item) for item in value]
    try:
        min_value, max_value = parse_random_range(rendered, field.replace("_", "-"))
    except argparse.ArgumentTypeError as exc:
        raise ValueError(f"{source_label} field '{field}' is invalid: {exc}") from exc
    if min_value == max_value:
        return [min_value]
    return [min_value, max_value]


def _expect_redaction_config(source_label: str, field: str, value: Any) -> Optional[RedactionConfig]:
    if isinstance(value, bool):
        return RedactionConfig(enabled=True) if value else None
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], int) and value[0] >= 0:
        return RedactionConfig(enabled=True, prefix=0, suffix=value[0])
    if isinstance(value, list) and len(value) == 2 and all(isinstance(item, int) and item >= 0 for item in value):
        return RedactionConfig(enabled=True, prefix=value[0], suffix=value[1])
    raise ValueError(
        f"{source_label} field '{field}' must be true, false, a one-integer suffix list like [4], or a two-integer list like [0, 4]."
    )


def _expect_stored_redaction_config(source_label: str, field: str, value: Any) -> bool | list[int]:
    if isinstance(value, bool):
        return value
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], int) and value[0] >= 0:
        return list(value)
    if isinstance(value, list) and len(value) == 2 and all(isinstance(item, int) and item >= 0 for item in value):
        return list(value)
    raise ValueError(
        f"{source_label} field '{field}' must be true, false, a one-integer suffix list like [4], or a two-integer list like [0, 4]."
    )


def _normalize_redaction_config(source_label: str, field: str, value: Any, *, mode: ProfileValueMode) -> Any:
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"true", "false"}:
            value = lowered == "true"
        elif value.strip().startswith("["):
            value = _parse_json_value(field, value)
        else:
            value = _coerce_redaction_window(source_label, field, value)
    if mode == "runtime":
        return _expect_redaction_config(source_label, field, value)
    return _expect_stored_redaction_config(source_label, field, value)


def _profile_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "profile"


def _mutable_profiles(config: dict[str, Any]) -> dict[str, Any]:
    profiles = config.get("profiles")
    if profiles is None:
        profiles = {}
        config["profiles"] = profiles
        return profiles
    if not isinstance(profiles, dict):
        raise ValueError("Config field 'profiles' must be a JSON object.")
    return profiles


def _write_config(path: str, config: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, sort_keys=True)
        f.write("\n")


def _parse_json_value(field: str, raw_value: str) -> Any:
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Profile input field '{field}' must use JSON syntax for this value type."
        ) from exc


def _coerce_string_list(source_label: str, field: str, value: Any) -> Any:
    if isinstance(value, str):
        if value.strip().startswith("["):
            return _parse_json_value(field, value)
        return _split_cli_list(source_label, field, value)
    return value


def _coerce_number_list(source_label: str, field: str, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if value.strip().startswith("["):
        return _parse_json_value(field, value)
    values = _split_cli_list(source_label, field, value)
    parsed: list[Any] = []
    for item in values:
        try:
            parsed.append(float(item))
        except ValueError:
            parsed.append(item)
    return parsed


def _coerce_redaction_window(source_label: str, field: str, value: str) -> list[Any]:
    values = _split_cli_list(source_label, field, value)
    parsed: list[Any] = []
    for item in values:
        try:
            parsed.append(int(item))
        except ValueError:
            parsed.append(item)
    return parsed


def _split_cli_list(source_label: str, field: str, raw_value: str) -> list[str]:
    values = [part.strip() for part in raw_value.replace(",", " ").split()]
    parsed = [value for value in values if value]
    if not parsed:
        raise ValueError(f"{source_label} field '{field}' must not be an empty list string.")
    return parsed


def _parse_cli_int(raw_value: str) -> int | str:
    try:
        return int(raw_value)
    except ValueError:
        return raw_value


def _parse_cli_bool(raw_value: str) -> bool | str:
    lowered = raw_value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return raw_value
