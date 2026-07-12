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
from delete_me_discord.privacy import RedactionConfig, set_redaction_config


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


def test_render_guilds_json_redacts_names_and_ids(capsys):
    set_redaction_config(RedactionConfig(enabled=True, prefix=0, suffix=4))
    try:
        render_guilds_json([{"id": "123456789012345678", "name": "Private Guild"}])
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["guilds"][0]["id"] == "***5678"
        assert payload["guilds"][0]["name"] == "***"
    finally:
        set_redaction_config(RedactionConfig())


def test_render_channels_json_redacts_nested_names_and_ids(capsys):
    set_redaction_config(RedactionConfig(enabled=True, prefix=0, suffix=4))
    try:
        data = {
            "dms": [{"id": "111111111111111111", "name": "Amy", "type": "DM"}],
            "guilds": [
                {
                    "id": "222222222222222222",
                    "name": "Alpha",
                    "categories": [
                        {
                            "id": "333333333333333333",
                            "name": "Secrets",
                            "channels": [{
                                "id": "444444444444444444",
                                "name": "thread",
                                "type": "PublicThread",
                                "parent_id": "555555555555555555",
                                "parent_name": "general",
                            }],
                        }
                    ],
                }
            ],
        }
        render_channels_json(data)
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["dms"][0]["id"] == "***1111"
        assert payload["dms"][0]["name"] == "***"
        assert payload["guilds"][0]["id"] == "***2222"
        assert payload["guilds"][0]["name"] == "***"
        assert payload["guilds"][0]["categories"][0]["id"] == "***3333"
        assert payload["guilds"][0]["categories"][0]["name"] == "***"
        assert payload["guilds"][0]["categories"][0]["channels"][0]["id"] == "***4444"
        assert payload["guilds"][0]["categories"][0]["channels"][0]["name"] == "***"
        assert payload["guilds"][0]["categories"][0]["channels"][0]["parent_id"] == "***5555"
        assert payload["guilds"][0]["categories"][0]["channels"][0]["parent_name"] == "***"
    finally:
        set_redaction_config(RedactionConfig())


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


def test_render_channels_rich_nests_threads_under_visible_parent():
    console = Console(record=True, width=140)
    data = {
        "dms": [],
        "guilds": [
            {
                "id": "g1",
                "name": "Guild",
                "categories": [
                    {
                        "id": "cat",
                        "name": "Category",
                        "channels": [
                            {"id": "text", "name": "Text", "type": "GuildText"},
                            {
                                "id": "thread",
                                "name": "Nested thread",
                                "type": "PublicThread",
                                "parent_id": "text",
                                "parent_name": "Text",
                                "archived": False,
                            },
                        ],
                    }
                ],
            }
        ],
    }

    render_channels_rich(data, console)
    lines = console.export_text().splitlines()

    parent_line = next(line for line in lines if "GuildText Text" in line)
    thread_line = next(line for line in lines if "PublicThread Nested thread" in line)
    assert parent_line.index("GuildText") < thread_line.index("PublicThread")
    assert "(active)" in thread_line
    assert "parent:" not in thread_line


def test_render_channels_rich_keeps_orphan_thread_at_category_level():
    console = Console(record=True, width=140)
    data = {
        "dms": [],
        "guilds": [
            {
                "id": "g1",
                "name": "Guild",
                "categories": [
                    {
                        "id": "cat",
                        "name": "Category",
                        "channels": [
                            {
                                "id": "thread",
                                "name": "Orphan thread",
                                "type": "PrivateThread",
                                "parent_id": "missing",
                                "parent_name": "Missing parent",
                                "archived": True,
                            }
                        ],
                    }
                ],
            }
        ],
    }

    render_channels_rich(data, console)
    text = console.export_text()

    assert "PrivateThread Orphan thread" in text
    assert "parent: Missing parent, archived" in text


def test_render_channels_rich_redacts_names_and_ids():
    set_redaction_config(RedactionConfig(enabled=True, prefix=0, suffix=4))
    try:
        console = Console(record=True)
        data = {
            "dms": [{"id": "111111111111111111", "name": "Amy", "type": "DM"}],
            "guilds": [
                {
                    "id": "222222222222222222",
                    "name": "Alpha",
                    "categories": [
                        {
                            "id": "333333333333333333",
                            "name": "Secrets",
                            "channels": [{"id": "444444444444444444", "name": "general", "type": "GuildText"}],
                        }
                    ],
                }
            ],
        }
        render_channels_rich(data, console)
        text = console.export_text()
        assert "Amy" not in text
        assert "Alpha" not in text
        assert "Secrets" not in text
        assert "general" not in text
        assert "111111111111111111" not in text
        assert "***1111" in text
        assert "***2222" in text
        assert "***3333" in text
        assert "***4444" in text
    finally:
        set_redaction_config(RedactionConfig())


def test_render_channels_rich_can_show_names_while_redacting_ids():
    set_redaction_config(RedactionConfig(enabled=True, prefix=0, suffix=4, redact_names=False))
    try:
        console = Console(record=True)
        data = {
            "dms": [{"id": "111111111111111111", "name": "Amy", "type": "DM"}],
            "guilds": [
                {
                    "id": "222222222222222222",
                    "name": "Alpha",
                    "categories": [
                        {
                            "id": "333333333333333333",
                            "name": "Secrets",
                            "channels": [{"id": "444444444444444444", "name": "general", "type": "GuildText"}],
                        }
                    ],
                }
            ],
        }
        render_channels_rich(data, console)
        text = console.export_text()
        assert "Amy" in text
        assert "Alpha" in text
        assert "Secrets" in text
        assert "general" in text
        assert "111111111111111111" not in text
        assert "***1111" in text
        assert "***2222" in text
        assert "***3333" in text
        assert "***4444" in text
    finally:
        set_redaction_config(RedactionConfig())


def test_render_channels_json_can_show_names_while_redacting_ids(capsys):
    set_redaction_config(RedactionConfig(enabled=True, prefix=0, suffix=4, redact_names=False))
    try:
        data = {
            "dms": [{"id": "111111111111111111", "name": "Amy", "type": "DM"}],
            "guilds": [{"id": "222222222222222222", "name": "Alpha", "categories": []}],
        }
        render_channels_json(data)
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["dms"][0]["id"] == "***1111"
        assert payload["dms"][0]["name"] == "Amy"
        assert payload["guilds"][0]["id"] == "***2222"
        assert payload["guilds"][0]["name"] == "Alpha"
    finally:
        set_redaction_config(RedactionConfig())


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
