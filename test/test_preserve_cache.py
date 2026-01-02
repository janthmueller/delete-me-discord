import json
import os
import sys
from pathlib import Path

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.preserve_cache import PreserveCache


def test_preserve_cache_loads_and_saves_ids(tmp_path):
    cache_path = tmp_path / "preserve_cache.json"
    cache = PreserveCache(path=str(cache_path))

    # Initially empty
    assert cache.get_ids("123") == []

    # Set and save
    cache.set_ids("123", ["a", "b", "c"])
    cache.save()
    assert cache_path.exists()

    # Reload from disk and verify
    cache2 = PreserveCache(path=str(cache_path))
    assert cache2.get_ids("123") == ["a", "b", "c"]


def test_preserve_cache_dedupes_and_preserves_order(tmp_path):
    cache_path = tmp_path / "preserve_cache.json"
    cache = PreserveCache(path=str(cache_path))
    cache.set_ids("chan", ["1", "2", "2", "3", "1"])
    cache.save()

    cache2 = PreserveCache(path=str(cache_path))
    assert cache2.get_ids("chan") == ["1", "2", "3"]


def test_preserve_cache_respects_custom_path(tmp_path):
    cache_path = tmp_path / "custom_cache.json"
    cache = PreserveCache(path=str(cache_path))
    cache.set_ids("chan", ["x"])
    cache.save()

    assert os.path.exists(cache_path)

    with open(cache_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["schema_version"] == cache.SCHEMA_VERSION
    assert raw["channels"]["chan"] == ["x"]
