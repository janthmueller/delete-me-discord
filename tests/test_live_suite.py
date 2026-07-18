import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from delete_me_discord.discord.channel_types import ChannelType
from tests.live import live_suite
from tests.live.fixture_client import (
    FixtureClientError,
    FixturePacer,
    FixturePacingPolicy,
    GuildChannelSnapshot,
    GuildConfigurationSnapshot,
    GuildMemberSnapshot,
    GuildRoleSnapshot,
    GuildSnapshot,
    InviteGrant,
)
from tests.live.live_suite import (
    AccountCheck,
    AccountValidationReport,
    LedgerResource,
    LiveLedger,
    LiveSuiteError,
    LiveSuiteSafetyError,
    SecretConfigurationError,
    load_account_tokens,
    new_run_id,
    read_secret_file,
    require_fixture_roles,
    validate_account_tokens,
)


class StubResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class StubSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def get(self, url, *, headers, timeout, params=None):
        self.requests.append((url, headers, timeout, params))
        return self.responses.pop(0)


class RecordingPacer:
    def __init__(self):
        self.events = []

    def wait_before_request(self, method):
        self.events.append(("wait", method))

    def note_request_finished(self):
        self.events.append(("finished", None))


def write_private_secret_file(path, contents):
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o600)


def valid_report():
    return AccountValidationReport(
        checks=(
            AccountCheck(fixture_role="owner", status_code=200, user_id="100"),
            AccountCheck(fixture_role="subject", status_code=200, user_id="200"),
        )
    )


def no_wait_pacer():
    return FixturePacer(
        FixturePacingPolicy(
            read_interval=(0.0, 0.0),
            mutation_interval=(0.0, 0.0),
        )
    )


def test_load_account_tokens_parses_file_and_applies_environment_override(tmp_path):
    secret_file = tmp_path / "secrets.env"
    write_private_secret_file(
        secret_file,
        "# dedicated fixtures\nTOKEN_OWNER=file.owner.token\nTOKEN_SUBJECT='subject.token'\n",
    )

    tokens = load_account_tokens(
        secret_file,
        environ={"TOKEN_OWNER": "environment.owner.token"},
    )

    assert tokens == {
        "owner": "environment.owner.token",
        "subject": "subject.token",
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable")
def test_read_secret_file_rejects_group_or_world_access(tmp_path):
    secret_file = tmp_path / "secrets.env"
    secret_file.write_text("TOKEN_OWNER=private.token\n", encoding="utf-8")
    secret_file.chmod(0o644)

    with pytest.raises(SecretConfigurationError, match="0600"):
        read_secret_file(secret_file)


def test_load_account_tokens_rejects_duplicate_token_without_exposing_it(tmp_path):
    secret = "same.private.token"
    secret_file = tmp_path / "secrets.env"
    write_private_secret_file(
        secret_file,
        f"TOKEN_OWNER={secret}\nTOKEN_SUBJECT={secret}\n",
    )

    with pytest.raises(SecretConfigurationError) as error:
        load_account_tokens(secret_file, environ={})

    assert secret not in str(error.value)
    assert "owner" not in str(error.value)
    assert "subject" not in str(error.value)


def test_environment_parse_error_does_not_expose_secret_key_or_value():
    secret_key = "TOKEN_PRIVATE_USERNAME"
    secret_value = "private token with spaces"

    with pytest.raises(SecretConfigurationError) as error:
        load_account_tokens(environ={secret_key: secret_value})

    assert secret_key not in str(error.value)
    assert "private_username" not in str(error.value)
    assert secret_value not in str(error.value)


def test_redacted_dmd_failure_diagnostic_applies_second_pass_scrubbing():
    secret = "abcdefghijklmnopqrstuvwxyz0123456789"
    diagnostic = live_suite._sanitize_redacted_dmd_failure(
        "token="
        f"{secret} https://discord.com/channels/123456789012345678 "
        "message 223456789012345678 failed"
    )

    assert secret not in diagnostic
    assert "discord.com" not in diagnostic
    assert "123456789012345678" not in diagnostic
    assert "223456789012345678" not in diagnostic
    assert "<redacted-secret>" in diagnostic


def test_scoped_dmd_uses_exact_scope_without_command_line_token(
    monkeypatch,
):
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return SimpleNamespace(
            returncode=0,
            stdout="Dry run enabled.",
            stderr="",
        )

    monkeypatch.setattr(live_suite.subprocess, "run", run)

    output = live_suite._run_scoped_dmd(
        "private-token",
        "scope-id",
        dry_run=True,
    )

    assert output == "Dry run enabled."
    assert "private-token" not in captured["command"]
    assert captured["environment"]["DISCORD_TOKEN"] == "private-token"
    assert captured["command"][-1] == "--dry-run"


def test_require_fixture_roles_orders_generic_roles_and_hides_invalid_keys():
    tokens = {
        "peer_b": "fourth.token",
        "owner": "first.token",
        "peer_a": "third.token",
        "subject": "second.token",
    }

    ordered = require_fixture_roles(tokens)

    assert list(ordered) == ["owner", "subject", "peer_a", "peer_b"]

    private_key = "private_username"
    with pytest.raises(SecretConfigurationError) as error:
        require_fixture_roles({private_key: "private.token"})
    assert private_key not in str(error.value)


def test_validate_account_tokens_returns_distinct_redacted_identities():
    session = StubSession(
        [
            StubResponse(200, {"id": "100", "username": "owner-name"}),
            StubResponse(200, {"id": "200", "username": "subject-name"}),
        ]
    )

    report = validate_account_tokens(
        {"owner": "owner.private.token", "subject": "subject.private.token"},
        session=session,
        pacer=no_wait_pacer(),
    )
    report.require_ready(2)

    assert report.ledger_identities == {"owner": "100", "subject": "200"}
    assert all("private.token" not in repr(check) for check in report.checks)
    assert "owner" not in repr(report)
    assert "subject" not in repr(report)
    assert "100" not in repr(report)
    assert "200" not in repr(report)
    assert len(session.requests) == 2


def test_observe_forum_starter_state_checks_thread_and_message_without_content():
    session = StubSession([
        StubResponse(200, {"id": "30", "parent_id": "20", "type": 11}),
        StubResponse(200, [{"id": "30", "content": "private-content"}]),
    ])
    pacer = RecordingPacer()

    observation = live_suite.observe_forum_starter_state(
        "private-token",
        "30",
        "30",
        "20",
        client=session,
        pacer=pacer,
    )

    assert observation == live_suite.ForumStarterObservation(True, True)
    assert len(session.requests) == 2
    assert session.requests[1][3] == {"around": "30", "limit": 1}
    assert pacer.events == [
        ("wait", "GET"),
        ("finished", None),
        ("wait", "GET"),
        ("finished", None),
    ]


def test_observe_forum_starter_state_maps_missing_thread_to_absent():
    observation = live_suite.observe_forum_starter_state(
        "private-token",
        "30",
        "30",
        "20",
        client=StubSession([StubResponse(404, {})]),
        pacer=RecordingPacer(),
    )

    assert observation == live_suite.ForumStarterObservation(False, False)


def test_observe_destructive_contract_scope_reads_full_redacted_state():
    scope = live_suite.DestructiveContractScope(
        "voice-chat",
        "channel:voice",
        "channel",
        ChannelType.GUILD_VOICE,
    )
    resource = LedgerResource(
        run_id="dmd-live-20260713T101112Z-0123abcd",
        fixture_key="channel:voice",
        kind="channel",
        resource_id="100",
        owner_handle="owner",
        guild_id="500",
    )
    messages = (
        LedgerResource(
            run_id=resource.run_id,
            fixture_key="message:subject",
            kind="message",
            resource_id="300",
            owner_handle="subject",
            guild_id="500",
            parent_id="100",
        ),
        LedgerResource(
            run_id=resource.run_id,
            fixture_key="message:foreign",
            kind="message",
            resource_id="200",
            owner_handle="peer_a",
            guild_id="500",
            parent_id="100",
        ),
    )
    session = StubSession([
        StubResponse(200, {"id": "100", "type": 2}),
        StubResponse(200, [
            {"id": "300", "author": {"id": "20"}},
            {
                "id": "200",
                "author": {"id": "30"},
                "reactions": [
                    {"count": 2, "me": True, "me_burst": False},
                    {"count": 1, "me": False, "me_burst": False},
                ],
            },
            {"id": "150", "author": {"id": "untracked"}},
        ]),
    ])

    observation = live_suite.observe_destructive_contract_scope(
        "private-token",
        scope,
        resource,
        messages,
        {"owner": "10", "subject": "20", "peer_a": "30"},
        client=session,
        pacer=RecordingPacer(),
    )

    assert observation.container_exists is True
    assert observation.archived is None
    assert observation.message_authors == {
        "300": "20",
        "200": "30",
        "150": "untracked",
    }
    assert observation.deletable_message_ids == {"150", "200", "300"}
    assert observation.subject_reactions_on_foreign_messages == 1
    assert observation.foreign_reactions_on_foreign_messages == 2
    assert "private-token" not in repr(observation)
    assert len(session.requests) == 2


def test_validate_account_tokens_paces_every_account_request():
    pacer = RecordingPacer()

    report = validate_account_tokens(
        {"owner": "first.token", "subject": "second.token"},
        session=StubSession(
            [
                StubResponse(200, {"id": "100"}),
                StubResponse(200, {"id": "200"}),
            ]
        ),
        pacer=pacer,
    )

    report.require_ready(2)
    assert pacer.events == [
        ("wait", "GET"),
        ("finished", None),
        ("wait", "GET"),
        ("finished", None),
    ]


def test_validate_account_tokens_reports_unauthorized_without_exposing_token():
    secret = "invalid.private.token"
    report = validate_account_tokens(
        {"subject": secret},
        session=StubSession([StubResponse(401, {"message": secret})]),
        pacer=no_wait_pacer(),
    )

    with pytest.raises(LiveSuiteError) as error:
        report.require_ready(1)

    assert report.checks[0].error == "HTTP 401"
    assert secret not in repr(report)
    assert secret not in str(error.value)
    assert "subject" not in repr(report)
    assert "subject" not in str(error.value)


def test_validate_account_tokens_rejects_duplicate_discord_account():
    report = validate_account_tokens(
        {"owner": "first.token", "subject": "second.token"},
        session=StubSession(
            [
                StubResponse(200, {"id": "100"}),
                StubResponse(200, {"id": "100"}),
            ]
        ),
        pacer=no_wait_pacer(),
    )

    with pytest.raises(LiveSuiteError, match="1 account") as error:
        report.require_ready(2)

    assert report.ledger_identities == {"owner": "100"}
    assert "owner" not in str(error.value)
    assert "subject" not in str(error.value)


def test_accounts_command_output_uses_only_ordinal_handles(
    tmp_path,
    monkeypatch,
    capsys,
):
    secret_key = "TOKEN_OWNER"
    secret_value = "owner.user.token"
    user_ids = [
        "123456789012345671",
        "123456789012345672",
        "123456789012345673",
        "123456789012345674",
    ]
    secret_file = tmp_path / "secrets.env"
    write_private_secret_file(
        secret_file,
        "\n".join(
            (
                f"{secret_key}={secret_value}",
                "TOKEN_SUBJECT=subject.user.token",
                "TOKEN_PEER_A=peer-a.user.token",
                "TOKEN_PEER_B=peer-b.user.token",
                "",
            )
        ),
    )
    report = AccountValidationReport(
        checks=tuple(
            AccountCheck(fixture_role=role, status_code=200, user_id=user_id)
            for role, user_id in zip(
                ("owner", "subject", "peer_a", "peer_b"),
                user_ids,
                strict=True,
            )
        )
    )
    monkeypatch.setattr(live_suite, "validate_account_tokens", lambda tokens: report)

    exit_code = live_suite.main(
        [
            "accounts",
            "--secrets-file",
            str(secret_file),
            "--expected-accounts",
            "4",
        ]
    )
    captured = capsys.readouterr()
    output = captured.out + captured.err

    assert exit_code == 0
    assert "account-1: valid (HTTP 200)" in output
    assert secret_key not in output
    assert secret_value not in output
    assert all(user_id not in output for user_id in user_ids)


def test_new_run_id_is_timestamped_and_unique():
    now = datetime(2026, 7, 13, 10, 11, 12, tzinfo=timezone.utc)

    first = new_run_id(now)
    second = new_run_id(now)

    assert first.startswith("dmd-live-20260713T101112Z-")
    assert first != second


def test_ledger_enforces_run_and_resource_ownership():
    ledger = LiveLedger.new(
        valid_report().ledger_identities,
        run_id="dmd-live-20260713T101112Z-0123abcd",
    )
    resource = LedgerResource(
        run_id=ledger.run_id,
        fixture_key="channel:test",
        kind="channel",
        resource_id="300",
        owner_handle="owner",
    )
    ledger.record_resource(resource)

    assert ledger.require_owned_resource("channel", "300") == resource
    with pytest.raises(LiveSuiteSafetyError, match="unowned") as error:
        ledger.require_owned_resource("channel", "301")
    assert "301" not in str(error.value)
    assert "owner" not in str(error.value)
    assert "100" not in repr(ledger)
    assert "200" not in repr(ledger)
    assert "300" not in repr(resource)
    assert "owner" not in repr(resource)
    with pytest.raises(LiveSuiteSafetyError, match="another run ID"):
        ledger.record_resource(
            LedgerResource(
                run_id="dmd-live-20260713T101112Z-deadbeef",
                fixture_key="channel:other",
                kind="channel",
                resource_id="301",
                owner_handle="owner",
            )
        )


def test_empty_teardown_is_idempotent_and_refuses_active_resources():
    empty_ledger = LiveLedger.new(
        valid_report().ledger_identities,
        run_id="dmd-live-20260713T101112Z-0123abcd",
    )

    assert empty_ledger.mark_empty_teardown_complete() is True
    assert empty_ledger.mark_empty_teardown_complete() is False

    active_ledger = LiveLedger.new(
        valid_report().ledger_identities,
        run_id="dmd-live-20260713T101112Z-deadbeef",
    )
    active_ledger.record_resource(
        LedgerResource(
            run_id=active_ledger.run_id,
            fixture_key="guild:matrix",
            kind="guild",
            resource_id="300",
            owner_handle="owner",
        )
    )

    with pytest.raises(LiveSuiteSafetyError, match="active ledger resources"):
        active_ledger.mark_empty_teardown_complete()


def test_ledger_round_trip_uses_owner_only_file(tmp_path):
    ledger = LiveLedger.new(
        valid_report().ledger_identities,
        run_id="dmd-live-20260713T101112Z-0123abcd",
    )
    ledger_path = tmp_path / "state" / "ledger.json"

    ledger.save(ledger_path)
    loaded = LiveLedger.load(ledger_path)

    assert loaded.to_dict() == ledger.to_dict()
    if os.name != "nt":
        assert ledger_path.stat().st_mode & 0o777 == 0o600


class StubFixtureClient:
    def __init__(self, guilds=()):
        self.guilds = list(guilds)
        self.created_names = []
        self.deleted_ids = []

    def list_current_guilds(self):
        return list(self.guilds)

    def create_guild(self, name):
        self.created_names.append(name)
        guild = GuildSnapshot(
            guild_id=f"created-{len(self.created_names)}",
            name=name,
            owned=True,
        )
        self.guilds.append(guild)
        return guild

    def delete_guild(self, guild_id):
        self.deleted_ids.append(guild_id)
        self.guilds = [guild for guild in self.guilds if guild.guild_id != guild_id]
        return "deleted"


def test_bootstrap_creates_and_immediately_records_two_guilds(tmp_path, capsys):
    ledger = LiveLedger.new(
        valid_report().ledger_identities,
        run_id="dmd-live-20260713T101112Z-0123abcd",
    )
    ledger_path = tmp_path / "state" / "ledger.json"
    ledger.save(ledger_path)
    client = StubFixtureClient()

    live_suite.bootstrap_fixture_guilds(ledger, ledger_path, client)

    saved = LiveLedger.load(ledger_path)
    assert saved.phase == "guilds_created"
    assert [resource.fixture_key for resource in saved.resources] == [
        "guild:matrix",
        "guild:permission",
    ]
    assert client.created_names == [
        f"{ledger.run_id}-matrix",
        f"{ledger.run_id}-permission",
    ]
    output = capsys.readouterr().out
    assert output == "guild-1: created\nguild-2: created\n"
    assert all(resource.resource_id not in output for resource in saved.resources)


def test_bootstrap_recovers_named_guild_after_interrupted_ledger_write(tmp_path):
    ledger = LiveLedger.new(
        valid_report().ledger_identities,
        run_id="dmd-live-20260713T101112Z-0123abcd",
    )
    ledger_path = tmp_path / "ledger.json"
    ledger.save(ledger_path)
    recovered = GuildSnapshot(
        guild_id="recovered-private-id",
        name=f"{ledger.run_id}-matrix",
        owned=True,
    )
    client = StubFixtureClient([recovered])

    live_suite.bootstrap_fixture_guilds(ledger, ledger_path, client)

    assert ledger.resource_for_fixture("guild:matrix").resource_id == recovered.guild_id
    assert client.created_names == [f"{ledger.run_id}-permission"]


def test_bootstrap_is_idempotent_for_recorded_matching_guilds(tmp_path, capsys):
    ledger = LiveLedger.new(
        valid_report().ledger_identities,
        run_id="dmd-live-20260713T101112Z-0123abcd",
    )
    ledger_path = tmp_path / "ledger.json"
    guilds = []
    for fixture_key, purpose in live_suite.GUILD_FIXTURES:
        guild = GuildSnapshot(
            guild_id=f"private-{purpose}-id",
            name=f"{ledger.run_id}-{purpose}",
            owned=True,
        )
        guilds.append(guild)
        ledger.record_resource(
            LedgerResource(
                run_id=ledger.run_id,
                fixture_key=fixture_key,
                kind="guild",
                resource_id=guild.guild_id,
                owner_handle="owner",
                guild_id=guild.guild_id,
            )
        )
    ledger.save(ledger_path)
    client = StubFixtureClient(guilds)

    live_suite.bootstrap_fixture_guilds(ledger, ledger_path, client)

    assert client.created_names == []
    assert len(ledger.resources) == 2
    assert capsys.readouterr().out == (
        "guild-1: already recorded\nguild-2: already recorded\n"
    )


def test_bootstrap_refuses_duplicate_name_for_recorded_guild(tmp_path):
    ledger = LiveLedger.new(
        valid_report().ledger_identities,
        run_id="dmd-live-20260713T101112Z-0123abcd",
    )
    ledger_path = tmp_path / "ledger.json"
    expected_name = f"{ledger.run_id}-matrix"
    guilds = [
        GuildSnapshot(
            guild_id="recorded-private-id",
            name=expected_name,
            owned=True,
        ),
        GuildSnapshot(
            guild_id="duplicate-private-id",
            name=expected_name,
            owned=True,
        ),
    ]
    ledger.record_resource(
        LedgerResource(
            run_id=ledger.run_id,
            fixture_key="guild:matrix",
            kind="guild",
            resource_id=guilds[0].guild_id,
            owner_handle="owner",
            guild_id=guilds[0].guild_id,
        )
    )
    ledger.save(ledger_path)

    with pytest.raises(LiveSuiteSafetyError, match="Multiple Discord guilds") as error:
        live_suite.bootstrap_fixture_guilds(
            ledger,
            ledger_path,
            StubFixtureClient(guilds),
        )

    assert expected_name not in str(error.value)
    assert all(guild.guild_id not in str(error.value) for guild in guilds)


def test_bootstrap_refuses_recorded_guild_with_changed_identity(tmp_path):
    ledger = LiveLedger.new(
        valid_report().ledger_identities,
        run_id="dmd-live-20260713T101112Z-0123abcd",
    )
    ledger_path = tmp_path / "ledger.json"
    ledger.record_resource(
        LedgerResource(
            run_id=ledger.run_id,
            fixture_key="guild:matrix",
            kind="guild",
            resource_id="private-guild-id",
            owner_handle="owner",
            guild_id="private-guild-id",
        )
    )
    ledger.save(ledger_path)
    mismatched = GuildSnapshot(
        guild_id="private-guild-id",
        name="unrelated-guild",
        owned=True,
    )

    with pytest.raises(LiveSuiteSafetyError, match="no longer matches") as error:
        live_suite.bootstrap_fixture_guilds(
            ledger,
            ledger_path,
            StubFixtureClient([mismatched]),
        )

    assert "private-guild-id" not in str(error.value)
    assert "unrelated-guild" not in str(error.value)


def test_teardown_deletes_verified_guilds_in_reverse_order(tmp_path, capsys):
    ledger = LiveLedger.new(
        valid_report().ledger_identities,
        run_id="dmd-live-20260713T101112Z-0123abcd",
    )
    ledger_path = tmp_path / "ledger.json"
    guilds = []
    for fixture_key, purpose in live_suite.GUILD_FIXTURES:
        guild = GuildSnapshot(
            guild_id=f"private-{purpose}-id",
            name=f"{ledger.run_id}-{purpose}",
            owned=True,
        )
        guilds.append(guild)
        ledger.record_resource(
            LedgerResource(
                run_id=ledger.run_id,
                fixture_key=fixture_key,
                kind="guild",
                resource_id=guild.guild_id,
                owner_handle="owner",
                guild_id=guild.guild_id,
            )
        )
    ledger.save(ledger_path)
    client = StubFixtureClient(guilds)

    live_suite.teardown_fixture_guilds(ledger, ledger_path, client)

    assert client.deleted_ids == ["private-permission-id", "private-matrix-id"]
    assert all(resource.state == "deleted" for resource in ledger.resources)
    assert ledger.phase == "teardown_complete"
    output = capsys.readouterr().out
    assert "private-" not in output
    assert output == "guild-1: deleted\nguild-2: deleted\n"


def test_teardown_marks_already_missing_guilds_absent(tmp_path):
    ledger = LiveLedger.new(
        valid_report().ledger_identities,
        run_id="dmd-live-20260713T101112Z-0123abcd",
    )
    ledger_path = tmp_path / "ledger.json"
    ledger.record_resource(
        LedgerResource(
            run_id=ledger.run_id,
            fixture_key="guild:matrix",
            kind="guild",
            resource_id="private-guild-id",
            owner_handle="owner",
            guild_id="private-guild-id",
        )
    )
    ledger.save(ledger_path)
    client = StubFixtureClient()

    live_suite.teardown_fixture_guilds(ledger, ledger_path, client)

    assert ledger.resources[0].state == "absent"
    assert ledger.phase == "teardown_complete"
    assert client.deleted_ids == []


class TopologyWorld:
    def __init__(self):
        self.channels = {"1000": [], "2000": []}
        self.roles = {
            "1000": [GuildRoleSnapshot("1000", "@everyone", 0, False)],
            "2000": [GuildRoleSnapshot("2000", "@everyone", 0, False)],
        }
        self.members = {"1000": {}, "2000": {}}
        self.configurations = {
            guild_id: GuildConfigurationSnapshot(
                frozenset(),
                None,
                None,
                0,
                0,
                0,
            )
            for guild_id in self.channels
        }
        self.invites = {}
        self.calls = []
        self.next_channel_id = 3000
        self.next_role_id = 4000
        self.next_invite_id = 1
        self.uncertain_channel_names = set()
        self.unsupported_channel_names = set()
        self.uncertain_accept_accounts = set()
        self._raised_channels = set()
        self._raised_accept_accounts = set()

    def guild_for_channel(self, channel_id):
        for guild_id, channels in self.channels.items():
            if any(channel.channel_id == channel_id for channel in channels):
                return guild_id
        raise AssertionError("unknown channel")


class StubTopologyClient:
    def __init__(self, world, account_role, user_id):
        self.world = world
        self.account_role = account_role
        self.user_id = user_id

    def list_guild_channels(self, guild_id):
        self.world.calls.append(("list_channels", self.account_role))
        return list(self.world.channels[guild_id])

    def create_channel(
        self,
        guild_id,
        channel_type,
        *,
        name,
        parent_id=None,
        permission_overwrites=(),
    ):
        self.world.calls.append(("create_channel", self.account_role))
        if name in self.world.unsupported_channel_names:
            raise FixtureClientError(
                "create fixture channel failed",
                status_code=400,
                discord_code=50024,
            )
        channel = GuildChannelSnapshot(
            channel_id=str(self.world.next_channel_id),
            name=name,
            channel_type=channel_type,
            parent_id=parent_id,
            permission_overwrites=tuple(permission_overwrites),
        )
        self.world.next_channel_id += 1
        self.world.channels[guild_id].append(channel)
        if (
            name in self.world.uncertain_channel_names
            and name not in self.world._raised_channels
        ):
            self.world._raised_channels.add(name)
            raise FixtureClientError(
                "create fixture channel ended with transport uncertainty"
            )
        return channel

    def list_guild_roles(self, guild_id):
        self.world.calls.append(("list_roles", self.account_role))
        return list(self.world.roles[guild_id])

    def create_role(self, guild_id, *, name, permissions):
        self.world.calls.append(("create_role", self.account_role))
        role = GuildRoleSnapshot(
            role_id=str(self.world.next_role_id),
            name=name,
            permissions=permissions,
            managed=False,
        )
        self.world.next_role_id += 1
        self.world.roles[guild_id].append(role)
        return role

    def get_guild_member(self, guild_id, user_id):
        self.world.calls.append(("get_member", self.account_role))
        return self.world.members[guild_id].get(user_id)

    def add_guild_role(self, guild_id, user_id, role_id):
        self.world.calls.append(("add_role", self.account_role))
        member = self.world.members[guild_id][user_id]
        self.world.members[guild_id][user_id] = GuildMemberSnapshot(
            user_id=user_id,
            role_ids=member.role_ids | {role_id},
            pending=False,
        )

    def create_one_use_invite(self, channel_id):
        return self.create_invite(channel_id, max_age=300, max_uses=1)

    def create_invite(self, channel_id, *, max_age, max_uses):
        self.world.calls.append(("create_invite", self.account_role))
        code = f"private-invite-{self.world.next_invite_id}"
        self.world.next_invite_id += 1
        self.world.invites[code] = (
            self.world.guild_for_channel(channel_id),
            channel_id,
            max_age,
            max_uses,
        )
        return InviteGrant(code)

    def accept_guild_invite(
        self,
        invite,
        *,
        expected_guild_id,
        expected_channel_id,
    ):
        self.world.calls.append(("accept_invite", self.account_role))
        guild_id, channel_id, _max_age, _max_uses = self.world.invites[invite.code]
        assert (guild_id, channel_id) == (
            expected_guild_id,
            expected_channel_id,
        )
        self.world.members[expected_guild_id][self.user_id] = GuildMemberSnapshot(
            user_id=self.user_id,
            role_ids=frozenset(),
            pending=False,
        )
        if (
            self.account_role in self.world.uncertain_accept_accounts
            and self.account_role not in self.world._raised_accept_accounts
        ):
            self.world._raised_accept_accounts.add(self.account_role)
            raise FixtureClientError(
                "accept fixture invite ended with transport uncertainty"
            )

    def get_guild_configuration(self, guild_id):
        self.world.calls.append(("get_configuration", self.account_role))
        return self.world.configurations[guild_id]

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
        self.world.calls.append(("configure_community", self.account_role))
        configuration = GuildConfigurationSnapshot(
            features=frozenset(features) | {"NEWS"},
            rules_channel_id=rules_channel_id,
            public_updates_channel_id=public_updates_channel_id,
            verification_level=verification_level,
            default_message_notifications=default_message_notifications,
            explicit_content_filter=explicit_content_filter,
        )
        self.world.configurations[guild_id] = configuration
        return configuration


def make_topology_fixture(tmp_path):
    identities = {
        "owner": "100",
        "subject": "200",
        "peer_a": "300",
        "peer_b": "400",
    }
    ledger = LiveLedger.new(
        identities,
        run_id="dmd-live-20260713T101112Z-0123abcd",
    )
    for fixture_key, guild_id in (
        ("guild:matrix", "1000"),
        ("guild:permission", "2000"),
    ):
        ledger.record_resource(
            LedgerResource(
                run_id=ledger.run_id,
                fixture_key=fixture_key,
                kind="guild",
                resource_id=guild_id,
                owner_handle="owner",
                guild_id=guild_id,
            )
        )
    ledger.phase = "guilds_created"
    ledger_path = tmp_path / "ledger.json"
    ledger.save(ledger_path)
    world = TopologyWorld()
    clients = {
        account_role: StubTopologyClient(world, account_role, user_id)
        for account_role, user_id in identities.items()
    }
    return ledger, ledger_path, world, clients


def test_topology_bootstrap_creates_complete_resumable_fixture(tmp_path, capsys):
    ledger, ledger_path, world, clients = make_topology_fixture(tmp_path)

    live_suite.bootstrap_fixture_topology(ledger, ledger_path, clients)

    assert ledger.phase == "topology_created"
    assert len(ledger.resources) == 20
    assert (
        len([resource for resource in ledger.resources if resource.kind == "channel"])
        == 14
    )
    assert (
        len([resource for resource in ledger.resources if resource.kind == "role"]) == 4
    )
    assert set(world.members["1000"]) == {"200", "300", "400"}
    assert set(world.members["2000"]) == {"200", "300", "400"}

    matrix_member_role = ledger.resource_for_fixture("role:matrix:member")
    permission_member_role = ledger.resource_for_fixture("role:permission:member")
    manager_role = ledger.resource_for_fixture("role:permission:thread-manager")
    reader_role = ledger.resource_for_fixture("role:permission:restricted-reader")
    assert matrix_member_role is not None
    assert permission_member_role is not None
    assert manager_role is not None
    assert reader_role is not None
    assert all(
        matrix_member_role.resource_id in member.role_ids
        for member in world.members["1000"].values()
    )
    assert all(
        permission_member_role.resource_id in member.role_ids
        for member in world.members["2000"].values()
    )
    assert manager_role.resource_id in world.members["2000"]["400"].role_ids
    assert reader_role.resource_id in world.members["2000"]["300"].role_ids

    restricted_resource = ledger.resource_for_fixture("channel:permission:restricted")
    assert restricted_resource is not None
    restricted = next(
        channel
        for channel in world.channels["2000"]
        if channel.channel_id == restricted_resource.resource_id
    )
    assert restricted.channel_type == ChannelType.GUILD_TEXT
    assert restricted.permission_overwrites == (
        live_suite.PermissionOverwriteSnapshot(
            target_id="2000",
            target_type=0,
            allow=0,
            deny=live_suite.VIEW_CHANNEL_PERMISSION,
        ),
        live_suite.PermissionOverwriteSnapshot(
            target_id=reader_role.resource_id,
            target_type=0,
            allow=(
                live_suite.VIEW_CHANNEL_PERMISSION
                | live_suite.SEND_MESSAGES_PERMISSION
                | live_suite.READ_MESSAGE_HISTORY_PERMISSION
            ),
            deny=0,
        ),
    )
    assert "COMMUNITY" in world.configurations["1000"].features

    output = capsys.readouterr().out
    private_values = {
        *ledger.accounts.values(),
        *(resource.resource_id for resource in ledger.resources),
        *world.invites,
    }
    assert all(value not in output for value in private_values)

    world.calls.clear()
    live_suite.bootstrap_fixture_topology(ledger, ledger_path, clients)

    mutating_operations = {
        "create_channel",
        "create_role",
        "create_invite",
        "accept_invite",
        "add_role",
        "configure_community",
    }
    assert not any(call[0] in mutating_operations for call in world.calls)
    assert len(LiveLedger.load(ledger_path).resources) == 20


def test_topology_recovers_uncertain_channel_create_and_invite_accept(
    tmp_path,
    capsys,
):
    ledger, ledger_path, world, clients = make_topology_fixture(tmp_path)
    world.uncertain_channel_names.add("dmd-live-text")
    world.uncertain_accept_accounts.add("subject")

    live_suite.bootstrap_fixture_topology(ledger, ledger_path, clients)

    assert ledger.phase == "topology_created"
    assert ledger.resource_for_fixture("channel:matrix:text") is not None
    assert "200" in world.members["1000"]
    assert "recovered" in capsys.readouterr().out


def test_topology_records_known_media_channel_capability_without_masking_failure(
    tmp_path,
    capsys,
):
    ledger, ledger_path, world, clients = make_topology_fixture(tmp_path)
    world.unsupported_channel_names.add("dmd-live-media")

    live_suite.bootstrap_fixture_topology(ledger, ledger_path, clients)

    assert ledger.phase == "topology_created"
    assert ledger.capabilities == {"channel:matrix:media": "unsupported:discord-50024"}
    assert "channel-14: unsupported" in capsys.readouterr().out

    world.calls.clear()
    live_suite.bootstrap_fixture_topology(ledger, ledger_path, clients)

    assert not any(call[0] == "create_channel" for call in world.calls)
    assert "unsupported" in capsys.readouterr().out


def test_topology_refuses_duplicate_fixture_channel_without_leaking_identity(
    tmp_path,
):
    ledger, ledger_path, world, clients = make_topology_fixture(tmp_path)
    private_ids = ("9001", "9002")
    for private_id in private_ids:
        world.channels["1000"].append(
            GuildChannelSnapshot(
                private_id,
                "dmd-live-matrix",
                ChannelType.GUILD_CATEGORY,
                None,
            )
        )

    with pytest.raises(
        LiveSuiteSafetyError, match="Multiple Discord channels"
    ) as error:
        live_suite.bootstrap_fixture_topology(ledger, ledger_path, clients)

    rendered = str(error.value)
    assert all(private_id not in rendered for private_id in private_ids)
    assert "dmd-live-matrix" not in rendered


def test_teardown_marks_nested_channel_and_role_resources_with_parent_guild(
    tmp_path,
):
    ledger, ledger_path, _world, _clients = make_topology_fixture(tmp_path)
    ledger.record_resource(
        LedgerResource(
            run_id=ledger.run_id,
            fixture_key="channel:test",
            kind="channel",
            resource_id="3000",
            owner_handle="owner",
            guild_id="1000",
            parent_id=None,
        )
    )
    ledger.record_resource(
        LedgerResource(
            run_id=ledger.run_id,
            fixture_key="role:test",
            kind="role",
            resource_id="4000",
            owner_handle="owner",
            guild_id="1000",
        )
    )
    ledger.save(ledger_path)
    guilds = [
        GuildSnapshot(
            guild_id="1000",
            name=f"{ledger.run_id}-matrix",
            owned=True,
        ),
        GuildSnapshot(
            guild_id="2000",
            name=f"{ledger.run_id}-permission",
            owned=True,
        ),
    ]

    live_suite.teardown_fixture_guilds(
        ledger,
        ledger_path,
        StubFixtureClient(guilds),
    )

    assert all(resource.state == "deleted" for resource in ledger.resources)
    assert ledger.phase == "teardown_complete"


def test_prepare_membership_invites_writes_only_private_short_lived_links(
    tmp_path,
    capsys,
):
    ledger, ledger_path, world, clients = make_topology_fixture(tmp_path)
    for fixture_key, guild_id, purpose in (
        ("guild:matrix", "1000", "matrix"),
        ("guild:permission", "2000", "permission"),
    ):
        category = GuildChannelSnapshot(
            channel_id=str(world.next_channel_id),
            name=f"dmd-live-{purpose}",
            channel_type=ChannelType.GUILD_CATEGORY,
            parent_id=None,
        )
        world.next_channel_id += 1
        lobby = GuildChannelSnapshot(
            channel_id=str(world.next_channel_id),
            name="dmd-live-lobby",
            channel_type=ChannelType.GUILD_TEXT,
            parent_id=category.channel_id,
        )
        world.next_channel_id += 1
        world.channels[guild_id].extend((category, lobby))
        ledger.record_resource(
            LedgerResource(
                run_id=ledger.run_id,
                fixture_key=f"channel:{purpose}:category",
                kind="channel",
                resource_id=category.channel_id,
                owner_handle="owner",
                guild_id=guild_id,
            )
        )
        ledger.record_resource(
            LedgerResource(
                run_id=ledger.run_id,
                fixture_key=f"channel:{purpose}:lobby",
                kind="channel",
                resource_id=lobby.channel_id,
                owner_handle="owner",
                guild_id=guild_id,
                parent_id=category.channel_id,
            )
        )
    ledger.save(ledger_path)
    invites_path = tmp_path / "state" / "membership-invites.json"

    live_suite.prepare_membership_invites(
        ledger,
        invites_path,
        clients["owner"],
        max_age=1800,
    )

    payload = json.loads(invites_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == ledger.run_id
    assert len(payload["invites"]) == 2
    assert [invite["guild_ordinal"] for invite in payload["invites"]] == [1, 2]
    assert all(invite["missing_accounts"] == 3 for invite in payload["invites"])
    assert all(
        invite["url"].startswith("https://discord.gg/") for invite in payload["invites"]
    )
    assert all(invite_data[2:] == (1800, 3) for invite_data in world.invites.values())
    if os.name != "nt":
        assert invites_path.stat().st_mode & 0o777 == 0o600
    output = capsys.readouterr().out
    assert all(code not in output for code in world.invites)
    assert all(resource.resource_id not in output for resource in ledger.resources)


def make_destructive_smoke_ledger(tmp_path):
    ledger = LiveLedger.new(
        {"owner": "100", "subject": "200", "peer_a": "300", "peer_b": "400"},
        run_id="dmd-live-20260713T160246Z-1234abcd",
    )
    ledger.record_resource(LedgerResource(
        run_id=ledger.run_id,
        fixture_key="guild:permission",
        kind="guild",
        resource_id="1000",
        owner_handle="owner",
        guild_id="1000",
    ))
    ledger.record_resource(LedgerResource(
        run_id=ledger.run_id,
        fixture_key="channel:permission:threads",
        kind="channel",
        resource_id="2000",
        owner_handle="owner",
        guild_id="1000",
    ))
    ledger.record_resource(LedgerResource(
        run_id=ledger.run_id,
        fixture_key="message:destructive:subject",
        kind="message",
        resource_id="3000",
        owner_handle="subject",
        guild_id="1000",
        parent_id="2000",
    ))
    ledger.phase = "dry_run_verified"
    ledger.destructive_unlocked = True
    ledger_path = tmp_path / "ledger.json"
    ledger.save(ledger_path)
    return ledger, ledger_path


def test_destructive_smoke_records_deleted_and_relocks(tmp_path, monkeypatch):
    ledger, ledger_path = make_destructive_smoke_ledger(tmp_path)
    outputs = iter((
        "Summary: messages 1 delete / 0 keep",
        "Summary: messages 1 deleted / 0 absent / 0 failed / 0 kept",
        "Summary: messages 0 delete / 0 keep",
    ))
    monkeypatch.setattr(live_suite, "_run_scoped_dmd", lambda *_args, **_kwargs: next(outputs))

    outcome = live_suite.execute_destructive_smoke(
        ledger, ledger_path, "private-token"
    )

    assert outcome == "deleted"
    saved = LiveLedger.load(ledger_path)
    assert saved.resource_for_fixture("message:destructive:subject").state == "deleted"
    assert saved.phase == "destructive_smoke_verified"
    assert saved.destructive_unlocked is False


def test_destructive_smoke_reconciles_absent_without_mutation(tmp_path, monkeypatch):
    ledger, ledger_path = make_destructive_smoke_ledger(tmp_path)
    outputs = iter((
        "Summary: messages 0 delete / 0 keep",
        "Summary: messages 0 delete / 0 keep",
    ))
    calls = []

    def run_scoped(*_args, **kwargs):
        calls.append(kwargs["dry_run"])
        return next(outputs)

    monkeypatch.setattr(live_suite, "_run_scoped_dmd", run_scoped)

    outcome = live_suite.execute_destructive_smoke(
        ledger, ledger_path, "private-token"
    )

    assert outcome == "absent"
    assert calls == [True, True]
    assert LiveLedger.load(ledger_path).resource_for_fixture(
        "message:destructive:subject"
    ).state == "absent"


def test_destructive_smoke_requires_verified_unlock(tmp_path):
    ledger, ledger_path = make_destructive_smoke_ledger(tmp_path)
    ledger.destructive_unlocked = False

    with pytest.raises(LiveSuiteSafetyError, match="immediately preceding"):
        live_suite.execute_destructive_smoke(ledger, ledger_path, "private-token")


class StubVolumeClient:
    def __init__(self, role):
        self.role = role
        self.calls = []
        self.next_id = 1

    def start_thread(self, channel_id, *, name, thread_type):
        self.calls.append(("thread", channel_id, name, thread_type))
        return SimpleNamespace(channel_id=f"thread-{self.role}-{self.next_id}")

    def send_message(self, channel_id, *, content):
        self.calls.append(("message", channel_id, content))
        message_id = f"message-{self.role}-{self.next_id}"
        self.next_id += 1
        return SimpleNamespace(message_id=message_id)

    def add_reaction(self, channel_id, message_id, *, emoji):
        self.calls.append(("reaction", channel_id, message_id, emoji))


def make_volume_ledger(tmp_path):
    ledger = LiveLedger.new(
        {"owner": "100", "subject": "200", "peer_a": "300", "peer_b": "400"},
        run_id="dmd-live-20260713T160246Z-1234abcd",
    )
    for fixture_key, kind, resource_id, guild_id in (
        ("channel:matrix:text", "channel", "1001", "1000"),
        ("channel:matrix:announcement", "channel", "1002", "1000"),
        ("channel:matrix:voice", "channel", "1003", "1000"),
        ("channel:matrix:stage", "channel", "1004", "1000"),
        ("thread:matrix:subject-public", "thread", "1005", "1000"),
        ("dm:subject-peer-a", "dm_channel", "1006", None),
        ("group-dm:subject-peers", "dm_channel", "1007", None),
    ):
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=fixture_key,
            kind=kind,
            resource_id=resource_id,
            owner_handle="subject" if kind != "channel" else "owner",
            guild_id=guild_id,
        ))
    ledger.phase = "destructive_smoke_verified"
    ledger_path = tmp_path / "ledger.json"
    ledger.save(ledger_path)
    return ledger, ledger_path


def test_volume_content_is_stable_and_varied():
    values = {
        live_suite._volume_message_content("run", "scope", index)
        for index in range(1, 20)
    }

    assert len(values) == 19
    assert live_suite._volume_message_content("run", "scope", 3) in values


def test_volume_matrix_is_resumable_across_all_scopes(tmp_path):
    ledger, ledger_path = make_volume_ledger(tmp_path)
    clients = {role: StubVolumeClient(role) for role in live_suite.FIXTURE_ROLES}

    mutations, complete = live_suite.seed_volume_matrix(
        ledger,
        ledger_path,
        clients,
        messages_per_scope=4,
    )

    assert complete is True
    assert mutations == 45
    assert ledger.phase == "volume_seeded"
    assert len([
        resource for resource in ledger.resources
        if resource.fixture_key.startswith("message:volume:")
    ]) == 36
    assert len([
        resource for resource in ledger.resources
        if resource.fixture_key.startswith("reaction:volume:")
    ]) == 9
    assert {
        resource.owner_handle for resource in ledger.resources
        if resource.fixture_key.startswith("message:volume:")
    } == {"subject", "peer_a", "peer_b"}
    assert {
        resource.owner_handle for resource in ledger.resources
        if resource.fixture_key.startswith("message:volume:dm:")
    } == {"subject", "peer_a"}
    assert {
        resource.owner_handle for resource in ledger.resources
        if resource.fixture_key.startswith("message:volume:group-dm:")
    } == {"subject", "peer_a", "peer_b"}

    mutations, complete = live_suite.seed_volume_matrix(
        ledger,
        ledger_path,
        clients,
        messages_per_scope=4,
    )

    assert complete is True
    assert mutations == 0


def test_volume_dry_run_gate_checks_all_scopes_without_unlocking(tmp_path, monkeypatch):
    ledger, ledger_path = make_volume_ledger(tmp_path)
    clients = {role: StubVolumeClient(role) for role in live_suite.FIXTURE_ROLES}
    live_suite.seed_volume_matrix(
        ledger,
        ledger_path,
        clients,
        messages_per_scope=4,
    )
    calls = []

    def run_scoped(
        _token,
        scope_id,
        *,
        dry_run,
    ):
        calls.append((scope_id, dry_run))
        return (
            "Dry run enabled. "
            "Summary: messages 12 delete / 0 keep, reactions 6 delete / 0 keep"
        )

    monkeypatch.setattr(live_suite, "_run_scoped_dmd", run_scoped)

    live_suite.verify_dmd_dry_runs(ledger, ledger_path, "private-token")

    assert len(calls) == 9
    assert all(dry_run for _scope_id, dry_run in calls)
    saved = LiveLedger.load(ledger_path)
    assert saved.phase == "volume_dry_run_verified"
    assert saved.destructive_unlocked is False


def test_volume_dry_run_gate_rejects_seed_undercount(tmp_path, monkeypatch):
    ledger, ledger_path = make_volume_ledger(tmp_path)
    ledger.phase = "volume_seeded"
    ledger.save(ledger_path)
    monkeypatch.setattr(
        live_suite,
        "_run_scoped_dmd",
        lambda *_args, **_kwargs: (
            "Dry run enabled. "
            "Summary: messages 11 delete / 0 keep, reactions 6 delete / 0 keep"
        ),
    )

    with pytest.raises(LiveSuiteSafetyError, match="undercounted"):
        live_suite.verify_dmd_dry_runs(ledger, ledger_path, "private-token")


class StubThreadMatrixWorld:
    def __init__(self):
        self.next_id = 5000
        self.threads = {}

    def new_id(self, prefix):
        self.next_id += 1
        return f"{prefix}-{self.next_id}"


class StubThreadMatrixClient:
    def __init__(self, world, role, *, super_reactions=False):
        self.world = world
        self.role = role
        self.super_reactions = super_reactions
        self.calls = []

    def start_thread(self, channel_id, *, name, thread_type):
        self.calls.append(("thread", channel_id, name, thread_type))
        thread = SimpleNamespace(
            channel_id=self.world.new_id("thread"),
            parent_id=channel_id,
            thread_type=thread_type,
            initial_message_id=None,
        )
        self.world.threads[thread.channel_id] = thread
        return thread

    def start_thread_from_message(self, channel_id, message_id, *, name):
        self.calls.append(("message-thread", channel_id, message_id, name))
        thread = SimpleNamespace(
            channel_id=self.world.new_id("announcement-thread"),
            parent_id=channel_id,
            thread_type=int(ChannelType.ANNOUNCEMENT_THREAD),
            initial_message_id=None,
        )
        self.world.threads[thread.channel_id] = thread
        return thread

    def start_forum_thread(self, channel_id, *, name, content):
        self.calls.append(("forum-thread", channel_id, name, content))
        thread = SimpleNamespace(
            channel_id=self.world.new_id("forum-thread"),
            parent_id=channel_id,
            thread_type=int(ChannelType.PUBLIC_THREAD),
            initial_message_id=self.world.new_id("initial-message"),
        )
        self.world.threads[thread.channel_id] = thread
        return thread

    def add_thread_member(self, channel_id, user_id):
        self.calls.append(("thread-member", channel_id, user_id))

    def set_thread_archived(self, channel_id, *, archived):
        self.calls.append(("archive", channel_id, archived))
        return self.world.threads[channel_id]

    def send_message(self, channel_id, *, content):
        self.calls.append(("message", channel_id, content))
        return SimpleNamespace(message_id=self.world.new_id(f"message-{self.role}"))

    def add_reaction(
        self,
        channel_id,
        message_id,
        *,
        emoji,
        reaction_type=0,
    ):
        self.calls.append((
            "reaction",
            channel_id,
            message_id,
            emoji,
            reaction_type,
        ))
        if reaction_type == 1 and not self.super_reactions:
            raise FixtureClientError("unsupported", status_code=403)


def make_thread_matrix_ledger(tmp_path):
    ledger, ledger_path = make_volume_ledger(tmp_path)
    for fixture_key, resource_id, guild_id in (
        ("channel:permission:threads", "1008", "2000"),
        ("channel:matrix:forum", "1009", "1000"),
    ):
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=fixture_key,
            kind="channel",
            resource_id=resource_id,
            owner_handle="owner",
            guild_id=guild_id,
        ))
    ledger.capabilities["channel:matrix:media"] = "unsupported:discord-50024"
    ledger.phase = "volume_dry_run_verified"
    ledger.save(ledger_path)
    return ledger, ledger_path


def make_thread_matrix_clients(*, super_reactions=False):
    world = StubThreadMatrixWorld()
    clients = {
        role: StubThreadMatrixClient(
            world,
            role,
            super_reactions=super_reactions,
        )
        for role in live_suite.FIXTURE_ROLES
    }
    return world, clients


def make_forum_starter_smoke_ledger(tmp_path):
    ledger, ledger_path = make_thread_matrix_ledger(tmp_path)
    ledger.phase = "thread_matrix_dry_run_verified"
    ledger.save(ledger_path)
    return ledger, ledger_path


def prepare_forum_starter_smoke_fixture(tmp_path, monkeypatch):
    ledger, ledger_path = make_forum_starter_smoke_ledger(tmp_path)
    _world, clients = make_thread_matrix_clients()
    monkeypatch.setattr(
        live_suite,
        "observe_forum_starter_state",
        lambda *_args, **_kwargs: live_suite.ForumStarterObservation(True, True),
    )
    monkeypatch.setattr(
        live_suite,
        "_run_scoped_dmd",
        lambda *_args, **_kwargs: (
            "Dry run enabled. Summary: messages 1 delete / 0 keep, "
            "reactions 0 delete / 0 keep"
        ),
    )

    live_suite.prepare_forum_starter_smoke(
        ledger,
        ledger_path,
        "private-token",
        clients["subject"],
        pacer=RecordingPacer(),
    )
    return ledger, ledger_path


def test_forum_starter_smoke_prepares_one_isolated_preview(tmp_path, monkeypatch):
    ledger, ledger_path = prepare_forum_starter_smoke_fixture(
        tmp_path,
        monkeypatch,
    )

    saved = LiveLedger.load(ledger_path)
    thread = saved.resource_for_fixture(live_suite.FORUM_STARTER_THREAD_KEY)
    message = saved.resource_for_fixture(live_suite.FORUM_STARTER_MESSAGE_KEY)
    assert thread is not None
    assert message is not None
    assert thread.owner_handle == "subject"
    assert message.owner_handle == "subject"
    assert message.parent_id == thread.resource_id
    assert saved.phase == "forum_starter_smoke_previewed"
    assert saved.destructive_unlocked is True
    assert ledger.phase == saved.phase


def test_forum_starter_smoke_deletes_message_but_preserves_container(
    tmp_path,
    monkeypatch,
):
    ledger, ledger_path = prepare_forum_starter_smoke_fixture(
        tmp_path,
        monkeypatch,
    )
    observations = iter((
        live_suite.ForumStarterObservation(True, True),
        live_suite.ForumStarterObservation(True, False),
    ))
    outputs = iter((
        "Summary: messages 1 delete / 0 keep, reactions 0 delete / 0 keep",
        "Summary: messages 1 deleted / 0 absent / 0 failed / 0 kept",
        "Summary: messages 0 delete / 0 keep, reactions 0 delete / 0 keep",
    ))
    monkeypatch.setattr(
        live_suite,
        "observe_forum_starter_state",
        lambda *_args, **_kwargs: next(observations),
    )
    monkeypatch.setattr(
        live_suite,
        "_run_scoped_dmd",
        lambda *_args, **_kwargs: next(outputs),
    )

    outcome = live_suite.execute_forum_starter_smoke(
        ledger,
        ledger_path,
        "private-token",
        pacer=RecordingPacer(),
    )

    assert outcome == ("deleted", "present")
    saved = LiveLedger.load(ledger_path)
    assert saved.resource_for_fixture(
        live_suite.FORUM_STARTER_MESSAGE_KEY
    ).state == "deleted"
    assert saved.resource_for_fixture(
        live_suite.FORUM_STARTER_THREAD_KEY
    ).state == "active"
    assert saved.capabilities[
        live_suite.FORUM_STARTER_CONTAINER_CAPABILITY
    ] == "present"
    assert saved.phase == "forum_starter_smoke_verified"
    assert saved.destructive_unlocked is False


def test_forum_starter_smoke_records_container_cascade(tmp_path, monkeypatch):
    ledger, ledger_path = prepare_forum_starter_smoke_fixture(
        tmp_path,
        monkeypatch,
    )
    observations = iter((
        live_suite.ForumStarterObservation(True, True),
        live_suite.ForumStarterObservation(False, False),
    ))
    outputs = iter((
        "Summary: messages 1 delete / 0 keep, reactions 0 delete / 0 keep",
        "Summary: messages 1 deleted / 0 absent / 0 failed / 0 kept",
    ))
    monkeypatch.setattr(
        live_suite,
        "observe_forum_starter_state",
        lambda *_args, **_kwargs: next(observations),
    )
    monkeypatch.setattr(
        live_suite,
        "_run_scoped_dmd",
        lambda *_args, **_kwargs: next(outputs),
    )

    outcome = live_suite.execute_forum_starter_smoke(
        ledger,
        ledger_path,
        "private-token",
        pacer=RecordingPacer(),
    )

    assert outcome == ("deleted", "absent")
    saved = LiveLedger.load(ledger_path)
    assert saved.resource_for_fixture(
        live_suite.FORUM_STARTER_THREAD_KEY
    ).state == "absent"
    assert saved.capabilities[
        live_suite.FORUM_STARTER_CONTAINER_CAPABILITY
    ] == "absent"


def test_destructive_contract_matrix_previews_executes_and_checkpoints(
    tmp_path,
    monkeypatch,
):
    ledger, ledger_path = make_volume_ledger(tmp_path)
    clients = {role: StubVolumeClient(role) for role in live_suite.FIXTURE_ROLES}
    live_suite.seed_volume_matrix(
        ledger,
        ledger_path,
        clients,
        messages_per_scope=4,
    )
    ledger.phase = "forum_starter_smoke_verified"
    ledger.save(ledger_path)
    scope = live_suite.DestructiveContractScope(
        "guild-text",
        "channel:matrix:text",
        "channel",
        ChannelType.GUILD_TEXT,
    )
    monkeypatch.setattr(live_suite, "DESTRUCTIVE_CONTRACT_SCOPES", (scope,))

    def observation(*_args, **_kwargs):
        messages = _args[3]
        accounts = _args[4]
        return live_suite.DestructiveContractObservation(
            True,
            None,
            {
                message.resource_id: accounts[message.owner_handle]
                for message in messages
            },
            frozenset(message.resource_id for message in messages),
            1,
            1,
        )

    monkeypatch.setattr(
        live_suite,
        "observe_destructive_contract_scope",
        observation,
    )
    monkeypatch.setattr(
        live_suite,
        "_run_scoped_dmd",
        lambda *_args, **_kwargs: (
            "Dry run enabled. Summary: messages 2 delete / 0 keep, "
            "reactions 1 delete / 0 keep"
        ),
    )

    previewed = live_suite.prepare_destructive_contract_matrix(
        ledger,
        ledger_path,
        "private-token",
        clients,
        pacer=RecordingPacer(),
    )

    assert previewed == 1
    saved = LiveLedger.load(ledger_path)
    assert saved.phase == "destructive_contract_previewed"
    assert saved.destructive_unlocked is True
    assert saved.capabilities[
        live_suite._contract_capability_key(scope, "subject-messages")
    ] == "2"

    observations = iter((
        observation(None, None, None, [
            resource
            for resource in ledger.resources
            if resource.kind == "message"
            and resource.parent_id == "1001"
            and resource.fixture_key.startswith("message:volume:guild-text:")
        ], ledger.accounts),
        live_suite.DestructiveContractObservation(
            True,
            None,
            {
                resource.resource_id: ledger.accounts[resource.owner_handle]
                for resource in ledger.resources
                if resource.kind == "message"
                and resource.parent_id == "1001"
                and resource.owner_handle != "subject"
            },
            frozenset(
                resource.resource_id
                for resource in ledger.resources
                if resource.kind == "message"
                and resource.parent_id == "1001"
                and resource.owner_handle != "subject"
            ),
            0,
            1,
        ),
    ))
    monkeypatch.setattr(
        live_suite,
        "observe_destructive_contract_scope",
        lambda *_args, **_kwargs: next(observations),
    )
    outputs = iter((
        "Dry run enabled. Summary: messages 2 delete / 0 keep, "
        "reactions 1 delete / 0 keep",
        "Summary: messages 2 deleted / 0 absent / 0 failed / 0 kept, "
        "reactions 1 deleted / 0 absent / 0 failed / 0 kept",
    ))
    monkeypatch.setattr(
        live_suite,
        "_run_scoped_dmd",
        lambda *_args, **_kwargs: next(outputs),
    )

    executed = live_suite.execute_destructive_contract_matrix(
        saved,
        ledger_path,
        "private-token",
        pacer=RecordingPacer(),
    )

    assert executed == 1
    completed = LiveLedger.load(ledger_path)
    assert completed.phase == "destructive_contract_verified"
    assert completed.destructive_unlocked is False
    assert completed.capabilities[
        live_suite._contract_capability_key(scope, "execution")
    ] == "deleted"
    subject_messages = [
        resource
        for resource in completed.resources
        if resource.kind == "message"
        and resource.parent_id == "1001"
        and resource.owner_handle == "subject"
    ]
    assert subject_messages
    assert all(resource.state == "deleted" for resource in subject_messages)


def test_completed_contract_matrix_stays_locked_when_prepared_again(
    tmp_path,
    monkeypatch,
):
    ledger, ledger_path = make_volume_ledger(tmp_path)
    clients = {role: StubVolumeClient(role) for role in live_suite.FIXTURE_ROLES}
    live_suite.seed_volume_matrix(
        ledger,
        ledger_path,
        clients,
        messages_per_scope=4,
    )
    scope = live_suite.DestructiveContractScope(
        "guild-text",
        "channel:matrix:text",
        "channel",
        ChannelType.GUILD_TEXT,
    )
    monkeypatch.setattr(live_suite, "DESTRUCTIVE_CONTRACT_SCOPES", (scope,))
    ledger.capabilities[
        live_suite._contract_capability_key(scope, "execution")
    ] = "deleted"
    ledger.phase = "destructive_contract_verified"
    ledger.destructive_unlocked = False
    ledger.save(ledger_path)

    monkeypatch.setattr(
        live_suite,
        "observe_destructive_contract_scope",
        lambda *_args, **_kwargs: pytest.fail("completed scope was re-observed"),
    )

    previewed = live_suite.prepare_destructive_contract_matrix(
        ledger,
        ledger_path,
        "private-token",
        clients,
        pacer=RecordingPacer(),
    )

    assert previewed == 0
    saved = LiveLedger.load(ledger_path)
    assert saved.phase == "destructive_contract_verified"
    assert saved.destructive_unlocked is False


def test_archived_contract_removes_subject_content_and_restores_container():
    ledger = LiveLedger.new(
        {"owner": "10", "subject": "20", "peer_a": "30"},
        run_id="dmd-live-20260713T160246Z-1234abcd",
    )
    scope = live_suite.DestructiveContractScope(
        "private-archived",
        "thread:private-archived",
        "thread",
        ChannelType.PRIVATE_THREAD,
        archived=True,
    )
    messages = (
        LedgerResource(
            run_id=ledger.run_id,
            fixture_key="message:subject",
            kind="message",
            resource_id="200",
            owner_handle="subject",
            parent_id="100",
        ),
        LedgerResource(
            run_id=ledger.run_id,
            fixture_key="message:foreign",
            kind="message",
            resource_id="300",
            owner_handle="peer_a",
            parent_id="100",
        ),
    )
    observation = live_suite.DestructiveContractObservation(
        True,
        True,
        {"300": "30"},
        frozenset({"300"}),
        0,
        2,
    )

    live_suite._assert_contract_postcondition(
        ledger,
        scope,
        messages,
        observation,
        baseline_subject_reactions=1,
        baseline_foreign_reactions=2,
    )

def test_archived_contract_rejects_complete_discord_message_rejection():
    output = (
        "Summary: messages 0 deleted / 0 absent / 6 failed / 0 kept, "
        "reactions 0 deleted / 0 absent / 0 failed / 0 kept"
    )

    with pytest.raises(LiveSuiteSafetyError, match="unexpected outcome"):
        live_suite._require_contract_execution(
            output,
            expected_messages=6,
            expected_reactions=0,
        )


def test_thread_matrix_seeds_all_core_forms_and_is_resumable(tmp_path):
    ledger, ledger_path = make_thread_matrix_ledger(tmp_path)
    _world, clients = make_thread_matrix_clients()

    mutations, complete = live_suite.seed_thread_matrix(
        ledger,
        ledger_path,
        clients,
        messages_per_thread=4,
    )

    assert complete is True
    assert mutations == 92
    assert ledger.phase == "thread_matrix_seeded"
    threads = [
        resource
        for resource in ledger.resources
        if resource.fixture_key.startswith("thread:matrix:")
        and resource.fixture_key not in {
            "thread:matrix:subject-public",
            "thread:matrix:volume-2",
            "thread:matrix:volume-3",
        }
    ]
    assert len(threads) == 8
    normal_reactions = [
        resource
        for resource in ledger.resources
        if resource.fixture_key.startswith("reaction:thread-matrix:")
        and resource.fixture_key.endswith(":normal")
    ]
    assert len(normal_reactions) == 40
    assert len({resource.resource_id for resource in normal_reactions}) == 40
    assert {
        resource.owner_handle
        for resource in ledger.resources
        if resource.fixture_key.startswith("message:thread-matrix:")
        and resource.fixture_key[-3:].isdigit()
    } == {"subject", "peer_a", "peer_b"}
    assert sum(
        call[0] == "thread-member"
        for call in clients["subject"].calls
    ) == 4
    assert sum(
        call[0] == "archive"
        for call in clients["subject"].calls
    ) == 4
    assert ledger.capabilities["super-reaction:subject"].startswith(
        "unsupported:"
    )
    assert ledger.capabilities["super-reaction:peer_a"].startswith(
        "unsupported:"
    )

    mutations, complete = live_suite.seed_thread_matrix(
        ledger,
        ledger_path,
        clients,
        messages_per_thread=4,
    )

    assert (mutations, complete) == (0, True)


def test_thread_matrix_pauses_at_mutation_boundary_and_resumes(tmp_path):
    ledger, ledger_path = make_thread_matrix_ledger(tmp_path)
    _world, clients = make_thread_matrix_clients()

    mutations, complete = live_suite.seed_thread_matrix(
        ledger,
        ledger_path,
        clients,
        messages_per_thread=4,
        max_new_mutations=1,
        try_super_reactions=False,
    )

    assert (mutations, complete) == (1, False)
    assert LiveLedger.load(ledger_path).phase == "thread_matrix_seeding"

    mutations, complete = live_suite.seed_thread_matrix(
        ledger,
        ledger_path,
        clients,
        messages_per_thread=4,
        try_super_reactions=False,
    )

    assert complete is True
    assert mutations == 89
    assert LiveLedger.load(ledger_path).phase == "thread_matrix_seeded"


def test_thread_matrix_dry_run_checks_active_and_archived_semantics(
    tmp_path,
    monkeypatch,
):
    ledger, ledger_path = make_volume_ledger(tmp_path)
    _world, clients = make_thread_matrix_clients()
    live_suite.seed_volume_matrix(
        ledger,
        ledger_path,
        clients,
        messages_per_scope=4,
    )
    for fixture_key, resource_id, guild_id in (
        ("channel:permission:threads", "1008", "2000"),
        ("channel:matrix:forum", "1009", "1000"),
    ):
        ledger.record_resource(LedgerResource(
            run_id=ledger.run_id,
            fixture_key=fixture_key,
            kind="channel",
            resource_id=resource_id,
            owner_handle="owner",
            guild_id=guild_id,
        ))
    ledger.capabilities["channel:matrix:media"] = "unsupported:discord-50024"
    ledger.phase = "volume_dry_run_verified"
    ledger.save(ledger_path)
    live_suite.seed_thread_matrix(
        ledger,
        ledger_path,
        clients,
        messages_per_thread=4,
        try_super_reactions=False,
    )
    thread_ids = {
        ledger.resource_for_fixture(fixture.fixture_key).resource_id
        for fixture in live_suite.THREAD_MATRIX_FIXTURES
        if not fixture.optional_parent
    }
    calls = []

    def run_scoped(
        _token,
        scope_id,
        *,
        dry_run,
    ):
        calls.append((scope_id, dry_run))
        if scope_id in thread_ids:
            return (
                "Dry run enabled. "
                "Summary: messages 20 delete / 0 keep, reactions 1 "
                "delete / 0 keep"
            )
        return (
            "Dry run enabled. "
            "Summary: messages 12 delete / 0 keep, reactions 6 delete / 0 keep"
        )

    monkeypatch.setattr(live_suite, "_run_scoped_dmd", run_scoped)

    live_suite.verify_dmd_dry_runs(ledger, ledger_path, "private-token")

    assert len(calls) == 17
    assert all(dry_run for _scope_id, dry_run in calls)
    saved = LiveLedger.load(ledger_path)
    assert saved.phase == "thread_matrix_dry_run_verified"
    assert saved.destructive_unlocked is False


class StubRaceWorld:
    def __init__(self):
        self.next_id = 9000
        self.threads = {}

    def new_id(self, prefix):
        self.next_id += 1
        return f"{prefix}-{self.next_id}"


class StubRaceClient:
    def __init__(self, world, role):
        self.world = world
        self.role = role
        self.calls = []

    def start_thread(
        self,
        channel_id,
        *,
        name,
        thread_type,
        auto_archive_duration,
    ):
        thread_id = self.world.new_id("thread")
        self.calls.append((
            "thread",
            channel_id,
            name,
            thread_type,
            auto_archive_duration,
        ))
        self.world.threads[thread_id] = {
            "archived": False,
            "locked": False,
        }
        return SimpleNamespace(
            channel_id=thread_id,
            parent_id=channel_id,
            thread_type=thread_type,
        )

    def send_message(self, channel_id, *, content):
        self.calls.append(("message", channel_id, content))
        return SimpleNamespace(message_id=self.world.new_id("message"))

    def add_reaction(self, channel_id, message_id, *, emoji):
        self.calls.append(("reaction", channel_id, message_id, emoji))

    def set_thread_state(self, channel_id, *, archived=None, locked=None):
        self.calls.append(("state", channel_id, archived, locked))
        state = self.world.threads[channel_id]
        if archived is not None:
            state["archived"] = archived
        if locked is not None:
            state["locked"] = locked
        return SimpleNamespace(
            archived=state["archived"],
            locked=state["locked"],
        )


def prepare_archived_thread_race_fixture(tmp_path, monkeypatch):
    ledger, ledger_path = make_thread_matrix_ledger(tmp_path)
    ledger.phase = "destructive_contract_verified"
    ledger.save(ledger_path)
    world = StubRaceWorld()
    clients = {
        role: StubRaceClient(world, role)
        for role in live_suite.FIXTURE_ROLES
    }
    executed = set()

    def observation(
        _token,
        scope,
        _resource,
        messages,
        accounts,
        *,
        target_role="subject",
        **_kwargs,
    ):
        scenario = next(
            candidate
            for candidate in live_suite.ARCHIVED_THREAD_RACE_SCENARIOS
            if candidate.scenario_key == scope.scope_key
        )
        cleaned = scenario.scenario_key in executed and scenario.expect_cleanup
        authors = {
            message.resource_id: accounts[message.owner_handle]
            for message in messages
            if not (
                cleaned
                and message.owner_handle == scenario.target_role
            )
        }
        return live_suite.DestructiveContractObservation(
            True,
            True,
            authors,
            frozenset(authors),
            0 if cleaned else 1,
            1,
            (
                True
                if (
                    scenario.initial_locked
                    or (
                        scenario.scenario_key in executed
                        and scenario.trigger == "lock-changed"
                    )
                )
                else False
            ),
            60,
        )

    monkeypatch.setattr(
        live_suite,
        "observe_destructive_contract_scope",
        observation,
    )
    monkeypatch.setattr(
        live_suite,
        "_run_scoped_dmd",
        lambda *_args, **_kwargs: (
            "Dry run enabled. Summary: messages 1 delete / 1 keep, "
            "reactions 1 delete / 1 keep"
        ),
    )
    tokens = {
        role: f"private-{role}-token"
        for role in live_suite.FIXTURE_ROLES
    }
    mutations, previewed = live_suite.prepare_archived_thread_race_matrix(
        ledger,
        ledger_path,
        tokens,
        clients,
        pacer=RecordingPacer(),
        journal_path=tmp_path / "journal.json",
    )
    return (
        ledger,
        ledger_path,
        tokens,
        clients,
        executed,
        mutations,
        previewed,
    )


def test_archived_thread_race_matrix_previews_and_executes_all_cases(
    tmp_path,
    monkeypatch,
):
    (
        _ledger,
        ledger_path,
        tokens,
        clients,
        executed,
        mutations,
        previewed,
    ) = prepare_archived_thread_race_fixture(tmp_path, monkeypatch)

    assert mutations == len(live_suite.ARCHIVED_THREAD_RACE_SCENARIOS) * 6
    assert previewed == len(live_suite.ARCHIVED_THREAD_RACE_SCENARIOS)
    preview_ledger = LiveLedger.load(ledger_path)
    assert preview_ledger.phase == "archived_thread_race_previewed"
    assert preview_ledger.destructive_unlocked is True

    def cleanup_runner(
        _token,
        _user_id,
        scenario,
        _thread,
        _manager,
        _journal_path,
        **_kwargs,
    ):
        executed.add(scenario.scenario_key)
        return (
            1 if scenario.expect_cleanup else 0,
            scenario.expected_hook_count,
        )

    verified = live_suite.execute_archived_thread_race_matrix(
        preview_ledger,
        ledger_path,
        tokens,
        clients["peer_b"],
        pacer=RecordingPacer(),
        journal_path=tmp_path / "journal.json",
        fetch_interval=(0, 0),
        delete_interval=(0, 0),
        cleanup_runner=cleanup_runner,
    )

    assert verified == len(live_suite.ARCHIVED_THREAD_RACE_SCENARIOS)
    completed = LiveLedger.load(ledger_path)
    assert completed.phase == "archived_thread_race_verified"
    assert completed.destructive_unlocked is False
    for scenario in live_suite.ARCHIVED_THREAD_RACE_SCENARIOS:
        expected = "cleaned" if scenario.expect_cleanup else "interrupted"
        assert completed.capabilities[
            live_suite._race_capability_key(scenario, "execution")
        ] == expected
        target_message = completed.resource_for_fixture(
            live_suite._race_fixture_key(scenario, "message:target")
        )
        target_reaction = completed.resource_for_fixture(
            live_suite._race_fixture_key(scenario, "reaction:target")
        )
        expected_state = "deleted" if scenario.expect_cleanup else "active"
        assert target_message.state == expected_state
        assert target_reaction.state == expected_state


def test_archived_thread_race_failure_relocks_resume_gate(
    tmp_path,
    monkeypatch,
):
    (
        _ledger,
        ledger_path,
        tokens,
        clients,
        _executed,
        _mutations,
        _previewed,
    ) = prepare_archived_thread_race_fixture(tmp_path, monkeypatch)
    preview_ledger = LiveLedger.load(ledger_path)

    def fail_cleanup(*_args, **_kwargs):
        raise LiveSuiteSafetyError("controlled failure")

    with pytest.raises(LiveSuiteSafetyError, match="controlled failure"):
        live_suite.execute_archived_thread_race_matrix(
            preview_ledger,
            ledger_path,
            tokens,
            clients["peer_b"],
            pacer=RecordingPacer(),
            journal_path=tmp_path / "journal.json",
            fetch_interval=(0, 0),
            delete_interval=(0, 0),
            cleanup_runner=fail_cleanup,
        )

    interrupted = LiveLedger.load(ledger_path)
    assert interrupted.phase == "archived_thread_race_interrupted"
    assert interrupted.destructive_unlocked is False


class StubRaceMutationAPI:
    def __init__(self):
        self.calls = []

    def delete_message(self, channel_id, message_id):
        self.calls.append(("message", channel_id, message_id))
        return "message-outcome"

    def delete_own_reaction(
        self,
        channel_id,
        message_id,
        emoji,
        reaction_type,
    ):
        self.calls.append((
            "reaction",
            channel_id,
            message_id,
            emoji,
            reaction_type,
        ))
        return "reaction-outcome"


class StubRaceManager:
    def __init__(self):
        self.calls = []

    def set_thread_state(self, channel_id, *, archived=None, locked=None):
        self.calls.append((channel_id, archived, locked))


def test_archived_thread_race_hook_applies_real_state_changes_at_boundaries():
    api = StubRaceMutationAPI()
    manager = StubRaceManager()
    clock = live_suite._RaceClock()
    likely = next(
        scenario
        for scenario in live_suite.ARCHIVED_THREAD_RACE_SCENARIOS
        if scenario.trigger == "likely-auto-archive"
    )
    wrapped = live_suite._ArchivedThreadRaceAPI(
        api,
        thread_id="thread",
        scenario=likely,
        manager=manager,
        clock=clock,
        auto_archive_duration_seconds=3600,
    )

    assert wrapped.delete_message("thread", "message") == "message-outcome"
    assert (
        wrapped.delete_own_reaction(
            "thread",
            "message",
            {"name": "wave"},
            0,
        )
        == "reaction-outcome"
    )
    assert wrapped.hook_count == 1
    assert manager.calls == [("thread", True, None)]
    assert clock.value == 3600

    manager.calls.clear()
    clock.value = 0
    second = next(
        scenario
        for scenario in live_suite.ARCHIVED_THREAD_RACE_SCENARIOS
        if scenario.trigger == "second-archive"
    )
    wrapped = live_suite._ArchivedThreadRaceAPI(
        api,
        thread_id="thread",
        scenario=second,
        manager=manager,
        clock=clock,
        auto_archive_duration_seconds=3600,
    )
    wrapped.delete_message("thread", "first")
    wrapped.delete_message("thread", "retry")
    wrapped.delete_message("thread", "later")

    assert wrapped.hook_count == 2
    assert manager.calls == [
        ("thread", True, None),
        ("thread", True, None),
    ]


def test_archived_thread_race_parser_defaults_to_preview():
    args = live_suite.build_parser().parse_args([
        "archived-thread-race-matrix",
        "--confirm-run-id",
        "dmd-live-20260713T160246Z-1234abcd",
    ])

    assert args.execute is False
    assert args.delay_min == 3.0
    assert args.delay_max == 6.0
    assert args.journal == live_suite.DEFAULT_ARCHIVED_THREAD_RACE_JOURNAL_PATH
    assert args.handler is live_suite._run_archived_thread_race_matrix


def test_archived_thread_race_preview_waits_for_discord_index(monkeypatch):
    outputs = iter((
        "Dry run enabled. Summary: messages 0 delete / 0 keep, "
        "reactions 0 delete / 0 keep",
        "Dry run enabled. Summary: messages 1 delete / 1 keep, "
        "reactions 1 delete / 1 keep",
    ))
    waits = []
    monkeypatch.setattr(
        live_suite,
        "_run_scoped_dmd",
        lambda *_args, **_kwargs: next(outputs),
    )

    live_suite._run_archived_thread_race_preview(
        "private-token",
        "thread-id",
        sleep=waits.append,
    )

    assert waits == [10.0]
