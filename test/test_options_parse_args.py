# delete-me-discord options parsing tests
import sys
from datetime import timedelta
from pathlib import Path

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from delete_me_discord.options import parse_args


def test_parse_args_clean_defaults():
    args = parse_args("1.0.0", argv=["clean"])
    assert args.command == "clean"
    assert args.profile is None
    assert args.include_ids == []
    assert args.exclude_ids == []
    assert args.token is None
    assert args.config_path.endswith("config.json")
    assert args.dry_run is False
    assert args.quiet is False
    assert args.verbose == 0
    assert args.max_retries == 5
    assert args.retry_time_buffer == [25, 35]
    assert args.fetch_sleep_time == [0.2, 0.4]
    assert args.delete_sleep_time == [1.5, 2]
    assert args.keep_last == 0
    assert args.keep_last_scope == "all"
    assert args.keep_within == timedelta(0)
    assert args.fetch_within is None
    assert args.max_messages is None
    assert args.buffer_per_channel is False
    assert args.keep_reactions is False
    assert args.preserve_cache is False
    assert args.json is False
    assert args.redact_sensitive is None


def test_parse_args_list_channels_json_flag():
    args = parse_args("1.0.0", argv=["list", "channels", "-j"])
    assert args.command == "list"
    assert args.list_command == "channels"
    assert args.json is True


def test_parse_args_list_channels_accepts_scope_filters():
    args = parse_args(
        "1.0.0",
        argv=["list", "channels", "--include-ids", "guild-1", "category-1", "--exclude-ids", "channel-1"],
    )
    assert args.command == "list"
    assert args.list_command == "channels"
    assert args.include_ids == ["guild-1", "category-1"]
    assert args.exclude_ids == ["channel-1"]


def test_parse_args_list_guilds_accepts_scope_filters():
    args = parse_args(
        "1.0.0",
        argv=["list", "guilds", "--include-ids", "guild-1", "--exclude-ids", "guild-2"],
    )
    assert args.command == "list"
    assert args.list_command == "guilds"
    assert args.include_ids == ["guild-1"]
    assert args.exclude_ids == ["guild-2"]


def test_parse_args_list_channels_no_json_flag():
    args = parse_args("1.0.0", argv=["list", "channels", "--no-json"])
    assert args.command == "list"
    assert args.list_command == "channels"
    assert args.json is False


def test_parse_args_list_profiles():
    args = parse_args("1.0.0", argv=["list", "profiles"])
    assert args.command == "list"
    assert args.list_command == "profiles"
    assert args.config_path.endswith("config.json")


def test_parse_args_profile_show():
    args = parse_args("1.0.0", argv=["profile", "show", "nightly-dms"])
    assert args.command == "profile"
    assert args.profile_command == "show"
    assert args.name == "nightly-dms"


def test_parse_args_profile_fields():
    args = parse_args("1.0.0", argv=["profile", "fields", "--json"])
    assert args.command == "profile"
    assert args.profile_command == "fields"
    assert args.json is True


def test_parse_args_profile_add():
    args = parse_args(
        "1.0.0",
        argv=["profile", "add", "nightly-dms", "--set", "keep_last=20", "--set", "dry_run=true"],
    )
    assert args.command == "profile"
    assert args.profile_command == "add"
    assert args.name == "nightly-dms"
    assert args.profile_set == ["keep_last=20", "dry_run=true"]


def test_parse_args_profile_update():
    args = parse_args(
        "1.0.0",
        argv=[
            "profile",
            "update",
            "nightly-dms",
            "--set",
            "keep_last=20",
            "--unset",
            "fetch_within",
            "max_messages",
        ],
    )
    assert args.command == "profile"
    assert args.profile_command == "update"
    assert args.name == "nightly-dms"
    assert args.profile_set == ["keep_last=20"]
    assert args.profile_unset == ["fetch_within", "max_messages"]


def test_parse_args_profile_remove():
    args = parse_args("1.0.0", argv=["profile", "remove", "nightly-dms"])
    assert args.command == "profile"
    assert args.profile_command == "remove"
    assert args.name == "nightly-dms"


def test_parse_args_cache_clear():
    args = parse_args("1.0.0", argv=["cache", "clear"])
    assert args.command == "cache"
    assert args.cache_command == "clear"
    assert args.preserve_cache_path.endswith("preserve_cache.json")


def test_parse_args_json_error_output(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args("1.0.0", argv=["clean", "-j", "--nope"])
    assert exc.value.code == 2
    out = capsys.readouterr().out.strip()
    assert '"type": "argument_error"' in out


def test_parse_args_no_json_overrides_profile_json_for_errors(tmp_path, capsys):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"json":true,"verbose":9}}}',
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc:
        parse_args(
            "1.0.0",
            argv=[
                "clean",
                "--config-path",
                str(config_path),
                "--profile",
                "nightly-dms",
                "--no-json",
            ],
        )
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "must be between 0 and 3" in err


def test_parse_args_non_json_error_output(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args("1.0.0", argv=["clean", "--nope"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "unrecognized arguments" in err


def test_parse_args_redact_sensitive_default_mask():
    args = parse_args("1.0.0", argv=["clean", "--redact-sensitive"])
    assert args.redact_sensitive.enabled is True
    assert args.redact_sensitive.prefix == 0
    assert args.redact_sensitive.suffix == 0


def test_parse_args_redact_sensitive_custom_window():
    args = parse_args("1.0.0", argv=["clean", "--redact-sensitive", "4", "4"])
    assert args.redact_sensitive.enabled is True
    assert args.redact_sensitive.prefix == 4
    assert args.redact_sensitive.suffix == 4


def test_parse_args_redact_sensitive_rejects_invalid_spec(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args("1.0.0", argv=["clean", "--redact-sensitive", "4"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "two integers" in err


def test_parse_args_redact_sensitive_rejects_comma_form(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args("1.0.0", argv=["clean", "--redact-sensitive", "0,4"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "two integers" in err


def test_parse_args_login_command():
    args = parse_args("1.0.0", argv=["login"])
    assert args.command == "login"
    assert args.replace is False
    assert args.config_path.endswith("config.json")


def test_parse_args_login_replace_command():
    args = parse_args("1.0.0", argv=["login", "--replace"])
    assert args.command == "login"
    assert args.replace is True


def test_parse_args_login_rejects_token(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args("1.0.0", argv=["login", "--token", "abc"])
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_parse_args_whoami_command_with_json():
    args = parse_args("1.0.0", argv=["whoami", "--json"])
    assert args.command == "whoami"
    assert args.json is True


def test_parse_args_clean_profile_option(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"profiles":{"nightly-dms":{}}}', encoding="utf-8")
    args = parse_args("1.0.0", argv=["clean", "--config-path", str(config_path), "--profile", "nightly-dms"])
    assert args.command == "clean"
    assert args.profile == "nightly-dms"
