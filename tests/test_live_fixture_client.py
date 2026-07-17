import logging
import os

import pytest

from tests.live.fixture_client import (
    DiscordFixtureClient,
    DiscordPySelfBackend,
    FixtureBackendError,
    FixtureClientError,
    FixtureLockError,
    FixturePacer,
    FixturePacingPolicy,
    GuildChannelSnapshot,
    GuildConfigurationSnapshot,
    GuildMemberSnapshot,
    GuildRoleSnapshot,
    GuildSnapshot,
    InviteGrant,
    PermissionOverwriteSnapshot,
    SuiteLock,
)


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


class StubBackend:
    def __init__(self, *, guilds=(), failures=None):
        self.guilds = list(guilds)
        self.failures = failures or {}
        self.calls = []

    def _record(self, operation, *details):
        self.calls.append((operation, *details))
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure

    def open(self):
        self._record("open")

    def close(self):
        self.calls.append(("close",))

    def list_current_guilds(self):
        self._record("list")
        return list(self.guilds)

    def create_guild(self, name):
        self._record("create", name)
        guild = {"id": "private-id", "name": name, "owner": True}
        self.guilds.append(guild)
        return guild

    def delete_guild(self, guild_id):
        self._record("delete", guild_id)


def make_pacer(clock, *, read=(1.0, 1.0), mutation=(3.0, 3.0)):
    return FixturePacer(
        FixturePacingPolicy(
            read_interval=read,
            mutation_interval=mutation,
        ),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        uniform=lambda lower, _upper: lower,
    )


def test_pacer_spaces_first_and_subsequent_reads_and_mutations():
    clock = FakeClock()
    pacer = make_pacer(clock)

    pacer.wait_before_request("GET")
    pacer.note_request_finished()
    clock.now += 0.25
    pacer.wait_before_request("GET")
    pacer.note_request_finished()
    pacer.wait_before_request("POST")

    assert clock.sleeps == [1.0, 0.75, 3.0]


def test_client_applies_longer_spacing_to_mutations():
    clock = FakeClock()
    backend = StubBackend()
    client = DiscordFixtureClient(
        "private-token",
        backend=backend,
        pacer=make_pacer(clock),
    )

    assert client.list_current_guilds() == []
    created = client.create_guild("fixture-name")

    assert created.guild_id == "private-id"
    assert clock.sleeps == [1.0, 1.0, 3.0]
    assert backend.calls == [
        ("open",),
        ("list",),
        ("create", "fixture-name"),
    ]


def test_client_uses_user_client_guild_delete_operation():
    clock = FakeClock()
    backend = StubBackend()
    client = DiscordFixtureClient(
        "private-token",
        backend=backend,
        pacer=make_pacer(clock),
    )

    assert client.delete_guild("private-id") == "deleted"

    assert backend.calls == [("open",), ("delete", "private-id")]
    assert clock.sleeps == [1.0, 3.0]


def test_wrapper_does_not_add_create_retry_after_backend_failure():
    clock = FakeClock()
    backend = StubBackend(failures={"create": FixtureBackendError(status_code=500)})
    client = DiscordFixtureClient(
        "private-token",
        backend=backend,
        pacer=make_pacer(clock),
    )

    with pytest.raises(FixtureClientError, match=r"HTTP 500"):
        client.create_guild("fixture-name")

    assert backend.calls == [("open",), ("create", "fixture-name")]
    assert clock.sleeps == [1.0, 3.0]


def test_client_reports_only_numeric_discord_error_code():
    backend = StubBackend(
        failures={
            "create": FixtureBackendError(
                status_code=403,
                discord_code=20001,
            )
        }
    )
    client = DiscordFixtureClient(
        "private-token",
        backend=backend,
        pacer=make_pacer(FakeClock()),
    )

    with pytest.raises(
        FixtureClientError,
        match=r"HTTP 403, Discord code 20001",
    ) as error:
        client.create_guild("fixture-name")

    assert "fixture-name" not in str(error.value)


def test_client_classifies_captcha_without_exposing_challenge_details():
    challenge_type = type("CaptchaRequired", (Exception,), {})
    backend_error = DiscordPySelfBackend._redacted_backend_error(challenge_type())
    assert backend_error.captcha_required is True
    backend = StubBackend(
        failures={
            "create": FixtureBackendError(
                status_code=400,
                discord_code=-1,
                captcha_required=True,
            )
        }
    )
    client = DiscordFixtureClient(
        "private-token",
        backend=backend,
        pacer=make_pacer(FakeClock()),
    )

    with pytest.raises(FixtureClientError, match="manual CAPTCHA") as error:
        client.create_guild("fixture-name")

    assert error.value.captcha_required is True
    assert error.value.status_code == 400
    assert "fixture-name" not in str(error.value)


def test_transport_uncertainty_does_not_expose_request_details():
    token = "private-token-value"
    private_id = "private-guild-id"
    backend = StubBackend(failures={"delete": FixtureBackendError()})
    client = DiscordFixtureClient(
        token,
        backend=backend,
        pacer=make_pacer(FakeClock()),
    )

    with pytest.raises(FixtureClientError) as error:
        client.delete_guild(private_id)

    assert token not in str(error.value)
    assert private_id not in str(error.value)
    assert error.value.__cause__ is None


def test_client_treats_backend_404_as_absent():
    backend = StubBackend(failures={"delete": FixtureBackendError(status_code=404)})
    client = DiscordFixtureClient(
        "private-token",
        backend=backend,
        pacer=make_pacer(FakeClock()),
    )

    assert client.delete_guild("private-id") == "absent"


class StubTopologyBackend(StubBackend):
    def list_guild_channels(self, guild_id):
        self._record("list_channels", guild_id)
        return [
            {
                "id": "10",
                "name": "existing-channel",
                "type": 0,
                "parent_id": None,
                "permission_overwrites": [],
            }
        ]

    def create_channel(
        self,
        guild_id,
        channel_type,
        *,
        name,
        parent_id,
        permission_overwrites,
    ):
        self._record("create_channel", guild_id, channel_type)
        return {
            "id": "11",
            "name": name,
            "type": channel_type,
            "parent_id": parent_id,
            "permission_overwrites": [
                {
                    "id": overwrite.target_id,
                    "type": overwrite.target_type,
                    "allow": str(overwrite.allow),
                    "deny": str(overwrite.deny),
                }
                for overwrite in permission_overwrites
            ],
        }

    def list_guild_roles(self, guild_id):
        self._record("list_roles", guild_id)
        return [
            {
                "id": "20",
                "name": "existing-role",
                "permissions": "0",
                "managed": False,
            }
        ]

    def create_role(self, guild_id, *, name, permissions):
        self._record("create_role", guild_id, permissions)
        return {
            "id": "21",
            "name": name,
            "permissions": str(permissions),
            "managed": False,
        }

    def get_guild_member(self, guild_id, user_id):
        self._record("get_member", guild_id, user_id)
        return {
            "user": {"id": user_id},
            "roles": ["20"],
            "pending": False,
        }

    def add_guild_role(self, guild_id, user_id, role_id):
        self._record("add_role", guild_id, user_id, role_id)

    def create_invite(self, channel_id, *, max_age, max_uses):
        self._record("create_invite", channel_id)
        assert (max_age, max_uses) == (300, 1)
        return {"code": "private-invite-code"}

    def inspect_invite(self, code):
        self._record("inspect_invite", code)
        return {
            "guild": {"id": "1"},
            "channel": {"id": "10", "type": 0},
        }

    def accept_guild_invite(
        self,
        code,
        *,
        guild_id,
        channel_id,
        channel_type,
    ):
        self._record(
            "accept_invite",
            code,
            guild_id,
            channel_id,
            channel_type,
        )

    def get_guild_configuration(self, guild_id):
        self._record("get_configuration", guild_id)
        return {
            "features": [],
            "rules_channel_id": None,
            "public_updates_channel_id": None,
            "verification_level": 0,
            "default_message_notifications": 0,
            "explicit_content_filter": 0,
        }

    def configure_community(
        self,
        guild_id,
        *,
        features,
        rules_channel_id,
        public_updates_channel_id,
        verification_level,
        default_message_notifications,
        explicit_content_filter,
    ):
        self._record("configure_community", guild_id)
        return {
            "features": list(features),
            "rules_channel_id": rules_channel_id,
            "public_updates_channel_id": public_updates_channel_id,
            "verification_level": verification_level,
            "default_message_notifications": default_message_notifications,
            "explicit_content_filter": explicit_content_filter,
        }


class StubThreadBackend(StubBackend):
    def __init__(self):
        super().__init__()
        self.threads = {}

    def start_thread(
        self,
        channel_id,
        *,
        name,
        thread_type,
        auto_archive_duration,
    ):
        self._record(
            "start_thread",
            channel_id,
            name,
            thread_type,
            auto_archive_duration,
        )
        payload = {"id": "30", "parent_id": channel_id, "type": thread_type}
        self.threads["30"] = payload
        return payload

    def start_thread_from_message(
        self,
        channel_id,
        message_id,
        *,
        name,
        auto_archive_duration,
    ):
        self._record(
            "start_thread_from_message",
            channel_id,
            message_id,
            name,
            auto_archive_duration,
        )
        payload = {"id": "31", "parent_id": channel_id, "type": 10}
        self.threads["31"] = payload
        return payload

    def start_forum_thread(
        self,
        channel_id,
        *,
        name,
        content,
        auto_archive_duration,
    ):
        self._record(
            "start_forum_thread",
            channel_id,
            name,
            content,
            auto_archive_duration,
        )
        payload = {
            "id": "32",
            "parent_id": channel_id,
            "type": 11,
            "message": {"id": "33"},
        }
        self.threads["32"] = payload
        return payload

    def add_thread_member(self, channel_id, user_id):
        self._record("add_thread_member", channel_id, user_id)

    def set_thread_state(self, channel_id, *, archived=None, locked=None):
        self._record("set_thread_state", channel_id, archived, locked)
        payload = dict(self.threads[channel_id])
        payload["thread_metadata"] = {
            "archived": archived if archived is not None else False,
            "locked": locked if locked is not None else False,
            "auto_archive_duration": 1440,
        }
        return payload


def test_client_normalizes_thread_creation_membership_and_archive_operations():
    backend = StubThreadBackend()
    client = DiscordFixtureClient(
        "private-token",
        backend=backend,
        pacer=make_pacer(FakeClock()),
    )

    public = client.start_thread("10", name="public", thread_type=11)
    announcement = client.start_thread_from_message("10", "20", name="news")
    forum = client.start_forum_thread("10", name="post", content="initial")
    client.add_thread_member(public.channel_id, "40")
    archived = client.set_thread_archived(public.channel_id, archived=True)
    locked = client.set_thread_state(
        public.channel_id,
        archived=True,
        locked=True,
    )

    assert (public.parent_id, public.thread_type) == ("10", 11)
    assert (announcement.parent_id, announcement.thread_type) == ("10", 10)
    assert forum.initial_message_id == "33"
    assert archived.archived is True
    assert archived.locked is False
    assert archived.auto_archive_duration == 1440
    assert locked.archived is True
    assert locked.locked is True
    assert backend.calls == [
        ("open",),
        ("start_thread", "10", "public", 11, 1440),
        ("start_thread_from_message", "10", "20", "news", 1440),
        ("start_forum_thread", "10", "post", "initial", 1440),
        ("add_thread_member", "30", "40"),
        ("set_thread_state", "30", True, None),
        ("set_thread_state", "30", True, True),
    ]


def test_client_normalizes_topology_operations_and_spaces_each_request():
    clock = FakeClock()
    backend = StubTopologyBackend()
    client = DiscordFixtureClient(
        "private-token",
        backend=backend,
        pacer=make_pacer(clock),
    )
    overwrite = PermissionOverwriteSnapshot(
        target_id="1",
        target_type=0,
        allow=3,
        deny=4,
    )

    assert client.list_guild_channels("1")[0].channel_id == "10"
    channel = client.create_channel(
        "1",
        0,
        name="created-channel",
        parent_id="10",
        permission_overwrites=(overwrite,),
    )
    assert channel.permission_overwrites == (overwrite,)
    assert client.list_guild_roles("1")[0].role_id == "20"
    role = client.create_role("1", name="created-role", permissions=8)
    assert role.permissions == 8
    member = client.get_guild_member("1", "30")
    assert member is not None and member.role_ids == frozenset({"20"})
    client.add_guild_role("1", "30", "21")
    invite = client.create_one_use_invite("10")
    client.accept_guild_invite(
        invite,
        expected_guild_id="1",
        expected_channel_id="10",
    )
    configuration = client.get_guild_configuration("1")
    assert configuration.features == frozenset()
    configured = client.configure_community(
        "1",
        features=("COMMUNITY",),
        rules_channel_id="10",
        public_updates_channel_id="11",
        verification_level=1,
        default_message_notifications=1,
        explicit_content_filter=2,
    )
    assert configured.features == frozenset({"COMMUNITY"})

    assert len(clock.sleeps) == 12
    assert clock.sleeps.count(1.0) == 6
    assert clock.sleeps.count(3.0) == 6


def test_get_member_maps_404_to_absent():
    backend = StubTopologyBackend(
        failures={"get_member": FixtureBackendError(status_code=404)}
    )
    client = DiscordFixtureClient(
        "private-token",
        backend=backend,
        pacer=make_pacer(FakeClock()),
    )

    assert client.get_guild_member("1", "30") is None


def test_invite_validation_error_redacts_code_and_ids():
    backend = StubTopologyBackend()
    client = DiscordFixtureClient(
        "private-token",
        backend=backend,
        pacer=make_pacer(FakeClock()),
    )
    invite = InviteGrant(code="private-invite-code")

    with pytest.raises(FixtureClientError, match="unexpected fixture invite") as error:
        client.accept_guild_invite(
            invite,
            expected_guild_id="999",
            expected_channel_id="998",
        )

    rendered = str(error.value)
    assert invite.code not in rendered
    assert "999" not in rendered
    assert "998" not in rendered


class FakeGuild:
    id = 123456789
    name = "fixture-name"


class FakeDiscordHTTP:
    def __init__(self, events):
        self.events = events

    async def get_guilds(self, *, with_counts):
        self.events.append(("list", with_counts))
        return [{"id": "1", "name": "existing", "owner": True}]

    async def delete_guild(self, guild_id):
        self.events.append(("delete", guild_id))

    async def get_all_guild_channels(self, guild_id):
        self.events.append(("list_channels", guild_id))
        return []

    async def create_channel(self, guild_id, channel_type, **fields):
        self.events.append(("create_channel", guild_id, channel_type, fields))
        return {
            "id": "11",
            "type": channel_type,
            "parent_id": fields.get("parent_id"),
            "permission_overwrites": fields.get("permission_overwrites", []),
            **fields,
        }

    async def get_roles(self, guild_id):
        self.events.append(("list_roles", guild_id))
        return []

    async def create_role(self, guild_id, **fields):
        self.events.append(("create_role", guild_id, fields))
        return {"id": "21", "managed": False, **fields}

    async def get_member(self, guild_id, user_id):
        self.events.append(("get_member", guild_id, user_id))
        return {"user": {"id": str(user_id)}, "roles": [], "pending": False}

    async def add_role(self, guild_id, user_id, role_id):
        self.events.append(("add_role", guild_id, user_id, role_id))

    async def create_invite(self, channel_id, **fields):
        self.events.append(("create_invite", channel_id, fields))
        return {"code": "private-invite-code"}

    async def get_invite(self, code, **fields):
        self.events.append(("get_invite", code, fields))
        return {
            "guild": {"id": "1"},
            "channel": {"id": "10", "type": 0},
        }

    async def accept_invite(
        self,
        code,
        invite_type,
        session_id,
        **fields,
    ):
        self.events.append(("accept_invite", code, invite_type, session_id, fields))

    async def get_guild(self, guild_id, *, with_counts):
        self.events.append(("get_configuration", guild_id, with_counts))
        return {
            "features": [],
            "rules_channel_id": None,
            "public_updates_channel_id": None,
            "verification_level": 0,
            "default_message_notifications": 0,
            "explicit_content_filter": 0,
        }

    async def edit_guild(self, guild_id, **fields):
        self.events.append(("configure_community", guild_id, fields))
        return fields

    async def start_thread_without_message(
        self,
        channel_id,
        *,
        name,
        auto_archive_duration,
        type,
    ):
        self.events.append((
            "start_thread",
            channel_id,
            name,
            auto_archive_duration,
            type,
        ))
        return {"id": "30", "parent_id": str(channel_id), "type": type}

    async def start_thread_with_message(
        self,
        channel_id,
        message_id,
        *,
        name,
        auto_archive_duration,
    ):
        self.events.append((
            "start_thread_from_message",
            channel_id,
            message_id,
            name,
            auto_archive_duration,
        ))
        return {"id": "31", "parent_id": str(channel_id), "type": 10}

    async def start_thread_in_forum(self, channel_id, *, params):
        self.events.append(("start_forum_thread", channel_id, params.payload))
        return {
            "id": "32",
            "parent_id": str(channel_id),
            "type": 11,
            "message": {"id": "33"},
        }

    async def add_user_to_thread(self, channel_id, user_id):
        self.events.append(("add_thread_member", channel_id, user_id))

    async def edit_channel(self, channel_id, **fields):
        self.events.append(("edit_channel", channel_id, fields))
        return {"id": str(channel_id), "parent_id": "10", "type": 11}


class FakeDiscordClient:
    def __init__(self, events):
        self.events = events
        self.http = FakeDiscordHTTP(events)
        self._connection = type("Connection", (), {"session_id": None})()

    async def login(self, token):
        self.events.append(("login", token))

    async def create_guild(self, *, name):
        self.events.append(("create", name))
        return FakeGuild()

    async def close(self):
        self.events.append(("close",))


class FakeMessageParameters:
    def __init__(self, *, content, channel_payload):
        self.payload = {"message": {"content": content}, **channel_payload}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeDiscordHTTPModule:
    @staticmethod
    def handle_message_parameters(*, content, channel_payload):
        return FakeMessageParameters(
            content=content,
            channel_payload=channel_payload,
        )


class FakeDiscordModule:
    HeadersContext = object
    __version__ = "2.2.0a"
    InviteType = type("InviteType", (), {"guild": "guild-invite"})
    http = FakeDiscordHTTPModule
    utils = type(
        "Utils",
        (),
        {"_generate_session_id": staticmethod(lambda: "generated-session")},
    )

    def __init__(self):
        self.events = []
        self.options = None
        self.client = FakeDiscordClient(self.events)

    def Client(self, **options):
        self.options = options
        return self.client


def test_discord_py_self_backend_logs_in_without_gateway_and_runs_guild_operations():
    module = FakeDiscordModule()
    backend = DiscordPySelfBackend(
        "private-token",
        module_loader=lambda name: module,
    )

    backend.open()
    assert backend.list_current_guilds() == [
        {"id": "1", "name": "existing", "owner": True}
    ]
    assert backend.create_guild("fixture-name") == {
        "id": "123456789",
        "name": "fixture-name",
        "owner": True,
    }
    backend.delete_guild("123456789")
    backend.close()

    assert module.options == {"sync_presence": False}
    assert module.events == [
        ("login", "private-token"),
        ("list", False),
        ("create", "fixture-name"),
        ("delete", 123456789),
        ("close",),
    ]


def test_discord_py_self_backend_uses_pinned_topology_http_operations():
    module = FakeDiscordModule()
    backend = DiscordPySelfBackend(
        "private-token",
        module_loader=lambda name: module,
    )
    overwrite = PermissionOverwriteSnapshot("1", 0, 3, 4)

    backend.open()
    assert backend.list_guild_channels("1") == []
    backend.create_channel(
        "1",
        0,
        name="created-channel",
        parent_id="10",
        permission_overwrites=(overwrite,),
    )
    assert backend.list_guild_roles("1") == []
    backend.create_role("1", name="created-role", permissions=8)
    backend.get_guild_member("1", "30")
    backend.add_guild_role("1", "30", "21")
    backend.create_invite("10", max_age=300, max_uses=1)
    backend.inspect_invite("private-invite-code")
    backend.accept_guild_invite(
        "private-invite-code",
        guild_id="1",
        channel_id="10",
        channel_type=0,
    )
    backend.get_guild_configuration("1")
    backend.configure_community(
        "1",
        features=("COMMUNITY",),
        rules_channel_id="10",
        public_updates_channel_id="11",
        verification_level=1,
        default_message_notifications=1,
        explicit_content_filter=2,
    )
    backend.close()

    create_channel_event = next(
        event for event in module.events if event[0] == "create_channel"
    )
    assert create_channel_event[1:3] == (1, 0)
    assert create_channel_event[3]["parent_id"] == 10
    assert create_channel_event[3]["permission_overwrites"] == [
        {"id": "1", "type": 0, "allow": "3", "deny": "4"}
    ]
    create_invite_event = next(
        event for event in module.events if event[0] == "create_invite"
    )
    assert create_invite_event[2] == {
        "max_age": 300,
        "max_uses": 1,
        "temporary": False,
        "unique": True,
    }
    accept_event = next(event for event in module.events if event[0] == "accept_invite")
    assert accept_event[2:4] == ("guild-invite", "generated-session")
    assert accept_event[4] == {
        "guild_id": 1,
        "channel_id": 10,
        "channel_type": 0,
    }


def test_discord_py_self_backend_uses_pinned_thread_http_operations():
    module = FakeDiscordModule()
    backend = DiscordPySelfBackend(
        "private-token",
        module_loader=lambda name: module,
    )

    backend.open()
    backend.start_thread("10", name="public", thread_type=11)
    backend.start_thread_from_message("10", "20", name="news")
    forum = backend.start_forum_thread("10", name="post", content="initial")
    backend.add_thread_member("30", "40")
    backend.set_thread_archived("30", archived=True)
    backend.set_thread_state("30", archived=True, locked=True)
    backend.close()

    assert forum["message"]["id"] == "33"
    assert ("start_thread", 10, "public", 1440, 11) in module.events
    assert ("start_thread_from_message", 10, 20, "news", 1440) in module.events
    forum_event = next(
        event for event in module.events if event[0] == "start_forum_thread"
    )
    assert forum_event == (
        "start_forum_thread",
        10,
        {
            "message": {"content": "initial"},
            "name": "post",
            "auto_archive_duration": 1440,
            "type": 11,
        },
    )
    assert ("add_thread_member", 30, 40) in module.events
    assert ("edit_channel", 30, {"archived": True}) in module.events
    assert (
        "edit_channel",
        30,
        {"archived": True, "locked": True},
    ) in module.events


def test_discord_py_self_backend_temporarily_silences_library_logs():
    logger = logging.getLogger("discord")
    previous = (list(logger.handlers), logger.level, logger.propagate, logger.disabled)
    module = FakeDiscordModule()
    backend = DiscordPySelfBackend(
        "private-token",
        module_loader=lambda name: module,
    )

    backend.open()

    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.NullHandler)
    assert logger.level == logging.CRITICAL + 1
    assert logger.propagate is False

    backend.close()

    assert (
        list(logger.handlers),
        logger.level,
        logger.propagate,
        logger.disabled,
    ) == previous


def test_discord_py_self_backend_reports_missing_live_extra():
    def missing_module(_name):
        raise ModuleNotFoundError

    backend = DiscordPySelfBackend(
        "private-token",
        module_loader=missing_module,
    )

    with pytest.raises(FixtureClientError, match=r"tests/live.*uv project"):
        backend.open()


def test_discord_py_self_backend_rejects_unpinned_client_version():
    module = FakeDiscordModule()
    module.__version__ = "2.1.0"
    backend = DiscordPySelfBackend(
        "private-token",
        module_loader=lambda name: module,
    )

    with pytest.raises(FixtureClientError, match=r"pinned live-suite client"):
        backend.open()


def test_guild_snapshot_repr_hides_identity_fields():
    snapshot = GuildSnapshot(
        guild_id="private-guild-id",
        name="private-guild-name",
        owned=True,
    )

    rendered = repr(snapshot)

    assert rendered == "GuildSnapshot(owned=True)"
    assert snapshot.guild_id not in rendered
    assert snapshot.name not in rendered


def test_topology_snapshot_reprs_hide_identity_fields():
    private_values = {
        "private-channel-id",
        "private-channel-name",
        "private-parent-id",
        "private-target-id",
        "private-role-id",
        "private-role-name",
        "private-user-id",
        "private-rules-id",
        "private-updates-id",
        "private-invite-code",
    }
    snapshots = (
        PermissionOverwriteSnapshot("private-target-id", 0, 1, 2),
        GuildChannelSnapshot(
            "private-channel-id",
            "private-channel-name",
            0,
            "private-parent-id",
        ),
        GuildRoleSnapshot("private-role-id", "private-role-name", 0, False),
        GuildMemberSnapshot("private-user-id", frozenset({"private-role-id"}), False),
        GuildConfigurationSnapshot(
            frozenset({"COMMUNITY"}),
            "private-rules-id",
            "private-updates-id",
            1,
            1,
            2,
        ),
        InviteGrant("private-invite-code"),
    )

    rendered = " ".join(repr(snapshot) for snapshot in snapshots)

    assert all(value not in rendered for value in private_values)


def test_suite_lock_rejects_concurrent_process_and_releases(tmp_path):
    lock_path = tmp_path / "state" / "suite.lock"

    with SuiteLock(lock_path):
        with pytest.raises(FixtureLockError, match="global lock"):
            with SuiteLock(lock_path):
                pass

    with SuiteLock(lock_path):
        pass

    if os.name != "nt":
        assert lock_path.stat().st_mode & 0o777 == 0o600
