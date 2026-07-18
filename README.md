# Delete Me Discord
[![PyPI Latest Release](https://img.shields.io/pypi/v/delete-me-discord.svg)](https://pypi.org/project/delete-me-discord/)
[![Pepy Total Downloads](https://img.shields.io/pepy/dt/delete-me-discord)](https://pepy.tech/project/delete-me-discord)
[![GitHub License](https://img.shields.io/github/license/janthmueller/delete-me-discord)](https://github.com/janthmueller/delete-me-discord/blob/main/LICENSE)

**Delete Me Discord** is a CLI for deleting your own Discord messages and reactions with explicit filters and retention rules. The installed command is available as `delete-me-discord` and the shorter alias `dmd`.

> ⚠️ Using automated tools on Discord may violate Discord’s [Terms of Service](https://discord.com/terms). Use at your own risk.

Typical workflow:

- discover targets first
- preview with `dmd clean --include <id> --dry-run`
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

All accessible active and archived threads are discovered and cleaned by default. Exclude thread states or channel types when needed:

```bash
dmd list channels -x archived
dmd list channels -x PrivateThread
dmd list channels -x threads
```

Inspect accepted filter values locally with `dmd list channel-types` and `dmd list thread-states`.

Preview the selected scope:

```bash
dmd clean --include <id> --dry-run
```

Then rerun without `--dry-run` when the plan looks right.
`--include` and `--exclude` accept complete Discord IDs, canonical channel
types from `dmd list channel-types`, `threads`, `active`, and `archived`.
Partial ID suffixes are not accepted in v3. Omit `--include` to use the default
scope: all eligible channels the tool can see.

```bash
dmd clean -i active --dry-run
dmd clean -i <category-id> -x GuildVoice GuildStageVoice --dry-run
dmd clean -x threads -i <thread-id> --dry-run
dmd clean -x threads -i PublicThread --dry-run
```

Selector conflicts are order-independent. Exact IDs are more specific than
concrete channel types, and concrete thread types are more specific than the
`threads` group. Exclusion wins when two selectors have the same specificity.
Thread states (`active` and `archived`) independently narrow the selected
thread types.

Discord's documented Reaction object includes total, normal, and Super Reaction counts plus separate ownership flags for the authenticated user. Dry-run validates those fields and reports the exact foreign normal and Super Reactions that would disappear with your deleted messages. It reports `unknown` instead of estimating when a required field is missing or inconsistent, or when an enclosing thread scan is incomplete.

Starting with v3, the default scope includes guild text and announcement channels, text chat in voice and stage channels, DMs, Group DMs, and accessible active and archived announcement, public, and private threads. Use `-x archived` for active threads only or `-x threads` when thread discovery is not needed. Thread discovery can make additional paginated Discord API requests for every thread-capable parent, so a complete run may take noticeably longer than before v3.

Archived threads are evaluated while archived and activated only when their cleanup plan contains work. DMD always attempts to restore the archived state afterward. By default it can continue when restoration rights cannot be established; use `--skip-unrestorable-threads` to require a reasonable restoration guarantee before cleanup.

If an active thread reaches its Discord auto-archive deadline during a long cleanup, DMD refreshes its state and retries that blocked action once for the detected archive event when the duration, lock state, pin state, and latest known activity indicate a likely automatic archive. A later complete auto-archive interval can be handled again; an immediate second failure, ambiguous state, or externally changed state stops the remaining actions for that thread.

`dmd clean` discovers and processes one guild and one thread parent at a time, including when exact include/exclude IDs are supplied. Explicit IDs are preflighted through the current guild/DM lists or an exact channel lookup before any mutation, and those top-level results are reused during traversal. Each parent's thread list is completed before that parent is mutated, then the parent and its threads are processed before discovery advances. `dmd list channels` remains eager because rendering the full tree requires the full inventory.

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
