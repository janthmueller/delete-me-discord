# delete-me-discord main orchestration tests
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import delete_me_discord
from delete_me_discord.privacy import RedactionConfig
from delete_me_discord.privacy import set_redaction_config


def _base_clean_args(tmp_path, **overrides):
    defaults = dict(
        command="clean",
        profile=None,
        _clean_args_finalized=True,
        _explicit_fields=set(),
        include_ids=[],
        exclude_ids=[],
        token="test-token",
        config_path=str(tmp_path / "config.json"),
        keep_within=None,
        keep_last=0,
        keep_last_scope="all",
        dry_run=False,
        max_retries=1,
        retry_time_buffer=["1", "1"],
        fetch_sleep_time=["0", "0"],
        delete_sleep_time=["0", "0"],
        fetch_within=None,
        max_messages=None,
        buffer_per_channel=False,
        keep_reactions=False,
        preserve_cache=False,
        preserve_cache_path=str(tmp_path / "cache.json"),
        quiet=False,
        verbose=0,
        json=False,
        redact_sensitive=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _base_list_args(tmp_path, **overrides):
    defaults = dict(
        command="list",
        list_command="guilds",
        token="test-token",
        config_path=str(tmp_path / "config.json"),
        max_retries=1,
        retry_time_buffer=["1", "1"],
        quiet=False,
        verbose=0,
        json=False,
        redact_sensitive=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _base_cache_args(tmp_path, **overrides):
    defaults = dict(
        command="cache",
        cache_command="clear",
        preserve_cache_path=str(tmp_path / "cache.json"),
        quiet=False,
        verbose=0,
        json=False,
        redact_sensitive=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _base_profile_args(tmp_path, **overrides):
    defaults = dict(
        command="profile",
        profile_command="show",
        name="nightly-dms",
        profile_set=[],
        profile_unset=[],
        config_path=str(tmp_path / "config.json"),
        quiet=False,
        verbose=0,
        json=False,
        redact_sensitive=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_main_cache_clear_exits_early(tmp_path, monkeypatch):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text("{}", encoding="utf-8")
    args = _base_cache_args(tmp_path, preserve_cache_path=str(cache_path))

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    class BoomAPI:
        def __init__(self, *args, **kwargs):
            raise AssertionError("DiscordAPI should not be created for cache clear.")

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", BoomAPI)
    delete_me_discord.main()
    assert not cache_path.exists()


def test_main_list_guilds_runs_discovery(tmp_path, monkeypatch):
    args = _base_list_args(tmp_path, list_command="guilds")

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)
    monkeypatch.setattr(delete_me_discord, "parse_random_range", lambda *_, **__: (0, 0))

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            pass

    called = {"discovery": False}

    def fake_discovery(**kwargs):
        called["discovery"] = True
        assert kwargs["list_guilds"] is True
        assert kwargs["list_channels"] is False

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", FakeAPI)
    monkeypatch.setattr(delete_me_discord, "run_discovery_commands", fake_discovery)
    delete_me_discord.main()
    assert called["discovery"] is True


def test_main_list_profiles_outputs_names(tmp_path, monkeypatch, capsys):
    args = _base_list_args(tmp_path, list_command="profiles")
    config_path = Path(args.config_path)
    config_path.write_text('{"profiles":{"nightly-dms":{},"manual-review":{}}}', encoding="utf-8")

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    delete_me_discord.main()
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["manual-review", "nightly-dms"]


def test_main_profile_show_outputs_raw_profile(tmp_path, monkeypatch, capsys):
    args = _base_profile_args(tmp_path, profile_command="show")
    config_path = Path(args.config_path)
    config_path.write_text('{"profiles":{"nightly-dms":{"verbose":9}}}', encoding="utf-8")

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    delete_me_discord.main()
    out = capsys.readouterr().out.strip()
    assert '"verbose": 9' in out


def test_main_profile_fields_outputs_specs(tmp_path, monkeypatch, capsys):
    args = _base_profile_args(tmp_path, profile_command="fields")

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    delete_me_discord.main()
    out = capsys.readouterr().out
    assert "keep_last: non-negative integer" in out
    assert "fetch_within: time delta string or none" in out
    assert "include_ids: string list" in out


def test_main_profile_add_updates_config(tmp_path, monkeypatch):
    args = _base_profile_args(
        tmp_path,
        profile_command="add",
        profile_set=["keep_last=20", "dry_run=true"],
    )

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    delete_me_discord.main()
    data = json.loads(Path(args.config_path).read_text(encoding="utf-8"))
    assert data["profiles"]["nightly-dms"]["keep_last"] == 20
    assert data["profiles"]["nightly-dms"]["dry_run"] is True


def test_main_profile_add_requires_set(tmp_path, monkeypatch, capsys):
    args = _base_profile_args(tmp_path, profile_command="add", profile_set=[])

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    with pytest.raises(SystemExit) as exc:
        delete_me_discord.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "requires at least one --set" in err


def test_main_profile_update_set_none_unsets_field(tmp_path, monkeypatch):
    args = _base_profile_args(
        tmp_path,
        profile_command="update",
        profile_set=["max_messages=none"],
    )
    Path(args.config_path).write_text(
        '{"profiles":{"nightly-dms":{"keep_last":5,"max_messages":100}}}',
        encoding="utf-8",
    )

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    delete_me_discord.main()
    data = json.loads(Path(args.config_path).read_text(encoding="utf-8"))
    assert data["profiles"]["nightly-dms"] == {"keep_last": 5}


def test_main_profile_update_accepts_redaction_comma_form(tmp_path, monkeypatch):
    args = _base_profile_args(
        tmp_path,
        profile_command="update",
        profile_set=["redact_sensitive=0,1"],
    )
    Path(args.config_path).write_text(
        '{"profiles":{"nightly-dms":{"keep_last":5}}}',
        encoding="utf-8",
    )

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    delete_me_discord.main()
    data = json.loads(Path(args.config_path).read_text(encoding="utf-8"))
    assert data["profiles"]["nightly-dms"]["redact_sensitive"] == [0, 1]


def test_main_profile_update_rejects_unsetting_field_not_present(tmp_path, monkeypatch, capsys):
    args = _base_profile_args(
        tmp_path,
        profile_command="update",
        profile_unset=["max_messages"],
    )
    Path(args.config_path).write_text(
        '{"profiles":{"nightly-dms":{"keep_last":5}}}',
        encoding="utf-8",
    )

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    with pytest.raises(SystemExit) as exc:
        delete_me_discord.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "does not currently define field" in err


def test_main_profile_update_rejects_set_and_unset_overlap(tmp_path, monkeypatch, capsys):
    args = _base_profile_args(
        tmp_path,
        profile_command="update",
        profile_set=["keep_last=20"],
        profile_unset=["keep_last"],
    )

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    with pytest.raises(SystemExit) as exc:
        delete_me_discord.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "both set and unset" in err


def test_main_profile_update_rejects_none_set_and_explicit_unset_overlap(tmp_path, monkeypatch, capsys):
    args = _base_profile_args(
        tmp_path,
        profile_command="update",
        profile_set=["max_messages=none"],
        profile_unset=["max_messages"],
    )

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    with pytest.raises(SystemExit) as exc:
        delete_me_discord.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "both set and unset" in err


def test_main_creates_cache_and_runs_cleaner(tmp_path, monkeypatch):
    args = _base_clean_args(
        tmp_path,
        preserve_cache=True,
        dry_run=True,
        preserve_cache_path=str(tmp_path / "cache.json"),
        _clean_args_finalized=False,
        _explicit_fields={"preserve_cache", "dry_run", "preserve_cache_path"},
    )

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)
    monkeypatch.setattr(delete_me_discord, "parse_random_range", lambda *_, **__: (0, 0))

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            pass

        def get_current_user(self):
            return {"id": "me", "username": "me"}

    cache_info = {}

    class FakeCache:
        def __init__(self, path):
            cache_info["path"] = path
            cache_info["saved"] = 0

        def save(self):
            cache_info["saved"] += 1

    cleaner_info = {}

    class FakeCleaner:
        def __init__(self, **kwargs):
            cleaner_info.update(kwargs)

        def clean_messages(self, **kwargs):
            return 0

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", FakeAPI)
    monkeypatch.setattr(delete_me_discord, "PreserveCache", FakeCache)
    monkeypatch.setattr(delete_me_discord, "MessageCleaner", FakeCleaner)

    delete_me_discord.main()
    assert cache_info["path"].endswith(".dryrun.json")
    assert cache_info["saved"] == 1
    assert cleaner_info["preserve_cache"] is not None


def test_main_profile_overrides_defaults(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"keep_within":"2w","preserve_cache":true,"dry_run":true,"verbose":2}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(delete_me_discord, "resolve_token", lambda *_: ("test-token", "argument"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dmd",
            "clean",
            "--config-path",
            str(config_path),
            "--profile",
            "nightly-dms",
        ],
    )

    captured_logging = {}

    def fake_setup_logging(**kwargs):
        captured_logging.update(kwargs)

    monkeypatch.setattr(delete_me_discord, "setup_logging", fake_setup_logging)

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            pass

        def get_current_user(self):
            return {"id": "me", "username": "me"}

    cleaner_kwargs = {}
    run_kwargs = {}

    class FakeCleaner:
        def __init__(self, **kwargs):
            cleaner_kwargs.update(kwargs)

        def clean_messages(self, **kwargs):
            run_kwargs.update(kwargs)
            return 0

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", FakeAPI)
    monkeypatch.setattr(delete_me_discord, "PreserveCache", lambda path: type("Cache", (), {"path": path, "save": lambda self: None})())
    monkeypatch.setattr(delete_me_discord, "MessageCleaner", FakeCleaner)

    delete_me_discord.main()

    assert cleaner_kwargs["preserve_last"].days == 14
    assert run_kwargs["dry_run"] is True
    assert captured_logging["verbosity"] == 2


def test_main_cli_explicit_value_overrides_profile(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"profiles":{"nightly-dms":{"keep_last":50}}}', encoding="utf-8")
    args = _base_clean_args(
        tmp_path,
        config_path=str(config_path),
        keep_last=3,
        profile="nightly-dms",
        _clean_args_finalized=False,
    )
    args._explicit_fields = {"keep_last"}

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "resolve_token", lambda *_: ("test-token", "argument"))
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            pass

        def get_current_user(self):
            return {"id": "me", "username": "me"}

    cleaner_kwargs = {}

    class FakeCleaner:
        def __init__(self, **kwargs):
            cleaner_kwargs.update(kwargs)

        def clean_messages(self, **kwargs):
            return 0

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", FakeAPI)
    monkeypatch.setattr(delete_me_discord, "MessageCleaner", FakeCleaner)

    delete_me_discord.main()
    assert cleaner_kwargs["preserve_n"] == 3


def test_main_passes_buffer_per_channel(tmp_path, monkeypatch):
    args = _base_clean_args(tmp_path, buffer_per_channel=True)
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)
    monkeypatch.setattr(delete_me_discord, "parse_random_range", lambda *_, **__: (0, 0))

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            pass

        def get_current_user(self):
            return {"id": "me", "username": "me"}

    cleaner_kwargs = {}

    class FakeCleaner:
        def __init__(self, **kwargs):
            cleaner_kwargs["init"] = kwargs

        def clean_messages(self, **kwargs):
            cleaner_kwargs["run"] = kwargs
            return 0

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", FakeAPI)
    monkeypatch.setattr(delete_me_discord, "MessageCleaner", FakeCleaner)

    delete_me_discord.main()
    assert cleaner_kwargs["run"]["buffer_channel_messages"] is True


def test_main_exits_on_negative_keep_last(tmp_path, monkeypatch):
    args = _base_clean_args(tmp_path, keep_last=-1)
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)

    log_calls = {"error": 0}

    def fake_error(*args, **kwargs):
        log_calls["error"] += 1

    monkeypatch.setattr(delete_me_discord.logging, "error", fake_error)

    class BoomAPI:
        def __init__(self, *args, **kwargs):
            raise AssertionError("DiscordAPI should not be created for invalid keep_last.")

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", BoomAPI)
    with pytest.raises(SystemExit) as exc:
        delete_me_discord.main()
    assert exc.value.code == 1
    assert log_calls["error"] == 1


def test_main_authentication_failure_exits(tmp_path, monkeypatch):
    args = _base_clean_args(tmp_path)
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)
    monkeypatch.setattr(delete_me_discord, "parse_random_range", lambda *_, **__: (0, 0))

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            pass

        def get_current_user(self):
            raise delete_me_discord.AuthenticationError("bad token")

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", FakeAPI)
    with pytest.raises(SystemExit) as exc:
        delete_me_discord.main()
    assert exc.value.code == 1


def test_main_configures_redaction_settings(tmp_path, monkeypatch):
    args = _base_clean_args(tmp_path, redact_sensitive=RedactionConfig(enabled=True, prefix=0, suffix=4))
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)

    captured = {}

    def fake_setup_logging(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(delete_me_discord, "setup_logging", fake_setup_logging)
    monkeypatch.setattr(delete_me_discord, "_run_clean", lambda *_: None)

    delete_me_discord.main()

    assert captured["redaction_config"].enabled is True
    assert captured["redaction_config"].prefix == 0
    assert captured["redaction_config"].suffix == 4
    assert captured["verbosity"] == 0
    assert captured["quiet"] is False


def test_main_routes_auth_commands(tmp_path, monkeypatch):
    args = _base_clean_args(tmp_path, command="login")
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)

    called = {"auth": False}

    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)
    monkeypatch.setattr(delete_me_discord, "run_auth_command", lambda passed_args: called.__setitem__("auth", passed_args is args))
    monkeypatch.setattr(delete_me_discord, "_run_clean", lambda *_: (_ for _ in ()).throw(AssertionError("clean run should not execute for auth commands")))

    delete_me_discord.main()
    assert called["auth"] is True


def test_run_clean_logs_redacted_authenticated_user(tmp_path, monkeypatch, caplog):
    args = _base_clean_args(tmp_path, redact_sensitive=RedactionConfig(enabled=True, prefix=0, suffix=4))
    set_redaction_config(args.redact_sensitive)
    try:
        monkeypatch.setattr(delete_me_discord, "parse_random_range", lambda *_, **__: (0, 0))

        class FakeAPI:
            def __init__(self, *args, **kwargs):
                pass

            def get_current_user(self):
                return {"id": "123456789012345678", "username": "example-user"}

        class FakeCleaner:
            def __init__(self, **kwargs):
                pass

            def clean_messages(self, **kwargs):
                return 0

        monkeypatch.setattr(delete_me_discord, "DiscordAPI", FakeAPI)
        monkeypatch.setattr(delete_me_discord, "MessageCleaner", FakeCleaner)

        with caplog.at_level("INFO"):
            delete_me_discord._run_clean(args)
    finally:
        set_redaction_config(RedactionConfig())

    assert "Authenticated as *** (***5678)." in caplog.text
    assert "example-user" not in caplog.text
    assert "123456789012345678" not in caplog.text


def test_run_clean_exits_early_without_any_token(tmp_path, monkeypatch):
    args = _base_clean_args(tmp_path)
    monkeypatch.setattr(delete_me_discord, "resolve_token", lambda *_: (None, None))

    class BoomAPI:
        def __init__(self, *args, **kwargs):
            raise AssertionError("DiscordAPI should not be created without a token.")

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", BoomAPI)

    with pytest.raises(SystemExit) as exc:
        delete_me_discord._run_clean(args)
    assert exc.value.code == 1


def test_run_clean_passes_resolved_token_to_api(tmp_path, monkeypatch):
    args = _base_clean_args(tmp_path)
    monkeypatch.setattr(delete_me_discord, "parse_random_range", lambda *_, **__: (0, 0))
    monkeypatch.setattr(delete_me_discord, "resolve_token", lambda *_: ("config-token", "config"))

    captured = {}

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            captured["token"] = kwargs["token"]

        def get_current_user(self):
            return {"id": "me", "username": "me"}

    class FakeCleaner:
        def __init__(self, **kwargs):
            pass

        def clean_messages(self, **kwargs):
            return 0

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", FakeAPI)
    monkeypatch.setattr(delete_me_discord, "MessageCleaner", FakeCleaner)

    delete_me_discord._run_clean(args)
    assert captured["token"] == "config-token"


def test_main_cache_clear_logs_error(tmp_path, monkeypatch):
    args = _base_cache_args(tmp_path, preserve_cache_path=str(tmp_path / "cache.json"))
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    monkeypatch.setattr(delete_me_discord.os.path, "exists", lambda *_: True)
    monkeypatch.setattr(delete_me_discord.os, "remove", lambda *_: (_ for _ in ()).throw(OSError("boom")))

    errors = {"count": 0}
    monkeypatch.setattr(delete_me_discord.logging, "error", lambda *_, **__: errors.__setitem__("count", errors["count"] + 1))

    delete_me_discord.main()
    assert errors["count"] == 1


def test_main_user_id_missing_exits(tmp_path, monkeypatch):
    args = _base_clean_args(tmp_path)
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)
    monkeypatch.setattr(delete_me_discord, "parse_random_range", lambda *_, **__: (0, 0))

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            pass

        def get_current_user(self):
            return {"username": "no-id"}

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", FakeAPI)
    with pytest.raises(SystemExit) as exc:
        delete_me_discord.main()
    assert exc.value.code == 1


def test_main_passes_fetch_since(tmp_path, monkeypatch):
    from datetime import timedelta

    args = _base_clean_args(tmp_path, fetch_within=timedelta(days=1))
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)
    monkeypatch.setattr(delete_me_discord, "parse_random_range", lambda *_, **__: (0, 0))

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            pass

        def get_current_user(self):
            return {"id": "me", "username": "me"}

    fetch_since_value = {}

    class FakeCleaner:
        def __init__(self, **kwargs):
            pass

        def clean_messages(self, **kwargs):
            fetch_since_value["value"] = kwargs.get("fetch_since")
            return 0

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", FakeAPI)
    monkeypatch.setattr(delete_me_discord, "MessageCleaner", FakeCleaner)

    delete_me_discord.main()
    assert fetch_since_value["value"] is not None


def test_main_json_exception_output(tmp_path, monkeypatch, capsys):
    args = _base_clean_args(tmp_path, json=True)
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(delete_me_discord, "_run_clean", boom)

    with pytest.raises(SystemExit) as exc:
        delete_me_discord.main()
    assert exc.value.code == 1
    payload = capsys.readouterr().out.strip()
    assert '"type": "exception"' in payload


def test_main_json_exception_output_keeps_redacted_preserve_cache_message(tmp_path, monkeypatch, capsys):
    args = _base_clean_args(tmp_path, json=True, redact_sensitive=RedactionConfig(enabled=True, prefix=0, suffix=4))
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    def boom(*args, **kwargs):
        raise ValueError(f"bad path {delete_me_discord.sensitive('/tmp/example-secret-path', full=True)}")

    monkeypatch.setattr(delete_me_discord, "_run_clean", boom)

    set_redaction_config(args.redact_sensitive)
    try:
        with pytest.raises(SystemExit) as exc:
            delete_me_discord.main()
        assert exc.value.code == 1
        payload = capsys.readouterr().out.strip()
        assert '"type": "exception"' in payload
        assert "/tmp/example-secret-path" not in payload
        assert "***" in payload
    finally:
        set_redaction_config(RedactionConfig())
