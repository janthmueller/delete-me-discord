# delete_me_discord/discovery_renderers.py
import json
from typing import Dict, Any, List

from rich.console import Console
from rich.markup import escape
from rich.tree import Tree


def render_guilds_json(guilds: List[Dict[str, Any]]) -> None:
    print(json.dumps({"guilds": guilds}, ensure_ascii=True))


def render_channels_json(data: Dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=True))


def render_guilds_rich(guilds: List[Dict[str, Any]], console: Console) -> None:
    tree = Tree("[blue]Guilds[/]")
    for guild in guilds:
        guild_id = guild.get("id")
        name = escape(guild.get("name", "Unknown"))
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
        guild_id = guild.get("id")
        guild_name = escape(guild.get("name", "Unknown"))
        guild_node = guilds_tree.add(f"[bright_white]{guild_name}[/] [dim](ID: {guild_id})[/]")
        for category in guild.get("categories", []):
            category_id = category.get("id") or "none"
            category_name = escape(category.get("name", "(no category)"))
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
    channel_name = escape(raw_name)
    type_color = "cyan"
    name_style = "bright_white"
    id_style = "dim"
    return f"[{type_color}]{channel_type}[/] [{name_style}]{channel_name}[/] [{id_style}](ID: {channel.get('id')})[/]"
