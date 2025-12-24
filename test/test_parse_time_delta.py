import argparse
from datetime import timedelta
import sys
from pathlib import Path

import pytest

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.utils import parse_time_delta


def test_legacy_parsing_all_units():
    result = parse_time_delta("weeks=1,days=2,hours=3,minutes=4,seconds=5")
    assert result == timedelta(weeks=1, days=2, hours=3, minutes=4, seconds=5)


def test_compact_parsing_all_units():
    result = parse_time_delta("2w3d4h5m6s")
    assert result == timedelta(weeks=2, days=3, hours=4, minutes=5, seconds=6)


def test_compact_parsing_with_spaces():
    assert parse_time_delta("2w 3d") == timedelta(weeks=2, days=3)


def test_zero_shortcut():
    assert parse_time_delta("0") == timedelta(0)
    assert parse_time_delta("0.0") == timedelta(0)


def test_legacy_duplicate_unit_rejected():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_time_delta("days=1,days=2")


def test_compact_duplicate_unit_rejected():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_time_delta("1d2d")


def test_negative_rejected_legacy_and_compact():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_time_delta("days=-1")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_time_delta("-1d")


def test_invalid_unit_rejected():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_time_delta("months=1")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_time_delta("1q")
