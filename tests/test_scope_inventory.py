import logging

from delete_me_discord.scope_filter import ScopeFilter
from delete_me_discord.scope_inventory import ScopeDiscoverySeed, ScopeInventory
from delete_me_discord.utils import ResourceUnavailable


class InventoryAPI:
    def __init__(self):
        self.logger = logging.getLogger("scope-inventory-test")
        self.guild_channel_calls = []

    def get_guilds(self):
        return [{"id": "g1"}, {"id": "g2"}]

    def get_root_channels(self):
        return [{"id": "dm", "type": 1}]

    def get_guild_channels(self, guild_id):
        self.guild_channel_calls.append(guild_id)
        return [{"id": f"channel-{guild_id}", "type": 0}]


def test_inventory_reuses_seed_and_fetches_only_safely_selected_guilds():
    api = InventoryAPI()
    seed = ScopeDiscoverySeed(
        guilds=({"id": "g1"}, {"id": "g2"}),
        root_channels=({"id": "dm", "type": 1},),
        guild_ids=frozenset({"g2"}),
    )

    inventory = ScopeInventory.fetch(
        api,
        scope_filter=ScopeFilter.without_threads(),
        seed=seed,
    )

    assert api.guild_channel_calls == ["g2"]
    assert inventory.guild_channels("g1") == []
    assert inventory.guild_channels("g2") == [{"id": "channel-g2", "type": 0}]
    assert inventory.root_channels == [{"id": "dm", "type": 1}]


def test_inventory_skips_one_unavailable_guild_without_losing_others():
    class API(InventoryAPI):
        def get_guild_channels(self, guild_id):
            if guild_id == "g1":
                raise ResourceUnavailable("no access")
            return super().get_guild_channels(guild_id)

    inventory = ScopeInventory.fetch(
        API(),
        scope_filter=ScopeFilter.without_threads(),
    )

    assert inventory.guild_channels("g1") == []
    assert inventory.guild_channels("g2")
