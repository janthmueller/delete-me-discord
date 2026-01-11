# delete_me_discord/discovery.py
from typing import Dict, Any, List
from rich.console import Console

from .api import DiscordAPI
from .utils import should_include_channel
from .discovery_renderers import (
    render_guilds_json,
    render_guilds_rich,
    render_channels_json,
    render_channels_rich,
)


def _guild_sort_key(guild):
    return ((guild.get("name") or "").lower(), guild.get("id"))


def run_discovery_commands(
    api: DiscordAPI,
    list_guilds: bool,
    list_channels: bool,
    include_ids,
    exclude_ids,
    json_output: bool = False,
) -> None:
    """
    Handle discovery-only commands and exit afterwards.
    """
    include_set = set(include_ids or [])
    exclude_set = set(exclude_ids or [])
    console = None
    if not json_output:
        console = Console()

    if list_guilds:
        guilds = collect_guilds(api, include_set, exclude_set)
        if json_output:
            render_guilds_json(guilds)
        else:
            render_guilds_rich(guilds, console)

    if list_channels:
        data = collect_channels(api, include_set, exclude_set)
        if json_output:
            render_channels_json(data)
        else:
            render_channels_rich(data, console)


def collect_guilds(
    api: DiscordAPI,
    include_set,
    exclude_set
) -> List[Dict[str, Any]]:
    """
    Collect guilds respecting include/exclude filters.
    """
    guilds = api.get_guilds()
    items = []
    for guild in sorted(guilds, key=_guild_sort_key):
        guild_id = guild.get("id")
        if guild_id in exclude_set:
            continue
        if include_set and guild_id not in include_set:
            continue
        items.append({
            "id": guild_id,
            "name": guild.get("name", "Unknown"),
        })
    return items


def collect_channels(
    api: DiscordAPI,
    include_set,
    exclude_set
) -> Dict[str, Any]:
    """
    Collect channels grouped by DMs and guilds, respecting include/exclude filters.
    """
    channel_types = {0: "GuildText", 1: "DM", 3: "GroupDM"}

    def include_channel(channel):
        return should_include_channel(
            channel=channel,
            include_ids=include_set,
            exclude_ids=exclude_set
        )

    def channel_sort_key(channel):
        type_order = {0: 0, 1: 1, 3: 2}  # GuildText, DM, GroupDM
        name = channel.get("name")
        if not name:
            recipients = channel.get("recipients") or []
            name = ', '.join([recipient.get("username", "Unknown") for recipient in recipients])
        return (type_order.get(channel.get("type"), 99), name.lower(), channel.get("id"))

    root_channels = api.get_root_channels()
    included_dms = []
    for channel in root_channels:
        if channel.get("type") not in channel_types:
            continue
        if not include_channel(channel):
            continue
        included_dms.append(channel)

    dms = []
    for channel in sorted(included_dms, key=channel_sort_key):
        dms.append({
            "id": channel.get("id"),
            "name": channel.get("name") or ', '.join(
                [recipient.get("username", "Unknown") for recipient in channel.get("recipients", [])]
            ),
            "type": channel_types.get(channel.get("type"), f"Type {channel.get('type')}"),
        })

    guilds = api.get_guilds()
    json_guilds = []
    for guild in sorted(guilds, key=_guild_sort_key):
        guild_id = guild.get("id")
        guild_name = guild.get("name", "Unknown")

        channels = api.get_guild_channels(guild_id)

        category_names = {
            c.get("id"): c.get("name") or "Unknown category"
            for c in channels
            if c.get("type") == 4  # Category
        }

        filtered_channels = []
        for channel in channels:
            if channel.get("type") not in channel_types:
                continue
            if not include_channel(channel):
                continue
            filtered_channels.append(channel)

        if not filtered_channels:
            continue

        grouped = {}
        for channel in filtered_channels:
            grouped.setdefault(channel.get("parent_id"), []).append(channel)

        categories = []

        def category_label(parent_id):
            return category_names.get(parent_id, "(no category)")

        for parent_id, chans in sorted(grouped.items(), key=lambda item: (category_label(item[0]).lower(), item[0] or "")):
            entries = []
            for channel in sorted(chans, key=channel_sort_key):
                entries.append({
                    "id": channel.get("id"),
                    "name": channel.get("name") or ', '.join(
                        [recipient.get("username", "Unknown") for recipient in channel.get("recipients", [])]
                    ),
                    "type": channel_types.get(channel.get("type"), f"Type {channel.get('type')}"),
                })
            categories.append({
                "id": parent_id,
                "name": category_label(parent_id),
                "channels": entries,
            })
        json_guilds.append({
            "id": guild_id,
            "name": guild_name,
            "categories": categories,
        })

    return {"dms": dms, "guilds": json_guilds}
