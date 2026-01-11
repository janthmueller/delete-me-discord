# delete-me-discord discovery collection tests
import sys
from pathlib import Path

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.discovery import collect_channels, collect_guilds


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


def test_collect_guilds_sorted_and_filtered():
    api = FakeAPI(
        guilds=[
            {"id": "2", "name": "Beta"},
            {"id": "1", "name": "Alpha"},
        ],
        root_channels=[],
        guild_channels_map={},
    )
    guilds = collect_guilds(api, include_set=set(), exclude_set=set())
    assert [g["id"] for g in guilds] == ["1", "2"]

    guilds = collect_guilds(api, include_set=set(), exclude_set={"1"})
    assert [g["id"] for g in guilds] == ["2"]


def test_collect_channels_tree_structure_and_sorting():
    api = FakeAPI(
        guilds=[
            {"id": "1", "name": "Alpha"},
        ],
        root_channels=[
            {"id": "dm2", "type": 3, "recipients": [{"username": "Zoo"}]},
            {"id": "dm1", "type": 1, "recipients": [{"username": "Amy"}]},
            {"id": "skip", "type": 99, "recipients": [{"username": "Ignored"}]},
        ],
        guild_channels_map={
            "1": [
                {"id": "cat1", "type": 4, "name": "General"},
                {"id": "cat2", "type": 4, "name": "Projects"},
                {"id": "c2", "type": 0, "name": "beta", "parent_id": "cat1"},
                {"id": "c1", "type": 0, "name": "alpha", "parent_id": "cat1"},
                {"id": "c3", "type": 0, "name": "random", "parent_id": None},
            ],
        },
    )

    data = collect_channels(api, include_set=set(), exclude_set=set())
    assert [dm["id"] for dm in data["dms"]] == ["dm1", "dm2"]
    assert data["guilds"][0]["id"] == "1"

    categories = data["guilds"][0]["categories"]
    assert [c["name"] for c in categories] == ["(no category)", "General"]

    uncategorized = categories[0]
    assert [c["id"] for c in uncategorized["channels"]] == ["c3"]

    general = categories[1]
    assert [c["id"] for c in general["channels"]] == ["c1", "c2"]
