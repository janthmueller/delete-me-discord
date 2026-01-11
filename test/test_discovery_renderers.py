# delete-me-discord discovery renderer tests
import json
import sys
from pathlib import Path

from rich.console import Console

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.discovery_renderers import (
    render_channels_json,
    render_guilds_json,
    render_channels_rich,
    render_guilds_rich,
)


def test_render_guilds_json_outputs_valid_json(capsys):
    render_guilds_json([{"id": "1", "name": "Alpha"}])
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["guilds"][0]["id"] == "1"


def test_render_channels_json_outputs_valid_json(capsys):
    data = {
        "dms": [{"id": "dm1", "name": "Amy", "type": "DM"}],
        "guilds": [{"id": "1", "name": "Alpha", "categories": []}],
    }
    render_channels_json(data)
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["dms"][0]["id"] == "dm1"


def test_render_channels_rich_outputs_text():
    console = Console(record=True)
    data = {
        "dms": [{"id": "dm1", "name": "Amy", "type": "DM"}],
        "guilds": [
            {
                "id": "1",
                "name": "Alpha",
                "categories": [
                    {
                        "id": None,
                        "name": "(no category)",
                        "channels": [{"id": "c1", "name": "general", "type": "GuildText"}],
                    }
                ],
            }
        ],
    }
    render_channels_rich(data, console)
    text = console.export_text()
    assert "Alpha" in text
    assert "general" in text


def test_render_guilds_rich_empty_outputs_notice():
    console = Console(record=True)
    render_guilds_rich([], console)
    text = console.export_text()
    assert "No guilds matched filters for this account." in text


def test_render_channels_rich_empty_outputs_notice():
    console = Console(record=True)
    render_channels_rich({"dms": [], "guilds": []}, console)
    text = console.export_text()
    assert "No channels matched filters for this account." in text
