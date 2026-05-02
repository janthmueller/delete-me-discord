import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.app_config import (
    add_profile,
    build_clean_defaults,
    load_config,
    load_profile,
    load_profile_names,
    load_raw_profile,
    parse_profile_set_assignments,
    remove_profile,
    resolve_effective_clean_settings,
    update_profile,
    validate_profile_unset_fields,
)
from delete_me_discord.options import parse_args


def test_load_profile_names_returns_sorted_names(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{},"alpha":{},"manual-review":{}}}',
        encoding="utf-8",
    )

    assert load_profile_names(str(config_path)) == ["alpha", "manual-review", "nightly-dms"]


def test_load_raw_profile_returns_stored_profile_without_validation(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"verbose":9}}}',
        encoding="utf-8",
    )

    assert load_raw_profile(str(config_path), "nightly-dms") == {"verbose": 9}


def test_load_config_returns_empty_for_missing_file(tmp_path):
    assert load_config(str(tmp_path / "missing.json")) == {}


def test_load_config_rejects_non_object_root(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('["not-an-object"]', encoding="utf-8")

    with pytest.raises(ValueError, match="Config root must be a JSON object"):
        load_config(str(config_path))


def test_load_profile_names_rejects_non_object_profiles_root(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"profiles":["bad"]}', encoding="utf-8")

    with pytest.raises(ValueError, match="profiles"):
        load_profile_names(str(config_path))


def test_load_profile_rejects_null_values(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"keep_within":null}}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be omitted instead of null"):
        load_profile(str(config_path), "nightly-dms")


def test_load_profile_rejects_unknown_profile(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"profiles":{"nightly-dms":{}}}', encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown profile"):
        load_profile(str(config_path), "missing")


def test_load_profile_rejects_non_object_profile(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"profiles":{"nightly-dms":["bad"]}}', encoding="utf-8")

    with pytest.raises(ValueError, match="must be a JSON object"):
        load_profile(str(config_path), "nightly-dms")


def test_load_profile_rejects_unknown_field(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"profiles":{"nightly-dms":{"wat":1}}}', encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported field"):
        load_profile(str(config_path), "nightly-dms")


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("include_ids", 1, "list of strings"),
        ("keep_last", -1, "non-negative integer"),
        ("keep_last_scope", "weird", "must be 'mine' or 'all'"),
        ("keep_within", [], "string or zero-like number"),
        ("buffer_per_channel", "yes", "must be true or false"),
        ("preserve_cache_path", "", "non-empty string"),
        ("max_retries", True, "non-negative integer"),
        ("retry_time_buffer", "bad", "list with one or two numbers"),
        ("verbose", 4, "between 0 and 3"),
        ("redact_sensitive", [1], "two-integer list"),
    ],
)
def test_load_profile_rejects_invalid_field_values(tmp_path, field, value, match):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"profiles": {"nightly-dms": {field: value}}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=match):
        load_profile(str(config_path), "nightly-dms")


def test_parse_profile_set_assignments_parses_and_validates_values():
    parsed = parse_profile_set_assignments(
        [
            "keep_last=20",
            "keep_within=2w",
            "preserve_cache=true",
            'include_ids=["123","456"]',
            "verbose=2",
            "fetch_within=none",
            "max_messages=5",
            "redact_sensitive=[0,4]",
        ]
    )

    assert parsed["keep_last"] == 20
    assert parsed["keep_within"].days == 14
    assert parsed["preserve_cache"] is True
    assert parsed["include_ids"] == ["123", "456"]
    assert parsed["verbose"] == 2
    assert parsed["fetch_within"] is None
    assert parsed["max_messages"] == 5
    assert parsed["redact_sensitive"].prefix == 0
    assert parsed["redact_sensitive"].suffix == 4


def test_parse_profile_set_assignments_accepts_none_for_nullable_fields():
    parsed = parse_profile_set_assignments(
        [
            "fetch_within=none",
            "max_messages=None",
        ]
    )

    assert parsed["fetch_within"] is None
    assert parsed["max_messages"] is None


@pytest.mark.parametrize(
    ("assignment", "match"),
    [
        ("keep_last=abc", "non-negative integer"),
        ("verbose=9", "between 0 and 3"),
        ("preserve_cache=maybe", "true or false"),
        ("keep_within=banana", "invalid"),
        ("include_ids=abc", "JSON syntax"),
        ("wat=1", "Unsupported profile field"),
        ("not-an-assignment", "Expected key=value"),
    ],
)
def test_parse_profile_set_assignments_rejects_invalid_values(assignment, match):
    with pytest.raises(ValueError, match=match):
        parse_profile_set_assignments([assignment])


def test_validate_profile_unset_fields_deduplicates_and_validates():
    assert validate_profile_unset_fields(["fetch_within", "max_messages", "fetch_within"]) == [
        "fetch_within",
        "max_messages",
    ]

    with pytest.raises(ValueError, match="Unsupported profile field"):
        validate_profile_unset_fields(["wat"])


def test_add_profile_preserves_unrelated_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"auth": {"token": "secret"}, "profiles": {"existing": {"keep_last": 1}}}),
        encoding="utf-8",
    )

    add_profile(str(config_path), "nightly-dms", {"keep_last": 20, "dry_run": True})

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["auth"]["token"] == "secret"
    assert saved["profiles"]["existing"]["keep_last"] == 1
    assert saved["profiles"]["nightly-dms"]["keep_last"] == 20
    assert saved["profiles"]["nightly-dms"]["dry_run"] is True


def test_add_profile_rejects_existing_name(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"profiles":{"nightly-dms":{}}}', encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        add_profile(str(config_path), "nightly-dms", {"keep_last": 20})


def test_update_profile_applies_set_and_unset(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"keep_last":5,"fetch_within":"2w","preserve_cache":true}}}',
        encoding="utf-8",
    )

    update_profile(
        str(config_path),
        "nightly-dms",
        {"keep_last": 10, "dry_run": True},
        ["fetch_within"],
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["profiles"]["nightly-dms"] == {
        "dry_run": True,
        "keep_last": 10,
        "preserve_cache": True,
    }


def test_update_profile_rejects_missing_name(tmp_path):
    with pytest.raises(ValueError, match="Unknown profile"):
        update_profile(str(tmp_path / "config.json"), "missing", {"keep_last": 10}, [])


def test_update_profile_rejects_unsetting_field_not_present(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"keep_last":5}}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not currently define field"):
        update_profile(str(config_path), "nightly-dms", {}, ["max_messages"])


def test_remove_profile_preserves_other_content(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "auth": {"token": "secret"},
                "profiles": {
                    "nightly-dms": {"keep_last": 5},
                    "manual-review": {"keep_within": "2w"},
                },
            }
        ),
        encoding="utf-8",
    )

    remove_profile(str(config_path), "nightly-dms")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["auth"]["token"] == "secret"
    assert "nightly-dms" not in saved["profiles"]
    assert saved["profiles"]["manual-review"]["keep_within"] == "2w"


def test_remove_profile_drops_empty_profiles_key(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"auth": {"token": "secret"}, "profiles": {"nightly-dms": {"keep_last": 5}}}),
        encoding="utf-8",
    )

    remove_profile(str(config_path), "nightly-dms")

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved == {"auth": {"token": "secret"}}


def test_build_clean_defaults_uses_global_default_when_profile_missing():
    defaults = build_clean_defaults(None, None)

    assert defaults["profile"] is None
    assert defaults["preserve_cache_path"].endswith("preserve_cache.json")


def test_parse_args_profile_applies_defaults(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"keep_last":25,"dry_run":true,"verbose":2}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=["clean", "--config-path", str(config_path), "--profile", "nightly-dms"],
    )

    assert args.profile == "nightly-dms"
    assert args.keep_last == 25
    assert args.dry_run is True
    assert args.verbose == 2


def test_parse_args_cli_values_override_profile_defaults(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"keep_last":25,"verbose":1}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=[
            "clean",
            "--config-path",
            str(config_path),
            "--profile",
            "nightly-dms",
            "--keep-last",
            "3",
            "-vvv",
        ],
    )

    assert args.keep_last == 3
    assert args.verbose == 3


def test_parse_args_cli_can_reset_profile_nullable_defaults(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"fetch_within":"2w","max_messages":50}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=[
            "clean",
            "--config-path",
            str(config_path),
            "--profile",
            "nightly-dms",
            "--fetch-within",
            "none",
            "--max-messages",
            "None",
        ],
    )

    assert args.fetch_within is None
    assert args.max_messages is None


def test_parse_args_cli_can_disable_profile_boolean_defaults(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"dry_run":true,"quiet":true,"json":true,"keep_reactions":true,"preserve_cache":true,"buffer_per_channel":true}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=[
            "clean",
            "--config-path",
            str(config_path),
            "--profile",
            "nightly-dms",
            "--no-dry-run",
            "--no-quiet",
            "--no-json",
            "--no-keep-reactions",
            "--no-preserve-cache",
            "--no-buffer-per-channel",
        ],
    )

    assert args.dry_run is False
    assert args.quiet is False
    assert args.json is False
    assert args.keep_reactions is False
    assert args.preserve_cache is False
    assert args.buffer_per_channel is False


def test_parse_args_profile_keeps_true_boolean_defaults_without_cli_override(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"dry_run":true,"quiet":true,"json":true,"keep_reactions":true,"preserve_cache":true,"buffer_per_channel":true}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=["clean", "--config-path", str(config_path), "--profile", "nightly-dms"],
    )

    assert args.dry_run is True
    assert args.quiet is True
    assert args.json is True
    assert args.keep_reactions is True
    assert args.preserve_cache is True
    assert args.buffer_per_channel is True


def test_resolve_effective_clean_settings_derives_profile_cache_path(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly dms":{"preserve_cache":true}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=["clean", "--config-path", str(config_path), "--profile", "nightly dms"],
    )
    settings = resolve_effective_clean_settings(args)

    assert settings.preserve_cache is True
    assert settings.preserve_cache_path.endswith("preserve-cache/nightly-dms.json")


def test_resolve_effective_clean_settings_derives_profile_cache_path_for_dry_run(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly dms":{"preserve_cache":true,"dry_run":true}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=["clean", "--config-path", str(config_path), "--profile", "nightly dms"],
    )
    settings = resolve_effective_clean_settings(args)

    assert settings.preserve_cache is True
    assert settings.dry_run is True
    assert settings.preserve_cache_path.endswith("preserve-cache/nightly-dms.dryrun.json")


def test_profile_explicit_global_preserve_cache_path_is_kept(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"preserve_cache":true,"preserve_cache_path":"~/.config/delete-me-discord/preserve_cache.json"}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=["clean", "--config-path", str(config_path), "--profile", "nightly-dms"],
    )

    assert args.preserve_cache_path == "~/.config/delete-me-discord/preserve_cache.json"


def test_resolve_effective_clean_settings_applies_dry_run_suffix_to_explicit_profile_cache_path(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"preserve_cache":true,"dry_run":true,"preserve_cache_path":"/tmp/nightly-cache.json"}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=["clean", "--config-path", str(config_path), "--profile", "nightly-dms"],
    )
    settings = resolve_effective_clean_settings(args)

    assert settings.preserve_cache is True
    assert settings.dry_run is True
    assert settings.preserve_cache_path == "/tmp/nightly-cache.dryrun.json"


def test_profile_can_set_preserve_cache_path_without_enabling_preserve_cache(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"preserve_cache":false,"preserve_cache_path":"/tmp/nightly-cache.json"}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=["clean", "--config-path", str(config_path), "--profile", "nightly-dms"],
    )
    settings = resolve_effective_clean_settings(args)

    assert settings.preserve_cache is False
    assert settings.preserve_cache_path == "/tmp/nightly-cache.json"


def test_profile_json_true_emits_json_parser_errors(tmp_path, capsys):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"json":true}}}',
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
                "--nope",
            ],
        )
    assert exc.value.code == 2
    out = capsys.readouterr().out.strip()
    assert '"type": "argument_error"' in out


def test_invalid_profile_with_json_true_emits_json_profile_errors(tmp_path, capsys):
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
            ],
        )
    assert exc.value.code == 2
    out = capsys.readouterr().out.strip()
    assert '"type": "argument_error"' in out
    assert "Profile 'nightly-dms' field 'verbose' must be between 0 and 3." in out


def test_profile_without_preserve_cache_keeps_global_default_path(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"keep_last":5}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=["clean", "--config-path", str(config_path), "--profile", "nightly-dms"],
    )
    settings = resolve_effective_clean_settings(args)

    assert settings.preserve_cache is False
    assert settings.preserve_cache_path.endswith("preserve_cache.json")


def test_cli_can_disable_profile_preserve_cache_while_keeping_profile_path(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"preserve_cache":true,"preserve_cache_path":"/tmp/nightly-cache.json"}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=[
            "clean",
            "--config-path",
            str(config_path),
            "--profile",
            "nightly-dms",
            "--no-preserve-cache",
        ],
    )
    settings = resolve_effective_clean_settings(args)

    assert settings.preserve_cache is False
    assert settings.preserve_cache_path == "/tmp/nightly-cache.json"
