# delete_me_discord/discovery.py
import logging
from rich.console import Console
from rich.markup import escape
from rich.tree import Tree

from .api import DiscordAPI, FetchError
from .utils import should_include_channel


def _guild_sort_key(guild):
    return ((guild.get("name") or "").lower(), guild.get("id"))


def run_discovery_commands(
    api: DiscordAPI,
    list_guilds: bool,
    list_channels: bool,
    include_ids,
    exclude_ids
) -> None:
    """
    Handle discovery-only commands and exit afterwards.
    """
    console = Console()
    include_set = set(include_ids or [])
    exclude_set = set(exclude_ids or [])

    if list_guilds:
        try:
            guilds = api.get_guilds()
        except FetchError as e:
            logging.error("Unable to list guilds: %s", e)
            return
        tree = Tree("[blue]Guilds[/]")
        for guild in sorted(guilds, key=_guild_sort_key):
            guild_id = guild.get("id")
            if guild_id in exclude_set:
                continue
            if include_set and guild_id not in include_set:
                continue
            tree.add(f"[bright_white]{escape(guild.get('name', 'Unknown'))}[/] [dim](ID: {guild_id})[/]")
        if tree.children:
            console.print(tree)
        else:
            console.print("[dim]No guilds matched filters for this account.[/]")

    if list_channels:
        _list_channels(api, include_set, exclude_set, console)


def _list_channels(api: DiscordAPI, include_set, exclude_set, console: Console) -> None:
    """
    List channels grouped by DMs and guilds, respecting include/exclude filters.
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

    def channel_display(channel):
        channel_type = channel_types.get(channel.get("type"), f"Type {channel.get('type')}")
        raw_name = channel.get("name") or ', '.join(
            [recipient.get("username", "Unknown") for recipient in channel.get("recipients", [])]
        )
        channel_name = escape(raw_name)
        type_color = "cyan"
        name_style = "bright_white"
        id_style = "dim"
        return f"[{type_color}]{channel_type}[/] [{name_style}]{channel_name}[/] [{id_style}](ID: {channel.get('id')})[/]"

    dm_tree = None
    try:
        root_channels = api.get_root_channels()
    except FetchError as e:
        logging.error("Unable to list DM/Group DM channels: %s", e)
        root_channels = []

    included_dms = []
    for channel in root_channels:
        if channel.get("type") not in channel_types:
            continue
        if not include_channel(channel):
            continue
        included_dms.append(channel)

    if included_dms:
        dm_tree = Tree("[magenta]Direct and Group DMs[/]")
        for channel in sorted(included_dms, key=channel_sort_key):
            dm_tree.add(channel_display(channel))

    # Guild channels
    try:
        guilds = api.get_guilds()
    except FetchError as e:
        logging.error("Unable to list guild channels: %s", e)
        return

    guilds_tree = None
    for guild in sorted(guilds, key=_guild_sort_key):
        guild_id = guild.get("id")
        guild_name = guild.get("name", "Unknown")
        escaped_guild_name = escape(guild_name)

        try:
            channels = api.get_guild_channels(guild_id)
        except FetchError as e:
            logging.error("  Failed to fetch channels for guild %s: %s", guild_id, e)
            continue

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

        if guilds_tree is None:
            guilds_tree = Tree("[blue]Guilds[/]")

        guild_node = guilds_tree.add(f"[bright_white]{escaped_guild_name}[/] [dim](ID: {guild_id})[/]")
        grouped = {}
        for channel in filtered_channels:
            grouped.setdefault(channel.get("parent_id"), []).append(channel)

        def category_label(parent_id):
            return category_names.get(parent_id, "(no category)")

        for parent_id, chans in sorted(grouped.items(), key=lambda item: (category_label(item[0]).lower(), item[0] or "")):
            parent_label = category_label(parent_id)
            category_node = guild_node.add(f"[yellow]Category[/] {escape(parent_label)} [dim](ID: {parent_id or 'none'})[/]")
            for channel in sorted(chans, key=channel_sort_key):
                category_node.add(channel_display(channel))

    printed = False
    if dm_tree and dm_tree.children:
        console.print(dm_tree)
        printed = True
    if guilds_tree and guilds_tree.children:
        console.print(guilds_tree)
        printed = True
    if not printed:
        console.print("[dim]No channels matched filters for this account.[/]")
