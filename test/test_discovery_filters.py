# delete-me-discord discovery filtering tests
import sys
from pathlib import Path

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.discovery import collect_channels


class FakeAPI:
    def __init__(self, guilds, root_channels, guild_channels_map):
        self._guilds = guilds
        self._root_channels = root_channels
        self._guild_channels_map = guild_channels_map

    def get_guilds(self):
        return self._guilds

    def get_root_channels(self):
        return self._root_channels

    def get_guild_channels(self, guild_id):
        return self._guild_channels_map.get(guild_id, [])


def test_collect_channels_include_parent_overrides_guild_exclude():
    api = FakeAPI(
        guilds=[{"id": "g1", "name": "Alpha"}],
        root_channels=[],
        guild_channels_map={
            "g1": [
                {"id": "cat1", "type": 4, "name": "General"},
                {"id": "c1", "type": 0, "name": "keep", "parent_id": "cat1", "guild_id": "g1"},
                {"id": "c2", "type": 0, "name": "drop", "parent_id": None, "guild_id": "g1"},
            ],
        },
    )

    data = collect_channels(api, include_set={"cat1"}, exclude_set={"g1"})
    categories = data["guilds"][0]["categories"]
    assert [c["id"] for c in categories[0]["channels"]] == ["c1"]


def test_collect_channels_include_channel_overrides_guild_exclude():
    api = FakeAPI(
        guilds=[{"id": "g1", "name": "Alpha"}],
        root_channels=[],
        guild_channels_map={
            "g1": [
                {"id": "c1", "type": 0, "name": "keep", "parent_id": None, "guild_id": "g1"},
                {"id": "c2", "type": 0, "name": "drop", "parent_id": None, "guild_id": "g1"},
            ],
        },
    )

    data = collect_channels(api, include_set={"c1"}, exclude_set={"g1"})
    categories = data["guilds"][0]["categories"]
    assert [c["id"] for c in categories[0]["channels"]] == ["c1"]
