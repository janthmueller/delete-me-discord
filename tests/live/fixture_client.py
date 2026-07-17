"""Independent Discord client and process lock for live fixture management."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class FixtureClientError(RuntimeError):
    """A redacted Discord fixture operation failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        captcha_required: bool = False,
        discord_code: int | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.captcha_required = captcha_required
        self.discord_code = discord_code


class FixtureLockError(RuntimeError):
    """Raised when another process owns the live-suite lock."""


@dataclass(frozen=True, repr=False)
class FixtureBackendError(RuntimeError):
    """Redacted failure reported by the optional user-client backend."""

    status_code: int | None = None
    discord_code: int | None = None
    captcha_required: bool = False

    def __repr__(self) -> str:
        return (
            "FixtureBackendError("
            f"status_code={self.status_code!r}, discord_code={self.discord_code!r}, "
            f"captcha_required={self.captcha_required!r}"
            ")"
        )


@dataclass(frozen=True)
class FixturePacingPolicy:
    """Conservative spacing applied before live Discord fixture requests."""

    read_interval: tuple[float, float] = (1.0, 2.0)
    mutation_interval: tuple[float, float] = (3.0, 6.0)

    def __post_init__(self) -> None:
        for name, interval in (
            ("read_interval", self.read_interval),
            ("mutation_interval", self.mutation_interval),
        ):
            lower, upper = interval
            if lower < 0 or upper < lower:
                raise ValueError(f"{name} must be a non-negative ascending interval")


class FixturePacer:
    """Serialize requests with conservative jittered spacing."""

    _READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

    def __init__(
        self,
        policy: FixturePacingPolicy | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] | None = None,
    ):
        self.policy = policy or FixturePacingPolicy()
        self._monotonic = monotonic
        self._sleep = sleep
        self._uniform = uniform or random.SystemRandom().uniform
        self._last_request_finished_at: float | None = None

    def wait_before_request(self, method: str) -> None:
        interval = (
            self.policy.read_interval
            if method.upper() in self._READ_METHODS
            else self.policy.mutation_interval
        )
        desired_spacing = self._uniform(*interval)
        if self._last_request_finished_at is None:
            delay = desired_spacing
        else:
            elapsed = max(0.0, self._monotonic() - self._last_request_finished_at)
            delay = max(0.0, desired_spacing - elapsed)
        if delay > 0:
            self._sleep(delay)

    def note_request_finished(self) -> None:
        self._last_request_finished_at = self._monotonic()


@dataclass(frozen=True, repr=False)
class GuildSnapshot:
    guild_id: str
    name: str
    owned: bool

    def __repr__(self) -> str:
        return f"GuildSnapshot(owned={self.owned!r})"


@dataclass(frozen=True, repr=False)
class PermissionOverwriteSnapshot:
    target_id: str
    target_type: int
    allow: int
    deny: int

    def __repr__(self) -> str:
        return (
            "PermissionOverwriteSnapshot("
            f"target_type={self.target_type!r}, allow={self.allow!r}, "
            f"deny={self.deny!r})"
        )


@dataclass(frozen=True, repr=False)
class GuildChannelSnapshot:
    channel_id: str
    name: str
    channel_type: int
    parent_id: str | None
    permission_overwrites: tuple[PermissionOverwriteSnapshot, ...] = ()

    def __repr__(self) -> str:
        return (
            "GuildChannelSnapshot("
            f"channel_type={self.channel_type!r}, "
            f"has_parent={self.parent_id is not None!r}, "
            f"permission_overwrites={len(self.permission_overwrites)})"
        )


@dataclass(frozen=True, repr=False)
class GuildRoleSnapshot:
    role_id: str
    name: str
    permissions: int
    managed: bool

    def __repr__(self) -> str:
        return (
            "GuildRoleSnapshot("
            f"permissions={self.permissions!r}, managed={self.managed!r})"
        )


@dataclass(frozen=True, repr=False)
class GuildMemberSnapshot:
    user_id: str
    role_ids: frozenset[str]
    pending: bool

    def __repr__(self) -> str:
        return (
            f"GuildMemberSnapshot(roles={len(self.role_ids)}, pending={self.pending!r})"
        )


@dataclass(frozen=True, repr=False)
class GuildConfigurationSnapshot:
    features: frozenset[str]
    rules_channel_id: str | None
    public_updates_channel_id: str | None
    verification_level: int
    default_message_notifications: int
    explicit_content_filter: int

    def __repr__(self) -> str:
        return (
            "GuildConfigurationSnapshot("
            f"features={len(self.features)}, "
            f"has_rules_channel={self.rules_channel_id is not None!r}, "
            "has_public_updates_channel="
            f"{self.public_updates_channel_id is not None!r})"
        )


@dataclass(frozen=True, repr=False)
class MessageSnapshot:
    message_id: str
    channel_id: str
    author_id: str | None

    def __repr__(self) -> str:
        return "MessageSnapshot(has_author=True)"


@dataclass(frozen=True, repr=False)
class ThreadSnapshot:
    channel_id: str
    parent_id: str
    thread_type: int
    initial_message_id: str | None = None
    archived: bool | None = None
    locked: bool | None = None
    auto_archive_duration: int | None = None

    def __repr__(self) -> str:
        return (
            "ThreadSnapshot("
            f"thread_type={self.thread_type!r}, archived={self.archived!r}, "
            f"locked={self.locked!r})"
        )


@dataclass(frozen=True, repr=False)
class PrivateChannelSnapshot:
    channel_id: str
    channel_type: int

    def __repr__(self) -> str:
        return f"PrivateChannelSnapshot(channel_type={self.channel_type!r})"


@dataclass(frozen=True, repr=False)
class InviteGrant:
    code: str

    def __repr__(self) -> str:
        return "InviteGrant(redacted=True)"


class SuiteLock:
    """Non-blocking process lock released automatically on process exit."""

    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def __enter__(self) -> SuiteLock:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        handle = self.path.open("a+b")
        if os.name != "nt":
            os.chmod(self.path, 0o600)
        try:
            self._acquire(handle)
        except OSError as exc:
            handle.close()
            raise FixtureLockError(
                "Another live-suite process holds the global lock."
            ) from exc
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._handle is None:
            return
        try:
            self._release(self._handle)
        finally:
            self._handle.close()
            self._handle = None

    @staticmethod
    def _acquire(handle) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _release(handle) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class DiscordPySelfBackend:
    """Minimal synchronous adapter around the optional discord.py-self client."""

    _SUPPORTED_VERSION = "2.2.0a"

    def __init__(
        self,
        token: str,
        *,
        module_loader: Callable[[str], Any] = importlib.import_module,
        loop_factory: Callable[[], asyncio.AbstractEventLoop] = asyncio.new_event_loop,
    ):
        self._token = token
        self._module_loader = module_loader
        self._loop_factory = loop_factory
        self._loop: asyncio.AbstractEventLoop | None = None
        self._discord: Any | None = None
        self._client: Any | None = None
        self._logger_state: tuple[list[logging.Handler], int, bool, bool] | None = None

    def open(self) -> None:
        if self._client is not None:
            return
        try:
            discord = self._module_loader("discord")
        except (ImportError, ModuleNotFoundError):
            raise FixtureClientError(
                "Guild fixture automation requires the pinned live dependency; "
                "run this command through the `tests/live` uv project."
            ) from None
        if (
            not hasattr(discord, "HeadersContext")
            or getattr(discord, "__version__", None) != self._SUPPORTED_VERSION
        ):
            raise FixtureClientError(
                "The installed `discord` module is not the pinned live-suite client; "
                "run this command through the `tests/live` uv project."
            )

        self._silence_library_logs()
        try:
            self._loop = self._loop_factory()
            self._discord = discord
            self._client = discord.Client(sync_presence=False)
            self._run(self._client.login(self._token))
        except FixtureBackendError:
            self.close()
            raise
        except Exception as exc:
            error = self._redacted_backend_error(exc)
            self.close()
            raise error from None

    def close(self) -> None:
        client = self._client
        loop = self._loop
        self._client = None
        self._loop = None
        self._discord = None
        try:
            if loop is not None and client is not None:
                loop.run_until_complete(client.close())
        except Exception:
            pass
        finally:
            if loop is not None:
                loop.close()
            self._restore_library_logs()

    def list_current_guilds(self) -> list[dict[str, Any]]:
        client = self._require_client()
        payload = self._run(client.http.get_guilds(with_counts=False))
        if not isinstance(payload, list):
            raise FixtureBackendError()
        return payload

    def create_guild(self, name: str) -> dict[str, Any]:
        client = self._require_client()
        guild = self._run(client.create_guild(name=name))
        guild_id = getattr(guild, "id", None)
        guild_name = getattr(guild, "name", None)
        if guild_id is None or not isinstance(guild_name, str):
            raise FixtureBackendError()
        return {"id": str(guild_id), "name": guild_name, "owner": True}

    def delete_guild(self, guild_id: str) -> None:
        client = self._require_client()
        self._run(client.http.delete_guild(self._numeric_id(guild_id)))

    def list_guild_channels(self, guild_id: str) -> list[dict[str, Any]]:
        client = self._require_client()
        payload = self._run(
            client.http.get_all_guild_channels(self._numeric_id(guild_id))
        )
        if not isinstance(payload, list):
            raise FixtureBackendError()
        return payload

    def create_channel(
        self,
        guild_id: str,
        channel_type: int,
        *,
        name: str,
        parent_id: str | None,
        permission_overwrites: Sequence[PermissionOverwriteSnapshot],
    ) -> dict[str, Any]:
        client = self._require_client()
        fields: dict[str, Any] = {"name": name}
        if parent_id is not None:
            fields["parent_id"] = self._numeric_id(parent_id)
        if permission_overwrites:
            fields["permission_overwrites"] = [
                {
                    "id": overwrite.target_id,
                    "type": overwrite.target_type,
                    "allow": str(overwrite.allow),
                    "deny": str(overwrite.deny),
                }
                for overwrite in permission_overwrites
            ]
        payload = self._run(
            client.http.create_channel(
                self._numeric_id(guild_id),
                channel_type,
                **fields,
            )
        )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def send_message(self, channel_id: str, *, content: str) -> dict[str, Any]:
        client = self._require_client()
        discord = self._discord
        if discord is None:
            raise FixtureBackendError()
        params = discord.http.handle_message_parameters(content=content)
        payload = self._run(
            client.http.send_message(self._numeric_id(channel_id), params=params)
        )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def open_private_channel(self, user_id: str) -> dict[str, Any]:
        client = self._require_client()
        payload = self._run(
            client.http.start_private_message(self._numeric_id(user_id))
        )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def open_group_channel(self, user_ids: Sequence[str]) -> dict[str, Any]:
        client = self._require_client()
        payload = self._run(
            client.http.start_group([self._numeric_id(user_id) for user_id in user_ids])
        )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def add_reaction(
        self,
        channel_id: str,
        message_id: str,
        *,
        emoji: str,
        reaction_type: int = 0,
    ) -> None:
        client = self._require_client()
        self._run(
            client.http.add_reaction(
                self._numeric_id(channel_id),
                self._numeric_id(message_id),
                emoji,
                type=reaction_type,
            )
        )

    def start_thread(
        self,
        channel_id: str,
        *,
        name: str,
        thread_type: int = 11,
        auto_archive_duration: int = 1440,
    ) -> dict[str, Any]:
        client = self._require_client()
        payload = self._run(
            client.http.start_thread_without_message(
                self._numeric_id(channel_id),
                name=name,
                auto_archive_duration=auto_archive_duration,
                type=thread_type,
            )
        )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def start_thread_from_message(
        self,
        channel_id: str,
        message_id: str,
        *,
        name: str,
        auto_archive_duration: int = 1440,
    ) -> dict[str, Any]:
        client = self._require_client()
        payload = self._run(
            client.http.start_thread_with_message(
                self._numeric_id(channel_id),
                self._numeric_id(message_id),
                name=name,
                auto_archive_duration=auto_archive_duration,
            )
        )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def start_forum_thread(
        self,
        channel_id: str,
        *,
        name: str,
        content: str,
        auto_archive_duration: int = 1440,
    ) -> dict[str, Any]:
        client = self._require_client()
        discord = self._discord
        if discord is None:
            raise FixtureBackendError()
        channel_payload = {
            "name": name,
            "auto_archive_duration": auto_archive_duration,
            "type": 11,
        }
        with discord.http.handle_message_parameters(
            content=content,
            channel_payload=channel_payload,
        ) as params:
            payload = self._run(
                client.http.start_thread_in_forum(
                    self._numeric_id(channel_id),
                    params=params,
                )
            )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def add_thread_member(self, channel_id: str, user_id: str) -> None:
        client = self._require_client()
        self._run(
            client.http.add_user_to_thread(
                self._numeric_id(channel_id),
                self._numeric_id(user_id),
            )
        )

    def set_thread_state(
        self,
        channel_id: str,
        *,
        archived: bool | None = None,
        locked: bool | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, bool] = {}
        if archived is not None:
            fields["archived"] = archived
        if locked is not None:
            fields["locked"] = locked
        if not fields:
            raise ValueError("Thread state updates require archived and/or locked.")
        client = self._require_client()
        payload = self._run(
            client.http.edit_channel(
                self._numeric_id(channel_id),
                **fields,
            )
        )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def set_thread_archived(self, channel_id: str, *, archived: bool) -> dict[str, Any]:
        return self.set_thread_state(channel_id, archived=archived)

    def list_guild_roles(self, guild_id: str) -> list[dict[str, Any]]:
        client = self._require_client()
        payload = self._run(client.http.get_roles(self._numeric_id(guild_id)))
        if not isinstance(payload, list):
            raise FixtureBackendError()
        return payload

    def create_role(
        self,
        guild_id: str,
        *,
        name: str,
        permissions: int,
    ) -> dict[str, Any]:
        client = self._require_client()
        payload = self._run(
            client.http.create_role(
                self._numeric_id(guild_id),
                name=name,
                permissions=str(permissions),
                hoist=False,
                mentionable=False,
            )
        )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def get_guild_member(self, guild_id: str, user_id: str) -> dict[str, Any]:
        client = self._require_client()
        payload = self._run(
            client.http.get_member(
                self._numeric_id(guild_id),
                self._numeric_id(user_id),
            )
        )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def add_guild_role(self, guild_id: str, user_id: str, role_id: str) -> None:
        client = self._require_client()
        self._run(
            client.http.add_role(
                self._numeric_id(guild_id),
                self._numeric_id(user_id),
                self._numeric_id(role_id),
            )
        )

    def create_invite(
        self,
        channel_id: str,
        *,
        max_age: int,
        max_uses: int,
    ) -> dict[str, Any]:
        client = self._require_client()
        payload = self._run(
            client.http.create_invite(
                self._numeric_id(channel_id),
                max_age=max_age,
                max_uses=max_uses,
                temporary=False,
                unique=True,
            )
        )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def inspect_invite(self, code: str) -> dict[str, Any]:
        client = self._require_client()
        payload = self._run(
            client.http.get_invite(
                code,
                with_counts=True,
                with_permissions=True,
                with_profile=True,
                input_value=code,
            )
        )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def accept_guild_invite(
        self,
        code: str,
        *,
        guild_id: str,
        channel_id: str,
        channel_type: int,
    ) -> None:
        client = self._require_client()
        discord = self._discord
        if discord is None:
            raise FixtureBackendError()
        session_id = getattr(client._connection, "session_id", None)
        if not session_id:
            session_id = discord.utils._generate_session_id()
        self._run(
            client.http.accept_invite(
                code,
                discord.InviteType.guild,
                session_id,
                guild_id=self._numeric_id(guild_id),
                channel_id=self._numeric_id(channel_id),
                channel_type=channel_type,
            )
        )

    def get_guild_configuration(self, guild_id: str) -> dict[str, Any]:
        client = self._require_client()
        payload = self._run(
            client.http.get_guild(self._numeric_id(guild_id), with_counts=False)
        )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def configure_community(
        self,
        guild_id: str,
        *,
        features: Sequence[str],
        rules_channel_id: str,
        public_updates_channel_id: str,
        verification_level: int,
        default_message_notifications: int,
        explicit_content_filter: int,
    ) -> dict[str, Any]:
        client = self._require_client()
        payload = self._run(
            client.http.edit_guild(
                self._numeric_id(guild_id),
                features=list(features),
                rules_channel_id=self._numeric_id(rules_channel_id),
                public_updates_channel_id=self._numeric_id(public_updates_channel_id),
                verification_level=verification_level,
                default_message_notifications=default_message_notifications,
                explicit_content_filter=explicit_content_filter,
            )
        )
        if not isinstance(payload, dict):
            raise FixtureBackendError()
        return payload

    def _require_client(self):
        if self._client is None:
            raise FixtureBackendError()
        return self._client

    @staticmethod
    def _numeric_id(value: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            raise FixtureBackendError() from None

    def _run(self, awaitable):
        if self._loop is None:
            raise FixtureBackendError()
        try:
            return self._loop.run_until_complete(awaitable)
        except Exception as exc:
            raise self._redacted_backend_error(exc) from None

    @staticmethod
    def _redacted_backend_error(error: Exception) -> FixtureBackendError:
        status_code = getattr(error, "status", None)
        discord_code = getattr(error, "code", None)
        return FixtureBackendError(
            status_code=(
                status_code
                if isinstance(status_code, int) and not isinstance(status_code, bool)
                else None
            ),
            discord_code=(
                discord_code
                if isinstance(discord_code, int) and not isinstance(discord_code, bool)
                else None
            ),
            captcha_required=error.__class__.__name__ == "CaptchaRequired",
        )

    def _silence_library_logs(self) -> None:
        logger = logging.getLogger("discord")
        self._logger_state = (
            list(logger.handlers),
            logger.level,
            logger.propagate,
            logger.disabled,
        )
        logger.handlers = [logging.NullHandler()]
        logger.setLevel(logging.CRITICAL + 1)
        logger.propagate = False
        logger.disabled = False

    def _restore_library_logs(self) -> None:
        if self._logger_state is None:
            return
        handlers, level, propagate, disabled = self._logger_state
        logger = logging.getLogger("discord")
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled
        self._logger_state = None


class DiscordFixtureClient:
    """Small independent client for creating and recovering live fixtures."""

    def __init__(
        self,
        token: str,
        *,
        backend: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        pacer: FixturePacer | None = None,
    ):
        self._backend = backend or DiscordPySelfBackend(token)
        self._pacer = pacer or FixturePacer(sleep=sleep)
        self._opened = False

    def __enter__(self) -> DiscordFixtureClient:
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._backend.close()
        finally:
            self._opened = False

    def list_current_guilds(self) -> list[GuildSnapshot]:
        payload = self._call(
            "GET",
            operation="list fixture guilds",
            function=self._backend.list_current_guilds,
        )
        if not isinstance(payload, list):
            raise FixtureClientError("Discord returned a malformed fixture guild list.")

        guilds: list[GuildSnapshot] = []
        for item in payload:
            if not isinstance(item, dict):
                raise FixtureClientError(
                    "Discord returned a malformed fixture guild entry."
                )
            guild_id = item.get("id")
            name = item.get("name")
            owned = item.get("owner")
            if (
                not isinstance(guild_id, str)
                or not isinstance(name, str)
                or not isinstance(owned, bool)
            ):
                raise FixtureClientError(
                    "Discord returned an incomplete fixture guild entry."
                )
            guilds.append(GuildSnapshot(guild_id=guild_id, name=name, owned=owned))
        return guilds

    def create_guild(self, name: str) -> GuildSnapshot:
        payload = self._call(
            "POST",
            operation="create fixture guild",
            function=lambda: self._backend.create_guild(name),
        )
        if not isinstance(payload, dict):
            raise FixtureClientError("Discord returned a malformed created guild.")
        guild_id = payload.get("id")
        returned_name = payload.get("name")
        if not isinstance(guild_id, str) or returned_name != name:
            raise FixtureClientError("Discord returned an incomplete created guild.")
        return GuildSnapshot(guild_id=guild_id, name=returned_name, owned=True)

    def delete_guild(self, guild_id: str) -> str:
        try:
            self._call(
                "POST",
                operation="delete fixture guild",
                function=lambda: self._backend.delete_guild(guild_id),
            )
        except FixtureClientError as exc:
            if exc.status_code == 404:
                return "absent"
            raise
        return "deleted"

    def list_guild_channels(self, guild_id: str) -> list[GuildChannelSnapshot]:
        payload = self._call(
            "GET",
            operation="list fixture channels",
            function=lambda: self._backend.list_guild_channels(guild_id),
        )
        if not isinstance(payload, list):
            raise FixtureClientError(
                "Discord returned a malformed fixture channel list."
            )
        return [self._parse_channel(item) for item in payload]

    def create_channel(
        self,
        guild_id: str,
        channel_type: int,
        *,
        name: str,
        parent_id: str | None = None,
        permission_overwrites: Sequence[PermissionOverwriteSnapshot] = (),
    ) -> GuildChannelSnapshot:
        payload = self._call(
            "POST",
            operation="create fixture channel",
            function=lambda: self._backend.create_channel(
                guild_id,
                channel_type,
                name=name,
                parent_id=parent_id,
                permission_overwrites=permission_overwrites,
            ),
        )
        channel = self._parse_channel(payload)
        if channel.name != name or channel.channel_type != channel_type:
            raise FixtureClientError("Discord returned an unexpected created channel.")
        return channel

    def send_message(self, channel_id: str, *, content: str) -> MessageSnapshot:
        if not content:
            raise ValueError("Fixture messages require non-empty content.")
        payload = self._call(
            "POST",
            operation="send fixture message",
            function=lambda: self._backend.send_message(
                channel_id,
                content=content,
            ),
        )
        if not isinstance(payload, Mapping):
            raise FixtureClientError("Discord returned a malformed fixture message.")
        message_id = self._parse_id(payload.get("id"), "fixture message ID")
        author = payload.get("author")
        author_id = None
        if isinstance(author, Mapping) and author.get("id") is not None:
            author_id = self._parse_id(author.get("id"), "fixture message author")
        return MessageSnapshot(
            message_id=message_id,
            channel_id=channel_id,
            author_id=author_id,
        )

    def open_private_channel(self, user_id: str) -> PrivateChannelSnapshot:
        payload = self._call(
            "POST",
            operation="open fixture DM",
            function=lambda: self._backend.open_private_channel(user_id),
        )
        return self._parse_private_channel(payload)

    def open_group_channel(self, user_ids: Sequence[str]) -> PrivateChannelSnapshot:
        if not user_ids:
            raise ValueError("Fixture Group DMs require at least one recipient.")
        payload = self._call(
            "POST",
            operation="open fixture Group DM",
            function=lambda: self._backend.open_group_channel(user_ids),
        )
        return self._parse_private_channel(payload)

    def add_reaction(
        self,
        channel_id: str,
        message_id: str,
        *,
        emoji: str,
        reaction_type: int = 0,
    ) -> None:
        if not emoji:
            raise ValueError("Fixture reactions require an emoji.")
        self._call(
            "PUT",
            operation="add fixture reaction",
            function=lambda: self._backend.add_reaction(
                channel_id,
                message_id,
                emoji=emoji,
                reaction_type=reaction_type,
            ),
        )

    def start_thread(
        self,
        channel_id: str,
        *,
        name: str,
        thread_type: int = 11,
        auto_archive_duration: int = 1440,
    ) -> ThreadSnapshot:
        if not name:
            raise ValueError("Fixture threads require a name.")
        payload = self._call(
            "POST",
            operation="create fixture thread",
            function=lambda: self._backend.start_thread(
                channel_id,
                name=name,
                thread_type=thread_type,
                auto_archive_duration=auto_archive_duration,
            ),
        )
        return self._parse_thread(payload, fallback_parent_id=channel_id)

    def start_thread_from_message(
        self,
        channel_id: str,
        message_id: str,
        *,
        name: str,
        auto_archive_duration: int = 1440,
    ) -> ThreadSnapshot:
        if not name:
            raise ValueError("Fixture threads require a name.")
        payload = self._call(
            "POST",
            operation="create fixture thread from message",
            function=lambda: self._backend.start_thread_from_message(
                channel_id,
                message_id,
                name=name,
                auto_archive_duration=auto_archive_duration,
            ),
        )
        return self._parse_thread(payload, fallback_parent_id=channel_id)

    def start_forum_thread(
        self,
        channel_id: str,
        *,
        name: str,
        content: str,
        auto_archive_duration: int = 1440,
    ) -> ThreadSnapshot:
        if not name:
            raise ValueError("Fixture forum threads require a name.")
        if not content:
            raise ValueError("Fixture forum threads require initial content.")
        payload = self._call(
            "POST",
            operation="create fixture forum thread",
            function=lambda: self._backend.start_forum_thread(
                channel_id,
                name=name,
                content=content,
                auto_archive_duration=auto_archive_duration,
            ),
        )
        return self._parse_thread(payload, fallback_parent_id=channel_id)

    def add_thread_member(self, channel_id: str, user_id: str) -> None:
        self._call(
            "PUT",
            operation="add fixture thread member",
            function=lambda: self._backend.add_thread_member(channel_id, user_id),
        )

    def set_thread_archived(self, channel_id: str, *, archived: bool) -> ThreadSnapshot:
        return self.set_thread_state(channel_id, archived=archived)

    def set_thread_state(
        self,
        channel_id: str,
        *,
        archived: bool | None = None,
        locked: bool | None = None,
    ) -> ThreadSnapshot:
        if archived is None and locked is None:
            raise ValueError("Thread state updates require archived and/or locked.")
        if archived is not None and not isinstance(archived, bool):
            raise ValueError("archived must be boolean when provided.")
        if locked is not None and not isinstance(locked, bool):
            raise ValueError("locked must be boolean when provided.")
        if archived is None:
            operation = "update fixture thread lock state"
        elif archived:
            operation = "archive fixture thread"
        else:
            operation = "unarchive fixture thread"
        payload = self._call(
            "PATCH",
            operation=operation,
            function=lambda: self._backend.set_thread_state(
                channel_id,
                archived=archived,
                locked=locked,
            ),
        )
        return self._parse_thread(payload)

    def list_guild_roles(self, guild_id: str) -> list[GuildRoleSnapshot]:
        payload = self._call(
            "GET",
            operation="list fixture roles",
            function=lambda: self._backend.list_guild_roles(guild_id),
        )
        if not isinstance(payload, list):
            raise FixtureClientError("Discord returned a malformed fixture role list.")
        return [self._parse_role(item) for item in payload]

    def create_role(
        self,
        guild_id: str,
        *,
        name: str,
        permissions: int,
    ) -> GuildRoleSnapshot:
        payload = self._call(
            "POST",
            operation="create fixture role",
            function=lambda: self._backend.create_role(
                guild_id,
                name=name,
                permissions=permissions,
            ),
        )
        role = self._parse_role(payload)
        if role.name != name or role.permissions != permissions or role.managed:
            raise FixtureClientError("Discord returned an unexpected created role.")
        return role

    def get_guild_member(
        self,
        guild_id: str,
        user_id: str,
    ) -> GuildMemberSnapshot | None:
        try:
            payload = self._call(
                "GET",
                operation="get fixture member",
                function=lambda: self._backend.get_guild_member(guild_id, user_id),
            )
        except FixtureClientError as exc:
            if exc.status_code == 404:
                return None
            raise
        return self._parse_member(payload, expected_user_id=user_id)

    def add_guild_role(self, guild_id: str, user_id: str, role_id: str) -> None:
        self._call(
            "PUT",
            operation="assign fixture role",
            function=lambda: self._backend.add_guild_role(
                guild_id,
                user_id,
                role_id,
            ),
        )

    def create_one_use_invite(self, channel_id: str) -> InviteGrant:
        return self.create_invite(channel_id, max_age=300, max_uses=1)

    def create_invite(
        self,
        channel_id: str,
        *,
        max_age: int,
        max_uses: int,
    ) -> InviteGrant:
        if max_age <= 0 or max_uses <= 0:
            raise ValueError("Fixture invite limits must be positive.")
        payload = self._call(
            "POST",
            operation="create fixture invite",
            function=lambda: self._backend.create_invite(
                channel_id,
                max_age=max_age,
                max_uses=max_uses,
            ),
        )
        if not isinstance(payload, Mapping):
            raise FixtureClientError("Discord returned a malformed fixture invite.")
        code = payload.get("code")
        if not isinstance(code, str) or not code:
            raise FixtureClientError("Discord returned an incomplete fixture invite.")
        return InviteGrant(code=code)

    def accept_guild_invite(
        self,
        invite: InviteGrant,
        *,
        expected_guild_id: str,
        expected_channel_id: str,
    ) -> None:
        payload = self._call(
            "GET",
            operation="inspect fixture invite",
            function=lambda: self._backend.inspect_invite(invite.code),
        )
        if not isinstance(payload, Mapping):
            raise FixtureClientError("Discord returned a malformed fixture invite.")
        guild = payload.get("guild")
        channel = payload.get("channel")
        if not isinstance(guild, Mapping) or not isinstance(channel, Mapping):
            raise FixtureClientError("Discord returned an incomplete fixture invite.")
        guild_id = self._parse_id(guild.get("id"), "fixture invite guild")
        channel_id = self._parse_id(channel.get("id"), "fixture invite channel")
        channel_type = self._parse_integer(
            channel.get("type"),
            "fixture invite channel type",
        )
        if guild_id != expected_guild_id or channel_id != expected_channel_id:
            raise FixtureClientError("Discord returned an unexpected fixture invite.")

        self._call(
            "POST",
            operation="accept fixture invite",
            function=lambda: self._backend.accept_guild_invite(
                invite.code,
                guild_id=guild_id,
                channel_id=channel_id,
                channel_type=channel_type,
            ),
        )

    def get_guild_configuration(
        self,
        guild_id: str,
    ) -> GuildConfigurationSnapshot:
        payload = self._call(
            "GET",
            operation="get fixture guild configuration",
            function=lambda: self._backend.get_guild_configuration(guild_id),
        )
        return self._parse_guild_configuration(payload)

    def configure_community(
        self,
        guild_id: str,
        *,
        features: Sequence[str],
        rules_channel_id: str,
        public_updates_channel_id: str,
        verification_level: int,
        default_message_notifications: int,
        explicit_content_filter: int,
    ) -> GuildConfigurationSnapshot:
        payload = self._call(
            "PATCH",
            operation="configure fixture community",
            function=lambda: self._backend.configure_community(
                guild_id,
                features=features,
                rules_channel_id=rules_channel_id,
                public_updates_channel_id=public_updates_channel_id,
                verification_level=verification_level,
                default_message_notifications=default_message_notifications,
                explicit_content_filter=explicit_content_filter,
            ),
        )
        return self._parse_guild_configuration(payload)

    def _ensure_open(self) -> None:
        if self._opened:
            return
        self._pacer.wait_before_request("GET")
        try:
            self._backend.open()
        except FixtureBackendError as exc:
            self._raise_backend_error("initialize fixture client", exc)
        finally:
            self._pacer.note_request_finished()
        self._opened = True

    @classmethod
    def _parse_private_channel(cls, payload: Any) -> PrivateChannelSnapshot:
        if not isinstance(payload, Mapping):
            raise FixtureClientError("Discord returned a malformed private channel.")
        return PrivateChannelSnapshot(
            channel_id=cls._parse_id(payload.get("id"), "fixture private channel ID"),
            channel_type=cls._parse_integer(
                payload.get("type"), "fixture private channel type"
            ),
        )

    @classmethod
    def _parse_thread(
        cls,
        payload: Any,
        *,
        fallback_parent_id: str | None = None,
    ) -> ThreadSnapshot:
        if not isinstance(payload, Mapping):
            raise FixtureClientError("Discord returned a malformed fixture thread.")
        parent_id = payload.get("parent_id")
        if parent_id is None:
            parent_id = fallback_parent_id
        initial_message_id = None
        initial_message = payload.get("message")
        if initial_message is not None:
            if not isinstance(initial_message, Mapping):
                raise FixtureClientError(
                    "Discord returned a malformed fixture thread message."
                )
            initial_message_id = cls._parse_id(
                initial_message.get("id"),
                "fixture thread message ID",
            )
        archived = None
        locked = None
        auto_archive_duration = None
        metadata = payload.get("thread_metadata")
        if metadata is not None:
            if not isinstance(metadata, Mapping):
                raise FixtureClientError(
                    "Discord returned malformed fixture thread metadata."
                )
            raw_archived = metadata.get("archived")
            raw_locked = metadata.get("locked")
            raw_auto_archive_duration = metadata.get("auto_archive_duration")
            if raw_archived is not None and not isinstance(raw_archived, bool):
                raise FixtureClientError(
                    "Discord returned an invalid fixture thread archive state."
                )
            if raw_locked is not None and not isinstance(raw_locked, bool):
                raise FixtureClientError(
                    "Discord returned an invalid fixture thread lock state."
                )
            if raw_auto_archive_duration is not None:
                auto_archive_duration = cls._parse_integer(
                    raw_auto_archive_duration,
                    "fixture thread auto-archive duration",
                )
            archived = raw_archived
            locked = raw_locked
        return ThreadSnapshot(
            channel_id=cls._parse_id(payload.get("id"), "fixture thread ID"),
            parent_id=cls._parse_id(parent_id, "fixture thread parent"),
            thread_type=cls._parse_integer(
                payload.get("type"),
                "fixture thread type",
            ),
            initial_message_id=initial_message_id,
            archived=archived,
            locked=locked,
            auto_archive_duration=auto_archive_duration,
        )

    @classmethod
    def _parse_channel(cls, payload: Any) -> GuildChannelSnapshot:
        if not isinstance(payload, Mapping):
            raise FixtureClientError(
                "Discord returned a malformed fixture channel entry."
            )
        channel_id = cls._parse_id(payload.get("id"), "fixture channel ID")
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            raise FixtureClientError(
                "Discord returned an incomplete fixture channel entry."
            )
        channel_type = cls._parse_integer(payload.get("type"), "fixture channel type")
        parent_id = cls._parse_optional_id(
            payload.get("parent_id"),
            "fixture channel parent",
        )
        raw_overwrites = payload.get("permission_overwrites", [])
        if not isinstance(raw_overwrites, list):
            raise FixtureClientError("Discord returned malformed channel permissions.")
        overwrites: list[PermissionOverwriteSnapshot] = []
        for raw_overwrite in raw_overwrites:
            if not isinstance(raw_overwrite, Mapping):
                raise FixtureClientError(
                    "Discord returned malformed channel permissions."
                )
            overwrites.append(
                PermissionOverwriteSnapshot(
                    target_id=cls._parse_id(
                        raw_overwrite.get("id"),
                        "permission target",
                    ),
                    target_type=cls._parse_integer(
                        raw_overwrite.get("type"),
                        "permission target type",
                    ),
                    allow=cls._parse_integer(
                        raw_overwrite.get("allow", 0),
                        "allowed permissions",
                    ),
                    deny=cls._parse_integer(
                        raw_overwrite.get("deny", 0),
                        "denied permissions",
                    ),
                )
            )
        return GuildChannelSnapshot(
            channel_id=channel_id,
            name=name,
            channel_type=channel_type,
            parent_id=parent_id,
            permission_overwrites=tuple(
                sorted(
                    overwrites,
                    key=lambda overwrite: (
                        overwrite.target_type,
                        overwrite.target_id,
                    ),
                )
            ),
        )

    @classmethod
    def _parse_role(cls, payload: Any) -> GuildRoleSnapshot:
        if not isinstance(payload, Mapping):
            raise FixtureClientError("Discord returned a malformed fixture role entry.")
        role_id = cls._parse_id(payload.get("id"), "fixture role ID")
        name = payload.get("name")
        managed = payload.get("managed", False)
        if not isinstance(name, str) or not name or not isinstance(managed, bool):
            raise FixtureClientError(
                "Discord returned an incomplete fixture role entry."
            )
        return GuildRoleSnapshot(
            role_id=role_id,
            name=name,
            permissions=cls._parse_integer(
                payload.get("permissions"),
                "fixture role permissions",
            ),
            managed=managed,
        )

    @classmethod
    def _parse_member(
        cls,
        payload: Any,
        *,
        expected_user_id: str,
    ) -> GuildMemberSnapshot:
        if not isinstance(payload, Mapping):
            raise FixtureClientError("Discord returned a malformed fixture member.")
        user = payload.get("user")
        if not isinstance(user, Mapping):
            raise FixtureClientError("Discord returned an incomplete fixture member.")
        user_id = cls._parse_id(user.get("id"), "fixture member ID")
        raw_roles = payload.get("roles")
        pending = payload.get("pending", False)
        if user_id != expected_user_id or not isinstance(raw_roles, list):
            raise FixtureClientError("Discord returned an unexpected fixture member.")
        if not isinstance(pending, bool):
            raise FixtureClientError("Discord returned an incomplete fixture member.")
        return GuildMemberSnapshot(
            user_id=user_id,
            role_ids=frozenset(
                cls._parse_id(role_id, "fixture member role") for role_id in raw_roles
            ),
            pending=pending,
        )

    @classmethod
    def _parse_guild_configuration(
        cls,
        payload: Any,
    ) -> GuildConfigurationSnapshot:
        if not isinstance(payload, Mapping):
            raise FixtureClientError("Discord returned malformed guild configuration.")
        raw_features = payload.get("features")
        if not isinstance(raw_features, list) or not all(
            isinstance(feature, str) and feature for feature in raw_features
        ):
            raise FixtureClientError("Discord returned incomplete guild configuration.")
        return GuildConfigurationSnapshot(
            features=frozenset(raw_features),
            rules_channel_id=cls._parse_optional_id(
                payload.get("rules_channel_id"),
                "rules channel",
            ),
            public_updates_channel_id=cls._parse_optional_id(
                payload.get("public_updates_channel_id"),
                "public updates channel",
            ),
            verification_level=cls._parse_integer(
                payload.get("verification_level"),
                "verification level",
            ),
            default_message_notifications=cls._parse_integer(
                payload.get("default_message_notifications"),
                "default message notifications",
            ),
            explicit_content_filter=cls._parse_integer(
                payload.get("explicit_content_filter"),
                "explicit content filter",
            ),
        )

    @staticmethod
    def _parse_id(value: Any, field: str) -> str:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise FixtureClientError(f"Discord returned an invalid {field}.")
        resolved = str(value)
        if not resolved or not resolved.isdecimal():
            raise FixtureClientError(f"Discord returned an invalid {field}.")
        return resolved

    @classmethod
    def _parse_optional_id(cls, value: Any, field: str) -> str | None:
        if value is None:
            return None
        return cls._parse_id(value, field)

    @staticmethod
    def _parse_integer(value: Any, field: str) -> int:
        if isinstance(value, bool):
            raise FixtureClientError(f"Discord returned an invalid {field}.")
        try:
            resolved = int(value)
        except (TypeError, ValueError):
            raise FixtureClientError(f"Discord returned an invalid {field}.") from None
        if resolved < 0:
            raise FixtureClientError(f"Discord returned an invalid {field}.")
        return resolved

    def _call(self, method: str, *, operation: str, function: Callable[[], Any]):
        self._ensure_open()
        self._pacer.wait_before_request(method)
        try:
            return function()
        except FixtureBackendError as exc:
            self._raise_backend_error(operation, exc)
        finally:
            self._pacer.note_request_finished()

    @staticmethod
    def _raise_backend_error(operation: str, error: FixtureBackendError) -> None:
        if error.captcha_required:
            raise FixtureClientError(
                f"{operation} requires manual CAPTCHA completion.",
                status_code=error.status_code,
                captcha_required=True,
                discord_code=error.discord_code,
            ) from None
        if error.status_code is None:
            raise FixtureClientError(
                f"{operation} ended with transport uncertainty; rerun to reconcile fixtures."
            ) from None
        code_suffix = (
            f", Discord code {error.discord_code}"
            if error.discord_code is not None
            else ""
        )
        raise FixtureClientError(
            f"{operation} failed (HTTP {error.status_code}{code_suffix}).",
            status_code=error.status_code,
            discord_code=error.discord_code,
        )
