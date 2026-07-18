"""Profile loading, validation entry points, migration, and persistence."""

import json
import os
from typing import Any

from ..auth import DEFAULT_CONFIG_PATH
from ..storage import atomic_write_json
from .schema import (
    _PROFILE_FIELD_NAMES,
    _migrate_legacy_thread_fields,
    _normalize_profile_data,
    _normalize_profile_value,
)


def load_config(path: str = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    config_path = path or DEFAULT_CONFIG_PATH
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as file:
        raw = json.load(file)
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a JSON object.")
    return raw


def load_profile_names(path: str = DEFAULT_CONFIG_PATH) -> list[str]:
    profiles = _load_profiles_dict(path)
    return sorted(profiles.keys())


def load_raw_profile(path: str, name: str) -> Any:
    profiles = _load_profiles_dict(path)
    if name not in profiles:
        raise ValueError(f"Unknown profile '{name}'.")
    return profiles[name]


def profile_requests_json_output(path: str, name: str) -> bool:
    profiles = _load_profiles_dict(path)
    raw = profiles.get(name)
    if not isinstance(raw, dict) or "json" not in raw:
        return False
    try:
        return (
            _normalize_profile_value(
                f"Profile '{name}'",
                "json",
                raw["json"],
                mode="runtime",
            )
            is True
        )
    except ValueError:
        return False


def load_profile(path: str, name: str) -> dict[str, Any]:
    profiles = _load_profiles_dict(path)
    if name not in profiles:
        raise ValueError(f"Unknown profile '{name}'.")
    return _normalize_profile_data(
        f"Profile '{name}'",
        profiles[name],
        mode="runtime",
    )


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
    profiles[name] = _normalize_profile_data(
        f"Profile '{name}'",
        profile_data,
        mode="stored",
    )
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

    current = dict(
        _migrate_legacy_thread_fields(f"Profile '{name}'", current_raw)
    )
    missing_unset_fields = [
        field for field in unset_fields if field not in current
    ]
    if missing_unset_fields:
        joined = ", ".join(sorted(missing_unset_fields))
        raise ValueError(
            f"Profile '{name}' does not currently define field(s): {joined}."
        )
    for field in unset_fields:
        current.pop(field, None)
    current.update(profile_updates)
    profiles[name] = _normalize_profile_data(
        f"Profile '{name}'",
        current,
        mode="stored",
    )
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


def _load_profiles_dict(path: str) -> dict[str, Any]:
    config = load_config(path)
    profiles = config.get("profiles")
    if profiles is None:
        return {}
    if not isinstance(profiles, dict):
        raise ValueError("Config field 'profiles' must be a JSON object.")
    return profiles


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
    atomic_write_json(path, config)
