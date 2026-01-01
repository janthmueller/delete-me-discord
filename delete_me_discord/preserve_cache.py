# delete_me_discord/preserve_cache.py

import json
import logging
import os
from typing import Dict, List, Any

DEFAULT_PRESERVE_CACHE_PATH = os.path.join(
    os.path.expanduser("~"),
    ".config",
    "delete-me-discord",
    "preserve_cache.json",
)


class PreserveCache:
    """
    Minimal cache to persist message IDs per channel between runs so they can be
    re-fetched and re-evaluated (for deletes and reaction removals).
    """

    SCHEMA_VERSION = 1

    def __init__(
        self,
        path: str = DEFAULT_PRESERVE_CACHE_PATH,
    ):
        self.path = path or DEFAULT_PRESERVE_CACHE_PATH
        self.logger = logging.getLogger(self.__class__.__name__)
        self.data: Dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "channels": {},
        }
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            self.logger.info("Preserve cache not found at %s; a new one will be created on save.", self.path)
            return

        self.logger.info("Loading preserve cache from %s.", self.path)
        with open(self.path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"Preserve cache root must be a JSON object; got {type(raw).__name__}")

        schema_version = raw.get("schema_version")
        if schema_version != self.SCHEMA_VERSION:
            raise ValueError(
                f"Preserve cache schema mismatch (found {schema_version}, expected {self.SCHEMA_VERSION}). "
                f"Delete the cache file ({self.path}) or run with --wipe-preserve-cache or --preserve-cache-path "
                f"to use a fresh file."
            )

        channels = raw.get("channels")
        if not isinstance(channels, dict):
            raise ValueError("Preserve cache is missing a 'channels' object.")
        # We expect keys as strings and values as lists of IDs.
        for k, v in channels.items():
            if not isinstance(v, list):
                raise ValueError(f"Preserve cache entry for channel {k} must be a list of IDs.")
        self.data["channels"] = channels

    def get_ids(self, channel_id: str) -> List[str]:
        return self.data["channels"].get(str(channel_id), [])

    def set_ids(self, channel_id: str, message_ids: List[str]) -> None:
        # Store a copy to avoid caller mutations; keep order as seen.
        deduped = list(dict.fromkeys([str(mid) for mid in message_ids]))
        self.data["channels"][str(channel_id)] = deduped

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "channels": self.data.get("channels", {}),
        }
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.error("Failed to write preserve cache at %s: %s", self.path, exc)
            raise
