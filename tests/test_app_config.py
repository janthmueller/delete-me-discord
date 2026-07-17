import json
import sys
from datetime import timedelta
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


def _assert_runtime_value(value, expected):
    if isinstance(expected, timedelta):
        assert value == expected
        return
    if isinstance(expected, tuple) and expected and expected[0] == "redaction":
        assert value.enabled is True
        assert value.prefix == expected[1]
        assert value.suffix == expected[2]
        return
    assert value == expected


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
    ("assignment", "expected"),
    [
        ("include_ids=123,456", {"include_ids": ["123", "456"]}),
        ("include_ids=123 456", {"include_ids": ["123", "456"]}),
        ('include_ids=["123","456"]', {"include_ids": ["123", "456"]}),
        ("exclude_ids=789,101112", {"exclude_ids": ["789", "101112"]}),
        (
            "exclude_channel_types=GuildVoice,PrivateThread",
            {"exclude_channel_types": ["GuildVoice", "PrivateThread"]},
        ),
        (
            "exclude_thread_states=archived",
            {"exclude_thread_states": ["archived"]},
        ),
        ("exclude_threads=true", {"exclude_threads": True}),
        ("keep_last=20", {"keep_last": 20}),
        ("keep_last_scope=mine", {"keep_last_scope": "mine"}),
        ("keep_last_scope=all", {"keep_last_scope": "all"}),
        ("keep_within=2w", {"keep_within": "2w"}),
        ("keep_within=weeks=2", {"keep_within": "weeks=2"}),
        ("fetch_within=1d", {"fetch_within": "1d"}),
        ("fetch_within=none", {"fetch_within": None}),
        ("max_messages=5", {"max_messages": 5}),
        ("max_messages=none", {"max_messages": None}),
        ("buffer_per_channel=true", {"buffer_per_channel": True}),
        ("buffer_per_channel=false", {"buffer_per_channel": False}),
        ("keep_reactions=true", {"keep_reactions": True}),
        ("keep_reactions=false", {"keep_reactions": False}),
        ("delete_owned_threads=self-only", {"delete_owned_threads": "self-only"}),
        (
            "skip_unrestorable_threads=true",
            {"skip_unrestorable_threads": True},
        ),
        ("preserve_cache=true", {"preserve_cache": True}),
        ("preserve_cache=false", {"preserve_cache": False}),
        ("preserve_cache_path=/tmp/cache.json", {"preserve_cache_path": "/tmp/cache.json"}),
        ("max_retries=7", {"max_retries": 7}),
        ("retry_time_buffer=25,35", {"retry_time_buffer": [25.0, 35.0]}),
        ("retry_time_buffer=25 35", {"retry_time_buffer": [25.0, 35.0]}),
        ("retry_time_buffer=[25,35]", {"retry_time_buffer": [25.0, 35.0]}),
        ("fetch_sleep_time=0.2,0.4", {"fetch_sleep_time": [0.2, 0.4]}),
        ("fetch_sleep_time=0.2 0.4", {"fetch_sleep_time": [0.2, 0.4]}),
        ("fetch_sleep_time=[0.2,0.4]", {"fetch_sleep_time": [0.2, 0.4]}),
        ("delete_sleep_time=1.5,2", {"delete_sleep_time": [1.5, 2.0]}),
        ("delete_sleep_time=1.5 2", {"delete_sleep_time": [1.5, 2.0]}),
        ("delete_sleep_time=[1.5,2]", {"delete_sleep_time": [1.5, 2.0]}),
        ("dry_run=true", {"dry_run": True}),
        ("dry_run=false", {"dry_run": False}),
        ("quiet=true", {"quiet": True}),
        ("quiet=false", {"quiet": False}),
        ("verbose=3", {"verbose": 3}),
        ("json=true", {"json": True}),
        ("json=false", {"json": False}),
        ("redact_sensitive=true", {"redact_sensitive": True}),
        ("redact_sensitive=false", {"redact_sensitive": False}),
        ("redact_sensitive=1", {"redact_sensitive": [1]}),
        ("redact_sensitive=0,1", {"redact_sensitive": [0, 1]}),
        ("redact_sensitive=0 1", {"redact_sensitive": [0, 1]}),
        ("redact_sensitive=[0,1]", {"redact_sensitive": [0, 1]}),
    ],
)
def test_parse_profile_set_assignments_accepts_all_supported_shapes(assignment, expected):
    assert parse_profile_set_assignments([assignment]) == expected


@pytest.mark.parametrize(
    ("field", "raw_value", "expected_runtime"),
    [
        ("include_ids", ["123", "456"], ["123", "456"]),
        ("include_ids", "123,456", ["123", "456"]),
        ("include_ids", "123 456", ["123", "456"]),
        ("include_ids", '["123","456"]', ["123", "456"]),
        ("exclude_ids", ["789", "101112"], ["789", "101112"]),
        ("exclude_ids", "789,101112", ["789", "101112"]),
        (
            "exclude_channel_types",
            ["GuildVoice", "PrivateThread"],
            ["GuildVoice", "PrivateThread"],
        ),
        ("exclude_channel_types", "GuildVoice,PrivateThread", ["GuildVoice", "PrivateThread"]),
        ("exclude_thread_states", ["active"], ["active"]),
        ("exclude_thread_states", "archived", ["archived"]),
        ("exclude_threads", True, True),
        ("exclude_threads", "false", False),
        ("keep_last", 20, 20),
        ("keep_last", "20", 20),
        ("keep_last_scope", "mine", "mine"),
        ("keep_last_scope", "all", "all"),
        ("keep_within", "2w", timedelta(weeks=2)),
        ("keep_within", "weeks=2", timedelta(weeks=2)),
        ("keep_within", 0, timedelta(0)),
        ("fetch_within", "1d", timedelta(days=1)),
        ("fetch_within", "days=1", timedelta(days=1)),
        ("fetch_within", 0, timedelta(0)),
        ("max_messages", 5, 5),
        ("max_messages", "5", 5),
        ("buffer_per_channel", True, True),
        ("buffer_per_channel", "true", True),
        ("buffer_per_channel", False, False),
        ("buffer_per_channel", "false", False),
        ("keep_reactions", True, True),
        ("keep_reactions", "true", True),
        ("delete_owned_threads", "none", "none"),
        ("delete_owned_threads", "self-only", "self-only"),
        ("delete_owned_threads", "all", "all"),
        ("skip_unrestorable_threads", True, True),
        ("skip_unrestorable_threads", "false", False),
        ("preserve_cache", True, True),
        ("preserve_cache", "true", True),
        ("preserve_cache_path", "/tmp/cache.json", "/tmp/cache.json"),
        ("max_retries", 7, 7),
        ("max_retries", "7", 7),
        ("retry_time_buffer", [25, 35], [25.0, 35.0]),
        ("retry_time_buffer", "25,35", [25.0, 35.0]),
        ("retry_time_buffer", "25 35", [25.0, 35.0]),
        ("retry_time_buffer", "[25,35]", [25.0, 35.0]),
        ("fetch_sleep_time", [0.2, 0.4], [0.2, 0.4]),
        ("fetch_sleep_time", "0.2,0.4", [0.2, 0.4]),
        ("fetch_sleep_time", "0.2 0.4", [0.2, 0.4]),
        ("fetch_sleep_time", "[0.2,0.4]", [0.2, 0.4]),
        ("delete_sleep_time", [1.5, 2], [1.5, 2.0]),
        ("delete_sleep_time", "1.5,2", [1.5, 2.0]),
        ("delete_sleep_time", "1.5 2", [1.5, 2.0]),
        ("delete_sleep_time", "[1.5,2]", [1.5, 2.0]),
        ("dry_run", True, True),
        ("dry_run", "true", True),
        ("quiet", True, True),
        ("quiet", "true", True),
        ("verbose", 3, 3),
        ("verbose", "3", 3),
        ("json", True, True),
        ("json", "true", True),
        ("redact_sensitive", True, ("redaction", 0, 0)),
        ("redact_sensitive", "true", ("redaction", 0, 0)),
        ("redact_sensitive", False, None),
        ("redact_sensitive", "false", None),
        ("redact_sensitive", [1], ("redaction", 0, 1)),
        ("redact_sensitive", "1", ("redaction", 0, 1)),
        ("redact_sensitive", [0, 1], ("redaction", 0, 1)),
        ("redact_sensitive", "0,1", ("redaction", 0, 1)),
        ("redact_sensitive", "0 1", ("redaction", 0, 1)),
        ("redact_sensitive", "[0,1]", ("redaction", 0, 1)),
        ("redact_names", True, True),
        ("redact_names", "true", True),
        ("redact_names", False, False),
        ("redact_names", "false", False),
    ],
)
def test_load_profile_accepts_all_supported_config_shapes(tmp_path, field, raw_value, expected_runtime):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"profiles": {"nightly-dms": {field: raw_value}}}),
        encoding="utf-8",
    )

    loaded = load_profile(str(config_path), "nightly-dms")

    _assert_runtime_value(loaded[field], expected_runtime)


@pytest.mark.parametrize(
    ("field", "raw_value", "expected_stored"),
    [
        ("include_ids", "123,456", ["123", "456"]),
        ("exclude_ids", "789 101112", ["789", "101112"]),
        ("exclude_channel_types", "GuildVoice,PrivateThread", ["GuildVoice", "PrivateThread"]),
        ("exclude_thread_states", "archived", ["archived"]),
        ("exclude_threads", "true", True),
        ("keep_last", "20", 20),
        ("keep_last_scope", "all", "all"),
        ("keep_within", "2w", "2w"),
        ("fetch_within", "1d", "1d"),
        ("max_messages", "5", 5),
        ("buffer_per_channel", "true", True),
        ("keep_reactions", "true", True),
        ("delete_owned_threads", "self-only", "self-only"),
        ("skip_unrestorable_threads", "true", True),
        ("preserve_cache", "true", True),
        ("preserve_cache_path", "/tmp/cache.json", "/tmp/cache.json"),
        ("max_retries", "7", 7),
        ("retry_time_buffer", "25,35", [25.0, 35.0]),
        ("fetch_sleep_time", "0.2,0.4", [0.2, 0.4]),
        ("delete_sleep_time", "1.5,2", [1.5, 2.0]),
        ("dry_run", "true", True),
        ("quiet", "true", True),
        ("verbose", "3", 3),
        ("json", "true", True),
        ("redact_sensitive", "true", True),
        ("redact_sensitive", "false", False),
        ("redact_sensitive", "1", [1]),
        ("redact_sensitive", "0,1", [0, 1]),
        ("redact_names", "false", False),
    ],
)
def test_update_profile_normalizes_all_supported_stored_shapes(tmp_path, field, raw_value, expected_stored):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"profiles":{"nightly-dms":{"keep_last":1}}}', encoding="utf-8")

    update_profile(str(config_path), "nightly-dms", {field: raw_value}, [])

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["profiles"]["nightly-dms"][field] == expected_stored


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("include_ids", 1, "list of strings"),
        ("include_ids", "", "empty list string"),
        ("exclude_channel_types", ["ForumPost"], "unsupported value"),
        ("exclude_thread_states", ["locked"], "unsupported value"),
        ("keep_last", -1, "non-negative integer"),
        ("keep_last_scope", "weird", "must be 'mine' or 'all'"),
        ("keep_within", [], "field 'keep_within' must be a string or zero-like number"),
        ("buffer_per_channel", "yes", "must be true or false"),
        ("delete_owned_threads", "mine", "must be 'none' or 'self-only' or 'all'"),
        (
            "skip_unrestorable_threads",
            "always",
            "must be true or false",
        ),
        ("preserve_cache_path", "", "non-empty string"),
        ("max_retries", True, "non-negative integer"),
        ("retry_time_buffer", "bad", "invalid"),
        ("verbose", 4, "between 0 and 3"),
        ("redact_sensitive", [1, 2, 3], "one-integer suffix list"),
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
            "include_ids=123,456",
            "exclude_ids=789 101112",
            "retry_time_buffer=25,35",
            "verbose=2",
            "fetch_within=none",
            "max_messages=5",
            "redact_sensitive=4",
        ]
    )

    assert parsed["keep_last"] == 20
    assert parsed["keep_within"] == "2w"
    assert parsed["preserve_cache"] is True
    assert parsed["include_ids"] == ["123", "456"]
    assert parsed["exclude_ids"] == ["789", "101112"]
    assert parsed["retry_time_buffer"] == [25.0, 35.0]
    assert parsed["verbose"] == 2
    assert parsed["fetch_within"] is None
    assert parsed["max_messages"] == 5
    assert parsed["redact_sensitive"] == [4]


def test_load_profile_accepts_config_convenience_strings(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "profiles": {
                    "nightly-dms": {
                        "include_ids": "123,456",
                        "exclude_ids": "789 101112",
                        "keep_last": "20",
                        "keep_within": "2w",
                        "fetch_within": "1d",
                        "max_messages": "5",
                        "preserve_cache": "true",
                        "retry_time_buffer": "25,35",
                        "redact_sensitive": "0,4",
                        "redact_names": "false",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    loaded = load_profile(str(config_path), "nightly-dms")

    assert loaded["include_ids"] == ["123", "456"]
    assert loaded["exclude_ids"] == ["789", "101112"]
    assert loaded["keep_last"] == 20
    assert loaded["keep_within"].days == 14
    assert loaded["fetch_within"].days == 1
    assert loaded["max_messages"] == 5
    assert loaded["preserve_cache"] is True
    assert loaded["retry_time_buffer"] == [25.0, 35.0]
    assert loaded["redact_sensitive"].prefix == 0
    assert loaded["redact_sensitive"].suffix == 4
    assert loaded["redact_names"] is False


def test_load_profile_accepts_redact_sensitive_false_without_unsetting(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"redact_sensitive":"false"}}}',
        encoding="utf-8",
    )

    loaded = load_profile(str(config_path), "nightly-dms")

    assert loaded["redact_sensitive"] is None


def test_parse_profile_set_assignments_accepts_json_arrays_for_list_values():
    parsed = parse_profile_set_assignments(
        [
            'include_ids=["123","456"]',
            "fetch_sleep_time=[0.2,0.4]",
            "redact_sensitive=[0,4]",
        ]
    )

    assert parsed["include_ids"] == ["123", "456"]
    assert parsed["fetch_sleep_time"] == [0.2, 0.4]
    assert parsed["redact_sensitive"] == [0, 4]


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
        ("exclude_channel_types=ForumPost", "unsupported value"),
        ("exclude_thread_states=locked", "unsupported value"),
        ("keep_within=banana", "invalid"),
        ("retry_time_buffer=abc", "invalid"),
        ("redact_sensitive=abc", "one-integer suffix list"),
        ("redact_sensitive=0,abc", "one-integer suffix list"),
        ("include_ids=", "empty list string"),
        ("retry_time_buffer=", "empty list string"),
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


def test_add_profile_normalizes_direct_profile_data(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"profiles":{}}', encoding="utf-8")

    add_profile(
        str(config_path),
        "nightly-dms",
        {"include_ids": "123,456", "keep_within": "2w", "preserve_cache": "true"},
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["profiles"]["nightly-dms"] == {
        "include_ids": ["123", "456"],
        "keep_within": "2w",
        "preserve_cache": True,
    }


def test_update_profile_stores_time_delta_strings(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"profiles":{"nightly-dms":{"keep_last":1}}}', encoding="utf-8")

    updates = parse_profile_set_assignments(["keep_within=2w", "fetch_within=1d"])
    update_profile(str(config_path), "nightly-dms", updates, [])

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["profiles"]["nightly-dms"]["keep_within"] == "2w"
    assert saved["profiles"]["nightly-dms"]["fetch_within"] == "1d"

    loaded = load_profile(str(config_path), "nightly-dms")
    assert loaded["keep_within"].days == 14
    assert loaded["fetch_within"].days == 1


def test_update_profile_normalizes_existing_convenience_config_values(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "profiles": {
                    "nightly-dms": {
                        "include_ids": "123,456",
                        "preserve_cache": "true",
                        "retry_time_buffer": "25,35",
                        "redact_sensitive": "false",
                        "redact_names": "false",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    update_profile(str(config_path), "nightly-dms", {"keep_last": 10}, [])

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["profiles"]["nightly-dms"] == {
        "include_ids": ["123", "456"],
        "keep_last": 10,
        "preserve_cache": True,
        "redact_sensitive": False,
        "redact_names": False,
        "retry_time_buffer": [25.0, 35.0],
    }


@pytest.mark.parametrize(
    ("legacy_fields", "expected_filters"),
    [
        (
            {"include_threads": False},
            {"exclude_threads": True},
        ),
        ({"include_threads": True}, {"exclude_thread_states": ["archived"]}),
        ({"include_archived_threads": True}, {}),
        ({"include_threads": True, "include_archived_threads": True}, {}),
        ({"threads": "none"}, {"exclude_threads": True}),
        ({"threads": "active"}, {"exclude_thread_states": ["archived"]}),
        ({"threads": "all"}, {}),
    ],
)
def test_load_profile_migrates_legacy_thread_fields(tmp_path, legacy_fields, expected_filters):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"profiles": {"nightly-dms": legacy_fields}}),
        encoding="utf-8",
    )

    assert load_profile(str(config_path), "nightly-dms") == expected_filters


def test_update_profile_rewrites_legacy_thread_fields(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"include_threads":true,"keep_last":1}}}',
        encoding="utf-8",
    )

    update_profile(
        str(config_path),
        "nightly-dms",
        {"exclude_thread_states": []},
        [],
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["profiles"]["nightly-dms"] == {
        "exclude_thread_states": [],
        "keep_last": 1,
    }


def test_load_profile_rejects_combined_current_and_legacy_thread_fields(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"threads":"all","exclude_channel_types":[]}}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot combine current and legacy thread filters"):
        load_profile(str(config_path), "nightly-dms")


def test_profile_update_can_store_redact_sensitive_false(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"profiles":{"nightly-dms":{"keep_last":1}}}', encoding="utf-8")

    updates = parse_profile_set_assignments(["redact_sensitive=false"])
    update_profile(str(config_path), "nightly-dms", updates, [])

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["profiles"]["nightly-dms"]["redact_sensitive"] is False


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
    assert defaults["exclude_channel_types"] == []
    assert defaults["exclude_thread_states"] == []
    assert defaults["exclude_threads"] is False
    assert defaults["delete_owned_threads"] == "none"
    assert defaults["preserve_cache_path"].endswith("preserve_cache.json")


def test_parse_args_profile_applies_defaults(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"keep_last":25,"dry_run":true,"verbose":2,"delete_owned_threads":"self-only"}}}',
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
    assert args.delete_owned_threads == "self-only"


def test_parse_args_profile_applies_redact_names_to_redaction_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"redact_sensitive":"0,4","redact_names":false}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=["clean", "--config-path", str(config_path), "--profile", "nightly-dms"],
    )

    assert args.redact_names is False
    assert args.redact_sensitive.prefix == 0
    assert args.redact_sensitive.suffix == 4
    assert args.redact_sensitive.redact_names is False


def test_parse_args_cli_can_enable_profile_redact_names_default(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"redact_sensitive":"0,4","redact_names":false}}}',
        encoding="utf-8",
    )

    args = parse_args(
        "1.0.0",
        argv=["clean", "--config-path", str(config_path), "--profile", "nightly-dms", "--redact-names"],
    )

    assert args.redact_names is True
    assert args.redact_sensitive.redact_names is True


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


def test_parse_args_cli_can_reset_profile_scope_exclusions(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"exclude_channel_types":["GuildVoice"],"exclude_thread_states":["archived"],"exclude_threads":true}}}',
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
            "--exclude-channel-types",
            "--exclude-thread-states",
            "--no-exclude-threads",
        ],
    )

    assert args.exclude_channel_types == []
    assert args.exclude_thread_states == []
    assert args.exclude_threads is False


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


def test_effective_settings_preserve_scope_exclusions():
    args = parse_args(
        "1.0.0",
        argv=[
            "clean",
            "--exclude-channel-types",
            "GuildVoice",
            "PrivateThread",
            "--exclude-thread-states",
            "archived",
            "--exclude-threads",
        ],
    )

    settings = resolve_effective_clean_settings(args)

    assert settings.exclude_channel_types == ["GuildVoice", "PrivateThread"]
    assert settings.exclude_thread_states == ["archived"]
    assert settings.exclude_threads is True


def test_effective_settings_include_owned_thread_deletion_mode():
    args = parse_args(
        "1.0.0",
        argv=["clean", "--delete-owned-threads", "self-only"],
    )

    settings = resolve_effective_clean_settings(args)

    assert settings.delete_owned_threads == "self-only"


def test_effective_settings_include_request_interval_overrides():
    args = parse_args(
        "1.0.0",
        argv=[
            "clean",
            "--request-interval",
            "thread-search=1.1,1.3",
        ],
    )

    settings = resolve_effective_clean_settings(args)

    assert settings.request_intervals == {
        "thread-search": (1.1, 1.3),
    }


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
    assert Path(settings.preserve_cache_path).parts[-2:] == ("preserve-cache", "nightly-dms.json")


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
    assert Path(settings.preserve_cache_path).parts[-2:] == ("preserve-cache", "nightly-dms.dryrun.json")


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


def test_invalid_profile_with_json_string_true_emits_json_profile_errors(tmp_path, capsys):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"profiles":{"nightly-dms":{"json":"true","verbose":9}}}',
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
