#!/usr/bin/env python3
"""Opt-in orchestration helpers for the live Discord test suite."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.discord.client import DiscordClient  # noqa: E402
from delete_me_discord.cleanup import MessageCleaner  # noqa: E402
from delete_me_discord.storage import atomic_write_json  # noqa: E402
from delete_me_discord.discord.channel_types import ChannelType  # noqa: E402
from delete_me_discord.privacy import (  # noqa: E402
    RedactionConfig,
    get_redaction_config,
    set_redaction_config,
)
from delete_me_discord.scope import ScopeInventory  # noqa: E402
from delete_me_discord.cleanup.threads import (  # noqa: E402
    ThreadRestorationJournal,
)
from delete_me_discord.discord.type_enums import MessageType  # noqa: E402
from tests.live.fixture_client import (  # noqa: E402
    DiscordFixtureClient,
    FixtureClientError,
    FixtureLockError,
    FixturePacer,
    FixturePacingPolicy,
    GuildChannelSnapshot,
    GuildConfigurationSnapshot,
    GuildMemberSnapshot,
    GuildRoleSnapshot,
    PermissionOverwriteSnapshot,
    SuiteLock,
)


DEFAULT_SECRETS_PATH = PROJECT_ROOT / "tests" / "live" / "secrets.env"
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "tests" / "live" / "state" / "ledger.json"
DEFAULT_LOCK_PATH = PROJECT_ROOT / "tests" / "live" / "state" / "suite.lock"
DEFAULT_MEMBERSHIP_INVITES_PATH = (
    PROJECT_ROOT / "tests" / "live" / "state" / "membership-invites.json"
)
DEFAULT_ARCHIVED_THREAD_RACE_JOURNAL_PATH = (
    PROJECT_ROOT
    / "tests"
    / "live"
    / "state"
    / "archived-thread-race-restoration.json"
)
DISCORD_API_BASE_URL = "https://discord.com/api/v10"
DISCORD_CURRENT_USER_URL = f"{DISCORD_API_BASE_URL}/users/@me"
LEDGER_SCHEMA_VERSION = 1
TOKEN_KEY_PATTERN = re.compile(r"TOKEN_([A-Za-z0-9_]+)\Z")
RUN_ID_PATTERN = re.compile(r"dmd-live-\d{8}T\d{6}Z-[0-9a-f]{8}\Z")
TERMINAL_RESOURCE_STATES = frozenset({"absent", "deleted"})
FIXTURE_ROLES = ("owner", "subject", "peer_a", "peer_b")
FORUM_STARTER_THREAD_KEY = "thread:smoke:forum-starter"
FORUM_STARTER_MESSAGE_KEY = "message:smoke:forum-starter"
FORUM_STARTER_CONTAINER_CAPABILITY = "forum-starter-delete:container"
GUILD_FIXTURES = (
    ("guild:matrix", "matrix"),
    ("guild:permission", "permission"),
)
MANAGE_THREADS_PERMISSION = 1 << 34
VIEW_CHANNEL_PERMISSION = 1 << 10
SEND_MESSAGES_PERMISSION = 1 << 11
READ_MESSAGE_HISTORY_PERMISSION = 1 << 16


@dataclass(frozen=True)
class RoleFixture:
    fixture_key: str
    guild_fixture_key: str
    name: str
    permissions: int
    assigned_accounts: tuple[str, ...]


@dataclass(frozen=True)
class ForumStarterObservation:
    thread_exists: bool
    message_exists: bool


@dataclass(frozen=True)
class DestructiveContractScope:
    scope_key: str
    fixture_key: str
    resource_kind: str
    channel_type: ChannelType
    archived: bool | None = None
    optional: bool = False


@dataclass(frozen=True)
class ArchivedThreadRaceScenario:
    scenario_key: str
    target_role: str
    trigger: str
    initial_locked: bool
    expect_cleanup: bool
    expected_hook_count: int


@dataclass(frozen=True, repr=False)
class DestructiveContractObservation:
    container_exists: bool
    archived: bool | None
    message_authors: Mapping[str, str]
    deletable_message_ids: frozenset[str]
    subject_reactions_on_foreign_messages: int
    foreign_reactions_on_foreign_messages: int
    locked: bool | None = None
    auto_archive_duration: int | None = None

    def __repr__(self) -> str:
        return (
            "DestructiveContractObservation("
            f"container_exists={self.container_exists!r}, "
            f"archived={self.archived!r}, "
            f"messages={len(self.message_authors)}, "
            f"deletable_messages={len(self.deletable_message_ids)}, "
            "subject_reactions_on_foreign_messages="
            f"{self.subject_reactions_on_foreign_messages}, "
            "foreign_reactions_on_foreign_messages="
            f"{self.foreign_reactions_on_foreign_messages}, "
            f"locked={self.locked!r}, "
            f"auto_archive_duration={self.auto_archive_duration!r})"
        )


@dataclass(frozen=True)
class ChannelFixture:
    fixture_key: str
    guild_fixture_key: str
    name: str
    channel_type: ChannelType
    parent_fixture_key: str | None = None
    permission_profile: str = "open"


@dataclass(frozen=True)
class ThreadMatrixFixture:
    scope_key: str
    fixture_key: str
    parent_fixture_key: str
    parent_kind: str
    thread_type: ChannelType
    creation_mode: str
    archived: bool
    optional_parent: bool = False


ROLE_FIXTURES = (
    RoleFixture(
        "role:matrix:member",
        "guild:matrix",
        "dmd-live-member",
        0,
        ("subject", "peer_a", "peer_b"),
    ),
    RoleFixture(
        "role:permission:member",
        "guild:permission",
        "dmd-live-member",
        0,
        ("subject", "peer_a", "peer_b"),
    ),
    RoleFixture(
        "role:permission:thread-manager",
        "guild:permission",
        "dmd-live-thread-manager",
        MANAGE_THREADS_PERMISSION,
        ("peer_b",),
    ),
    RoleFixture(
        "role:permission:restricted-reader",
        "guild:permission",
        "dmd-live-restricted-reader",
        0,
        ("peer_a",),
    ),
)

BASE_CHANNEL_FIXTURES = (
    ChannelFixture(
        "channel:matrix:category",
        "guild:matrix",
        "dmd-live-matrix",
        ChannelType.GUILD_CATEGORY,
    ),
    ChannelFixture(
        "channel:matrix:lobby",
        "guild:matrix",
        "dmd-live-lobby",
        ChannelType.GUILD_TEXT,
        "channel:matrix:category",
    ),
    ChannelFixture(
        "channel:permission:category",
        "guild:permission",
        "dmd-live-permission",
        ChannelType.GUILD_CATEGORY,
    ),
    ChannelFixture(
        "channel:permission:lobby",
        "guild:permission",
        "dmd-live-lobby",
        ChannelType.GUILD_TEXT,
        "channel:permission:category",
    ),
)

STANDARD_CHANNEL_FIXTURES = (
    ChannelFixture(
        "channel:matrix:text",
        "guild:matrix",
        "dmd-live-text",
        ChannelType.GUILD_TEXT,
        "channel:matrix:category",
    ),
    ChannelFixture(
        "channel:matrix:rules",
        "guild:matrix",
        "dmd-live-rules",
        ChannelType.GUILD_TEXT,
        "channel:matrix:category",
    ),
    ChannelFixture(
        "channel:matrix:updates",
        "guild:matrix",
        "dmd-live-updates",
        ChannelType.GUILD_TEXT,
        "channel:matrix:category",
    ),
    ChannelFixture(
        "channel:matrix:voice",
        "guild:matrix",
        "dmd-live-voice",
        ChannelType.GUILD_VOICE,
        "channel:matrix:category",
    ),
    ChannelFixture(
        "channel:permission:threads",
        "guild:permission",
        "dmd-live-threads",
        ChannelType.GUILD_TEXT,
        "channel:permission:category",
    ),
    ChannelFixture(
        "channel:permission:restricted",
        "guild:permission",
        "dmd-live-restricted",
        ChannelType.GUILD_TEXT,
        "channel:permission:category",
        "restricted-reader",
    ),
)

COMMUNITY_CHANNEL_FIXTURES = (
    ChannelFixture(
        "channel:matrix:announcement",
        "guild:matrix",
        "dmd-live-announcements",
        ChannelType.GUILD_ANNOUNCEMENT,
        "channel:matrix:category",
    ),
    ChannelFixture(
        "channel:matrix:stage",
        "guild:matrix",
        "dmd-live-stage",
        ChannelType.GUILD_STAGE_VOICE,
        "channel:matrix:category",
    ),
    ChannelFixture(
        "channel:matrix:forum",
        "guild:matrix",
        "dmd-live-forum",
        ChannelType.GUILD_FORUM,
        "channel:matrix:category",
    ),
    ChannelFixture(
        "channel:matrix:media",
        "guild:matrix",
        "dmd-live-media",
        ChannelType.GUILD_MEDIA,
        "channel:matrix:category",
    ),
)


class LiveSuiteError(RuntimeError):
    """Base error for safe, user-facing live-suite failures."""


class SecretConfigurationError(LiveSuiteError):
    """Raised when account secrets are missing or insecurely configured."""


class LiveSuiteSafetyError(LiveSuiteError):
    """Raised when a live operation violates a ledger safety contract."""


def _parse_token_value(value: str, *, location: str) -> str:
    value = value.strip()
    if value.startswith(("'", '"')):
        quote = value[0]
        if len(value) < 2 or not value.endswith(quote):
            raise SecretConfigurationError(
                f"Mismatched quotes in token entry at {location}."
            )
        value = value[1:-1]
    elif value.endswith(("'", '"')):
        raise SecretConfigurationError(
            f"Mismatched quotes in token entry at {location}."
        )

    if not value:
        raise SecretConfigurationError(f"Empty token entry at {location}.")
    if any(character.isspace() for character in value):
        raise SecretConfigurationError(
            f"Token entry contains whitespace at {location}."
        )
    return value


def read_secret_file(path: Path) -> dict[str, str]:
    """Parse an owner-only TOKEN_* file without executing it as shell code."""
    path = path.expanduser()
    if path.is_symlink():
        raise SecretConfigurationError(f"Secret path must not be a symlink: {path}")
    path = path.resolve()
    if not path.exists():
        raise SecretConfigurationError(f"Secret file does not exist: {path}")
    if not path.is_file():
        raise SecretConfigurationError(f"Secret path must be a regular file: {path}")

    if os.name != "nt":
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise SecretConfigurationError(
                f"Secret file permissions must be 0600, found {mode:04o}: {path}"
            )

    tokens: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SecretConfigurationError(
                f"Malformed secret entry at {path}:{line_number}."
            )

        name, raw_value = line.split("=", 1)
        name = name.strip()
        match = TOKEN_KEY_PATTERN.fullmatch(name)
        if match is None:
            raise SecretConfigurationError(
                f"Unsupported secret entry at {path}:{line_number}; expected TOKEN_<alias>."
            )
        alias = match.group(1).lower()
        if alias in tokens:
            raise SecretConfigurationError(
                f"Duplicate account entry at {path}:{line_number}."
            )
        tokens[alias] = _parse_token_value(
            raw_value,
            location=f"{path}:{line_number}",
        )
    return tokens


def load_account_tokens(
    secret_file: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Load TOKEN_* values from a private file and environment overrides."""
    tokens = read_secret_file(secret_file) if secret_file is not None else {}
    source = os.environ if environ is None else environ
    for name, raw_value in sorted(source.items()):
        match = TOKEN_KEY_PATTERN.fullmatch(name)
        if match is None:
            continue
        alias = match.group(1).lower()
        tokens[alias] = _parse_token_value(raw_value, location="environment")

    if not tokens:
        raise SecretConfigurationError(
            "No TOKEN_<alias> account secrets were configured."
        )

    seen_tokens: set[str] = set()
    for token in tokens.values():
        if token in seen_tokens:
            raise SecretConfigurationError(
                "Multiple account entries use the same token."
            )
        seen_tokens.add(token)
    return tokens


def require_fixture_roles(tokens: Mapping[str, str]) -> dict[str, str]:
    """Require the four generic fixture roles without exposing configured keys."""
    if set(tokens) != set(FIXTURE_ROLES):
        raise SecretConfigurationError(
            "Live secrets must define exactly the four generic roles from secrets.env.example."
        )
    return {role: tokens[role] for role in FIXTURE_ROLES}


@dataclass(frozen=True, repr=False)
class AccountCheck:
    fixture_role: str
    status_code: int | None
    user_id: str | None = None
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.user_id is not None and self.error is None

    def __repr__(self) -> str:
        return (
            "AccountCheck("
            f"status_code={self.status_code!r}, valid={self.valid!r}, error={self.error!r}"
            ")"
        )


@dataclass(frozen=True, repr=False)
class AccountValidationReport:
    checks: tuple[AccountCheck, ...]

    @property
    def valid_count(self) -> int:
        return sum(check.valid for check in self.checks)

    @property
    def ledger_identities(self) -> dict[str, str]:
        return {
            check.fixture_role: check.user_id
            for check in self.checks
            if check.valid and check.user_id is not None
        }

    def __repr__(self) -> str:
        return (
            "AccountValidationReport("
            f"accounts={len(self.checks)}, valid={self.valid_count}"
            ")"
        )

    def require_ready(self, expected_accounts: int) -> None:
        if len(self.checks) != expected_accounts:
            raise LiveSuiteError(
                f"Expected {expected_accounts} configured accounts, found {len(self.checks)}."
            )
        failed_count = sum(not check.valid for check in self.checks)
        if failed_count:
            raise LiveSuiteError(
                f"Account validation failed for {failed_count} account(s)."
            )
        if len(self.ledger_identities) != expected_accounts:
            raise LiveSuiteError(
                "Configured tokens do not represent distinct Discord accounts."
            )


def validate_account_tokens(
    tokens: Mapping[str, str],
    *,
    session: Any | None = None,
    timeout: tuple[float, float] = (10.0, 20.0),
    pacer: FixturePacer | None = None,
) -> AccountValidationReport:
    """Validate account tokens through a read-only Discord endpoint."""
    owns_session = session is None
    client = requests.Session() if session is None else session
    request_pacer = pacer or FixturePacer()
    checks: list[AccountCheck] = []
    seen_user_ids: set[str] = set()

    try:
        for fixture_role, token in tokens.items():
            request_pacer.wait_before_request("GET")
            try:
                response = client.get(
                    DISCORD_CURRENT_USER_URL,
                    headers={
                        "Authorization": token,
                        "User-Agent": "delete-me-discord-live-suite/1.0",
                    },
                    timeout=timeout,
                )
            except requests.RequestException:
                request_pacer.note_request_finished()
                checks.append(
                    AccountCheck(
                        fixture_role=fixture_role,
                        status_code=None,
                        error="transport error",
                    )
                )
                continue
            request_pacer.note_request_finished()

            status_code = int(response.status_code)
            if status_code != 200:
                checks.append(
                    AccountCheck(
                        fixture_role=fixture_role,
                        status_code=status_code,
                        error=f"HTTP {status_code}",
                    )
                )
                continue

            try:
                payload = response.json()
            except (TypeError, ValueError):
                payload = None
            user_id = payload.get("id") if isinstance(payload, dict) else None
            if not isinstance(user_id, str) or not user_id:
                checks.append(
                    AccountCheck(
                        fixture_role=fixture_role,
                        status_code=status_code,
                        error="malformed response",
                    )
                )
                continue

            if user_id in seen_user_ids:
                checks.append(
                    AccountCheck(
                        fixture_role=fixture_role,
                        status_code=status_code,
                        error="duplicates another configured account",
                    )
                )
                continue
            seen_user_ids.add(user_id)
            checks.append(
                AccountCheck(
                    fixture_role=fixture_role,
                    status_code=status_code,
                    user_id=user_id,
                )
            )
    finally:
        if owns_session:
            client.close()

    return AccountValidationReport(checks=tuple(checks))


def observe_forum_starter_state(
    token: str,
    thread_id: str,
    message_id: str,
    parent_id: str,
    *,
    client: Any | None = None,
    pacer: FixturePacer | None = None,
) -> ForumStarterObservation:
    """Observe one forum starter without exposing Discord response content."""
    owns_client = client is None
    session = client or requests.Session()
    request_pacer = pacer or FixturePacer()
    headers = {
        "Authorization": token,
        "User-Agent": "delete-me-discord-live-suite/1.0",
    }

    def request(path: str, *, params: Mapping[str, Any] | None = None):
        request_pacer.wait_before_request("GET")
        try:
            response = session.get(
                f"{DISCORD_API_BASE_URL}{path}",
                headers=headers,
                params=params,
                timeout=(10.0, 30.0),
            )
        except requests.RequestException:
            raise LiveSuiteSafetyError(
                "Forum starter observation ended with transport uncertainty."
            ) from None
        finally:
            request_pacer.note_request_finished()
        if response.status_code not in {200, 404}:
            raise LiveSuiteSafetyError(
                f"Forum starter observation failed (HTTP {response.status_code})."
            )
        return response

    try:
        thread_response = request(f"/channels/{thread_id}")
        if thread_response.status_code == 404:
            return ForumStarterObservation(
                thread_exists=False,
                message_exists=False,
            )
        try:
            thread_payload = thread_response.json()
        except (TypeError, ValueError):
            thread_payload = None
        if (
            not isinstance(thread_payload, Mapping)
            or str(thread_payload.get("id")) != thread_id
            or str(thread_payload.get("parent_id")) != parent_id
            or thread_payload.get("type") != int(ChannelType.PUBLIC_THREAD)
        ):
            raise LiveSuiteSafetyError(
                "Discord returned an unexpected forum starter thread."
            )

        message_response = request(
            f"/channels/{thread_id}/messages",
            params={"around": message_id, "limit": 1},
        )
        if message_response.status_code == 404:
            return ForumStarterObservation(
                thread_exists=False,
                message_exists=False,
            )
        try:
            message_payload = message_response.json()
        except (TypeError, ValueError):
            message_payload = None
        if not isinstance(message_payload, list):
            raise LiveSuiteSafetyError(
                "Discord returned an unexpected forum starter message result."
            )
        message_exists = any(
            isinstance(item, Mapping) and str(item.get("id")) == message_id
            for item in message_payload
        )
        return ForumStarterObservation(
            thread_exists=True,
            message_exists=message_exists,
        )
    finally:
        if owns_client:
            session.close()


def new_run_id(now: datetime | None = None) -> str:
    timestamp = now or datetime.now(timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    return f"dmd-live-{timestamp:%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, repr=False)
class LedgerResource:
    run_id: str
    fixture_key: str
    kind: str
    resource_id: str
    owner_handle: str
    guild_id: str | None = None
    parent_id: str | None = None
    state: str = "active"

    def __repr__(self) -> str:
        return f"LedgerResource(kind={self.kind!r}, state={self.state!r})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "fixture_key": self.fixture_key,
            "kind": self.kind,
            "id": self.resource_id,
            "owner_handle": self.owner_handle,
            "guild_id": self.guild_id,
            "parent_id": self.parent_id,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> LedgerResource:
        required = ("run_id", "fixture_key", "kind", "id", "owner_handle", "state")
        values: dict[str, str] = {}
        for key in required:
            value = payload.get(key)
            if not isinstance(value, str) or not value:
                raise LiveSuiteSafetyError(
                    f"Ledger resource field {key!r} must be a non-empty string."
                )
            values[key] = value
        guild_id = payload.get("guild_id")
        parent_id = payload.get("parent_id")
        if guild_id is not None and not isinstance(guild_id, str):
            raise LiveSuiteSafetyError(
                "Ledger resource guild_id must be a string or null."
            )
        if parent_id is not None and not isinstance(parent_id, str):
            raise LiveSuiteSafetyError(
                "Ledger resource parent_id must be a string or null."
            )
        return cls(
            run_id=values["run_id"],
            fixture_key=values["fixture_key"],
            kind=values["kind"],
            resource_id=values["id"],
            owner_handle=values["owner_handle"],
            guild_id=guild_id,
            parent_id=parent_id,
            state=values["state"],
        )


@dataclass(repr=False)
class LiveLedger:
    run_id: str
    created_at: str
    updated_at: str
    phase: str
    accounts: dict[str, str]
    resources: list[LedgerResource]
    destructive_unlocked: bool = False
    capabilities: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            "LiveLedger("
            f"run_id={self.run_id!r}, phase={self.phase!r}, "
            f"accounts={len(self.accounts)}, resources={len(self.resources)}, "
            f"capabilities={len(self.capabilities)}, "
            f"destructive_unlocked={self.destructive_unlocked!r}"
            ")"
        )

    @classmethod
    def new(
        cls,
        identities: Mapping[str, str],
        *,
        run_id: str | None = None,
    ) -> LiveLedger:
        resolved_run_id = run_id or new_run_id()
        if RUN_ID_PATTERN.fullmatch(resolved_run_id) is None:
            raise LiveSuiteSafetyError(
                f"Invalid live-suite run ID: {resolved_run_id!r}"
            )
        if not identities:
            raise LiveSuiteSafetyError(
                "A live ledger requires at least one validated account."
            )
        if len(set(identities.values())) != len(identities):
            raise LiveSuiteSafetyError(
                "Live ledger accounts must have distinct Discord user IDs."
            )
        timestamp = _utc_timestamp()
        return cls(
            run_id=resolved_run_id,
            created_at=timestamp,
            updated_at=timestamp,
            phase="initialized",
            accounts=dict(sorted(identities.items())),
            resources=[],
            capabilities={},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "phase": self.phase,
            "destructive_unlocked": self.destructive_unlocked,
            "accounts": self.accounts,
            "resources": [resource.to_dict() for resource in self.resources],
            "capabilities": self.capabilities,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> LiveLedger:
        if payload.get("schema_version") != LEDGER_SCHEMA_VERSION:
            raise LiveSuiteSafetyError(
                "Unsupported or missing live ledger schema version."
            )
        run_id = payload.get("run_id")
        if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise LiveSuiteSafetyError("Live ledger contains an invalid run ID.")
        created_at = payload.get("created_at")
        updated_at = payload.get("updated_at")
        phase = payload.get("phase")
        if not all(
            isinstance(value, str) and value
            for value in (created_at, updated_at, phase)
        ):
            raise LiveSuiteSafetyError(
                "Live ledger timestamps and phase must be non-empty strings."
            )
        destructive_unlocked = payload.get("destructive_unlocked")
        if not isinstance(destructive_unlocked, bool):
            raise LiveSuiteSafetyError(
                "Live ledger destructive_unlocked must be boolean."
            )

        raw_accounts = payload.get("accounts")
        if not isinstance(raw_accounts, dict) or not raw_accounts:
            raise LiveSuiteSafetyError(
                "Live ledger accounts must be a non-empty object."
            )
        accounts: dict[str, str] = {}
        for alias, user_id in raw_accounts.items():
            if (
                not isinstance(alias, str)
                or not alias
                or not isinstance(user_id, str)
                or not user_id
            ):
                raise LiveSuiteSafetyError(
                    "Live ledger account aliases and IDs must be non-empty strings."
                )
            accounts[alias] = user_id
        if len(set(accounts.values())) != len(accounts):
            raise LiveSuiteSafetyError(
                "Live ledger accounts must have distinct Discord user IDs."
            )

        raw_resources = payload.get("resources")
        if not isinstance(raw_resources, list):
            raise LiveSuiteSafetyError("Live ledger resources must be an array.")
        resources = [LedgerResource.from_dict(resource) for resource in raw_resources]
        raw_capabilities = payload.get("capabilities", {})
        if not isinstance(raw_capabilities, dict):
            raise LiveSuiteSafetyError("Live ledger capabilities must be an object.")
        capabilities: dict[str, str] = {}
        for key, value in raw_capabilities.items():
            if (
                not isinstance(key, str)
                or not key
                or not isinstance(value, str)
                or not value
            ):
                raise LiveSuiteSafetyError(
                    "Live ledger capabilities must contain strings."
                )
            capabilities[key] = value
        ledger = cls(
            run_id=run_id,
            created_at=created_at,
            updated_at=updated_at,
            phase=phase,
            accounts=accounts,
            resources=resources,
            destructive_unlocked=destructive_unlocked,
            capabilities=capabilities,
        )
        ledger._validate_resources()
        return ledger

    @classmethod
    def load(cls, path: Path) -> LiveLedger:
        import json

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise LiveSuiteSafetyError(f"Live ledger does not exist: {path}") from exc
        except (OSError, ValueError) as exc:
            raise LiveSuiteSafetyError(f"Unable to read live ledger: {path}") from exc
        if not isinstance(payload, dict):
            raise LiveSuiteSafetyError("Live ledger root must be an object.")
        return cls.from_dict(payload)

    def save(self, path: Path) -> None:
        self.updated_at = _utc_timestamp()
        atomic_write_json(str(path), self.to_dict())

    def record_resource(self, resource: LedgerResource) -> None:
        if resource.run_id != self.run_id:
            raise LiveSuiteSafetyError(
                "Refusing to record a resource owned by another run ID."
            )
        if resource.owner_handle not in self.accounts:
            raise LiveSuiteSafetyError("Resource uses an unknown account handle.")
        if any(
            current.kind == resource.kind
            and current.resource_id == resource.resource_id
            for current in self.resources
        ):
            raise LiveSuiteSafetyError(
                f"Duplicate ledger resource of kind {resource.kind!r}."
            )
        if any(
            current.fixture_key == resource.fixture_key for current in self.resources
        ):
            raise LiveSuiteSafetyError("Duplicate ledger fixture key.")
        self.resources.append(resource)

    def resource_for_fixture(self, fixture_key: str) -> LedgerResource | None:
        for resource in self.resources:
            if resource.fixture_key == fixture_key:
                return resource
        return None

    def set_resource_state(self, fixture_key: str, state: str) -> None:
        for index, resource in enumerate(self.resources):
            if resource.fixture_key == fixture_key:
                self.resources[index] = replace(resource, state=state)
                return
        raise LiveSuiteSafetyError("Refusing to update an unknown fixture resource.")

    def require_owned_resource(self, kind: str, resource_id: str) -> LedgerResource:
        for resource in self.resources:
            if resource.kind == kind and resource.resource_id == resource_id:
                if resource.run_id != self.run_id:
                    break
                return resource
        raise LiveSuiteSafetyError(
            f"Refusing operation on an unowned {kind!r} resource."
        )

    def mark_empty_teardown_complete(self) -> bool:
        if self.phase == "teardown_complete":
            return False
        remaining = [
            resource
            for resource in self.resources
            if resource.state not in TERMINAL_RESOURCE_STATES
        ]
        if remaining:
            raise LiveSuiteSafetyError(
                "Empty teardown cannot complete while active ledger resources remain."
            )
        self.phase = "teardown_complete"
        self.destructive_unlocked = False
        return True

    def _validate_resources(self) -> None:
        seen: set[tuple[str, str]] = set()
        seen_fixture_keys: set[str] = set()
        for resource in self.resources:
            if resource.run_id != self.run_id:
                raise LiveSuiteSafetyError(
                    "Ledger resource run ID does not match its ledger."
                )
            if resource.owner_handle not in self.accounts:
                raise LiveSuiteSafetyError(
                    "Ledger resource uses an unknown account handle."
                )
            key = (resource.kind, resource.resource_id)
            if key in seen:
                raise LiveSuiteSafetyError(
                    f"Duplicate ledger resource of kind {resource.kind!r}."
                )
            seen.add(key)
            if resource.fixture_key in seen_fixture_keys:
                raise LiveSuiteSafetyError("Ledger contains a duplicate fixture key.")
            seen_fixture_keys.add(resource.fixture_key)


def _add_account_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--secrets-file",
        type=Path,
        default=DEFAULT_SECRETS_PATH,
        help=f"Owner-only TOKEN_* file (default: {DEFAULT_SECRETS_PATH})",
    )
    parser.add_argument(
        "--expected-accounts",
        type=int,
        default=4,
        help="Required number of distinct valid accounts (default: 4)",
    )


def _load_validated_accounts(
    args: argparse.Namespace,
) -> tuple[dict[str, str], AccountValidationReport]:
    tokens = require_fixture_roles(load_account_tokens(args.secrets_file))
    report = validate_account_tokens(tokens)
    for index, check in enumerate(report.checks, start=1):
        if check.valid:
            print(f"account-{index}: valid (HTTP {check.status_code})")
        else:
            detail = check.error or "unknown error"
            print(f"account-{index}: invalid ({detail})")
    report.require_ready(args.expected_accounts)
    print(f"Validated {report.valid_count} distinct accounts.")
    return tokens, report


def _validated_accounts(args: argparse.Namespace) -> AccountValidationReport:
    _, report = _load_validated_accounts(args)
    return report


def _confirm_run_id(ledger: LiveLedger, provided_run_id: str | None) -> None:
    if provided_run_id != ledger.run_id:
        raise LiveSuiteSafetyError(
            "Mutation requires --confirm-run-id matching the current private ledger."
        )


def _require_current_accounts(
    ledger: LiveLedger,
    report: AccountValidationReport,
) -> None:
    if report.ledger_identities != ledger.accounts:
        raise LiveSuiteSafetyError(
            "Validated fixture accounts do not match the current private ledger."
        )


def _guild_name(run_id: str, purpose: str) -> str:
    return f"{run_id}-{purpose}"


def _guild_fixture_purpose(fixture_key: str) -> str:
    for expected_key, purpose in GUILD_FIXTURES:
        if fixture_key == expected_key:
            return purpose
    raise LiveSuiteSafetyError("Ledger contains an unknown guild fixture key.")


def bootstrap_fixture_guilds(
    ledger: LiveLedger,
    ledger_path: Path,
    client: DiscordFixtureClient,
) -> None:
    """Create or reconcile the two run-owned guild fixtures."""
    if ledger.phase == "teardown_complete":
        raise LiveSuiteSafetyError("A completed run cannot be bootstrapped again.")

    guilds = client.list_current_guilds()
    guilds_by_id = {guild.guild_id: guild for guild in guilds}

    for index, (fixture_key, purpose) in enumerate(GUILD_FIXTURES, start=1):
        expected_name = _guild_name(ledger.run_id, purpose)
        matching_name = [guild for guild in guilds if guild.name == expected_name]
        if len(matching_name) > 1:
            raise LiveSuiteSafetyError(
                "Multiple Discord guilds match one fixture name."
            )

        recorded = ledger.resource_for_fixture(fixture_key)
        if recorded is not None:
            if (
                recorded.kind != "guild"
                or recorded.state not in TERMINAL_RESOURCE_STATES | {"active"}
            ):
                raise LiveSuiteSafetyError(
                    "Ledger contains a malformed guild fixture resource."
                )
            if recorded.state in TERMINAL_RESOURCE_STATES:
                raise LiveSuiteSafetyError(
                    "A removed guild fixture cannot be recreated in the same run."
                )
            observed = guilds_by_id.get(recorded.resource_id)
            if observed is None or not observed.owned or observed.name != expected_name:
                raise LiveSuiteSafetyError(
                    "Recorded guild fixture no longer matches an owned Discord guild."
                )
            print(f"guild-{index}: already recorded")
            continue

        if matching_name:
            observed = matching_name[0]
            if not observed.owned:
                raise LiveSuiteSafetyError(
                    "Matching fixture guild is not owned by the controller."
                )
            outcome = "recovered"
        else:
            observed = client.create_guild(expected_name)
            guilds.append(observed)
            guilds_by_id[observed.guild_id] = observed
            outcome = "created"

        ledger.record_resource(
            LedgerResource(
                run_id=ledger.run_id,
                fixture_key=fixture_key,
                kind="guild",
                resource_id=observed.guild_id,
                owner_handle="owner",
                guild_id=observed.guild_id,
            )
        )
        ledger.save(ledger_path)
        print(f"guild-{index}: {outcome}")

    ledger.phase = "guilds_created"
    ledger.save(ledger_path)


def _require_active_resource(
    ledger: LiveLedger,
    fixture_key: str,
    kind: str,
    *,
    guild_id: str | None = None,
) -> LedgerResource:
    resource = ledger.resource_for_fixture(fixture_key)
    if (
        resource is None
        or resource.kind != kind
        or resource.state != "active"
        or (guild_id is not None and resource.guild_id != guild_id)
    ):
        raise LiveSuiteSafetyError(f"Fixture requires an active {kind!r} resource.")
    return resource


def _fixture_guild_resources(ledger: LiveLedger) -> dict[str, LedgerResource]:
    return {
        fixture_key: _require_active_resource(ledger, fixture_key, "guild")
        for fixture_key, _purpose in GUILD_FIXTURES
    }


def _validate_role_fixture(
    role: GuildRoleSnapshot,
    fixture: RoleFixture,
) -> None:
    if (
        role.name != fixture.name
        or role.permissions != fixture.permissions
        or role.managed
    ):
        raise LiveSuiteSafetyError(
            "Observed fixture role does not match its definition."
        )


def _reconcile_role_fixture(
    ledger: LiveLedger,
    ledger_path: Path,
    client: DiscordFixtureClient,
    guild: LedgerResource,
    fixture: RoleFixture,
    roles: list[GuildRoleSnapshot],
) -> tuple[GuildRoleSnapshot, str]:
    matching_name = [role for role in roles if role.name == fixture.name]
    if len(matching_name) > 1:
        raise LiveSuiteSafetyError(
            "Multiple Discord roles match one fixture definition."
        )

    recorded = ledger.resource_for_fixture(fixture.fixture_key)
    if recorded is not None:
        if (
            recorded.kind != "role"
            or recorded.state != "active"
            or recorded.guild_id != guild.resource_id
        ):
            raise LiveSuiteSafetyError(
                "Ledger contains a malformed role fixture resource."
            )
        observed = next(
            (role for role in roles if role.role_id == recorded.resource_id),
            None,
        )
        if observed is None or matching_name != [observed]:
            raise LiveSuiteSafetyError(
                "Recorded role fixture no longer matches a Discord role."
            )
        _validate_role_fixture(observed, fixture)
        return observed, "already recorded"

    if matching_name:
        observed = matching_name[0]
        _validate_role_fixture(observed, fixture)
        outcome = "recovered"
    else:
        try:
            observed = client.create_role(
                guild.resource_id,
                name=fixture.name,
                permissions=fixture.permissions,
            )
            roles.append(observed)
            outcome = "created"
        except FixtureClientError as create_error:
            roles[:] = client.list_guild_roles(guild.resource_id)
            recovered = [role for role in roles if role.name == fixture.name]
            if len(recovered) > 1:
                raise LiveSuiteSafetyError(
                    "Multiple Discord roles match one fixture definition."
                ) from None
            if not recovered:
                raise create_error
            observed = recovered[0]
            _validate_role_fixture(observed, fixture)
            outcome = "recovered"

    ledger.record_resource(
        LedgerResource(
            run_id=ledger.run_id,
            fixture_key=fixture.fixture_key,
            kind="role",
            resource_id=observed.role_id,
            owner_handle="owner",
            guild_id=guild.resource_id,
        )
    )
    ledger.save(ledger_path)
    return observed, outcome


def _channel_parent_id(
    ledger: LiveLedger,
    guild: LedgerResource,
    fixture: ChannelFixture,
) -> str | None:
    if fixture.parent_fixture_key is None:
        return None
    return _require_active_resource(
        ledger,
        fixture.parent_fixture_key,
        "channel",
        guild_id=guild.resource_id,
    ).resource_id


def _channel_permission_overwrites(
    ledger: LiveLedger,
    guild: LedgerResource,
    fixture: ChannelFixture,
) -> tuple[PermissionOverwriteSnapshot, ...]:
    if fixture.permission_profile == "open":
        return ()
    if fixture.permission_profile != "restricted-reader":
        raise LiveSuiteSafetyError(
            "Channel fixture uses an unknown permission profile."
        )
    reader_role = _require_active_resource(
        ledger,
        "role:permission:restricted-reader",
        "role",
        guild_id=guild.resource_id,
    )
    overwrites = (
        PermissionOverwriteSnapshot(
            target_id=guild.resource_id,
            target_type=0,
            allow=0,
            deny=VIEW_CHANNEL_PERMISSION,
        ),
        PermissionOverwriteSnapshot(
            target_id=reader_role.resource_id,
            target_type=0,
            allow=(
                VIEW_CHANNEL_PERMISSION
                | SEND_MESSAGES_PERMISSION
                | READ_MESSAGE_HISTORY_PERMISSION
            ),
            deny=0,
        ),
    )
    return tuple(
        sorted(
            overwrites,
            key=lambda overwrite: (overwrite.target_type, overwrite.target_id),
        )
    )


def _validate_channel_fixture(
    channel: GuildChannelSnapshot,
    fixture: ChannelFixture,
    *,
    parent_id: str | None,
    permission_overwrites: tuple[PermissionOverwriteSnapshot, ...],
) -> None:
    if (
        channel.name != fixture.name
        or channel.channel_type != int(fixture.channel_type)
        or channel.parent_id != parent_id
        or channel.permission_overwrites != permission_overwrites
    ):
        raise LiveSuiteSafetyError(
            "Observed fixture channel does not match its definition."
        )


def _reconcile_channel_fixture(
    ledger: LiveLedger,
    ledger_path: Path,
    client: DiscordFixtureClient,
    guild: LedgerResource,
    fixture: ChannelFixture,
    channels: list[GuildChannelSnapshot],
) -> tuple[GuildChannelSnapshot | None, str]:
    parent_id = _channel_parent_id(ledger, guild, fixture)
    permission_overwrites = _channel_permission_overwrites(ledger, guild, fixture)
    matching_name = [channel for channel in channels if channel.name == fixture.name]
    if len(matching_name) > 1:
        raise LiveSuiteSafetyError(
            "Multiple Discord channels match one fixture definition."
        )

    recorded = ledger.resource_for_fixture(fixture.fixture_key)
    if recorded is not None:
        if (
            recorded.kind != "channel"
            or recorded.state != "active"
            or recorded.guild_id != guild.resource_id
            or recorded.parent_id != parent_id
        ):
            raise LiveSuiteSafetyError(
                "Ledger contains a malformed channel fixture resource."
            )
        observed = next(
            (
                channel
                for channel in channels
                if channel.channel_id == recorded.resource_id
            ),
            None,
        )
        if observed is None or matching_name != [observed]:
            raise LiveSuiteSafetyError(
                "Recorded channel fixture no longer matches a Discord channel."
            )
        _validate_channel_fixture(
            observed,
            fixture,
            parent_id=parent_id,
            permission_overwrites=permission_overwrites,
        )
        return observed, "already recorded"

    if matching_name:
        observed = matching_name[0]
        _validate_channel_fixture(
            observed,
            fixture,
            parent_id=parent_id,
            permission_overwrites=permission_overwrites,
        )
        outcome = "recovered"
    elif ledger.capabilities.get(fixture.fixture_key) == "unsupported:discord-50024":
        return None, "unsupported"
    else:
        try:
            observed = client.create_channel(
                guild.resource_id,
                int(fixture.channel_type),
                name=fixture.name,
                parent_id=parent_id,
                permission_overwrites=permission_overwrites,
            )
            channels.append(observed)
            outcome = "created"
        except FixtureClientError as create_error:
            if (
                fixture.channel_type == ChannelType.GUILD_MEDIA
                and create_error.discord_code == 50024
            ):
                ledger.capabilities[fixture.fixture_key] = "unsupported:discord-50024"
                ledger.save(ledger_path)
                return None, "unsupported"
            channels[:] = client.list_guild_channels(guild.resource_id)
            recovered = [
                channel for channel in channels if channel.name == fixture.name
            ]
            if len(recovered) > 1:
                raise LiveSuiteSafetyError(
                    "Multiple Discord channels match one fixture definition."
                ) from None
            if not recovered:
                raise create_error
            observed = recovered[0]
            _validate_channel_fixture(
                observed,
                fixture,
                parent_id=parent_id,
                permission_overwrites=permission_overwrites,
            )
            outcome = "recovered"

    ledger.record_resource(
        LedgerResource(
            run_id=ledger.run_id,
            fixture_key=fixture.fixture_key,
            kind="channel",
            resource_id=observed.channel_id,
            owner_handle="owner",
            guild_id=guild.resource_id,
            parent_id=parent_id,
        )
    )
    ledger.save(ledger_path)
    return observed, outcome


def _require_fixture_member(
    member: GuildMemberSnapshot | None,
) -> GuildMemberSnapshot:
    if member is None:
        raise LiveSuiteSafetyError(
            "Fixture account is not a member of the expected guild."
        )
    if member.pending:
        raise LiveSuiteSafetyError("Fixture guild membership is still pending.")
    return member


def _ensure_fixture_member(
    owner_client: DiscordFixtureClient,
    member_client: DiscordFixtureClient,
    *,
    guild_id: str,
    channel_id: str,
    user_id: str,
) -> str:
    observed = owner_client.get_guild_member(guild_id, user_id)
    if observed is not None:
        _require_fixture_member(observed)
        return "already joined"

    invite = owner_client.create_one_use_invite(channel_id)
    try:
        member_client.accept_guild_invite(
            invite,
            expected_guild_id=guild_id,
            expected_channel_id=channel_id,
        )
    except FixtureClientError as accept_error:
        observed = owner_client.get_guild_member(guild_id, user_id)
        if observed is None:
            if accept_error.captcha_required:
                raise LiveSuiteSafetyError(
                    "Discord requires manual membership acceptance; use the "
                    "`membership-invites` command."
                ) from None
            raise accept_error
        _require_fixture_member(observed)
        return "joined"

    _require_fixture_member(owner_client.get_guild_member(guild_id, user_id))
    return "joined"


def _ensure_role_assignment(
    client: DiscordFixtureClient,
    *,
    guild_id: str,
    user_id: str,
    role_id: str,
) -> str:
    member = _require_fixture_member(client.get_guild_member(guild_id, user_id))
    if role_id in member.role_ids:
        return "already assigned"
    try:
        client.add_guild_role(guild_id, user_id, role_id)
    except FixtureClientError as assignment_error:
        member = _require_fixture_member(client.get_guild_member(guild_id, user_id))
        if role_id not in member.role_ids:
            raise assignment_error
        return "assigned"
    member = _require_fixture_member(client.get_guild_member(guild_id, user_id))
    if role_id not in member.role_ids:
        raise LiveSuiteSafetyError("Discord did not retain a fixture role assignment.")
    return "assigned"


def _community_configuration_matches(
    configuration: GuildConfigurationSnapshot,
    *,
    rules_channel_id: str,
    public_updates_channel_id: str,
) -> bool:
    return (
        "COMMUNITY" in configuration.features
        and configuration.rules_channel_id == rules_channel_id
        and configuration.public_updates_channel_id == public_updates_channel_id
        and configuration.verification_level >= 1
        and configuration.default_message_notifications == 1
        and configuration.explicit_content_filter >= 2
    )


def _ensure_community_configuration(
    client: DiscordFixtureClient,
    *,
    guild_id: str,
    rules_channel_id: str,
    public_updates_channel_id: str,
) -> str:
    current = client.get_guild_configuration(guild_id)
    if "COMMUNITY" in current.features and (
        current.rules_channel_id != rules_channel_id
        or current.public_updates_channel_id != public_updates_channel_id
    ):
        raise LiveSuiteSafetyError(
            "Existing Community configuration does not use fixture channels."
        )
    if _community_configuration_matches(
        current,
        rules_channel_id=rules_channel_id,
        public_updates_channel_id=public_updates_channel_id,
    ):
        return "already configured"

    try:
        observed = client.configure_community(
            guild_id,
            features=sorted(current.features | {"COMMUNITY"}),
            rules_channel_id=rules_channel_id,
            public_updates_channel_id=public_updates_channel_id,
            verification_level=max(1, current.verification_level),
            default_message_notifications=1,
            explicit_content_filter=max(2, current.explicit_content_filter),
        )
    except FixtureClientError as configure_error:
        observed = client.get_guild_configuration(guild_id)
        if not _community_configuration_matches(
            observed,
            rules_channel_id=rules_channel_id,
            public_updates_channel_id=public_updates_channel_id,
        ):
            raise configure_error
        return "configured"

    if not _community_configuration_matches(
        observed,
        rules_channel_id=rules_channel_id,
        public_updates_channel_id=public_updates_channel_id,
    ):
        raise LiveSuiteSafetyError("Discord did not retain Community configuration.")
    return "configured"


def bootstrap_fixture_topology(
    ledger: LiveLedger,
    ledger_path: Path,
    clients: Mapping[str, DiscordFixtureClient],
) -> None:
    """Create and reconcile members, roles, permissions, and channel topology."""
    if ledger.phase == "teardown_complete":
        raise LiveSuiteSafetyError("A completed run cannot be bootstrapped again.")
    if set(clients) != set(FIXTURE_ROLES):
        raise LiveSuiteSafetyError("Topology bootstrap requires all fixture clients.")
    if set(ledger.accounts) != set(FIXTURE_ROLES):
        raise LiveSuiteSafetyError("Topology bootstrap requires all fixture accounts.")

    owner_client = clients["owner"]
    guilds = _fixture_guild_resources(ledger)
    channels_by_guild = {
        fixture_key: owner_client.list_guild_channels(guild.resource_id)
        for fixture_key, guild in guilds.items()
    }
    roles_by_guild = {
        fixture_key: owner_client.list_guild_roles(guild.resource_id)
        for fixture_key, guild in guilds.items()
    }

    channel_number = 0
    for fixture in BASE_CHANNEL_FIXTURES:
        channel_number += 1
        _channel, outcome = _reconcile_channel_fixture(
            ledger,
            ledger_path,
            owner_client,
            guilds[fixture.guild_fixture_key],
            fixture,
            channels_by_guild[fixture.guild_fixture_key],
        )
        print(f"channel-{channel_number}: {outcome}")

    reconciled_roles: dict[str, GuildRoleSnapshot] = {}
    for role_number, fixture in enumerate(ROLE_FIXTURES, start=1):
        role, outcome = _reconcile_role_fixture(
            ledger,
            ledger_path,
            owner_client,
            guilds[fixture.guild_fixture_key],
            fixture,
            roles_by_guild[fixture.guild_fixture_key],
        )
        reconciled_roles[fixture.fixture_key] = role
        print(f"role-{role_number}: {outcome}")

    membership_number = 0
    for guild_fixture_key, _purpose in GUILD_FIXTURES:
        guild = guilds[guild_fixture_key]
        lobby = _require_active_resource(
            ledger,
            f"channel:{_purpose}:lobby",
            "channel",
            guild_id=guild.resource_id,
        )
        for account_role in FIXTURE_ROLES[1:]:
            membership_number += 1
            outcome = _ensure_fixture_member(
                owner_client,
                clients[account_role],
                guild_id=guild.resource_id,
                channel_id=lobby.resource_id,
                user_id=ledger.accounts[account_role],
            )
            print(f"membership-{membership_number}: {outcome}")

    assignment_number = 0
    for fixture in ROLE_FIXTURES:
        guild = guilds[fixture.guild_fixture_key]
        role = reconciled_roles[fixture.fixture_key]
        for account_role in fixture.assigned_accounts:
            assignment_number += 1
            outcome = _ensure_role_assignment(
                owner_client,
                guild_id=guild.resource_id,
                user_id=ledger.accounts[account_role],
                role_id=role.role_id,
            )
            print(f"role-assignment-{assignment_number}: {outcome}")

    for fixture in STANDARD_CHANNEL_FIXTURES:
        channel_number += 1
        _channel, outcome = _reconcile_channel_fixture(
            ledger,
            ledger_path,
            owner_client,
            guilds[fixture.guild_fixture_key],
            fixture,
            channels_by_guild[fixture.guild_fixture_key],
        )
        print(f"channel-{channel_number}: {outcome}")

    matrix_guild = guilds["guild:matrix"]
    rules_channel = _require_active_resource(
        ledger,
        "channel:matrix:rules",
        "channel",
        guild_id=matrix_guild.resource_id,
    )
    updates_channel = _require_active_resource(
        ledger,
        "channel:matrix:updates",
        "channel",
        guild_id=matrix_guild.resource_id,
    )
    community_outcome = _ensure_community_configuration(
        owner_client,
        guild_id=matrix_guild.resource_id,
        rules_channel_id=rules_channel.resource_id,
        public_updates_channel_id=updates_channel.resource_id,
    )
    print(f"community-1: {community_outcome}")

    for fixture in COMMUNITY_CHANNEL_FIXTURES:
        channel_number += 1
        _channel, outcome = _reconcile_channel_fixture(
            ledger,
            ledger_path,
            owner_client,
            guilds[fixture.guild_fixture_key],
            fixture,
            channels_by_guild[fixture.guild_fixture_key],
        )
        print(f"channel-{channel_number}: {outcome}")

    ledger.phase = "topology_created"
    ledger.save(ledger_path)


def seed_content_fixtures(
    ledger: LiveLedger,
    ledger_path: Path,
    clients: Mapping[str, DiscordFixtureClient],
) -> None:
    """Create a small resumable multi-user message/reaction/thread fixture."""
    if ledger.phase not in {"topology_created", "content_seeded", "dry_run_verified"}:
        raise LiveSuiteSafetyError("Content fixtures require completed topology.")
    if set(clients) != set(FIXTURE_ROLES):
        raise LiveSuiteSafetyError("Content seeding requires all fixture clients.")

    channel = _require_active_resource(
        ledger, "channel:matrix:text", "channel", guild_id=_require_active_resource(
            ledger, "guild:matrix", "guild"
        ).resource_id
    )
    message_keys = (
        ("message:matrix:subject", "subject", "dmd-live-subject-message"),
        ("message:matrix:peer-a", "peer_a", "dmd-live-peer-a-message"),
        ("message:matrix:peer-b", "peer_b", "dmd-live-peer-b-message"),
    )
    messages: dict[str, str] = {}
    for fixture_key, account_role, content in message_keys:
        existing = ledger.resource_for_fixture(fixture_key)
        if existing is not None:
            _require_active_resource(ledger, fixture_key, "message")
            messages[fixture_key] = existing.resource_id
            print(f"{fixture_key}: already seeded")
            continue
        message = clients[account_role].send_message(channel.resource_id, content=content)
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=fixture_key,
            kind="message",
            resource_id=message.message_id,
            owner_handle=account_role,
            guild_id=channel.guild_id,
            parent_id=channel.resource_id,
        ))
        ledger.save(ledger_path)
        messages[fixture_key] = message.message_id
        print(f"{fixture_key}: created")

    reaction_key = "reaction:matrix:subject-message:peer-a"
    if ledger.resource_for_fixture(reaction_key) is None:
        clients["peer_a"].add_reaction(
            channel.resource_id,
            messages["message:matrix:subject"],
            emoji="👍",
        )
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=reaction_key,
            kind="reaction",
            resource_id=messages["message:matrix:subject"],
            owner_handle="peer_a",
            guild_id=channel.guild_id,
            parent_id=channel.resource_id,
        ))
        ledger.save(ledger_path)
        print(f"{reaction_key}: created")
    else:
        print(f"{reaction_key}: already seeded")

    thread_key = "thread:matrix:subject-public"
    if ledger.resource_for_fixture(thread_key) is None:
        thread = clients["subject"].start_thread(
            channel.resource_id,
            name="dmd-live-subject-thread",
            thread_type=11,
        )
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=thread_key,
            kind="thread",
            resource_id=thread.channel_id,
            owner_handle="subject",
            guild_id=channel.guild_id,
            parent_id=channel.resource_id,
        ))
        ledger.save(ledger_path)
        print(f"{thread_key}: created")
    else:
        print(f"{thread_key}: already seeded")

    thread = _require_active_resource(ledger, thread_key, "thread")
    thread_message_keys = (
        ("message:matrix:thread:subject", "subject", "dmd-live-thread-subject"),
        ("message:matrix:thread:peer-a", "peer_a", "dmd-live-thread-peer-a"),
    )
    thread_messages: dict[str, str] = {}
    for fixture_key, account_role, content in thread_message_keys:
        existing = ledger.resource_for_fixture(fixture_key)
        if existing is not None:
            _require_active_resource(ledger, fixture_key, "message")
            thread_messages[fixture_key] = existing.resource_id
            print(f"{fixture_key}: already seeded")
            continue
        message = clients[account_role].send_message(thread.resource_id, content=content)
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=fixture_key,
            kind="message",
            resource_id=message.message_id,
            owner_handle=account_role,
            guild_id=thread.guild_id,
            parent_id=thread.resource_id,
        ))
        ledger.save(ledger_path)
        thread_messages[fixture_key] = message.message_id
        print(f"{fixture_key}: created")

    thread_reaction_key = "reaction:matrix:thread:subject-message:peer-a"
    if ledger.resource_for_fixture(thread_reaction_key) is None:
        clients["peer_a"].add_reaction(
            thread.resource_id,
            thread_messages["message:matrix:thread:subject"],
            emoji="✅",
        )
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=thread_reaction_key,
            kind="reaction",
            resource_id=thread_messages["message:matrix:thread:subject"],
            owner_handle="peer_a",
            guild_id=thread.guild_id,
            parent_id=thread.resource_id,
        ))
        ledger.save(ledger_path)
        print(f"{thread_reaction_key}: created")
    else:
        print(f"{thread_reaction_key}: already seeded")

    private_channel = _ensure_private_content_channel(
        ledger,
        ledger_path,
        clients["subject"],
        "dm:subject-peer-a",
        clients["subject"],
        [ledger.accounts["peer_a"]],
        "dm",
    )
    _seed_private_messages(
        ledger,
        ledger_path,
        clients,
        private_channel,
        (
            ("message:dm:subject", "subject", "dmd-live-dm-subject"),
            ("message:dm:peer-a", "peer_a", "dmd-live-dm-peer-a"),
        ),
        "reaction:dm:subject:peer-a",
    )
    group_channel = _ensure_private_content_channel(
        ledger,
        ledger_path,
        clients["subject"],
        "group-dm:subject-peers",
        clients["subject"],
        [ledger.accounts["peer_a"], ledger.accounts["peer_b"]],
        "group-dm",
    )
    _seed_private_messages(
        ledger,
        ledger_path,
        clients,
        group_channel,
        (
            ("message:group-dm:subject", "subject", "dmd-live-group-subject"),
            ("message:group-dm:peer-b", "peer_b", "dmd-live-group-peer-b"),
        ),
        "reaction:group-dm:subject:peer-b",
    )

    destructive_channel = _require_active_resource(
        ledger,
        "channel:permission:threads",
        "channel",
    )
    destructive_key = "message:destructive:subject"
    if ledger.resource_for_fixture(destructive_key) is None:
        message = clients["subject"].send_message(
            destructive_channel.resource_id,
            content="dmd-live-destructive-smoke",
        )
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=destructive_key,
            kind="message",
            resource_id=message.message_id,
            owner_handle="subject",
            guild_id=destructive_channel.guild_id,
            parent_id=destructive_channel.resource_id,
        ))
        ledger.save(ledger_path)
        print(f"{destructive_key}: created")
    else:
        print(f"{destructive_key}: already seeded")

    ledger.phase = "content_seeded"
    ledger.save(ledger_path)


def verify_dmd_dry_runs(
    ledger: LiveLedger,
    ledger_path: Path,
    subject_token: str,
) -> None:
    """Run redacted DMD previews against every seeded private/content scope."""
    if ledger.phase not in {
        "content_seeded",
        "volume_seeded",
        "thread_matrix_seeded",
    }:
        raise LiveSuiteSafetyError("DMD dry-runs require seeded content fixtures.")
    volume_mode = ledger.phase in {"volume_seeded", "thread_matrix_seeded"}
    thread_matrix_mode = ledger.phase == "thread_matrix_seeded"
    if volume_mode:
        scope_fixtures = [
            (scope_key, fixture_key, resource_kind, 12, 6, None)
            for scope_key, fixture_key, resource_kind, _authors
            in VOLUME_SCOPE_FIXTURES
        ]
    else:
        scope_fixtures = [
            ("guild-text", "channel:matrix:text", "channel", 0, 0, None),
            (
                "public-thread",
                "thread:matrix:subject-public",
                "thread",
                0,
                0,
                None,
            ),
            ("dm", "dm:subject-peer-a", "dm_channel", 0, 0, None),
            (
                "group-dm",
                "group-dm:subject-peers",
                "dm_channel",
                0,
                0,
                None,
            ),
        ]
    if thread_matrix_mode:
        for fixture in THREAD_MATRIX_FIXTURES:
            thread = ledger.resource_for_fixture(fixture.fixture_key)
            if thread is None and fixture.optional_parent:
                continue
            thread = _require_active_resource(
                ledger,
                fixture.fixture_key,
                "thread",
            )
            expected_messages = sum(
                resource.kind == "message"
                and resource.owner_handle == "subject"
                and resource.parent_id == thread.resource_id
                and resource.state not in TERMINAL_RESOURCE_STATES
                for resource in ledger.resources
            )
            scope_fixtures.append((
                fixture.scope_key,
                fixture.fixture_key,
                "thread",
                expected_messages,
                1,
                None,
            ))
    summary_pattern = re.compile(
        r"Summary:\s+messages\s+(\d+)\s+delete\s+/\s+\d+\s+keep,\s+"
        r"reactions\s+(\d+)\s+delete\s+/\s+\d+\s+keep"
    )
    for ordinal, (
        scope_key,
        fixture_key,
        resource_kind,
        minimum_messages,
        minimum_reactions,
        exact_reactions,
    ) in enumerate(
        scope_fixtures,
        start=1,
    ):
        resource = _require_active_resource(ledger, fixture_key, resource_kind)
        output = _run_scoped_dmd(
            subject_token,
            resource.resource_id,
            dry_run=True,
        )
        summaries = summary_pattern.findall(output)
        if "Dry run enabled." not in output or not summaries:
            raise LiveSuiteSafetyError(
                f"DMD dry-run returned an incomplete report for scope-{ordinal}."
            )
        message_count, reaction_count = map(int, summaries[-1])
        if (
            message_count < minimum_messages
            or reaction_count < minimum_reactions
            or (
                exact_reactions is not None
                and reaction_count != exact_reactions
            )
        ):
            raise LiveSuiteSafetyError(
                f"DMD dry-run undercounted seeded content for scope-{ordinal}."
            )
        print(f"dry-run-{ordinal}: verified ({scope_key})")
    if thread_matrix_mode:
        ledger.phase = "thread_matrix_dry_run_verified"
    elif volume_mode:
        ledger.phase = "volume_dry_run_verified"
    else:
        ledger.phase = "dry_run_verified"
    ledger.destructive_unlocked = not volume_mode and not thread_matrix_mode
    ledger.save(ledger_path)


def _sanitize_redacted_dmd_failure(output: str) -> str:
    normalized = " ".join(output.split())
    normalized = re.sub(r"https?://\S+", "<redacted-url>", normalized)
    normalized = re.sub(r"\b\d{10,}\b", "<redacted-id>", normalized)
    normalized = re.sub(
        r"(?i)(authorization|token)(\s*[=:]\s*)\S+",
        r"\1\2<redacted-secret>",
        normalized,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-])",
        "<redacted-value>",
        normalized,
    )
    if not normalized:
        return "no redacted diagnostic was produced"
    return normalized[-1200:]


def _run_scoped_dmd(
    subject_token: str,
    scope_id: str,
    *,
    dry_run: bool,
) -> str:
    environment = os.environ.copy()
    environment["DISCORD_TOKEN"] = subject_token
    command = [
        "uv",
        "run",
        "dmd",
        "clean",
        "--include",
        scope_id,
        "--redact-sensitive",
        "--redact-names",
    ]
    if dry_run:
        command.append("--dry-run")
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        diagnostic = _sanitize_redacted_dmd_failure(
            completed.stdout + "\n" + completed.stderr
        )
        raise LiveSuiteSafetyError(
            "Scoped DMD execution failed. Redacted diagnostic: "
            f"{diagnostic}"
        )
    return " ".join((completed.stdout + "\n" + completed.stderr).split())


def execute_destructive_smoke(
    ledger: LiveLedger,
    ledger_path: Path,
    subject_token: str,
) -> str:
    """Delete one isolated subject message and prove the scope is empty."""
    if ledger.phase != "dry_run_verified" or not ledger.destructive_unlocked:
        raise LiveSuiteSafetyError(
            "Destructive smoke requires the immediately preceding verified dry-run."
        )
    message = _require_active_resource(
        ledger, "message:destructive:subject", "message"
    )
    if message.owner_handle != "subject" or message.parent_id is None:
        raise LiveSuiteSafetyError("Destructive fixture ownership is invalid.")
    _require_active_resource(
        ledger,
        "channel:permission:threads",
        "channel",
        guild_id=message.guild_id,
    )

    preview = _run_scoped_dmd(subject_token, message.parent_id, dry_run=True)
    if re.search(r"Summary:\s+messages\s+0\s+delete", preview):
        outcome = "absent"
    elif re.search(r"Summary:\s+messages\s+1\s+delete\s+/\s+0\s+keep", preview):
        result = _run_scoped_dmd(subject_token, message.parent_id, dry_run=False)
        if not re.search(
            r"Summary:\s+messages\s+1\s+deleted\s+/\s+0\s+absent\s+/\s+0\s+failed",
            result,
        ):
            raise LiveSuiteSafetyError("Destructive smoke returned an unexpected result.")
        outcome = "deleted"
    else:
        raise LiveSuiteSafetyError(
            "Destructive fixture scope contains unexpected message state."
        )

    postcondition = _run_scoped_dmd(subject_token, message.parent_id, dry_run=True)
    if not re.search(r"Summary:\s+messages\s+0\s+delete", postcondition):
        raise LiveSuiteSafetyError("Destructive smoke postcondition failed.")
    ledger.set_resource_state(message.fixture_key, outcome)
    ledger.phase = "destructive_smoke_verified"
    ledger.destructive_unlocked = False
    ledger.save(ledger_path)
    return outcome


def _require_forum_starter_preview(output: str) -> None:
    if "owned threads" in output or not re.search(
        r"Summary:\s+messages\s+1\s+delete\s+/\s+0\s+keep",
        output,
    ):
        raise LiveSuiteSafetyError(
            "Forum starter preview returned an unexpected cleanup plan."
        )


def prepare_forum_starter_smoke(
    ledger: LiveLedger,
    ledger_path: Path,
    subject_token: str,
    subject_client: DiscordFixtureClient,
    *,
    pacer: FixturePacer | None = None,
) -> None:
    """Create and preview one isolated forum starter deletion fixture."""
    if ledger.phase not in {
        "thread_matrix_dry_run_verified",
        "forum_starter_smoke_seeded",
        "forum_starter_smoke_previewed",
    }:
        raise LiveSuiteSafetyError(
            "Forum starter preparation requires the verified thread-matrix dry-run."
        )
    parent = _require_active_resource(
        ledger,
        "channel:matrix:forum",
        "channel",
    )
    thread = ledger.resource_for_fixture(FORUM_STARTER_THREAD_KEY)
    message = ledger.resource_for_fixture(FORUM_STARTER_MESSAGE_KEY)
    if (thread is None) != (message is None):
        raise LiveSuiteSafetyError("Forum starter ledger state is incomplete.")

    if thread is None:
        created = subject_client.start_forum_thread(
            parent.resource_id,
            name="dmd-live-forum-starter-smoke",
            content="Isolated forum starter message for destructive verification.",
        )
        if (
            created.parent_id != parent.resource_id
            or created.thread_type != int(ChannelType.PUBLIC_THREAD)
            or created.initial_message_id is None
        ):
            raise LiveSuiteSafetyError(
                "Discord created an unexpected forum starter smoke fixture."
            )
        thread = LedgerResource(
            run_id=ledger.run_id,
            fixture_key=FORUM_STARTER_THREAD_KEY,
            kind="thread",
            resource_id=created.channel_id,
            owner_handle="subject",
            guild_id=parent.guild_id,
            parent_id=parent.resource_id,
        )
        message = LedgerResource(
            run_id=ledger.run_id,
            fixture_key=FORUM_STARTER_MESSAGE_KEY,
            kind="message",
            resource_id=created.initial_message_id,
            owner_handle="subject",
            guild_id=parent.guild_id,
            parent_id=created.channel_id,
        )
        ledger.record_resource(thread)
        ledger.record_resource(message)
        ledger.phase = "forum_starter_smoke_seeded"
        ledger.destructive_unlocked = False
        ledger.save(ledger_path)
    else:
        thread = _require_active_resource(
            ledger,
            FORUM_STARTER_THREAD_KEY,
            "thread",
            guild_id=parent.guild_id,
        )
        message = _require_active_resource(
            ledger,
            FORUM_STARTER_MESSAGE_KEY,
            "message",
            guild_id=parent.guild_id,
        )
        if (
            thread.owner_handle != "subject"
            or thread.parent_id != parent.resource_id
            or message.owner_handle != "subject"
            or message.parent_id != thread.resource_id
        ):
            raise LiveSuiteSafetyError("Forum starter fixture ownership is invalid.")

    observation = observe_forum_starter_state(
        subject_token,
        thread.resource_id,
        message.resource_id,
        parent.resource_id,
        pacer=pacer,
    )
    if not observation.thread_exists or not observation.message_exists:
        raise LiveSuiteSafetyError(
            "Forum starter fixture is unavailable before its destructive preview."
        )
    preview = _run_scoped_dmd(
        subject_token,
        thread.resource_id,
        dry_run=True,
    )
    _require_forum_starter_preview(preview)
    ledger.phase = "forum_starter_smoke_previewed"
    ledger.destructive_unlocked = True
    ledger.save(ledger_path)


def execute_forum_starter_smoke(
    ledger: LiveLedger,
    ledger_path: Path,
    subject_token: str,
    *,
    pacer: FixturePacer | None = None,
) -> tuple[str, str]:
    """Delete one forum starter normally and observe the container postcondition."""
    if (
        ledger.phase != "forum_starter_smoke_previewed"
        or not ledger.destructive_unlocked
    ):
        raise LiveSuiteSafetyError(
            "Forum starter execution requires its immediately preceding preview."
        )
    parent = _require_active_resource(
        ledger,
        "channel:matrix:forum",
        "channel",
    )
    thread = _require_active_resource(
        ledger,
        FORUM_STARTER_THREAD_KEY,
        "thread",
        guild_id=parent.guild_id,
    )
    message = _require_active_resource(
        ledger,
        FORUM_STARTER_MESSAGE_KEY,
        "message",
        guild_id=parent.guild_id,
    )
    if (
        thread.owner_handle != "subject"
        or thread.parent_id != parent.resource_id
        or message.owner_handle != "subject"
        or message.parent_id != thread.resource_id
    ):
        raise LiveSuiteSafetyError("Forum starter fixture ownership is invalid.")

    before = observe_forum_starter_state(
        subject_token,
        thread.resource_id,
        message.resource_id,
        parent.resource_id,
        pacer=pacer,
    )
    if before.thread_exists and before.message_exists:
        preview = _run_scoped_dmd(
            subject_token,
            thread.resource_id,
            dry_run=True,
        )
        _require_forum_starter_preview(preview)
        result = _run_scoped_dmd(
            subject_token,
            thread.resource_id,
            dry_run=False,
        )
        if not re.search(
            r"Summary:\s+messages\s+1\s+deleted\s+/\s+0\s+absent\s+/\s+0\s+failed",
            result,
        ):
            raise LiveSuiteSafetyError(
                "Forum starter deletion returned an unexpected result."
            )
        outcome = "deleted"
    elif not before.message_exists:
        outcome = "absent"
    else:
        raise LiveSuiteSafetyError(
            "Forum starter observation returned an inconsistent precondition."
        )

    after = observe_forum_starter_state(
        subject_token,
        thread.resource_id,
        message.resource_id,
        parent.resource_id,
        pacer=pacer,
    )
    if after.message_exists:
        raise LiveSuiteSafetyError("Forum starter message still exists after cleanup.")
    container_state = "present" if after.thread_exists else "absent"
    if after.thread_exists:
        postcondition = _run_scoped_dmd(
            subject_token,
            thread.resource_id,
            dry_run=True,
        )
        if not re.search(r"Summary:\s+messages\s+0\s+delete", postcondition):
            raise LiveSuiteSafetyError(
                "Forum starter cleanup postcondition failed."
            )
    else:
        ledger.set_resource_state(thread.fixture_key, "absent")

    ledger.set_resource_state(message.fixture_key, outcome)
    ledger.capabilities[FORUM_STARTER_CONTAINER_CAPABILITY] = container_state
    ledger.phase = "forum_starter_smoke_verified"
    ledger.destructive_unlocked = False
    ledger.save(ledger_path)
    return outcome, container_state


VOLUME_MESSAGE_TEMPLATES = (
    "Checking how the cleanup walk handles this part of the fixture.",
    "This note belongs to a mixed ownership history scenario.",
    "Recording another point in the retention test timeline.",
    "A reaction boundary will be evaluated around this message.",
    "This fixture entry helps exercise paginated message traversal.",
    "Keeping the conversation varied for deterministic live validation.",
    "The surrounding messages intentionally use different authors.",
    "This line contributes to the channel history test window.",
    "Another cleanup candidate is being placed in the fixture stream.",
    "This message is part of the resumable multi-account matrix.",
    "Testing ownership decisions with a distinct piece of content.",
    "The live suite will later compare this state with its dry-run plan.",
)

VOLUME_SCOPE_FIXTURES = (
    ("guild-text", "channel:matrix:text", "channel", ("subject", "peer_a", "subject", "peer_b")),
    ("announcement", "channel:matrix:announcement", "channel", ("subject", "peer_a", "subject", "peer_b")),
    ("voice-chat", "channel:matrix:voice", "channel", ("subject", "peer_a", "subject", "peer_b")),
    ("stage-chat", "channel:matrix:stage", "channel", ("subject", "peer_a", "subject", "peer_b")),
    ("public-thread-1", "thread:matrix:subject-public", "thread", ("subject", "peer_a", "subject", "peer_b")),
    ("public-thread-2", "thread:matrix:volume-2", "thread", ("subject", "peer_a", "subject", "peer_b")),
    ("public-thread-3", "thread:matrix:volume-3", "thread", ("subject", "peer_a", "subject", "peer_b")),
    ("dm", "dm:subject-peer-a", "dm_channel", ("subject", "peer_a")),
    ("group-dm", "group-dm:subject-peers", "dm_channel", ("subject", "peer_a", "subject", "peer_b")),
)

THREAD_MATRIX_FIXTURES = (
    ThreadMatrixFixture(
        "public-active",
        "thread:matrix:public-active",
        "channel:matrix:text",
        "channel",
        ChannelType.PUBLIC_THREAD,
        "without-message",
        False,
    ),
    ThreadMatrixFixture(
        "public-archived",
        "thread:matrix:public-archived",
        "channel:matrix:text",
        "channel",
        ChannelType.PUBLIC_THREAD,
        "without-message",
        True,
    ),
    ThreadMatrixFixture(
        "announcement-active",
        "thread:matrix:announcement-active",
        "channel:matrix:announcement",
        "channel",
        ChannelType.ANNOUNCEMENT_THREAD,
        "from-message",
        False,
    ),
    ThreadMatrixFixture(
        "announcement-archived",
        "thread:matrix:announcement-archived",
        "channel:matrix:announcement",
        "channel",
        ChannelType.ANNOUNCEMENT_THREAD,
        "from-message",
        True,
    ),
    ThreadMatrixFixture(
        "private-active",
        "thread:matrix:private-active",
        "channel:permission:threads",
        "channel",
        ChannelType.PRIVATE_THREAD,
        "without-message",
        False,
    ),
    ThreadMatrixFixture(
        "private-archived",
        "thread:matrix:private-archived",
        "channel:permission:threads",
        "channel",
        ChannelType.PRIVATE_THREAD,
        "without-message",
        True,
    ),
    ThreadMatrixFixture(
        "forum-active",
        "thread:matrix:forum-active",
        "channel:matrix:forum",
        "channel",
        ChannelType.PUBLIC_THREAD,
        "forum-post",
        False,
    ),
    ThreadMatrixFixture(
        "forum-archived",
        "thread:matrix:forum-archived",
        "channel:matrix:forum",
        "channel",
        ChannelType.PUBLIC_THREAD,
        "forum-post",
        True,
    ),
    ThreadMatrixFixture(
        "media-active",
        "thread:matrix:media-active",
        "channel:matrix:media",
        "channel",
        ChannelType.PUBLIC_THREAD,
        "forum-post",
        False,
        True,
    ),
    ThreadMatrixFixture(
        "media-archived",
        "thread:matrix:media-archived",
        "channel:matrix:media",
        "channel",
        ChannelType.PUBLIC_THREAD,
        "forum-post",
        True,
        True,
    ),
)

DESTRUCTIVE_CONTRACT_SCOPES = (
    DestructiveContractScope(
        "guild-text",
        "channel:matrix:text",
        "channel",
        ChannelType.GUILD_TEXT,
    ),
    DestructiveContractScope(
        "announcement",
        "channel:matrix:announcement",
        "channel",
        ChannelType.GUILD_ANNOUNCEMENT,
    ),
    DestructiveContractScope(
        "voice-chat",
        "channel:matrix:voice",
        "channel",
        ChannelType.GUILD_VOICE,
    ),
    DestructiveContractScope(
        "stage-chat",
        "channel:matrix:stage",
        "channel",
        ChannelType.GUILD_STAGE_VOICE,
    ),
    DestructiveContractScope(
        "dm",
        "dm:subject-peer-a",
        "dm_channel",
        ChannelType.DM,
    ),
    DestructiveContractScope(
        "group-dm",
        "group-dm:subject-peers",
        "dm_channel",
        ChannelType.GROUP_DM,
    ),
    *(
        DestructiveContractScope(
            fixture.scope_key,
            fixture.fixture_key,
            "thread",
            fixture.thread_type,
            archived=fixture.archived,
            optional=fixture.optional_parent,
        )
        for fixture in THREAD_MATRIX_FIXTURES
    ),
)

ARCHIVED_THREAD_RACE_SCENARIOS = (
    ArchivedThreadRaceScenario(
        "temporary-happy",
        "subject",
        "none",
        False,
        True,
        0,
    ),
    ArchivedThreadRaceScenario(
        "early-external-archive",
        "subject",
        "early-archive",
        False,
        False,
        1,
    ),
    ArchivedThreadRaceScenario(
        "likely-auto-archive",
        "subject",
        "likely-auto-archive",
        False,
        True,
        1,
    ),
    ArchivedThreadRaceScenario(
        "locked-manager",
        "peer_b",
        "none",
        True,
        True,
        0,
    ),
    ArchivedThreadRaceScenario(
        "lock-changed",
        "subject",
        "lock-changed",
        False,
        False,
        1,
    ),
    ArchivedThreadRaceScenario(
        "second-archive",
        "subject",
        "second-archive",
        False,
        False,
        2,
    ),
)

THREAD_MATRIX_AUTHOR_CYCLE = ("subject", "peer_a", "subject", "peer_b")
THREAD_MATRIX_REACTIONS = (
    ("subject", "subject", "🧭"),
    ("subject", "peer_a", "🧭"),
    ("subject", "peer_b", "🧭"),
    ("peer_a", "subject", "🧩"),
    ("peer_a", "peer_b", "🧩"),
)


def _volume_message_content(run_id: str, scope_key: str, index: int) -> str:
    digest = hashlib.sha256(f"{run_id}:{scope_key}:{index}".encode()).digest()
    template = VOLUME_MESSAGE_TEMPLATES[digest[0] % len(VOLUME_MESSAGE_TEMPLATES)]
    return f"{template} [fixture {scope_key} {index:02d}]"


def seed_volume_matrix(
    ledger: LiveLedger,
    ledger_path: Path,
    clients: Mapping[str, DiscordFixtureClient],
    *,
    messages_per_scope: int,
    max_new_mutations: int = 0,
) -> tuple[int, bool]:
    """Seed a resumable, varied multi-scope message and reaction matrix."""
    if ledger.phase not in {
        "destructive_smoke_verified",
        "volume_seeding",
        "volume_seeded",
    }:
        raise LiveSuiteSafetyError(
            "Volume fixtures require the completed destructive smoke phase."
        )
    if messages_per_scope < 1:
        raise ValueError("messages-per-scope must be positive.")
    if max_new_mutations < 0:
        raise ValueError("max-new-mutations must be non-negative.")

    text_channel = _require_active_resource(
        ledger, "channel:matrix:text", "channel"
    )
    for thread_index, account_role in ((2, "peer_a"), (3, "subject")):
        fixture_key = f"thread:matrix:volume-{thread_index}"
        if ledger.resource_for_fixture(fixture_key) is not None:
            continue
        thread = clients[account_role].start_thread(
            text_channel.resource_id,
            name=f"dmd-live-volume-thread-{thread_index}",
            thread_type=int(ChannelType.PUBLIC_THREAD),
        )
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=fixture_key,
            kind="thread",
            resource_id=thread.channel_id,
            owner_handle=account_role,
            guild_id=text_channel.guild_id,
            parent_id=text_channel.resource_id,
        ))
        ledger.save(ledger_path)

    emoji_cycle = ("🔍", "🧪", "✅", "📌")
    mutations = 0

    for scope_key, resource_key, resource_kind, author_cycle in VOLUME_SCOPE_FIXTURES:
        channel = _require_active_resource(ledger, resource_key, resource_kind)
        for index in range(1, messages_per_scope + 1):
            fixture_key = f"message:volume:{scope_key}:{index:03d}"
            account_role = author_cycle[(index - 1) % len(author_cycle)]
            message = ledger.resource_for_fixture(fixture_key)
            if message is None:
                if max_new_mutations and mutations >= max_new_mutations:
                    ledger.phase = "volume_seeding"
                    ledger.save(ledger_path)
                    return mutations, False
                created = clients[account_role].send_message(
                    channel.resource_id,
                    content=_volume_message_content(ledger.run_id, scope_key, index),
                )
                message = LedgerResource(
                    run_id=ledger.run_id,
                    fixture_key=fixture_key,
                    kind="message",
                    resource_id=created.message_id,
                    owner_handle=account_role,
                    guild_id=channel.guild_id,
                    parent_id=channel.resource_id,
                )
                ledger.record_resource(message)
                ledger.save(ledger_path)
                mutations += 1
            else:
                _require_active_resource(ledger, fixture_key, "message")

            if index % 4 != 0:
                continue
            reactor = next(
                role for role in author_cycle if role != account_role
            )
            reaction_key = f"reaction:volume:{scope_key}:{index:03d}:{reactor}"
            if ledger.resource_for_fixture(reaction_key) is not None:
                continue
            if max_new_mutations and mutations >= max_new_mutations:
                ledger.phase = "volume_seeding"
                ledger.save(ledger_path)
                return mutations, False
            clients[reactor].add_reaction(
                channel.resource_id,
                message.resource_id,
                emoji=emoji_cycle[(index // 4 - 1) % len(emoji_cycle)],
            )
            ledger.record_resource(LedgerResource(
                run_id=ledger.run_id,
                fixture_key=reaction_key,
                kind="reaction",
                resource_id=message.resource_id,
                owner_handle=reactor,
                guild_id=channel.guild_id,
                parent_id=channel.resource_id,
            ))
            ledger.save(ledger_path)
            mutations += 1

    ledger.phase = "volume_seeded"
    ledger.save(ledger_path)
    return mutations, True


def _thread_matrix_message_content(
    run_id: str,
    scope_key: str,
    index: int,
) -> str:
    return _volume_message_content(run_id, f"thread-{scope_key}", index)


def _reaction_resource_id(
    message_id: str,
    account_role: str,
    emoji: str,
    reaction_type: int,
) -> str:
    identity = f"{message_id}:{account_role}:{emoji}:{reaction_type}"
    return hashlib.sha256(identity.encode()).hexdigest()


def _thread_capability_key(fixture_key: str, field: str) -> str:
    return f"{fixture_key}:{field}"


def _validate_thread_snapshot(
    fixture: ThreadMatrixFixture,
    thread: Any,
    parent: LedgerResource,
) -> None:
    if (
        thread.parent_id != parent.resource_id
        or thread.thread_type != int(fixture.thread_type)
    ):
        raise LiveSuiteSafetyError(
            "Discord created an unexpected thread fixture shape."
        )


def _pause_thread_matrix(
    ledger: LiveLedger,
    ledger_path: Path,
    mutations: int,
) -> tuple[int, bool]:
    ledger.phase = "thread_matrix_seeding"
    ledger.destructive_unlocked = False
    ledger.save(ledger_path)
    return mutations, False


def seed_thread_matrix(
    ledger: LiveLedger,
    ledger_path: Path,
    clients: Mapping[str, DiscordFixtureClient],
    *,
    messages_per_thread: int,
    max_new_mutations: int = 0,
    try_super_reactions: bool = True,
) -> tuple[int, bool]:
    """Seed active/archived thread forms and shared multi-user reactions."""
    if ledger.phase not in {
        "volume_dry_run_verified",
        "thread_matrix_seeding",
        "thread_matrix_seeded",
    }:
        raise LiveSuiteSafetyError(
            "Thread fixtures require the verified volume dry-run phase."
        )
    if set(clients) != set(FIXTURE_ROLES):
        raise LiveSuiteSafetyError("Thread seeding requires all fixture clients.")
    if messages_per_thread < len(THREAD_MATRIX_AUTHOR_CYCLE):
        raise ValueError(
            "messages-per-thread must cover subject and both peer authors."
        )
    if max_new_mutations < 0:
        raise ValueError("max-new-mutations must be non-negative.")

    mutations = 0

    def budget_exhausted() -> bool:
        return bool(max_new_mutations and mutations >= max_new_mutations)

    for fixture in THREAD_MATRIX_FIXTURES:
        parent_resource = ledger.resource_for_fixture(fixture.parent_fixture_key)
        if parent_resource is None:
            capability = ledger.capabilities.get(fixture.parent_fixture_key, "")
            if fixture.optional_parent and capability.startswith("unsupported:"):
                continue
            raise LiveSuiteSafetyError("A required thread parent fixture is absent.")
        parent = _require_active_resource(
            ledger,
            fixture.parent_fixture_key,
            fixture.parent_kind,
        )

        thread_resource = ledger.resource_for_fixture(fixture.fixture_key)
        if thread_resource is None:
            starter_message: LedgerResource | None = None
            if fixture.creation_mode == "from-message":
                starter_key = f"message:thread-matrix:{fixture.scope_key}:starter"
                starter_message = ledger.resource_for_fixture(starter_key)
                if starter_message is None:
                    if budget_exhausted():
                        return _pause_thread_matrix(ledger, ledger_path, mutations)
                    created_starter = clients["subject"].send_message(
                        parent.resource_id,
                        content=(
                            "Starting a dedicated announcement-thread fixture "
                            f"for {fixture.scope_key}."
                        ),
                    )
                    starter_message = LedgerResource(
                        run_id=ledger.run_id,
                        fixture_key=starter_key,
                        kind="message",
                        resource_id=created_starter.message_id,
                        owner_handle="subject",
                        guild_id=parent.guild_id,
                        parent_id=parent.resource_id,
                    )
                    ledger.record_resource(starter_message)
                    ledger.save(ledger_path)
                    mutations += 1
                else:
                    starter_message = _require_active_resource(
                        ledger,
                        starter_key,
                        "message",
                        guild_id=parent.guild_id,
                    )

            if budget_exhausted():
                return _pause_thread_matrix(ledger, ledger_path, mutations)
            thread_name = f"dmd-live-{fixture.scope_key}"
            if fixture.creation_mode == "from-message":
                if starter_message is None:
                    raise LiveSuiteSafetyError(
                        "Announcement thread fixture has no starter message."
                    )
                created_thread = clients["subject"].start_thread_from_message(
                    parent.resource_id,
                    starter_message.resource_id,
                    name=thread_name,
                )
            elif fixture.creation_mode == "forum-post":
                created_thread = clients["subject"].start_forum_thread(
                    parent.resource_id,
                    name=thread_name,
                    content=(
                        "Initial post for the deterministic live thread fixture "
                        f"{fixture.scope_key}."
                    ),
                )
            else:
                created_thread = clients["subject"].start_thread(
                    parent.resource_id,
                    name=thread_name,
                    thread_type=int(fixture.thread_type),
                )
            mutations += 1
            _validate_thread_snapshot(fixture, created_thread, parent)
            thread_resource = LedgerResource(
                run_id=ledger.run_id,
                fixture_key=fixture.fixture_key,
                kind="thread",
                resource_id=created_thread.channel_id,
                owner_handle="subject",
                guild_id=parent.guild_id,
                parent_id=parent.resource_id,
            )
            ledger.record_resource(thread_resource)
            if fixture.creation_mode == "forum-post":
                if created_thread.initial_message_id is None:
                    raise LiveSuiteSafetyError(
                        "Discord omitted the initial forum fixture message."
                    )
                ledger.record_resource(LedgerResource(
                    run_id=ledger.run_id,
                    fixture_key=(
                        f"message:thread-matrix:{fixture.scope_key}:initial"
                    ),
                    kind="message",
                    resource_id=created_thread.initial_message_id,
                    owner_handle="subject",
                    guild_id=parent.guild_id,
                    parent_id=created_thread.channel_id,
                ))
            ledger.capabilities[
                _thread_capability_key(fixture.fixture_key, "type")
            ] = str(int(fixture.thread_type))
            ledger.capabilities[
                _thread_capability_key(fixture.fixture_key, "state")
            ] = "active"
            ledger.save(ledger_path)
        else:
            thread_resource = _require_active_resource(
                ledger,
                fixture.fixture_key,
                "thread",
                guild_id=parent.guild_id,
            )
            if thread_resource.parent_id != parent.resource_id:
                raise LiveSuiteSafetyError(
                    "A recorded thread fixture has an unexpected parent."
                )
            recorded_type = ledger.capabilities.get(
                _thread_capability_key(fixture.fixture_key, "type")
            )
            if recorded_type != str(int(fixture.thread_type)):
                raise LiveSuiteSafetyError(
                    "A recorded thread fixture has an unexpected type."
                )

        if fixture.thread_type == ChannelType.PRIVATE_THREAD:
            for account_role in ("peer_a", "peer_b"):
                member_key = _thread_capability_key(
                    fixture.fixture_key,
                    f"member:{account_role}",
                )
                if ledger.capabilities.get(member_key) == "joined":
                    continue
                if budget_exhausted():
                    return _pause_thread_matrix(ledger, ledger_path, mutations)
                clients["subject"].add_thread_member(
                    thread_resource.resource_id,
                    ledger.accounts[account_role],
                )
                mutations += 1
                ledger.capabilities[member_key] = "joined"
                ledger.save(ledger_path)

        target_messages: dict[str, LedgerResource] = {}
        for index in range(1, messages_per_thread + 1):
            account_role = THREAD_MATRIX_AUTHOR_CYCLE[
                (index - 1) % len(THREAD_MATRIX_AUTHOR_CYCLE)
            ]
            message_key = (
                f"message:thread-matrix:{fixture.scope_key}:{index:03d}"
            )
            message = ledger.resource_for_fixture(message_key)
            if message is None:
                if budget_exhausted():
                    return _pause_thread_matrix(ledger, ledger_path, mutations)
                created_message = clients[account_role].send_message(
                    thread_resource.resource_id,
                    content=_thread_matrix_message_content(
                        ledger.run_id,
                        fixture.scope_key,
                        index,
                    ),
                )
                message = LedgerResource(
                    run_id=ledger.run_id,
                    fixture_key=message_key,
                    kind="message",
                    resource_id=created_message.message_id,
                    owner_handle=account_role,
                    guild_id=thread_resource.guild_id,
                    parent_id=thread_resource.resource_id,
                )
                ledger.record_resource(message)
                ledger.save(ledger_path)
                mutations += 1
            else:
                message = _require_active_resource(
                    ledger,
                    message_key,
                    "message",
                    guild_id=thread_resource.guild_id,
                )
            target_messages.setdefault(account_role, message)

        for target_role, account_role, emoji in THREAD_MATRIX_REACTIONS:
            target = target_messages[target_role]
            reaction_key = (
                f"reaction:thread-matrix:{fixture.scope_key}:"
                f"{target_role}:{account_role}:normal"
            )
            if ledger.resource_for_fixture(reaction_key) is not None:
                continue
            if budget_exhausted():
                return _pause_thread_matrix(ledger, ledger_path, mutations)
            clients[account_role].add_reaction(
                thread_resource.resource_id,
                target.resource_id,
                emoji=emoji,
            )
            mutations += 1
            ledger.record_resource(LedgerResource(
                run_id=ledger.run_id,
                fixture_key=reaction_key,
                kind="reaction",
                resource_id=_reaction_resource_id(
                    target.resource_id,
                    account_role,
                    emoji,
                    0,
                ),
                owner_handle=account_role,
                guild_id=thread_resource.guild_id,
                parent_id=thread_resource.resource_id,
            ))
            ledger.save(ledger_path)

        state_key = _thread_capability_key(fixture.fixture_key, "state")
        if fixture.archived and ledger.capabilities.get(state_key) != "archived":
            if budget_exhausted():
                return _pause_thread_matrix(ledger, ledger_path, mutations)
            archived_thread = clients["subject"].set_thread_archived(
                thread_resource.resource_id,
                archived=True,
            )
            mutations += 1
            _validate_thread_snapshot(fixture, archived_thread, parent)
            ledger.capabilities[state_key] = "archived"
            ledger.save(ledger_path)

    if try_super_reactions:
        active_thread = _require_active_resource(
            ledger,
            "thread:matrix:public-active",
            "thread",
        )
        super_targets = (
            (
                "subject",
                _require_active_resource(
                    ledger,
                    "message:thread-matrix:public-active:002",
                    "message",
                ),
            ),
            (
                "peer_a",
                _require_active_resource(
                    ledger,
                    "message:thread-matrix:public-active:001",
                    "message",
                ),
            ),
        )
        for account_role, target in super_targets:
            capability_key = f"super-reaction:{account_role}"
            capability = ledger.capabilities.get(capability_key)
            if capability is not None:
                continue
            if budget_exhausted():
                return _pause_thread_matrix(ledger, ledger_path, mutations)
            try:
                clients[account_role].add_reaction(
                    active_thread.resource_id,
                    target.resource_id,
                    emoji="✨",
                    reaction_type=1,
                )
            except FixtureClientError as exc:
                if exc.status_code not in {400, 403}:
                    raise
                ledger.capabilities[capability_key] = (
                    f"unsupported:http-{exc.status_code}"
                )
            else:
                ledger.capabilities[capability_key] = "supported"
                ledger.record_resource(LedgerResource(
                    run_id=ledger.run_id,
                    fixture_key=(
                        f"reaction:thread-matrix:public-active:"
                        f"super:{account_role}"
                    ),
                    kind="reaction",
                    resource_id=_reaction_resource_id(
                        target.resource_id,
                        account_role,
                        "✨",
                        1,
                    ),
                    owner_handle=account_role,
                    guild_id=active_thread.guild_id,
                    parent_id=active_thread.resource_id,
                ))
            mutations += 1
            ledger.save(ledger_path)

    ledger.phase = "thread_matrix_seeded"
    ledger.destructive_unlocked = False
    ledger.save(ledger_path)
    return mutations, True


def _contract_capability_key(scope: DestructiveContractScope, field: str) -> str:
    return f"destructive-contract:{scope.scope_key}:{field}"


def _contract_scope_messages(
    ledger: LiveLedger,
    scope: LedgerResource,
) -> list[LedgerResource]:
    messages = [
        resource
        for resource in ledger.resources
        if resource.kind == "message"
        and resource.parent_id == scope.resource_id
        and resource.state not in TERMINAL_RESOURCE_STATES
    ]
    if not messages:
        raise LiveSuiteSafetyError(
            "A destructive contract scope has no active tracked messages."
        )
    if not any(message.owner_handle == "subject" for message in messages):
        raise LiveSuiteSafetyError(
            "A destructive contract scope has no tracked subject message."
        )
    if not any(message.owner_handle != "subject" for message in messages):
        raise LiveSuiteSafetyError(
            "A destructive contract scope has no tracked foreign message."
        )
    return messages


def observe_destructive_contract_scope(
    token: str,
    scope: DestructiveContractScope,
    resource: LedgerResource,
    messages: Sequence[LedgerResource],
    accounts: Mapping[str, str],
    *,
    target_role: str = "subject",
    client: Any | None = None,
    pacer: FixturePacer | None = None,
) -> DestructiveContractObservation:
    """Read one tracked scope without exposing channel or message data."""
    owns_client = client is None
    session = client or requests.Session()
    request_pacer = pacer or FixturePacer()
    headers = {
        "Authorization": token,
        "User-Agent": "delete-me-discord-live-suite/1.0",
    }
    target_id = accounts.get(target_role)
    if not isinstance(target_id, str) or not target_id:
        raise LiveSuiteSafetyError(
            "The destructive contract target identity is invalid."
        )

    def request(path: str, *, params: Mapping[str, Any] | None = None):
        request_pacer.wait_before_request("GET")
        try:
            response = session.get(
                f"{DISCORD_API_BASE_URL}{path}",
                headers=headers,
                params=params,
                timeout=(10.0, 30.0),
            )
        except requests.RequestException:
            raise LiveSuiteSafetyError(
                "Destructive contract observation ended with transport uncertainty."
            ) from None
        finally:
            request_pacer.note_request_finished()
        if response.status_code not in {200, 404}:
            raise LiveSuiteSafetyError(
                "Destructive contract observation failed "
                f"(HTTP {response.status_code})."
            )
        return response

    try:
        channel_response = request(f"/channels/{resource.resource_id}")
        if channel_response.status_code == 404:
            return DestructiveContractObservation(
                False,
                None,
                {},
                frozenset(),
                0,
                0,
            )
        try:
            channel_payload = channel_response.json()
        except (TypeError, ValueError):
            channel_payload = None
        if (
            not isinstance(channel_payload, Mapping)
            or str(channel_payload.get("id")) != resource.resource_id
            or channel_payload.get("type") != int(scope.channel_type)
        ):
            raise LiveSuiteSafetyError(
                "Discord returned an unexpected destructive contract container."
            )

        archived: bool | None = None
        locked: bool | None = None
        auto_archive_duration: int | None = None
        if scope.channel_type in {
            ChannelType.ANNOUNCEMENT_THREAD,
            ChannelType.PUBLIC_THREAD,
            ChannelType.PRIVATE_THREAD,
        }:
            metadata = channel_payload.get("thread_metadata")
            if not isinstance(metadata, Mapping) or not isinstance(
                metadata.get("archived"), bool
            ):
                raise LiveSuiteSafetyError(
                    "Discord omitted destructive contract thread state."
                )
            archived = metadata["archived"]
            raw_locked = metadata.get("locked")
            raw_auto_archive_duration = metadata.get("auto_archive_duration")
            if not isinstance(raw_locked, bool):
                raise LiveSuiteSafetyError(
                    "Discord omitted destructive contract thread lock state."
                )
            if (
                isinstance(raw_auto_archive_duration, bool)
                or not isinstance(raw_auto_archive_duration, int)
                or raw_auto_archive_duration <= 0
            ):
                raise LiveSuiteSafetyError(
                    "Discord omitted destructive contract auto-archive duration."
                )
            locked = raw_locked
            auto_archive_duration = raw_auto_archive_duration
            if scope.archived is not None and archived is not scope.archived:
                raise LiveSuiteSafetyError(
                    "A destructive contract thread changed archive state."
                )

        observed_payloads: dict[str, Mapping[str, Any]] = {}
        before: str | None = None
        for _page in range(100):
            params: dict[str, Any] = {"limit": 100}
            if before is not None:
                params["before"] = before
            message_response = request(
                f"/channels/{resource.resource_id}/messages",
                params=params,
            )
            if message_response.status_code == 404:
                return DestructiveContractObservation(
                    False,
                    None,
                    {},
                    frozenset(),
                    0,
                    0,
                )
            try:
                page = message_response.json()
            except (TypeError, ValueError):
                page = None
            if not isinstance(page, list) or not all(
                isinstance(item, Mapping) for item in page
            ):
                raise LiveSuiteSafetyError(
                    "Discord returned malformed destructive contract history."
                )
            if not page:
                break
            page_ids: list[int] = []
            for item in page:
                message_id = item.get("id")
                if not isinstance(message_id, str) or not message_id.isdigit():
                    raise LiveSuiteSafetyError(
                        "Discord returned an invalid message in contract history."
                    )
                page_ids.append(int(message_id))
                observed_payloads[message_id] = item
            oldest_id = min(page_ids)
            if len(page) < 100:
                break
            before = str(oldest_id)
        else:
            raise LiveSuiteSafetyError(
                "Destructive contract history exceeded its bounded observation."
            )

        message_authors: dict[str, str] = {}
        deletable_message_ids: set[str] = set()
        subject_reactions = 0
        foreign_reactions = 0
        deletable_type_values = {
            int(message_type)
            for message_type in MessageType
            if message_type.deletable
        }
        for message_id, payload in observed_payloads.items():
            author = payload.get("author")
            author_id = author.get("id") if isinstance(author, Mapping) else None
            if isinstance(author_id, str):
                message_authors[message_id] = author_id
            message_type = payload.get("type", 0)
            if (
                isinstance(message_type, int)
                and message_type in deletable_type_values
            ):
                deletable_message_ids.add(message_id)
            if author_id == target_id:
                continue
            reactions = payload.get("reactions", [])
            if not isinstance(reactions, list):
                raise LiveSuiteSafetyError(
                    "Discord returned malformed destructive contract reactions."
                )
            for reaction in reactions:
                if not isinstance(reaction, Mapping):
                    raise LiveSuiteSafetyError(
                        "Discord returned malformed destructive contract reactions."
                    )
                count = reaction.get("count", 0)
                if not isinstance(count, int) or count < 0:
                    raise LiveSuiteSafetyError(
                        "Discord returned an invalid destructive contract reaction count."
                    )
                own_count = int(reaction.get("me") is True) + int(
                    reaction.get("me_burst") is True
                )
                subject_reactions += own_count
                foreign_reactions += max(0, count - own_count)

        return DestructiveContractObservation(
            True,
            archived,
            message_authors,
            frozenset(deletable_message_ids),
            subject_reactions,
            foreign_reactions,
            locked,
            auto_archive_duration,
        )
    finally:
        if owns_client:
            session.close()


def _adopt_contract_deletable_messages(
    ledger: LiveLedger,
    ledger_path: Path,
    scope: DestructiveContractScope,
    resource: LedgerResource,
    observation: DestructiveContractObservation,
) -> list[LedgerResource]:
    tracked_by_id = {
        message.resource_id: message
        for message in ledger.resources
        if message.kind == "message"
        and message.parent_id == resource.resource_id
        and message.state not in TERMINAL_RESOURCE_STATES
    }
    roles_by_id = {user_id: role for role, user_id in ledger.accounts.items()}
    adopted = False
    for message_id in sorted(observation.deletable_message_ids):
        if message_id in tracked_by_id:
            continue
        author_id = observation.message_authors.get(message_id)
        account_role = roles_by_id.get(author_id)
        if account_role is None:
            continue
        digest = hashlib.sha256(message_id.encode()).hexdigest()[:20]
        fixture_key = _contract_capability_key(
            scope,
            f"message:{digest}",
        )
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=fixture_key,
            kind="message",
            resource_id=message_id,
            owner_handle=account_role,
            guild_id=resource.guild_id,
            parent_id=resource.resource_id,
        ))
        adopted = True
    if adopted:
        ledger.phase = "destructive_contract_previewing"
        ledger.destructive_unlocked = False
        ledger.save(ledger_path)
    return _contract_scope_messages(ledger, resource)


def _assert_contract_precondition(
    ledger: LiveLedger,
    scope: DestructiveContractScope,
    messages: Sequence[LedgerResource],
    observation: DestructiveContractObservation,
) -> tuple[int, int, int, int]:
    if not observation.container_exists:
        raise LiveSuiteSafetyError(
            "A destructive contract container is absent before execution."
        )
    expected_authors = {
        message.resource_id: ledger.accounts[message.owner_handle]
        for message in messages
    }
    for message_id, expected_author in expected_authors.items():
        if observation.message_authors.get(message_id) != expected_author:
            raise LiveSuiteSafetyError(
                "A destructive contract scope does not match its tracked messages."
            )
    untracked_subject_messages = {
        message_id
        for message_id in observation.deletable_message_ids
        if observation.message_authors.get(message_id) == ledger.accounts["subject"]
        and message_id not in expected_authors
    }
    if untracked_subject_messages:
        raise LiveSuiteSafetyError(
            "A destructive contract scope has untracked subject messages."
        )
    if scope.archived is not None and observation.archived is not scope.archived:
        raise LiveSuiteSafetyError(
            "A destructive contract thread has an unexpected archive state."
        )
    subject_messages = sum(
        message.owner_handle == "subject" for message in messages
    )
    foreign_messages = len(messages) - subject_messages
    if observation.subject_reactions_on_foreign_messages < 1:
        raise LiveSuiteSafetyError(
            "A destructive contract scope has no subject reaction on foreign content."
        )
    if observation.foreign_reactions_on_foreign_messages < 1:
        raise LiveSuiteSafetyError(
            "A destructive contract scope has no foreign reaction to preserve."
        )
    return (
        subject_messages,
        foreign_messages,
        observation.subject_reactions_on_foreign_messages,
        observation.foreign_reactions_on_foreign_messages,
    )


def _assert_contract_postcondition(
    ledger: LiveLedger,
    scope: DestructiveContractScope,
    messages: Sequence[LedgerResource],
    observation: DestructiveContractObservation,
    *,
    baseline_subject_reactions: int,
    baseline_foreign_reactions: int,
    subject_messages_removed: bool = True,
) -> None:
    if not observation.container_exists:
        raise LiveSuiteSafetyError(
            "DMD removed a destructive contract container unexpectedly."
        )
    if scope.archived is not None and observation.archived is not scope.archived:
        raise LiveSuiteSafetyError(
            "DMD changed a destructive contract thread archive state."
        )
    expected_remaining_ids = {
        message.resource_id
        for message in messages
        if message.owner_handle != "subject" or not subject_messages_removed
    }
    observed_configured_ids = {
        message_id
        for message_id in observation.deletable_message_ids
        if observation.message_authors.get(message_id) in ledger.accounts.values()
    }
    if observed_configured_ids != expected_remaining_ids:
        raise LiveSuiteSafetyError(
            "Tracked message membership changed during destructive cleanup."
        )
    for message in messages:
        observed_author = observation.message_authors.get(message.resource_id)
        if message.owner_handle == "subject":
            expected_subject_author = (
                None
                if subject_messages_removed
                else ledger.accounts[message.owner_handle]
            )
            if observed_author != expected_subject_author:
                raise LiveSuiteSafetyError(
                    "A tracked subject message has an unexpected destructive "
                    "postcondition."
                )
            continue
        if observed_author != ledger.accounts[message.owner_handle]:
            raise LiveSuiteSafetyError(
                "A tracked foreign message changed during destructive cleanup."
            )
    expected_subject_reactions = 0
    if (
        observation.subject_reactions_on_foreign_messages
        != expected_subject_reactions
    ):
        raise LiveSuiteSafetyError(
            "Subject reaction cleanup produced an unexpected postcondition."
        )
    if (
        observation.foreign_reactions_on_foreign_messages
        != baseline_foreign_reactions
    ):
        raise LiveSuiteSafetyError(
            "Foreign reactions changed during destructive cleanup."
        )


def _record_contract_reaction(
    ledger: LiveLedger,
    ledger_path: Path,
    scope: DestructiveContractScope,
    resource: LedgerResource,
    message: LedgerResource,
    account_role: str,
    emoji: str,
    client: DiscordFixtureClient,
) -> None:
    fixture_key = _contract_capability_key(
        scope,
        f"reaction:{account_role}",
    )
    existing = ledger.resource_for_fixture(fixture_key)
    client.add_reaction(resource.resource_id, message.resource_id, emoji=emoji)
    if existing is None:
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=fixture_key,
            kind="reaction",
            resource_id=_reaction_resource_id(
                message.resource_id,
                account_role,
                emoji,
                0,
            ),
            owner_handle=account_role,
            guild_id=resource.guild_id,
            parent_id=resource.resource_id,
        ))
    else:
        _require_active_resource(ledger, fixture_key, "reaction")
    ledger.phase = "destructive_contract_previewing"
    ledger.destructive_unlocked = False
    ledger.save(ledger_path)


def _ensure_contract_reaction_coverage(
    ledger: LiveLedger,
    ledger_path: Path,
    scope: DestructiveContractScope,
    resource: LedgerResource,
    messages: Sequence[LedgerResource],
    observation: DestructiveContractObservation,
    clients: Mapping[str, DiscordFixtureClient],
) -> bool:
    missing_subject = observation.subject_reactions_on_foreign_messages == 0
    missing_foreign = observation.foreign_reactions_on_foreign_messages == 0
    if not missing_subject and not missing_foreign:
        return False
    if scope.archived:
        raise LiveSuiteSafetyError(
            "An archived contract thread is missing its seeded reaction coverage."
        )
    foreign_message = next(
        message for message in messages if message.owner_handle != "subject"
    )
    if missing_subject:
        _record_contract_reaction(
            ledger,
            ledger_path,
            scope,
            resource,
            foreign_message,
            "subject",
            "🔎",
            clients["subject"],
        )
    if missing_foreign:
        account_role = foreign_message.owner_handle
        _record_contract_reaction(
            ledger,
            ledger_path,
            scope,
            resource,
            foreign_message,
            account_role,
            "📌",
            clients[account_role],
        )
    return True


_CONTRACT_DRY_RUN_PATTERN = re.compile(
    r"Summary:\s+messages\s+(\d+)\s+delete\s+/\s+(\d+)\s+keep,\s+"
    r"reactions\s+(\d+)\s+delete\s+/\s+(\d+)\s+keep"
)
_CONTRACT_EXECUTION_PATTERN = re.compile(
    r"Summary:\s+messages\s+(\d+)\s+deleted\s+/\s+(\d+)\s+absent\s+/\s+"
    r"(\d+)\s+failed\s+/\s+(\d+)\s+kept,\s+reactions\s+(\d+)\s+deleted\s+"
    r"/\s+(\d+)\s+absent\s+/\s+(\d+)\s+failed\s+/\s+(\d+)\s+kept"
)


def _require_contract_preview(
    output: str,
    *,
    expected_messages: int,
    expected_reactions: int,
) -> None:
    match = _CONTRACT_DRY_RUN_PATTERN.search(output)
    if match is None:
        raise LiveSuiteSafetyError(
            "A destructive contract preview returned an incomplete summary."
        )
    messages_delete, _messages_keep, reactions_delete, _reactions_keep = map(
        int,
        match.groups(),
    )
    if (
        messages_delete != expected_messages
        or reactions_delete != expected_reactions
    ):
        raise LiveSuiteSafetyError(
            "A destructive contract preview did not match tracked state "
            f"(messages expected={expected_messages}, actual={messages_delete}; "
            f"reactions expected={expected_reactions}, actual={reactions_delete})."
        )


def _require_contract_execution(
    output: str,
    *,
    expected_messages: int,
    expected_reactions: int,
    allow_message_rejection: bool = False,
) -> bool:
    match = _CONTRACT_EXECUTION_PATTERN.search(output)
    if match is None:
        raise LiveSuiteSafetyError(
            "A destructive contract execution returned an incomplete summary."
        )
    (
        messages_deleted,
        messages_absent,
        messages_failed,
        _messages_kept,
        reactions_deleted,
        reactions_absent,
        reactions_failed,
        _reactions_kept,
    ) = map(int, match.groups())
    reactions_match = (
        reactions_deleted + reactions_absent == expected_reactions
        and reactions_failed == 0
    )
    messages_removed = (
        messages_deleted + messages_absent == expected_messages
        and messages_failed == 0
    )
    messages_rejected = (
        allow_message_rejection
        and messages_deleted == 0
        and messages_absent == 0
        and messages_failed == expected_messages
    )
    if not reactions_match or not (messages_removed or messages_rejected):
        raise LiveSuiteSafetyError(
            "A destructive contract execution reported an unexpected outcome."
        )
    return messages_removed


def _run_archived_thread_race_preview(
    token: str,
    thread_id: str,
    *,
    expected_messages: int = 1,
    expected_reactions: int = 1,
    attempts: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Allow Discord's thread-search index a bounded window to expose a new fixture."""
    if attempts < 1:
        raise ValueError("Archived-thread race preview attempts must be positive.")
    for attempt in range(1, attempts + 1):
        output = _run_scoped_dmd(
            token,
            thread_id,
            dry_run=True,
        )
        try:
            _require_contract_preview(
                output,
                expected_messages=expected_messages,
                expected_reactions=expected_reactions,
            )
        except LiveSuiteSafetyError:
            if attempt == attempts:
                raise
            sleep(float(attempt * 10))
        else:
            return


def _contract_baseline(
    ledger: LiveLedger,
    scope: DestructiveContractScope,
) -> tuple[int, int, int, int]:
    fields = (
        "subject-messages",
        "foreign-messages",
        "subject-reactions",
        "foreign-reactions",
    )
    values: list[int] = []
    for field_name in fields:
        raw_value = ledger.capabilities.get(
            _contract_capability_key(scope, field_name)
        )
        if raw_value is None or not raw_value.isdigit():
            raise LiveSuiteSafetyError(
                "A destructive contract scope has no valid preview baseline."
            )
        values.append(int(raw_value))
    return values[0], values[1], values[2], values[3]


def _available_contract_scopes(
    ledger: LiveLedger,
) -> list[tuple[DestructiveContractScope, LedgerResource]]:
    available = []
    for scope in DESTRUCTIVE_CONTRACT_SCOPES:
        resource = ledger.resource_for_fixture(scope.fixture_key)
        if resource is None:
            if scope.optional:
                ledger.capabilities[
                    _contract_capability_key(scope, "preview")
                ] = "unsupported"
                continue
            raise LiveSuiteSafetyError(
                "A required destructive contract scope is absent."
            )
        available.append((
            scope,
            _require_active_resource(
                ledger,
                scope.fixture_key,
                scope.resource_kind,
            ),
        ))
    return available


def prepare_destructive_contract_matrix(
    ledger: LiveLedger,
    ledger_path: Path,
    subject_token: str,
    clients: Mapping[str, DiscordFixtureClient],
    *,
    pacer: FixturePacer | None = None,
) -> int:
    """Verify exact previews for every available message-bearing scope."""
    if ledger.phase not in {
        "forum_starter_smoke_verified",
        "destructive_contract_previewing",
        "destructive_contract_previewed",
        "destructive_contract_verified",
    }:
        raise LiveSuiteSafetyError(
            "The destructive contract matrix requires forum starter verification."
        )
    if set(clients) != set(FIXTURE_ROLES):
        raise LiveSuiteSafetyError(
            "The destructive contract matrix requires all fixture clients."
        )
    request_pacer = pacer or FixturePacer()
    available = _available_contract_scopes(ledger)
    pending = [
        (scope, resource)
        for scope, resource in available
        if ledger.capabilities.get(
            _contract_capability_key(scope, "execution")
        ) not in {"deleted", "reconciled-absent"}
    ]
    if not pending:
        ledger.phase = "destructive_contract_verified"
        ledger.destructive_unlocked = False
        ledger.save(ledger_path)
        return 0

    previewed = 0
    for ordinal, (scope, resource) in enumerate(pending, start=1):
        messages = _contract_scope_messages(ledger, resource)
        observation = observe_destructive_contract_scope(
            subject_token,
            scope,
            resource,
            messages,
            ledger.accounts,
            pacer=request_pacer,
        )
        messages = _adopt_contract_deletable_messages(
            ledger,
            ledger_path,
            scope,
            resource,
            observation,
        )
        if _ensure_contract_reaction_coverage(
            ledger,
            ledger_path,
            scope,
            resource,
            messages,
            observation,
            clients,
        ):
            observation = observe_destructive_contract_scope(
                subject_token,
                scope,
                resource,
                messages,
                ledger.accounts,
                pacer=request_pacer,
            )
        baseline = _assert_contract_precondition(
            ledger,
            scope,
            messages,
            observation,
        )
        expected_reactions = baseline[2]
        preview = _run_scoped_dmd(
            subject_token,
            resource.resource_id,
            dry_run=True,
        )
        _require_contract_preview(
            preview,
            expected_messages=baseline[0],
            expected_reactions=expected_reactions,
        )
        for field_name, value in zip(
            (
                "subject-messages",
                "foreign-messages",
                "subject-reactions",
                "foreign-reactions",
            ),
            baseline,
            strict=True,
        ):
            ledger.capabilities[
                _contract_capability_key(scope, field_name)
            ] = str(value)
        ledger.capabilities[_contract_capability_key(scope, "preview")] = (
            "verified"
        )
        ledger.phase = "destructive_contract_previewing"
        ledger.destructive_unlocked = False
        ledger.save(ledger_path)
        previewed += 1
        print(f"contract-preview-{ordinal}: verified ({scope.scope_key})")

    ledger.phase = "destructive_contract_previewed"
    ledger.destructive_unlocked = True
    ledger.save(ledger_path)
    return previewed


def _mark_contract_subject_messages(
    ledger: LiveLedger,
    messages: Sequence[LedgerResource],
    state: str,
) -> None:
    for message in messages:
        if message.owner_handle == "subject":
            ledger.set_resource_state(message.fixture_key, state)


def execute_destructive_contract_matrix(
    ledger: LiveLedger,
    ledger_path: Path,
    subject_token: str,
    *,
    pacer: FixturePacer | None = None,
) -> int:
    """Execute and independently verify each previewed contract scope."""
    if ledger.phase not in {
        "destructive_contract_previewed",
        "destructive_contract_executing",
    } or not ledger.destructive_unlocked:
        raise LiveSuiteSafetyError(
            "Destructive contract execution requires its verified preview gate."
        )
    request_pacer = pacer or FixturePacer()
    available = _available_contract_scopes(ledger)
    ledger.phase = "destructive_contract_executing"
    ledger.save(ledger_path)
    verified = 0
    for ordinal, (scope, resource) in enumerate(available, start=1):
        execution_key = _contract_capability_key(scope, "execution")
        if ledger.capabilities.get(execution_key) in {
            "deleted",
            "reconciled-absent",
        }:
            verified += 1
            continue
        if ledger.capabilities.get(
            _contract_capability_key(scope, "preview")
        ) != "verified":
            raise LiveSuiteSafetyError(
                "A destructive contract scope was not previewed."
            )
        messages = _contract_scope_messages(ledger, resource)
        baseline = _contract_baseline(ledger, scope)
        observation = observe_destructive_contract_scope(
            subject_token,
            scope,
            resource,
            messages,
            ledger.accounts,
            pacer=request_pacer,
        )
        present_subject_messages = sum(
            message.owner_handle == "subject"
            and message.resource_id in observation.message_authors
            for message in messages
        )
        if present_subject_messages == 0:
            _assert_contract_postcondition(
                ledger,
                scope,
                messages,
                observation,
                baseline_subject_reactions=baseline[2],
                baseline_foreign_reactions=baseline[3],
            )
            _mark_contract_subject_messages(ledger, messages, "absent")
            ledger.capabilities[execution_key] = "reconciled-absent"
            ledger.save(ledger_path)
            verified += 1
            print(f"contract-execute-{ordinal}: reconciled ({scope.scope_key})")
            continue
        if present_subject_messages != baseline[0]:
            raise LiveSuiteSafetyError(
                "A destructive contract scope is partially modified "
                f"(subject messages present={present_subject_messages}, "
                f"expected={baseline[0]})."
            )
        current = _assert_contract_precondition(
            ledger,
            scope,
            messages,
            observation,
        )
        if current != baseline:
            raise LiveSuiteSafetyError(
                "A destructive contract scope changed after preview."
            )
        expected_reactions = baseline[2]
        preview = _run_scoped_dmd(
            subject_token,
            resource.resource_id,
            dry_run=True,
        )
        _require_contract_preview(
            preview,
            expected_messages=baseline[0],
            expected_reactions=expected_reactions,
        )
        result = _run_scoped_dmd(
            subject_token,
            resource.resource_id,
            dry_run=False,
        )
        subject_messages_removed = _require_contract_execution(
            result,
            expected_messages=baseline[0],
            expected_reactions=expected_reactions,
        )
        postcondition = observe_destructive_contract_scope(
            subject_token,
            scope,
            resource,
            messages,
            ledger.accounts,
            pacer=request_pacer,
        )
        _assert_contract_postcondition(
            ledger,
            scope,
            messages,
            postcondition,
            baseline_subject_reactions=baseline[2],
            baseline_foreign_reactions=baseline[3],
            subject_messages_removed=subject_messages_removed,
        )
        if not subject_messages_removed:
            raise LiveSuiteSafetyError(
                "Archived temporary cleanup did not remove subject messages."
            )
        _mark_contract_subject_messages(ledger, messages, "deleted")
        ledger.capabilities[execution_key] = "deleted"
        outcome = "verified"
        ledger.save(ledger_path)
        verified += 1
        print(f"contract-execute-{ordinal}: {outcome} ({scope.scope_key})")

    ledger.phase = "destructive_contract_verified"
    ledger.destructive_unlocked = False
    ledger.save(ledger_path)
    return verified


def _race_capability_key(
    scenario: ArchivedThreadRaceScenario,
    field: str,
) -> str:
    return f"archived-thread-race:{scenario.scenario_key}:{field}"


def _race_fixture_key(
    scenario: ArchivedThreadRaceScenario,
    artifact: str,
) -> str:
    return f"archived-thread-race:{scenario.scenario_key}:{artifact}"


def _race_scope(
    scenario: ArchivedThreadRaceScenario,
) -> DestructiveContractScope:
    return DestructiveContractScope(
        scenario.scenario_key,
        _race_fixture_key(scenario, "thread"),
        "thread",
        ChannelType.PUBLIC_THREAD,
        archived=True,
    )


def _race_foreign_roles(
    scenario: ArchivedThreadRaceScenario,
) -> tuple[str, str]:
    if scenario.target_role == "peer_b":
        return "subject", "peer_a"
    return "peer_a", "peer_b"


def _race_messages(
    ledger: LiveLedger,
    scenario: ArchivedThreadRaceScenario,
    thread: LedgerResource,
) -> tuple[LedgerResource, LedgerResource]:
    target = _require_active_resource(
        ledger,
        _race_fixture_key(scenario, "message:target"),
        "message",
    )
    foreign = _require_active_resource(
        ledger,
        _race_fixture_key(scenario, "message:foreign"),
        "message",
    )
    foreign_role, _foreign_reactor = _race_foreign_roles(scenario)
    if (
        target.owner_handle != scenario.target_role
        or foreign.owner_handle != foreign_role
        or target.parent_id != thread.resource_id
        or foreign.parent_id != thread.resource_id
    ):
        raise LiveSuiteSafetyError(
            "An archived-thread race fixture has invalid tracked ownership."
        )
    return target, foreign


def _ensure_race_thread(
    ledger: LiveLedger,
    ledger_path: Path,
    scenario: ArchivedThreadRaceScenario,
    parent: LedgerResource,
    clients: Mapping[str, DiscordFixtureClient],
) -> tuple[LedgerResource, int]:
    fixture_key = _race_fixture_key(scenario, "thread")
    existing = ledger.resource_for_fixture(fixture_key)
    if existing is not None:
        thread = _require_active_resource(
            ledger,
            fixture_key,
            "thread",
            guild_id=parent.guild_id,
        )
        if thread.parent_id != parent.resource_id or thread.owner_handle != "subject":
            raise LiveSuiteSafetyError(
                "An archived-thread race fixture has invalid tracked ancestry."
            )
        return thread, 0

    snapshot = clients["subject"].start_thread(
        parent.resource_id,
        name=f"dmd-live-race-{scenario.scenario_key}",
        thread_type=int(ChannelType.PUBLIC_THREAD),
        auto_archive_duration=60,
    )
    if (
        snapshot.parent_id != parent.resource_id
        or snapshot.thread_type != int(ChannelType.PUBLIC_THREAD)
    ):
        raise LiveSuiteSafetyError(
            "Discord returned an unexpected archived-thread race fixture."
        )
    thread = LedgerResource(
        run_id=ledger.run_id,
        fixture_key=fixture_key,
        kind="thread",
        resource_id=snapshot.channel_id,
        owner_handle="subject",
        guild_id=parent.guild_id,
        parent_id=parent.resource_id,
    )
    ledger.record_resource(thread)
    ledger.phase = "archived_thread_race_seeding"
    ledger.destructive_unlocked = False
    ledger.save(ledger_path)
    return thread, 1


def _ensure_race_message(
    ledger: LiveLedger,
    ledger_path: Path,
    scenario: ArchivedThreadRaceScenario,
    thread: LedgerResource,
    *,
    artifact: str,
    owner_role: str,
    client: DiscordFixtureClient,
) -> tuple[LedgerResource, int]:
    fixture_key = _race_fixture_key(scenario, f"message:{artifact}")
    existing = ledger.resource_for_fixture(fixture_key)
    if existing is not None:
        message = _require_active_resource(
            ledger,
            fixture_key,
            "message",
            guild_id=thread.guild_id,
        )
        if (
            message.parent_id != thread.resource_id
            or message.owner_handle != owner_role
        ):
            raise LiveSuiteSafetyError(
                "An archived-thread race message has invalid tracked ownership."
            )
        return message, 0

    snapshot = client.send_message(
        thread.resource_id,
        content=(
            "DMD live archived-thread race fixture "
            f"{scenario.scenario_key} {artifact}."
        ),
    )
    message = LedgerResource(
        run_id=ledger.run_id,
        fixture_key=fixture_key,
        kind="message",
        resource_id=snapshot.message_id,
        owner_handle=owner_role,
        guild_id=thread.guild_id,
        parent_id=thread.resource_id,
    )
    ledger.record_resource(message)
    ledger.phase = "archived_thread_race_seeding"
    ledger.destructive_unlocked = False
    ledger.save(ledger_path)
    return message, 1


def _ensure_race_reaction(
    ledger: LiveLedger,
    ledger_path: Path,
    scenario: ArchivedThreadRaceScenario,
    thread: LedgerResource,
    foreign_message: LedgerResource,
    *,
    artifact: str,
    owner_role: str,
    emoji: str,
    client: DiscordFixtureClient,
) -> int:
    fixture_key = _race_fixture_key(scenario, f"reaction:{artifact}")
    existing = ledger.resource_for_fixture(fixture_key)
    if existing is not None:
        reaction = _require_active_resource(
            ledger,
            fixture_key,
            "reaction",
            guild_id=thread.guild_id,
        )
        if (
            reaction.parent_id != thread.resource_id
            or reaction.owner_handle != owner_role
        ):
            raise LiveSuiteSafetyError(
                "An archived-thread race reaction has invalid tracked ownership."
            )
        return 0

    client.add_reaction(
        thread.resource_id,
        foreign_message.resource_id,
        emoji=emoji,
    )
    ledger.record_resource(LedgerResource(
        run_id=ledger.run_id,
        fixture_key=fixture_key,
        kind="reaction",
        resource_id=_reaction_resource_id(
            foreign_message.resource_id,
            owner_role,
            emoji,
            0,
        ),
        owner_handle=owner_role,
        guild_id=thread.guild_id,
        parent_id=thread.resource_id,
    ))
    ledger.phase = "archived_thread_race_seeding"
    ledger.destructive_unlocked = False
    ledger.save(ledger_path)
    return 1


def _seed_archived_thread_race_scenario(
    ledger: LiveLedger,
    ledger_path: Path,
    scenario: ArchivedThreadRaceScenario,
    parent: LedgerResource,
    clients: Mapping[str, DiscordFixtureClient],
) -> tuple[LedgerResource, tuple[LedgerResource, LedgerResource], int]:
    thread, mutations = _ensure_race_thread(
        ledger,
        ledger_path,
        scenario,
        parent,
        clients,
    )
    foreign_role, foreign_reactor = _race_foreign_roles(scenario)
    target_message, count = _ensure_race_message(
        ledger,
        ledger_path,
        scenario,
        thread,
        artifact="target",
        owner_role=scenario.target_role,
        client=clients[scenario.target_role],
    )
    mutations += count
    foreign_message, count = _ensure_race_message(
        ledger,
        ledger_path,
        scenario,
        thread,
        artifact="foreign",
        owner_role=foreign_role,
        client=clients[foreign_role],
    )
    mutations += count
    mutations += _ensure_race_reaction(
        ledger,
        ledger_path,
        scenario,
        thread,
        foreign_message,
        artifact="target",
        owner_role=scenario.target_role,
        emoji="🧪",
        client=clients[scenario.target_role],
    )
    mutations += _ensure_race_reaction(
        ledger,
        ledger_path,
        scenario,
        thread,
        foreign_message,
        artifact="foreign",
        owner_role=foreign_reactor,
        emoji="🔒",
        client=clients[foreign_reactor],
    )
    state_key = _race_capability_key(scenario, "seed-state")
    expected_state = "archived-locked" if scenario.initial_locked else "archived"
    state = clients["peer_b"].set_thread_state(
        thread.resource_id,
        archived=True,
        locked=scenario.initial_locked,
    )
    if (
        state.archived is not None
        and state.archived is not True
    ) or (
        state.locked is not None
        and state.locked is not scenario.initial_locked
    ):
        raise LiveSuiteSafetyError(
            "Discord returned an unexpected archived-thread race state."
        )
    ledger.capabilities[state_key] = expected_state
    ledger.phase = "archived_thread_race_seeding"
    ledger.destructive_unlocked = False
    ledger.save(ledger_path)
    mutations += 1
    return thread, (target_message, foreign_message), mutations


def _assert_race_precondition(
    ledger: LiveLedger,
    scenario: ArchivedThreadRaceScenario,
    messages: Sequence[LedgerResource],
    observation: DestructiveContractObservation,
) -> tuple[int, int]:
    if (
        not observation.container_exists
        or observation.archived is not True
        or observation.locked is not scenario.initial_locked
        or observation.auto_archive_duration != 60
    ):
        raise LiveSuiteSafetyError(
            "An archived-thread race fixture has an unexpected thread state."
        )
    expected_authors = {
        message.resource_id: ledger.accounts[message.owner_handle]
        for message in messages
    }
    if any(
        observation.message_authors.get(message_id) != author_id
        for message_id, author_id in expected_authors.items()
    ):
        raise LiveSuiteSafetyError(
            "An archived-thread race fixture does not match tracked messages."
        )
    target_id = ledger.accounts[scenario.target_role]
    untracked_target_messages = {
        message_id
        for message_id in observation.deletable_message_ids
        if observation.message_authors.get(message_id) == target_id
        and message_id not in expected_authors
    }
    if untracked_target_messages:
        raise LiveSuiteSafetyError(
            "An archived-thread race fixture has untracked target messages."
        )
    if observation.subject_reactions_on_foreign_messages != 1:
        raise LiveSuiteSafetyError(
            "An archived-thread race fixture has unexpected target reactions."
        )
    if observation.foreign_reactions_on_foreign_messages < 1:
        raise LiveSuiteSafetyError(
            "An archived-thread race fixture has no foreign reaction to preserve."
        )
    return (
        observation.subject_reactions_on_foreign_messages,
        observation.foreign_reactions_on_foreign_messages,
    )


def _assert_race_postcondition(
    ledger: LiveLedger,
    scenario: ArchivedThreadRaceScenario,
    messages: Sequence[LedgerResource],
    observation: DestructiveContractObservation,
    *,
    baseline_foreign_reactions: int,
) -> None:
    expected_locked = (
        True if scenario.trigger == "lock-changed" else scenario.initial_locked
    )
    if (
        not observation.container_exists
        or observation.archived is not True
        or observation.locked is not expected_locked
        or observation.auto_archive_duration != 60
    ):
        raise LiveSuiteSafetyError(
            "Archived-thread cleanup produced an unexpected final thread state."
        )
    target_message, foreign_message = messages
    target_author = observation.message_authors.get(target_message.resource_id)
    foreign_author = observation.message_authors.get(foreign_message.resource_id)
    expected_target_author = (
        None
        if scenario.expect_cleanup
        else ledger.accounts[scenario.target_role]
    )
    if target_author != expected_target_author:
        raise LiveSuiteSafetyError(
            "Archived-thread cleanup produced an unexpected target-message state."
        )
    if foreign_author != ledger.accounts[foreign_message.owner_handle]:
        raise LiveSuiteSafetyError(
            "Archived-thread cleanup changed a foreign message."
        )
    expected_target_reactions = 0 if scenario.expect_cleanup else 1
    if (
        observation.subject_reactions_on_foreign_messages
        != expected_target_reactions
    ):
        raise LiveSuiteSafetyError(
            "Archived-thread cleanup produced an unexpected target-reaction state."
        )
    if (
        observation.foreign_reactions_on_foreign_messages
        != baseline_foreign_reactions
    ):
        raise LiveSuiteSafetyError(
            "Archived-thread cleanup changed a foreign reaction."
        )


class _RaceClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class _ArchivedThreadRaceAPI:
    def __init__(
        self,
        api: DiscordClient,
        *,
        thread_id: str,
        scenario: ArchivedThreadRaceScenario,
        manager: DiscordFixtureClient,
        clock: _RaceClock,
        auto_archive_duration_seconds: float,
    ) -> None:
        self._api = api
        self._thread_id = thread_id
        self._scenario = scenario
        self._manager = manager
        self._clock = clock
        self._auto_archive_duration_seconds = auto_archive_duration_seconds
        self.hook_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._api, name)

    def _before_mutation(self, channel_id: str) -> None:
        if str(channel_id) != self._thread_id:
            raise LiveSuiteSafetyError(
                "A race hook received a mutation outside its isolated thread."
            )
        next_hook = self.hook_count + 1
        trigger = self._scenario.trigger
        should_archive = (
            trigger in {"early-archive", "likely-auto-archive", "lock-changed"}
            and next_hook == 1
        ) or (trigger == "second-archive" and next_hook <= 2)
        if not should_archive:
            return

        if trigger == "early-archive":
            self._clock.value = 60.0
        else:
            self._clock.value = self._auto_archive_duration_seconds
        self._manager.set_thread_state(
            self._thread_id,
            archived=True,
            locked=True if trigger == "lock-changed" else None,
        )
        self.hook_count = next_hook

    def delete_message(self, channel_id: str, message_id: str):
        self._before_mutation(channel_id)
        return self._api.delete_message(channel_id, message_id)

    def delete_own_reaction(
        self,
        channel_id: str,
        message_id: str,
        emoji,
        reaction_type=0,
    ):
        self._before_mutation(channel_id)
        return self._api.delete_own_reaction(
            channel_id,
            message_id,
            emoji,
            reaction_type,
        )


@contextmanager
def _silenced_redacted_dmd_logging() -> Iterator[None]:
    previous_config = get_redaction_config()
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level
    previous_disabled = root_logger.disabled
    set_redaction_config(RedactionConfig(enabled=True, redact_names=True))
    root_logger.handlers = [logging.NullHandler()]
    root_logger.setLevel(logging.CRITICAL + 1)
    root_logger.disabled = False
    try:
        yield
    finally:
        set_redaction_config(previous_config)
        root_logger.handlers = previous_handlers
        root_logger.setLevel(previous_level)
        root_logger.disabled = previous_disabled


def _race_inventory(
    api: DiscordClient,
    thread: LedgerResource,
) -> tuple[ScopeInventory, dict[str, Any]]:
    if thread.guild_id is None or thread.parent_id is None:
        raise LiveSuiteSafetyError(
            "An archived-thread race fixture has incomplete ancestry."
        )
    guild = next(
        (
            candidate
            for candidate in api.get_guilds()
            if str(candidate.get("id")) == thread.guild_id
        ),
        None,
    )
    if guild is None:
        raise LiveSuiteSafetyError(
            "The target account cannot access the archived-thread race guild."
        )
    parent = api.get_channel(thread.parent_id)
    current_thread = api.get_channel(thread.resource_id)
    if (
        str(parent.get("id")) != thread.parent_id
        or str(current_thread.get("id")) != thread.resource_id
        or str(current_thread.get("parent_id")) != thread.parent_id
        or str(current_thread.get("guild_id")) != thread.guild_id
        or current_thread.get("type") != int(ChannelType.PUBLIC_THREAD)
    ):
        raise LiveSuiteSafetyError(
            "Discord returned mismatched archived-thread race scope data."
        )
    normalized_parent = dict(parent)
    normalized_parent.setdefault("guild_id", thread.guild_id)
    normalized_thread = dict(current_thread)
    normalized_thread.setdefault("guild_id", thread.guild_id)
    if normalized_parent.get("parent_id") is not None:
        normalized_thread["category_id"] = str(normalized_parent["parent_id"])
    return (
        ScopeInventory(
            guilds=[guild],
            root_channels=[],
            guild_channels_by_guild={thread.guild_id: [normalized_parent]},
            threads_by_guild={thread.guild_id: [normalized_thread]},
        ),
        normalized_thread,
    )


def _run_archived_thread_race_cleanup(
    token: str,
    user_id: str,
    scenario: ArchivedThreadRaceScenario,
    thread: LedgerResource,
    manager: DiscordFixtureClient,
    journal_path: Path,
    *,
    fetch_interval: tuple[float, float] = (1.0, 2.0),
    delete_interval: tuple[float, float] = (3.0, 6.0),
    api_factory: Callable[..., DiscordClient] = DiscordClient,
) -> tuple[int, int]:
    api = api_factory(token=token)
    try:
        with _silenced_redacted_dmd_logging():
            inventory, current_thread = _race_inventory(api, thread)
            metadata = current_thread.get("thread_metadata")
            auto_archive_duration = (
                metadata.get("auto_archive_duration")
                if isinstance(metadata, Mapping)
                else None
            )
            if (
                isinstance(auto_archive_duration, bool)
                or not isinstance(auto_archive_duration, int)
                or auto_archive_duration <= 0
            ):
                raise LiveSuiteSafetyError(
                    "Discord omitted the archived-thread race timeout."
                )
            clock = _RaceClock()
            wrapped_api = _ArchivedThreadRaceAPI(
                api,
                thread_id=thread.resource_id,
                scenario=scenario,
                manager=manager,
                clock=clock,
                auto_archive_duration_seconds=float(
                    auto_archive_duration * 60
                ),
            )
            cleaner = MessageCleaner(
                api=wrapped_api,
                user_id=user_id,
                include_ids=[thread.resource_id],
                scope_inventory=inventory,
                thread_restoration_journal=ThreadRestorationJournal(
                    str(journal_path)
                ),
            )
            cleaner._thread_state_clock = clock
            deleted = cleaner.clean_messages(
                fetch_sleep_time_range=fetch_interval,
                delete_sleep_time_range=delete_interval,
                delete_reactions=True,
                archived_thread_cleanup="temporary",
            )
            return deleted, wrapped_api.hook_count
    except LiveSuiteError:
        raise
    except Exception:
        raise LiveSuiteSafetyError(
            "The archived-thread race cleanup failed with a redacted diagnostic."
        ) from None
    finally:
        api.close()


def _assert_race_journal_empty(
    journal_path: Path,
    accounts: Mapping[str, str],
) -> None:
    journal = ThreadRestorationJournal(str(journal_path))
    try:
        has_pending = any(
            journal.pending(user_id) for user_id in accounts.values()
        )
    except (OSError, TypeError, ValueError):
        raise LiveSuiteSafetyError(
            "The archived-thread race restoration journal is invalid."
        ) from None
    if has_pending:
        raise LiveSuiteSafetyError(
            "The archived-thread race restoration journal is not empty."
        )


def prepare_archived_thread_race_matrix(
    ledger: LiveLedger,
    ledger_path: Path,
    tokens: Mapping[str, str],
    clients: Mapping[str, DiscordFixtureClient],
    *,
    pacer: FixturePacer | None = None,
    journal_path: Path = DEFAULT_ARCHIVED_THREAD_RACE_JOURNAL_PATH,
) -> tuple[int, int]:
    """Seed isolated race fixtures and require exact redacted previews."""
    allowed_phases = {
        "destructive_contract_verified",
        "archived_thread_race_seeding",
        "archived_thread_race_previewing",
        "archived_thread_race_previewed",
        "archived_thread_race_interrupted",
        "archived_thread_race_verified",
    }
    if ledger.phase not in allowed_phases:
        raise LiveSuiteSafetyError(
            "Archived-thread race preview requires destructive contract verification."
        )
    if set(tokens) != set(FIXTURE_ROLES) or set(clients) != set(FIXTURE_ROLES):
        raise LiveSuiteSafetyError(
            "Archived-thread race testing requires all fixture accounts."
        )
    _assert_race_journal_empty(journal_path, ledger.accounts)
    parent = _require_active_resource(
        ledger,
        "channel:permission:threads",
        "channel",
    )
    request_pacer = pacer or FixturePacer()
    mutations = 0
    previewed = 0

    pending = [
        scenario
        for scenario in ARCHIVED_THREAD_RACE_SCENARIOS
        if ledger.capabilities.get(
            _race_capability_key(scenario, "execution")
        ) not in {"cleaned", "interrupted", "reconciled-cleaned"}
    ]
    if not pending:
        ledger.phase = "archived_thread_race_verified"
        ledger.destructive_unlocked = False
        ledger.save(ledger_path)
        return 0, 0

    for ordinal, scenario in enumerate(pending, start=1):
        thread, messages, new_mutations = _seed_archived_thread_race_scenario(
            ledger,
            ledger_path,
            scenario,
            parent,
            clients,
        )
        mutations += new_mutations
        scope = _race_scope(scenario)
        observation = observe_destructive_contract_scope(
            tokens[scenario.target_role],
            scope,
            thread,
            messages,
            ledger.accounts,
            target_role=scenario.target_role,
            pacer=request_pacer,
        )
        baseline_foreign_reactions = ledger.capabilities.get(
            _race_capability_key(scenario, "foreign-reactions")
        )
        if (
            scenario.expect_cleanup
            and messages[0].resource_id not in observation.message_authors
            and observation.subject_reactions_on_foreign_messages == 0
            and baseline_foreign_reactions is not None
            and baseline_foreign_reactions.isdigit()
        ):
            _assert_race_postcondition(
                ledger,
                scenario,
                messages,
                observation,
                baseline_foreign_reactions=int(
                    baseline_foreign_reactions
                ),
            )
            ledger.set_resource_state(messages[0].fixture_key, "absent")
            ledger.set_resource_state(
                _race_fixture_key(scenario, "reaction:target"),
                "absent",
            )
            ledger.capabilities[
                _race_capability_key(scenario, "execution")
            ] = "reconciled-cleaned"
            ledger.save(ledger_path)
            print(
                f"thread-race-preview-{ordinal}: reconciled "
                f"({scenario.scenario_key})"
            )
            continue
        target_reactions, foreign_reactions = _assert_race_precondition(
            ledger,
            scenario,
            messages,
            observation,
        )
        _run_archived_thread_race_preview(
            tokens[scenario.target_role],
            thread.resource_id,
            expected_messages=1,
            expected_reactions=target_reactions,
        )
        ledger.capabilities[
            _race_capability_key(scenario, "foreign-reactions")
        ] = str(foreign_reactions)
        ledger.capabilities[
            _race_capability_key(scenario, "preview")
        ] = "verified"
        ledger.phase = "archived_thread_race_previewing"
        ledger.destructive_unlocked = False
        ledger.save(ledger_path)
        previewed += 1
        print(f"thread-race-preview-{ordinal}: verified ({scenario.scenario_key})")

    execution_values = {
        ledger.capabilities.get(_race_capability_key(scenario, "execution"))
        for scenario in ARCHIVED_THREAD_RACE_SCENARIOS
    }
    complete_values = {"cleaned", "interrupted", "reconciled-cleaned"}
    all_complete = execution_values <= complete_values
    ledger.phase = (
        "archived_thread_race_verified"
        if all_complete
        else "archived_thread_race_previewed"
    )
    ledger.destructive_unlocked = not all_complete
    ledger.save(ledger_path)
    return mutations, previewed


def execute_archived_thread_race_matrix(
    ledger: LiveLedger,
    ledger_path: Path,
    tokens: Mapping[str, str],
    manager: DiscordFixtureClient,
    *,
    pacer: FixturePacer | None = None,
    journal_path: Path = DEFAULT_ARCHIVED_THREAD_RACE_JOURNAL_PATH,
    fetch_interval: tuple[float, float] = (1.0, 2.0),
    delete_interval: tuple[float, float] = (3.0, 6.0),
    cleanup_runner: Callable[..., tuple[int, int]] = _run_archived_thread_race_cleanup,
) -> int:
    """Execute each gated race and independently verify final Discord state."""
    if (
        ledger.phase not in {
            "archived_thread_race_previewed",
            "archived_thread_race_executing",
        }
        or not ledger.destructive_unlocked
    ):
        raise LiveSuiteSafetyError(
            "Archived-thread race execution requires its verified preview gate."
        )
    if set(tokens) != set(FIXTURE_ROLES):
        raise LiveSuiteSafetyError(
            "Archived-thread race testing requires all fixture accounts."
        )
    _assert_race_journal_empty(journal_path, ledger.accounts)
    request_pacer = pacer or FixturePacer()
    ledger.phase = "archived_thread_race_executing"
    ledger.save(ledger_path)
    verified = 0
    completed = False
    try:
        for ordinal, scenario in enumerate(
            ARCHIVED_THREAD_RACE_SCENARIOS,
            start=1,
        ):
            execution_key = _race_capability_key(scenario, "execution")
            if ledger.capabilities.get(execution_key) in {
                "cleaned",
                "interrupted",
                "reconciled-cleaned",
            }:
                verified += 1
                continue
            if ledger.capabilities.get(
                _race_capability_key(scenario, "preview")
            ) != "verified":
                raise LiveSuiteSafetyError(
                    "An archived-thread race scenario was not previewed."
                )
            thread = _require_active_resource(
                ledger,
                _race_fixture_key(scenario, "thread"),
                "thread",
            )
            messages = _race_messages(ledger, scenario, thread)
            baseline_foreign_reactions = ledger.capabilities.get(
                _race_capability_key(scenario, "foreign-reactions")
            )
            if (
                baseline_foreign_reactions is None
                or not baseline_foreign_reactions.isdigit()
            ):
                raise LiveSuiteSafetyError(
                    "An archived-thread race scenario has no valid baseline."
                )
            before = observe_destructive_contract_scope(
                tokens[scenario.target_role],
                _race_scope(scenario),
                thread,
                messages,
                ledger.accounts,
                target_role=scenario.target_role,
                pacer=request_pacer,
            )
            target_message_present = (
                messages[0].resource_id in before.message_authors
            )
            target_reaction_present = (
                before.subject_reactions_on_foreign_messages == 1
            )
            if (
                scenario.expect_cleanup
                and not target_message_present
                and not target_reaction_present
            ):
                _assert_race_postcondition(
                    ledger,
                    scenario,
                    messages,
                    before,
                    baseline_foreign_reactions=int(
                        baseline_foreign_reactions
                    ),
                )
                ledger.set_resource_state(
                    messages[0].fixture_key,
                    "absent",
                )
                ledger.set_resource_state(
                    _race_fixture_key(scenario, "reaction:target"),
                    "absent",
                )
                ledger.capabilities[execution_key] = "reconciled-cleaned"
                ledger.save(ledger_path)
                verified += 1
                print(
                    f"thread-race-execute-{ordinal}: reconciled "
                    f"({scenario.scenario_key})"
                )
                continue
            _assert_race_precondition(
                ledger,
                scenario,
                messages,
                before,
            )
            _run_archived_thread_race_preview(
                tokens[scenario.target_role],
                thread.resource_id,
                expected_messages=1,
                expected_reactions=1,
            )
            deleted, hook_count = cleanup_runner(
                tokens[scenario.target_role],
                ledger.accounts[scenario.target_role],
                scenario,
                thread,
                manager,
                journal_path,
                fetch_interval=fetch_interval,
                delete_interval=delete_interval,
            )
            expected_deleted = 1 if scenario.expect_cleanup else 0
            if (
                deleted != expected_deleted
                or hook_count != scenario.expected_hook_count
            ):
                raise LiveSuiteSafetyError(
                    "An archived-thread race execution reported an unexpected outcome."
                )
            after = observe_destructive_contract_scope(
                tokens[scenario.target_role],
                _race_scope(scenario),
                thread,
                messages,
                ledger.accounts,
                target_role=scenario.target_role,
                pacer=request_pacer,
            )
            _assert_race_postcondition(
                ledger,
                scenario,
                messages,
                after,
                baseline_foreign_reactions=int(
                    baseline_foreign_reactions
                ),
            )
            _assert_race_journal_empty(journal_path, ledger.accounts)
            if scenario.expect_cleanup:
                ledger.set_resource_state(messages[0].fixture_key, "deleted")
                ledger.set_resource_state(
                    _race_fixture_key(scenario, "reaction:target"),
                    "deleted",
                )
                ledger.capabilities[execution_key] = "cleaned"
                outcome = "cleaned"
            else:
                ledger.capabilities[execution_key] = "interrupted"
                outcome = "interrupted"
            ledger.save(ledger_path)
            verified += 1
            print(
                f"thread-race-execute-{ordinal}: {outcome} "
                f"({scenario.scenario_key})"
            )

        _assert_race_journal_empty(journal_path, ledger.accounts)
        ledger.phase = "archived_thread_race_verified"
        completed = True
        return verified
    finally:
        ledger.destructive_unlocked = False
        if not completed:
            ledger.phase = "archived_thread_race_interrupted"
        ledger.save(ledger_path)


def _ensure_private_content_channel(
    ledger: LiveLedger,
    ledger_path: Path,
    client: DiscordFixtureClient,
    fixture_key: str,
    opener: DiscordFixtureClient,
    user_ids: Sequence[str],
    channel_kind: str,
) -> LedgerResource:
    existing = ledger.resource_for_fixture(fixture_key)
    if existing is not None:
        return _require_active_resource(ledger, fixture_key, "dm_channel")
    channel = (
        opener.open_private_channel(user_ids[0])
        if channel_kind == "dm"
        else opener.open_group_channel(user_ids)
    )
    resource = LedgerResource(
        run_id=ledger.run_id,
        fixture_key=fixture_key,
        kind="dm_channel",
        resource_id=channel.channel_id,
        owner_handle="subject",
    )
    ledger.record_resource(resource)
    ledger.save(ledger_path)
    print(f"{fixture_key}: created")
    return resource


def _seed_private_messages(
    ledger: LiveLedger,
    ledger_path: Path,
    clients: Mapping[str, DiscordFixtureClient],
    channel: LedgerResource,
    message_fixtures: Sequence[tuple[str, str, str]],
    reaction_key: str,
) -> None:
    messages: dict[str, str] = {}
    for fixture_key, account_role, content in message_fixtures:
        existing = ledger.resource_for_fixture(fixture_key)
        if existing is not None:
            messages[fixture_key] = _require_active_resource(
                ledger, fixture_key, "message"
            ).resource_id
            continue
        message = clients[account_role].send_message(channel.resource_id, content=content)
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=fixture_key,
            kind="message",
            resource_id=message.message_id,
            owner_handle=account_role,
            parent_id=channel.resource_id,
        ))
        ledger.save(ledger_path)
        messages[fixture_key] = message.message_id
        print(f"{fixture_key}: created")
    if ledger.resource_for_fixture(reaction_key) is None:
        clients[message_fixtures[1][1]].add_reaction(
            channel.resource_id,
            messages[message_fixtures[0][0]],
            emoji="💬",
        )
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=reaction_key,
            kind="reaction",
            resource_id=messages[message_fixtures[0][0]],
            owner_handle=message_fixtures[1][1],
            parent_id=channel.resource_id,
        ))
        ledger.save(ledger_path)
        print(f"{reaction_key}: created")


def prepare_membership_invites(
    ledger: LiveLedger,
    invites_path: Path,
    client: DiscordFixtureClient,
    *,
    max_age: int = 3600,
) -> None:
    """Write short-lived manual invite links without exposing them in output."""
    if ledger.phase == "teardown_complete":
        raise LiveSuiteSafetyError("A completed run cannot prepare membership invites.")
    if max_age <= 0:
        raise ValueError("Membership invite lifetime must be positive.")

    guilds = _fixture_guild_resources(ledger)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": ledger.run_id,
        "generated_at": _utc_timestamp(),
        "invites": [],
    }
    atomic_write_json(str(invites_path), payload)

    for guild_number, (guild_fixture_key, purpose) in enumerate(
        GUILD_FIXTURES,
        start=1,
    ):
        guild = guilds[guild_fixture_key]
        lobby = _require_active_resource(
            ledger,
            f"channel:{purpose}:lobby",
            "channel",
            guild_id=guild.resource_id,
        )
        missing_accounts = 0
        for account_role in FIXTURE_ROLES[1:]:
            member = client.get_guild_member(
                guild.resource_id,
                ledger.accounts[account_role],
            )
            if member is None:
                missing_accounts += 1
            else:
                _require_fixture_member(member)

        if missing_accounts == 0:
            print(f"guild-{guild_number}: memberships already complete")
            continue

        invite = client.create_invite(
            lobby.resource_id,
            max_age=max_age,
            max_uses=missing_accounts,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=max_age)
        payload["invites"].append(
            {
                "guild_ordinal": guild_number,
                "missing_accounts": missing_accounts,
                "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
                "url": f"https://discord.gg/{invite.code}",
            }
        )
        atomic_write_json(str(invites_path), payload)
        print(
            f"guild-{guild_number}: invite prepared for {missing_accounts} account(s)"
        )


def teardown_fixture_guilds(
    ledger: LiveLedger,
    ledger_path: Path,
    client: DiscordFixtureClient,
) -> None:
    """Delete only verified, run-owned guild fixtures and persist each outcome."""
    guild_resources = {
        resource.resource_id: resource
        for resource in ledger.resources
        if resource.kind == "guild" and resource.state not in TERMINAL_RESOURCE_STATES
    }
    unsupported = [
        resource
        for resource in ledger.resources
        if resource.state not in TERMINAL_RESOURCE_STATES
        and resource.kind not in {
            "guild",
            "channel",
            "role",
            "message",
            "reaction",
            "thread",
            "dm_channel",
        }
    ]
    if unsupported:
        raise LiveSuiteSafetyError(
            "Guild teardown found an unsupported active fixture resource."
        )
    detached = [
        resource
        for resource in ledger.resources
        if resource.state not in TERMINAL_RESOURCE_STATES
        and resource.kind in {"channel", "role"}
        and resource.guild_id not in guild_resources
    ]
    if detached:
        raise LiveSuiteSafetyError(
            "Guild teardown found a fixture resource without an active parent guild."
        )

    guilds_by_id = {guild.guild_id: guild for guild in client.list_current_guilds()}
    ledger.phase = "teardown_in_progress"
    ledger.destructive_unlocked = False
    ledger.save(ledger_path)

    resources = [
        resource
        for resource in reversed(ledger.resources)
        if resource.kind == "guild" and resource.state not in TERMINAL_RESOURCE_STATES
    ]
    for index, resource in enumerate(resources, start=1):
        purpose = _guild_fixture_purpose(resource.fixture_key)
        expected_name = _guild_name(ledger.run_id, purpose)
        observed = guilds_by_id.get(resource.resource_id)
        if observed is None:
            outcome = "absent"
        else:
            if not observed.owned or observed.name != expected_name:
                raise LiveSuiteSafetyError(
                    "Refusing to delete a guild that does not match its run-owned fixture."
                )
            outcome = client.delete_guild(resource.resource_id)
        ledger.set_resource_state(resource.fixture_key, outcome)
        for child in ledger.resources:
            if (
                child.kind in {"channel", "role"}
                and child.guild_id == resource.resource_id
                and child.state not in TERMINAL_RESOURCE_STATES
            ):
                ledger.set_resource_state(child.fixture_key, outcome)
        ledger.save(ledger_path)
        print(f"guild-{index}: {outcome}")

    ledger.mark_empty_teardown_complete()
    ledger.save(ledger_path)


def _run_accounts(args: argparse.Namespace) -> int:
    _validated_accounts(args)
    return 0


def _run_init(args: argparse.Namespace) -> int:
    with SuiteLock(args.lock):
        if args.ledger.exists():
            raise LiveSuiteSafetyError(
                f"Refusing to overwrite existing live ledger: {args.ledger}"
            )
        report = _validated_accounts(args)
        ledger = LiveLedger.new(report.ledger_identities, run_id=args.run_id)
        ledger.save(args.ledger)
    print(f"Initialized run {ledger.run_id} at {args.ledger}.")
    return 0


def _run_bootstrap(args: argparse.Namespace) -> int:
    with SuiteLock(args.lock):
        ledger = LiveLedger.load(args.ledger)
        _confirm_run_id(ledger, args.confirm_run_id)
        tokens, report = _load_validated_accounts(args)
        _require_current_accounts(ledger, report)
        with DiscordFixtureClient(tokens["owner"]) as client:
            bootstrap_fixture_guilds(ledger, args.ledger, client)
    print("Guild bootstrap complete.")
    return 0


def _run_topology(args: argparse.Namespace) -> int:
    with SuiteLock(args.lock):
        ledger = LiveLedger.load(args.ledger)
        _confirm_run_id(ledger, args.confirm_run_id)
        tokens, report = _load_validated_accounts(args)
        _require_current_accounts(ledger, report)
        shared_pacer = FixturePacer()
        with ExitStack() as stack:
            clients = {
                fixture_role: stack.enter_context(
                    DiscordFixtureClient(token, pacer=shared_pacer)
                )
                for fixture_role, token in tokens.items()
            }
            bootstrap_fixture_topology(ledger, args.ledger, clients)
    print("Fixture topology bootstrap complete.")
    return 0


def _run_content(args: argparse.Namespace) -> int:
    with SuiteLock(args.lock):
        ledger = LiveLedger.load(args.ledger)
        _confirm_run_id(ledger, args.confirm_run_id)
        tokens, report = _load_validated_accounts(args)
        _require_current_accounts(ledger, report)
        shared_pacer = FixturePacer()
        with ExitStack() as stack:
            clients = {
                fixture_role: stack.enter_context(
                    DiscordFixtureClient(token, pacer=shared_pacer)
                )
                for fixture_role, token in tokens.items()
            }
            seed_content_fixtures(ledger, args.ledger, clients)
    print("Fixture content seeding complete.")
    return 0


def _run_dry_run(args: argparse.Namespace) -> int:
    with SuiteLock(args.lock):
        ledger = LiveLedger.load(args.ledger)
        _confirm_run_id(ledger, args.confirm_run_id)
        tokens, report = _load_validated_accounts(args)
        _require_current_accounts(ledger, report)
        verify_dmd_dry_runs(ledger, args.ledger, tokens["subject"])
    print("DMD dry-run verification complete.")
    return 0


def _run_destructive_smoke(args: argparse.Namespace) -> int:
    with SuiteLock(args.lock):
        ledger = LiveLedger.load(args.ledger)
        _confirm_run_id(ledger, args.confirm_run_id)
        tokens, report = _load_validated_accounts(args)
        _require_current_accounts(ledger, report)
        outcome = execute_destructive_smoke(
            ledger,
            args.ledger,
            tokens["subject"],
        )
    print(f"Destructive smoke verification complete: {outcome}.")
    return 0


def _run_volume(args: argparse.Namespace) -> int:
    if args.delay_min < 0 or args.delay_max < args.delay_min:
        raise LiveSuiteSafetyError("Content delay bounds must be non-negative and ascending.")
    with SuiteLock(args.lock):
        ledger = LiveLedger.load(args.ledger)
        _confirm_run_id(ledger, args.confirm_run_id)
        tokens, report = _load_validated_accounts(args)
        _require_current_accounts(ledger, report)
        shared_pacer = FixturePacer(
            FixturePacingPolicy(
                read_interval=(1.0, 2.0),
                mutation_interval=(args.delay_min, args.delay_max),
            )
        )
        with ExitStack() as stack:
            clients = {
                fixture_role: stack.enter_context(
                    DiscordFixtureClient(token, pacer=shared_pacer)
                )
                for fixture_role, token in tokens.items()
            }
            mutations, complete = seed_volume_matrix(
                ledger,
                args.ledger,
                clients,
                messages_per_scope=args.messages_per_scope,
                max_new_mutations=args.max_new_mutations,
            )
    state = "complete" if complete else "paused"
    print(f"Volume fixture seeding {state}: {mutations} new mutation(s).")
    return 0


def _run_thread_matrix(args: argparse.Namespace) -> int:
    if args.delay_min < 0 or args.delay_max < args.delay_min:
        raise LiveSuiteSafetyError(
            "Content delay bounds must be non-negative and ascending."
        )
    with SuiteLock(args.lock):
        ledger = LiveLedger.load(args.ledger)
        _confirm_run_id(ledger, args.confirm_run_id)
        tokens, report = _load_validated_accounts(args)
        _require_current_accounts(ledger, report)
        shared_pacer = FixturePacer(
            FixturePacingPolicy(
                read_interval=(1.0, 2.0),
                mutation_interval=(args.delay_min, args.delay_max),
            )
        )
        with ExitStack() as stack:
            clients = {
                fixture_role: stack.enter_context(
                    DiscordFixtureClient(token, pacer=shared_pacer)
                )
                for fixture_role, token in tokens.items()
            }
            mutations, complete = seed_thread_matrix(
                ledger,
                args.ledger,
                clients,
                messages_per_thread=args.messages_per_thread,
                max_new_mutations=args.max_new_mutations,
                try_super_reactions=args.super_reactions,
            )
    state = "complete" if complete else "paused"
    print(f"Thread fixture seeding {state}: {mutations} new mutation(s).")
    return 0


def _run_forum_starter_smoke(args: argparse.Namespace) -> int:
    if args.delay_min < 0 or args.delay_max < args.delay_min:
        raise LiveSuiteSafetyError(
            "Content delay bounds must be non-negative and ascending."
        )
    with SuiteLock(args.lock):
        ledger = LiveLedger.load(args.ledger)
        _confirm_run_id(ledger, args.confirm_run_id)
        tokens, report = _load_validated_accounts(args)
        _require_current_accounts(ledger, report)
        pacer = FixturePacer(
            FixturePacingPolicy(
                read_interval=(1.0, 2.0),
                mutation_interval=(args.delay_min, args.delay_max),
            )
        )
        if args.execute:
            outcome, container_state = execute_forum_starter_smoke(
                ledger,
                args.ledger,
                tokens["subject"],
                pacer=pacer,
            )
        else:
            with DiscordFixtureClient(
                tokens["subject"],
                pacer=pacer,
            ) as client:
                prepare_forum_starter_smoke(
                    ledger,
                    args.ledger,
                    tokens["subject"],
                    client,
                    pacer=pacer,
                )
            outcome = "previewed"
            container_state = "unmodified"
    print(
        "Forum starter smoke complete: "
        f"message={outcome}, container={container_state}."
    )
    return 0


def _run_destructive_contract_matrix(args: argparse.Namespace) -> int:
    if args.delay_min < 0 or args.delay_max < args.delay_min:
        raise LiveSuiteSafetyError(
            "Content delay bounds must be non-negative and ascending."
        )
    with SuiteLock(args.lock):
        ledger = LiveLedger.load(args.ledger)
        _confirm_run_id(ledger, args.confirm_run_id)
        tokens, report = _load_validated_accounts(args)
        _require_current_accounts(ledger, report)
        pacer = FixturePacer(
            FixturePacingPolicy(
                read_interval=(1.0, 2.0),
                mutation_interval=(args.delay_min, args.delay_max),
            )
        )
        if args.execute:
            verified = execute_destructive_contract_matrix(
                ledger,
                args.ledger,
                tokens["subject"],
                pacer=pacer,
            )
            mode = "executed"
        else:
            with ExitStack() as stack:
                clients = {
                    fixture_role: stack.enter_context(
                        DiscordFixtureClient(token, pacer=pacer)
                    )
                    for fixture_role, token in tokens.items()
                }
                verified = prepare_destructive_contract_matrix(
                    ledger,
                    args.ledger,
                    tokens["subject"],
                    clients,
                    pacer=pacer,
                )
            mode = "previewed"
    print(
        "Destructive contract matrix complete: "
        f"{verified} scope(s) {mode}."
    )
    return 0


def _run_archived_thread_race_matrix(args: argparse.Namespace) -> int:
    if args.delay_min < 0 or args.delay_max < args.delay_min:
        raise LiveSuiteSafetyError(
            "Content delay bounds must be non-negative and ascending."
        )
    with SuiteLock(args.lock):
        ledger = LiveLedger.load(args.ledger)
        _confirm_run_id(ledger, args.confirm_run_id)
        tokens, report = _load_validated_accounts(args)
        _require_current_accounts(ledger, report)
        pacer = FixturePacer(
            FixturePacingPolicy(
                read_interval=(1.0, 2.0),
                mutation_interval=(args.delay_min, args.delay_max),
            )
        )
        if args.execute:
            with DiscordFixtureClient(
                tokens["peer_b"],
                pacer=pacer,
            ) as manager:
                verified = execute_archived_thread_race_matrix(
                    ledger,
                    args.ledger,
                    tokens,
                    manager,
                    pacer=pacer,
                    journal_path=args.journal,
                    delete_interval=(args.delay_min, args.delay_max),
                )
            mutations = 0
            mode = "executed"
        else:
            with ExitStack() as stack:
                clients = {
                    fixture_role: stack.enter_context(
                        DiscordFixtureClient(token, pacer=pacer)
                    )
                    for fixture_role, token in tokens.items()
                }
                mutations, verified = prepare_archived_thread_race_matrix(
                    ledger,
                    args.ledger,
                    tokens,
                    clients,
                    pacer=pacer,
                    journal_path=args.journal,
                )
            mode = "previewed"
    print(
        "Archived-thread race matrix complete: "
        f"{verified} scenario(s) {mode}, {mutations} fixture mutation(s)."
    )
    return 0


def _run_membership_invites(args: argparse.Namespace) -> int:
    with SuiteLock(args.lock):
        ledger = LiveLedger.load(args.ledger)
        _confirm_run_id(ledger, args.confirm_run_id)
        tokens, report = _load_validated_accounts(args)
        _require_current_accounts(ledger, report)
        with DiscordFixtureClient(tokens["owner"]) as client:
            prepare_membership_invites(
                ledger,
                args.output,
                client,
                max_age=args.max_age,
            )
    print(f"Private membership invites written to {args.output}.")
    return 0


def _run_status(args: argparse.Namespace) -> int:
    ledger = LiveLedger.load(args.ledger)
    active_resources = sum(
        resource.state not in TERMINAL_RESOURCE_STATES for resource in ledger.resources
    )
    print(f"run_id={ledger.run_id}")
    print(f"phase={ledger.phase}")
    print(f"accounts={len(ledger.accounts)}")
    print(f"resources={len(ledger.resources)}")
    print(f"active_resources={active_resources}")
    print(f"destructive_unlocked={str(ledger.destructive_unlocked).lower()}")
    return 0


def _run_teardown(args: argparse.Namespace) -> int:
    with SuiteLock(args.lock):
        ledger = LiveLedger.load(args.ledger)
        active_resources = [
            resource
            for resource in ledger.resources
            if resource.state not in TERMINAL_RESOURCE_STATES
        ]
        if not active_resources:
            changed = ledger.mark_empty_teardown_complete()
            if changed:
                ledger.save(args.ledger)
                print(f"Marked empty run {ledger.run_id} as teardown_complete.")
            else:
                print(f"Run {ledger.run_id} was already teardown_complete.")
            return 0

        _confirm_run_id(ledger, args.confirm_run_id)
        tokens, report = _load_validated_accounts(args)
        _require_current_accounts(ledger, report)
        with DiscordFixtureClient(tokens["owner"]) as client:
            teardown_fixture_guilds(ledger, args.ledger, client)
    print("Guild teardown complete.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    accounts_parser = subparsers.add_parser(
        "accounts",
        help="Validate configured accounts through read-only /users/@me requests",
    )
    _add_account_arguments(accounts_parser)
    accounts_parser.set_defaults(handler=_run_accounts)

    init_parser = subparsers.add_parser(
        "init",
        help="Validate accounts and initialize a non-destructive run ledger",
    )
    _add_account_arguments(init_parser)
    init_parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    init_parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    init_parser.add_argument("--run-id", default=None)
    init_parser.set_defaults(handler=_run_init)

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="Create or recover the two run-owned fixture guilds",
    )
    _add_account_arguments(bootstrap_parser)
    bootstrap_parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    bootstrap_parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    bootstrap_parser.add_argument("--confirm-run-id", required=True)
    bootstrap_parser.set_defaults(handler=_run_bootstrap)

    topology_parser = subparsers.add_parser(
        "topology",
        help="Create or recover fixture memberships, roles, and channels",
    )
    _add_account_arguments(topology_parser)
    topology_parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    topology_parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    topology_parser.add_argument("--confirm-run-id", required=True)
    topology_parser.set_defaults(handler=_run_topology)

    content_parser = subparsers.add_parser(
        "content",
        help="Create or recover multi-user messages, reactions, and threads",
    )
    _add_account_arguments(content_parser)
    content_parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    content_parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    content_parser.add_argument("--confirm-run-id", required=True)
    content_parser.set_defaults(handler=_run_content)

    dry_run_parser = subparsers.add_parser(
        "dry-run",
        help="Verify redacted DMD previews for all seeded content scopes",
    )
    _add_account_arguments(dry_run_parser)
    dry_run_parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    dry_run_parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    dry_run_parser.add_argument("--confirm-run-id", required=True)
    dry_run_parser.set_defaults(handler=_run_dry_run)

    destructive_parser = subparsers.add_parser(
        "destructive-smoke",
        help="Delete only the isolated subject-message fixture after a verified dry-run",
    )
    _add_account_arguments(destructive_parser)
    destructive_parser.add_argument(
        "--ledger", type=Path, default=DEFAULT_LEDGER_PATH
    )
    destructive_parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    destructive_parser.add_argument("--confirm-run-id", required=True)
    destructive_parser.set_defaults(handler=_run_destructive_smoke)

    volume_parser = subparsers.add_parser(
        "volume",
        help="Seed a varied, resumable multi-scope content matrix",
    )
    _add_account_arguments(volume_parser)
    volume_parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    volume_parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    volume_parser.add_argument("--confirm-run-id", required=True)
    volume_parser.add_argument("--messages-per-scope", type=int, default=24)
    volume_parser.add_argument(
        "--max-new-mutations",
        type=int,
        default=0,
        help="Pause after this many new writes; zero completes the matrix",
    )
    volume_parser.add_argument("--delay-min", type=float, default=4.0)
    volume_parser.add_argument("--delay-max", type=float, default=12.0)
    volume_parser.set_defaults(handler=_run_volume)

    thread_matrix_parser = subparsers.add_parser(
        "thread-matrix",
        help="Seed active/archived thread forms and shared reactions",
    )
    _add_account_arguments(thread_matrix_parser)
    thread_matrix_parser.add_argument(
        "--ledger",
        type=Path,
        default=DEFAULT_LEDGER_PATH,
    )
    thread_matrix_parser.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_LOCK_PATH,
    )
    thread_matrix_parser.add_argument("--confirm-run-id", required=True)
    thread_matrix_parser.add_argument("--messages-per-thread", type=int, default=12)
    thread_matrix_parser.add_argument(
        "--max-new-mutations",
        type=int,
        default=0,
        help="Pause after this many new writes; zero completes the matrix",
    )
    thread_matrix_parser.add_argument(
        "--super-reactions",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Capability-test Super Reactions for subject and one peer",
    )
    thread_matrix_parser.add_argument("--delay-min", type=float, default=4.0)
    thread_matrix_parser.add_argument("--delay-max", type=float, default=12.0)
    thread_matrix_parser.set_defaults(handler=_run_thread_matrix)

    forum_starter_parser = subparsers.add_parser(
        "forum-starter-smoke",
        help="Preview or execute isolated forum starter-message deletion",
    )
    _add_account_arguments(forum_starter_parser)
    forum_starter_parser.add_argument(
        "--ledger",
        type=Path,
        default=DEFAULT_LEDGER_PATH,
    )
    forum_starter_parser.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_LOCK_PATH,
    )
    forum_starter_parser.add_argument("--confirm-run-id", required=True)
    forum_starter_parser.add_argument(
        "--execute",
        action="store_true",
        help="Delete the previewed starter message and verify the container",
    )
    forum_starter_parser.add_argument("--delay-min", type=float, default=4.0)
    forum_starter_parser.add_argument("--delay-max", type=float, default=12.0)
    forum_starter_parser.set_defaults(handler=_run_forum_starter_smoke)

    contract_parser = subparsers.add_parser(
        "destructive-contract-matrix",
        help="Preview or execute per-channel message and reaction contracts",
    )
    _add_account_arguments(contract_parser)
    contract_parser.add_argument(
        "--ledger",
        type=Path,
        default=DEFAULT_LEDGER_PATH,
    )
    contract_parser.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_LOCK_PATH,
    )
    contract_parser.add_argument("--confirm-run-id", required=True)
    contract_parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute all immediately revalidated contract scopes",
    )
    contract_parser.add_argument("--delay-min", type=float, default=4.0)
    contract_parser.add_argument("--delay-max", type=float, default=12.0)
    contract_parser.set_defaults(handler=_run_destructive_contract_matrix)

    race_parser = subparsers.add_parser(
        "archived-thread-race-matrix",
        help="Preview or execute isolated archived-thread state races",
    )
    _add_account_arguments(race_parser)
    race_parser.add_argument(
        "--ledger",
        type=Path,
        default=DEFAULT_LEDGER_PATH,
    )
    race_parser.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_LOCK_PATH,
    )
    race_parser.add_argument(
        "--journal",
        type=Path,
        default=DEFAULT_ARCHIVED_THREAD_RACE_JOURNAL_PATH,
    )
    race_parser.add_argument("--confirm-run-id", required=True)
    race_parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute all immediately revalidated archived-thread races",
    )
    race_parser.add_argument("--delay-min", type=float, default=3.0)
    race_parser.add_argument("--delay-max", type=float, default=6.0)
    race_parser.set_defaults(handler=_run_archived_thread_race_matrix)

    membership_invites_parser = subparsers.add_parser(
        "membership-invites",
        help="Prepare private short-lived links for manual CAPTCHA completion",
    )
    _add_account_arguments(membership_invites_parser)
    membership_invites_parser.add_argument(
        "--ledger",
        type=Path,
        default=DEFAULT_LEDGER_PATH,
    )
    membership_invites_parser.add_argument(
        "--lock",
        type=Path,
        default=DEFAULT_LOCK_PATH,
    )
    membership_invites_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_MEMBERSHIP_INVITES_PATH,
    )
    membership_invites_parser.add_argument(
        "--max-age",
        type=int,
        default=3600,
        help="Invite lifetime in seconds (default: 3600)",
    )
    membership_invites_parser.add_argument("--confirm-run-id", required=True)
    membership_invites_parser.set_defaults(handler=_run_membership_invites)

    status_parser = subparsers.add_parser("status", help="Show redacted ledger status")
    status_parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    status_parser.set_defaults(handler=_run_status)

    teardown_parser = subparsers.add_parser(
        "teardown",
        help="Delete verified fixture guilds or complete an empty teardown",
    )
    _add_account_arguments(teardown_parser)
    teardown_parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    teardown_parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK_PATH)
    teardown_parser.add_argument("--confirm-run-id")
    teardown_parser.set_defaults(handler=_run_teardown)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (LiveSuiteError, FixtureClientError, FixtureLockError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
