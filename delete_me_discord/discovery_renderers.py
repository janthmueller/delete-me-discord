# delete_me_discord/discovery_renderers.py
import json
from typing import Dict, Any, List

from rich.console import Console
from rich.markup import escape
from rich.tree import Tree

from .privacy import sensitive


def render_guilds_json(guilds: List[Dict[str, Any]]) -> None:
    print(json.dumps({"guilds": _redact_discovery_data(guilds)}, ensure_ascii=True))


def render_channels_json(data: Dict[str, Any]) -> None:
    print(json.dumps(_redact_discovery_data(data), ensure_ascii=True))


def render_guilds_rich(guilds: List[Dict[str, Any]], console: Console) -> None:
    tree = Tree("[blue]Guilds[/]")
    for guild in guilds:
        guild_id = _redact_id(guild.get("id"))
        name = escape(_redact_name(guild.get("name", "Unknown")))
        tree.add(f"[bright_white]{name}[/] [dim](ID: {guild_id})[/]")
    if tree.children:
        console.print(tree)
    else:
        console.print("[dim]No guilds matched filters for this account.[/]")


def render_channels_rich(data: Dict[str, Any], console: Console) -> None:
    dms = data.get("dms") or []
    guilds = data.get("guilds") or []

    dm_tree = None
    if dms:
        dm_tree = Tree("[magenta]Direct and Group DMs[/]")
        for channel in dms:
            dm_tree.add(_channel_display(channel))

    guilds_tree = None
    for guild in guilds:
        if guilds_tree is None:
            guilds_tree = Tree("[blue]Guilds[/]")
        guild_id = _redact_id(guild.get("id"))
        guild_name = escape(_redact_name(guild.get("name", "Unknown")))
        guild_node = guilds_tree.add(f"[bright_white]{guild_name}[/] [dim](ID: {guild_id})[/]")
        for category in guild.get("categories", []):
            category_id = _redact_id(category.get("id")) if category.get("id") else "none"
            category_name = escape(_redact_name(category.get("name", "(no category)")))
            category_node = guild_node.add(f"[yellow]Category[/] {category_name} [dim](ID: {category_id})[/]")
            for channel in category.get("channels", []):
                category_node.add(_channel_display(channel))

    printed = False
    if dm_tree and dm_tree.children:
        console.print(dm_tree)
        printed = True
    if guilds_tree and guilds_tree.children:
        console.print(guilds_tree)
        printed = True
    if not printed:
        console.print("[dim]No channels matched filters for this account.[/]")


def _channel_display(channel: Dict[str, Any]) -> str:
    channel_type = channel.get("type", "Unknown")
    raw_name = channel.get("name", "Unknown")
    channel_name = escape(_redact_name(raw_name))
    type_color = "cyan"
    name_style = "bright_white"
    id_style = "dim"
    return f"[{type_color}]{channel_type}[/] [{name_style}]{channel_name}[/] [{id_style}](ID: {_redact_id(channel.get('id'))})[/]"


def _redact_discovery_data(value):
    if isinstance(value, list):
        return [_redact_discovery_data(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _redact_discovery_field(key, item)
            for key, item in value.items()
        }
    return value


def _redact_discovery_field(key: str, value):
    if key == "id":
        return _redact_id(value)
    if key == "name":
        return _redact_name(value)
    return _redact_discovery_data(value)


def _redact_id(value) -> str | None:
    if value is None:
        return None
    return str(sensitive(value))


def _redact_name(value) -> str:
    return str(sensitive(value, full=True))
