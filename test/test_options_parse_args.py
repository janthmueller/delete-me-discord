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


def test_parse_args_defaults():
    args = parse_args("1.0.0", argv=[])
    assert args.include_ids == []
    assert args.exclude_ids == []
    assert args.dry_run is False
    assert args.log_level == "INFO"
    assert args.max_retries == 5
    assert args.retry_time_buffer == [25, 35]
    assert args.fetch_sleep_time == [0.2, 0.4]
    assert args.delete_sleep_time == [1.5, 2]
    assert args.preserve_n == 12
    assert args.preserve_n_mode == "mine"
    assert args.preserve_last == timedelta(weeks=2)
    assert args.fetch_max_age is None
    assert args.max_messages is None
    assert args.delete_reactions is False
    assert args.list_guilds is False
    assert args.list_channels is False
    assert args.preserve_cache is False
    assert args.wipe_preserve_cache is False
    assert args.json is False


def test_parse_args_json_flag():
    args = parse_args("1.0.0", argv=["--json"])
    assert args.json is True


def test_parse_args_json_error_output(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args("1.0.0", argv=["--json", "--nope"])
    assert exc.value.code == 2
    out = capsys.readouterr().out.strip()
    assert '"type": "argument_error"' in out


def test_parse_args_non_json_error_output(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args("1.0.0", argv=["--nope"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "unrecognized arguments" in err
