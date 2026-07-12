# Delete Me Discord
[![PyPI Latest Release](https://img.shields.io/pypi/v/delete-me-discord.svg)](https://pypi.org/project/delete-me-discord/)
[![Pepy Total Downloads](https://img.shields.io/pepy/dt/delete-me-discord)](https://pepy.tech/project/delete-me-discord)
[![GitHub License](https://img.shields.io/github/license/janthmueller/delete-me-discord)](https://github.com/janthmueller/delete-me-discord/blob/main/LICENSE)

**Delete Me Discord** is a CLI for deleting your own Discord messages and reactions with explicit filters and retention rules. The installed command is available as `delete-me-discord` and the shorter alias `dmd`.

> ⚠️ Using automated tools on Discord may violate Discord’s [Terms of Service](https://discord.com/terms). Use at your own risk.

Typical workflow:

- discover targets first
- preview with `dmd clean --include-ids <id> --dry-run`
- execute once the plan matches your intent

## Installation

Python installs require Python 3.10 or newer.

```bash
pipx install delete-me-discord
```

Other install paths:

- `pip install delete-me-discord`
- prebuilt binaries from the [latest release](https://github.com/janthmueller/delete-me-discord/releases/latest)
- `nix run github:janthmueller/delete-me-discord -- --help`

More install details: [Installation docs](https://janthmueller.github.io/delete-me-discord/getting-started/installation/)

## Quick Start

Authenticate once:

```bash
dmd login
```

Discover targets:

```bash
dmd list guilds
dmd list channels
```

All accessible active and archived threads are discovered by default. Exclude thread states or channel types when needed:

```bash
dmd list channels --exclude-thread-states archived
dmd list channels --exclude-channel-types PrivateThread
dmd list channels --exclude-threads
```

Inspect accepted filter values locally with `dmd list channel-types` and `dmd list thread-states`.

Preview the selected scope:

```bash
dmd clean --include-ids <id> --dry-run
```

Then rerun without `--dry-run` when the plan looks right.
`--include-ids` accepts one or more guild, category, supported channel, thread-parent, or thread IDs.
Omit `--include-ids` to use the default scope: all eligible channels the tool can see.

Discord's documented Reaction object includes total, normal, and Super Reaction counts plus separate ownership flags for the authenticated user. Dry-run validates those fields and reports the exact foreign normal and Super Reactions that would disappear with your deleted messages. It reports `unknown` instead of estimating when a required field is missing or inconsistent, or when an enclosing thread scan is incomplete.

Starting with v3, the default scope includes guild text and announcement channels, text chat in voice and stage channels, DMs, Group DMs, and all accessible announcement, public, and private threads. Use `--exclude-threads` when thread discovery is not needed. Use `--exclude-channel-types` for exact channel-type exclusions and `--exclude-thread-states` to omit active or archived threads. Thread discovery can make additional paginated Discord API requests for every thread-capable parent, so `dmd list channels` may take noticeably longer than before v3. In archived threads, the tool can delete your messages but skips reaction removal because Discord restricts archived-thread mutations.

Thread containers are never deleted by default. Creator-owned thread deletion is an explicit destructive option:

```bash
# Delete only after a complete scan finds no messages from other authors.
dmd clean --include-ids <thread-id> --delete-owned-threads self-only --dry-run

# Delete your thread even when it contains other users' messages.
dmd clean --include-ids <thread-id> --delete-owned-threads all --dry-run
```

Both modes require Discord's `MANAGE_THREADS` permission. Deleting a thread removes the entire shared conversation and overrides retention settings for that thread. `self-only` checks message authors at scan time, but can still remove other users' reactions and cannot eliminate the small race in which a new message arrives before deletion. Run a narrow dry-run first.

Alternative auth for one shell:

```bash
export DISCORD_TOKEN=<your_token>
```

If you need token extraction steps, use [this guide](https://github.com/victornpb/undiscord/wiki/authToken).

## Documentation

See the [documentation](https://janthmueller.github.io/delete-me-discord/) for installation, first-run guidance, rolling retention, and the full CLI reference.

## Contributing

Contributions are welcome. Open an issue or pull request with improvements or bug fixes.

## License

Licensed under the [MIT License](LICENSE).
