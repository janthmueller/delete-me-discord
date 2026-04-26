# delete-me-discord main orchestration tests
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


def _base_args(tmp_path, **overrides):
    defaults = dict(
        include_ids=[],
        exclude_ids=[],
        preserve_last=None,
        preserve_n=0,
        preserve_n_mode="mine",
        dry_run=False,
        max_retries=1,
        retry_time_buffer=["1", "1"],
        fetch_sleep_time=["0", "0"],
        delete_sleep_time=["0", "0"],
        fetch_max_age=None,
        max_messages=None,
        buffer_channel_messages=False,
        delete_reactions=False,
        list_guilds=False,
        list_channels=False,
        preserve_cache=False,
        wipe_preserve_cache=False,
        preserve_cache_path=str(tmp_path / "cache.json"),
        log_level="INFO",
        json=False,
        redact_sensitive=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_main_wipe_preserve_cache_exits_early(tmp_path, monkeypatch):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text("{}", encoding="utf-8")
    args = _base_args(tmp_path, wipe_preserve_cache=True, preserve_cache_path=str(cache_path))

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    class BoomAPI:
        def __init__(self, *args, **kwargs):
            raise AssertionError("DiscordAPI should not be created for wipe-preserve-cache.")

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", BoomAPI)
    delete_me_discord.main()
    assert not cache_path.exists()


def test_main_list_guilds_runs_discovery(tmp_path, monkeypatch):
    args = _base_args(tmp_path, list_guilds=True)

    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)
    monkeypatch.setattr(delete_me_discord, "parse_random_range", lambda *_, **__: (0, 0))

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            pass

        def get_current_user(self):
            return {"id": "me", "username": "me"}

    called = {"discovery": False}

    def fake_discovery(**kwargs):
        called["discovery"] = True

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", FakeAPI)
    monkeypatch.setattr(delete_me_discord, "run_discovery_commands", fake_discovery)
    delete_me_discord.main()
    assert called["discovery"] is True


def test_main_creates_cache_and_runs_cleaner(tmp_path, monkeypatch):
    args = _base_args(
        tmp_path,
        preserve_cache=True,
        dry_run=True,
        preserve_cache_path=str(tmp_path / "cache.json"),
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


def test_main_passes_buffer_channel_messages(tmp_path, monkeypatch):
    args = _base_args(tmp_path, buffer_channel_messages=True)
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


def test_main_exits_on_negative_preserve_n(tmp_path, monkeypatch):
    args = _base_args(tmp_path, preserve_n=-1)
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)

    log_calls = {"error": 0}

    def fake_error(*args, **kwargs):
        log_calls["error"] += 1

    monkeypatch.setattr(delete_me_discord.logging, "error", fake_error)

    class BoomAPI:
        def __init__(self, *args, **kwargs):
            raise AssertionError("DiscordAPI should not be created for invalid preserve_n.")

    monkeypatch.setattr(delete_me_discord, "DiscordAPI", BoomAPI)
    delete_me_discord.main()
    assert log_calls["error"] == 1


def test_main_authentication_failure_exits(tmp_path, monkeypatch):
    args = _base_args(tmp_path)
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
    args = _base_args(tmp_path, redact_sensitive=RedactionConfig(enabled=True, prefix=0, suffix=4))
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)

    captured = {}

    def fake_setup_logging(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(delete_me_discord, "setup_logging", fake_setup_logging)
    monkeypatch.setattr(delete_me_discord, "_run", lambda *_: None)

    delete_me_discord.main()

    assert captured["redaction_config"].enabled is True
    assert captured["redaction_config"].prefix == 0
    assert captured["redaction_config"].suffix == 4


def test_run_logs_redacted_authenticated_user(tmp_path, monkeypatch, caplog):
    args = _base_args(tmp_path, redact_sensitive=RedactionConfig(enabled=True, prefix=0, suffix=4))
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
            delete_me_discord._run(args)
    finally:
        set_redaction_config(RedactionConfig())

    assert "Authenticated as *** (***5678)." in caplog.text
    assert "example-user" not in caplog.text
    assert "123456789012345678" not in caplog.text


def test_main_wipe_preserve_cache_logs_error(tmp_path, monkeypatch):
    args = _base_args(tmp_path, wipe_preserve_cache=True, preserve_cache_path=str(tmp_path / "cache.json"))
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    monkeypatch.setattr(delete_me_discord.os.path, "exists", lambda *_: True)
    monkeypatch.setattr(delete_me_discord.os, "remove", lambda *_: (_ for _ in ()).throw(OSError("boom")))

    errors = {"count": 0}
    monkeypatch.setattr(delete_me_discord.logging, "error", lambda *_, **__: errors.__setitem__("count", errors["count"] + 1))

    delete_me_discord.main()
    assert errors["count"] == 1


def test_main_user_id_missing_exits(tmp_path, monkeypatch):
    args = _base_args(tmp_path)
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

    args = _base_args(tmp_path, fetch_max_age=timedelta(days=1))
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
    args = _base_args(tmp_path, json=True)
    monkeypatch.setattr(delete_me_discord, "parse_args", lambda *_: args)
    monkeypatch.setattr(delete_me_discord, "setup_logging", lambda **_: None)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(delete_me_discord, "_run", boom)

    with pytest.raises(SystemExit) as exc:
        delete_me_discord.main()
    assert exc.value.code == 1
    payload = capsys.readouterr().out.strip()
    assert '"type": "exception"' in payload
