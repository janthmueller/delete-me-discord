"""Typed effective runtime configuration."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from ..privacy import RedactionConfig


@dataclass(frozen=True)
class EffectiveCleanSettings:
    token: Optional[str]
    config_path: str
    profile: Optional[str]
    include_ids: list[str]
    exclude_ids: list[str]
    include_channel_types: list[str]
    include_thread_states: list[str]
    include_threads: bool
    exclude_channel_types: list[str]
    exclude_thread_states: list[str]
    exclude_threads: bool
    keep_last: int
    keep_last_scope: str
    keep_within: timedelta
    fetch_within: Optional[timedelta]
    max_messages: Optional[int]
    buffer_per_channel: bool
    keep_reactions: bool
    delete_owned_threads: str
    skip_unrestorable_threads: bool
    preserve_cache: bool
    preserve_cache_path: str
    max_retries: int
    retry_time_buffer: tuple[float, float]
    request_intervals: dict[str, tuple[float, float]]
    fetch_sleep_time: tuple[float, float]
    delete_sleep_time: tuple[float, float]
    dry_run: bool
    quiet: bool
    verbose: int
    json: bool
    redact_sensitive: Optional[RedactionConfig]
    redact_names: bool
