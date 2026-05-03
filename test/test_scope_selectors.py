import sys
from pathlib import Path

import pytest

# Ensure project root is importable when running tests without installation.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from delete_me_discord.scope_inventory import ScopeInventory
from delete_me_discord.scope_selectors import ScopeSelectorResolver, ScopeTarget, discover_scope_targets, resolve_scope_selectors
from delete_me_discord.utils import ResourceUnavailable


class FakeAPI:
    def __init__(self):
        self.guilds = [
            {"id": "111111111111111111", "name": "Alpha"},
            {"id": "222222222222222222", "name": "Beta"},
        ]
        self.root_channels = [
            {"id": "333333333333333333", "type": 1, "recipients": [{"username": "Amy"}]},
            {"id": "444444444444444444", "type": 3, "name": "Group"},
        ]
        self.guild_channels = {
            "111111111111111111": [
                {"id": "555555555555555555", "type": 4, "name": "General"},
                {"id": "555555555555555556", "type": 4, "name": "Voice"},
                {"id": "666666666666666666", "type": 0, "name": "chat", "parent_id": "555555555555555555"},
            ],
            "222222222222222222": [
                {"id": "777777777777777777", "type": 0, "name": "other"},
                {"id": "888888888888888888", "type": 2, "name": "voice"},
            ],
        }

    def get_guilds(self):
        return self.guilds

    def get_root_channels(self):
        return self.root_channels

    def get_guild_channels(self, guild_id):
        return self.guild_channels.get(guild_id, [])


def test_discover_scope_targets_collects_supported_target_ids():
    targets = discover_scope_targets(ScopeInventory.fetch(FakeAPI()))
    by_id = {target.id: target for target in targets}

    assert by_id["111111111111111111"].kind == "Guild"
    assert by_id["333333333333333333"].kind == "DM"
    assert by_id["444444444444444444"].kind == "GroupDM"
    assert by_id["555555555555555555"].kind == "Category"
    assert "555555555555555556" not in by_id
    assert by_id["666666666666666666"].kind == "GuildText"
    assert "888888888888888888" not in by_id


def test_scope_inventory_skips_unavailable_guild_channels():
    class API(FakeAPI):
        def __init__(self):
            super().__init__()

            class Logger:
                def warning(self, *args, **kwargs):
                    pass

            self.logger = Logger()

        def get_guild_channels(self, guild_id):
            if guild_id == "111111111111111111":
                raise ResourceUnavailable("no access")
            return super().get_guild_channels(guild_id)

    inventory = ScopeInventory.fetch(API())

    assert inventory.guild_channels("111111111111111111") == []
    assert inventory.guild_channels("222222222222222222")


def test_resolver_accepts_exact_full_id():
    resolver = ScopeSelectorResolver([ScopeTarget("123456789012345678", "GuildText", "chat")])

    assert resolver.resolve("123456789012345678") == "123456789012345678"


def test_resolver_accepts_unique_suffix_of_any_length():
    resolver = ScopeSelectorResolver([
        ScopeTarget("123456789012345678", "GuildText", "chat"),
        ScopeTarget("123456789012345679", "GuildText", "other"),
    ])

    assert resolver.resolve("8") == "123456789012345678"


def test_resolver_rejects_missing_selector():
    resolver = ScopeSelectorResolver([ScopeTarget("123456789012345678", "GuildText", "chat")])

    with pytest.raises(ValueError, match="did not match"):
        resolver.resolve("9")


def test_resolver_rejects_ambiguous_selector():
    resolver = ScopeSelectorResolver([
        ScopeTarget("123456789012345678", "GuildText", "chat"),
        ScopeTarget("223456789012345678", "GroupDM", "group"),
    ])

    with pytest.raises(ValueError) as exc:
        resolver.resolve("5678")

    assert "Could not resolve ID selector '5678' uniquely" in str(exc.value)
    assert "no action was taken" in str(exc.value)
    assert "GuildText" in str(exc.value)
    assert "GroupDM" in str(exc.value)


def test_resolve_scope_selectors_returns_full_ids_and_rejects_resolved_overlap():
    inventory = ScopeInventory.fetch(FakeAPI())

    include_ids, exclude_ids = resolve_scope_selectors(inventory, ["6666"], ["7777"])
    assert include_ids == ["666666666666666666"]
    assert exclude_ids == ["777777777777777777"]

    with pytest.raises(ValueError, match="disjoint"):
        resolve_scope_selectors(inventory, ["6666"], ["666666666666666666"])
