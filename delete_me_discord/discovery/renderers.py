import json
from typing import Dict, Any, List

from rich.console import Console
from rich.markup import escape
from rich.tree import Tree

from ..privacy import sensitive, sensitive_name


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
            _render_category_channels(category_node, category.get("channels", []))

    printed = False
    if dm_tree and dm_tree.children:
        console.print(dm_tree)
        printed = True
    if guilds_tree and guilds_tree.children:
        console.print(guilds_tree)
        printed = True
    if not printed:
        console.print("[dim]No channels matched filters for this account.[/]")


def _render_category_channels(category_node: Tree, channels: List[Dict[str, Any]]) -> None:
    parents = [channel for channel in channels if not _is_thread_entry(channel)]
    parent_ids = {
        str(channel["id"])
        for channel in parents
        if channel.get("id") is not None
    }
    threads_by_parent: dict[str, list[Dict[str, Any]]] = {}
    orphan_threads = []
    for channel in channels:
        if not _is_thread_entry(channel):
            continue
        parent_id = channel.get("parent_id")
        parent_key = str(parent_id) if parent_id is not None else None
        if parent_key is not None and parent_key in parent_ids:
            threads_by_parent.setdefault(parent_key, []).append(channel)
        else:
            orphan_threads.append(channel)

    for channel in parents:
        channel_id = channel.get("id")
        children = threads_by_parent.get(str(channel_id), []) if channel_id is not None else []
        if not children:
            category_node.add(_channel_display(channel))
            continue
        parent_node = category_node.add(_channel_display(channel))
        for thread in children:
            parent_node.add(_channel_display(thread, show_parent=False))

    for thread in orphan_threads:
        category_node.add(_channel_display(thread))


def _is_thread_entry(channel: Dict[str, Any]) -> bool:
    return "archived" in channel and "parent_id" in channel


def _channel_display(channel: Dict[str, Any], *, show_parent: bool = True) -> str:
    channel_type = channel.get("type", "Unknown")
    raw_name = channel.get("name", "Unknown")
    channel_name = escape(_redact_name(raw_name))
    type_color = "cyan"
    name_style = "bright_white"
    id_style = "dim"
    context = ""
    state = None
    if "archived" in channel:
        state = "archived" if channel.get("archived") else "active"
        context = f" [dim]({state})[/]"
    if show_parent and channel.get("parent_name"):
        parent_name = escape(_redact_name(channel["parent_name"]))
        thread_state = state or "unknown state"
        context = f" [dim](parent: {parent_name}, {thread_state})[/]"
    return (
        f"[{type_color}]{channel_type}[/] [{name_style}]{channel_name}[/]"
        f"{context} [{id_style}](ID: {_redact_id(channel.get('id'))})[/]"
    )


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
    if key == "id" or key.endswith("_id"):
        return _redact_id(value)
    if key in {"name", "parent_name"}:
        return _redact_name(value)
    return _redact_discovery_data(value)


def _redact_id(value) -> str | None:
    if value is None:
        return None
    return str(sensitive(value))


def _redact_name(value) -> str:
    return str(sensitive_name(value))
