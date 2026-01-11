# delete-me-discord discovery run tests
import sys
from pathlib import Path

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import delete_me_discord.discovery as discovery


class FakeAPI:
    def get_guilds(self):
        return []

    def get_root_channels(self):
        return []

    def get_guild_channels(self, guild_id):
        return []


def test_run_discovery_commands_json_calls_json_renderers(monkeypatch):
    called = {"guilds": False, "channels": False}

    monkeypatch.setattr(discovery, "collect_guilds", lambda *_: [{"id": "1", "name": "G"}])
    monkeypatch.setattr(discovery, "collect_channels", lambda *_: {"dms": [], "guilds": []})
    monkeypatch.setattr(discovery, "render_guilds_json", lambda *_: called.__setitem__("guilds", True))
    monkeypatch.setattr(discovery, "render_channels_json", lambda *_: called.__setitem__("channels", True))

    discovery.run_discovery_commands(
        api=FakeAPI(),
        list_guilds=True,
        list_channels=True,
        include_ids=[],
        exclude_ids=[],
        json_output=True,
    )
    assert called["guilds"] is True
    assert called["channels"] is True


def test_run_discovery_commands_rich_calls_rich_renderers(monkeypatch):
    called = {"guilds": False, "channels": False}

    monkeypatch.setattr(discovery, "collect_guilds", lambda *_: [])
    monkeypatch.setattr(discovery, "collect_channels", lambda *_: {"dms": [], "guilds": []})
    monkeypatch.setattr(discovery, "render_guilds_rich", lambda *_: called.__setitem__("guilds", True))
    monkeypatch.setattr(discovery, "render_channels_rich", lambda *_: called.__setitem__("channels", True))

    discovery.run_discovery_commands(
        api=FakeAPI(),
        list_guilds=True,
        list_channels=True,
        include_ids=[],
        exclude_ids=[],
        json_output=False,
    )
    assert called["guilds"] is True
    assert called["channels"] is True
