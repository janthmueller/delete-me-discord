# Delete Me Discord
[![PyPI Latest Release](https://img.shields.io/pypi/v/delete-me-discord.svg)](https://pypi.org/project/delete-me-discord/)
[![Pepy Total Downloads](https://img.shields.io/pepy/dt/delete-me-discord)](https://pepy.tech/project/delete-me-discord)
[![GitHub License](https://img.shields.io/github/license/janthmueller/delete-me-discord)](https://github.com/janthmueller/delete-me-discord/blob/main/LICENSE)

**Delete Me Discord** is a command-line tool to delete your own Discord messages (and reactions) with filters and retention controls.

> ⚠️ Using automated tools on Discord may violate Discord’s [Terms of Service](https://discord.com/terms). Use at your own risk.

**TL;DR**
- Deletes your messages/reactions in bulk.
- Filters by time/count and include/exclude (channels, categories/parents*, guilds).
- Dry-run + discovery helpers (`--list-guilds`, `--list-channels`) to target safely.
- GitHub Actions example: see the [workflow](https://github.com/janthmueller/delete-me-discord-workflow).

*\*Discord’s API uses `parent_id` for categories; we use “category” and “parent” interchangeably.*


## Installation

Install options:
- pipx (recommended for CLI isolation): `pipx install delete-me-discord`
- pip: `pip install delete-me-discord`
- Prebuilt binaries: download the asset for your OS from the [latest release](https://github.com/janthmueller/delete-me-discord/releases/latest), extract, and run from a terminal.

## Usage
### Quick Start

- Export your token first (See [this guide](https://github.com/victornpb/undiscord/wiki/authToken) to obtain your token):  
  `export DISCORD_TOKEN=<your_token>`

- Find IDs first (safe):  
  `delete-me-discord --list-guilds` and `delete-me-discord --list-channels`

- Full channel wipe (preview first):  
  `delete-me-discord --include-ids <channel_id> --delete-reactions --preserve-n 0 --preserve-last "seconds=0" --dry-run`  
  Drop `--dry-run` to execute.

**Rolling retention (keep recent, expire older)**  
- Initial purge (once): run without `--fetch-max-age` to process the whole history and establish your preserve settings.
- Daily/recurring: fetch only the recent slice to speed things up.
- Time-only (no cache needed):  
  - Initial/full: `delete-me-discord --preserve-n 0 --preserve-last "weeks=2" --dry-run`  
  - Daily: `delete-me-discord --preserve-n 0 --preserve-last "weeks=2" --fetch-max-age "weeks=2,days=1" --dry-run`  
  Drop `--dry-run` to keep the last 2 weeks; everything older is deletable.  
- Time and/or count (cache recommended):  
  - Initial/full: `delete-me-discord --preserve-last "weeks=2" --preserve-n 20 --preserve-cache --dry-run`  
  - Daily: `delete-me-discord --preserve-last "weeks=2" --preserve-n 20 --fetch-max-age "weeks=2,days=1" --preserve-cache --dry-run`  
  Drop `--dry-run` to keep the last 2 weeks and last 20 messages; cache avoids “forgotten” preserves when using `fetch-max-age`.
- Daily guidance: set `--fetch-max-age` to your preserve window + 1 day (e.g., `--preserve-last "weeks=2"` + `--fetch-max-age "weeks=2,days=1"`), so you only fetch the recent slice while keeping your preservation buffer. With `--preserve-cache` + `--preserve-n`, you can also use a smaller window (e.g., `--fetch-max-age 1d`) because at least the last 2 weeks of preserved messages from the previous run are cached; if you skip days, widen `fetch-max-age` to cover the gap.

### Command-Line Options

| Short | Long | Default | Description |
| --- | --- | --- | --- |
| `-g` | `--list-guilds` | off | List guild IDs and names, then exit. |
| `-c` | `--list-channels` | off | List channels grouped by guild/category/parent plus DMs, then exit. |
| `-d` | `--dry-run` | off | Simulate deletions without making changes. Also switches preserve-cache operations to a separate `.dryrun.json` file. |
| `-R` | `--delete-reactions` | off | Remove your reactions on messages encountered once the deletion window is reached. |
| `-i` | `--include-ids` | all eligible | Restrict processing to matching channel, guild, or parent IDs. Channel and parent includes punch through higher-level excludes. |
| `-x` | `--exclude-ids` | none | Exclude matching channel, guild, or parent IDs from processing. |
| `-p` | `--preserve-last` | `weeks=2` | Keep messages and reactions newer than this delta, for example `weeks=1,days=3` or `1w3d`. |
| `-n` | `--preserve-n` | `12` | Always keep the last N messages in each channel. |
| `-` | `--preserve-n-mode` | `mine` | Count preserved messages as only your deletable messages (`mine`) or all recent messages in the channel (`all`). |
| `-` | `--preserve-cache` | off | Persist preserved message IDs between runs. Most useful with `--preserve-n` and `--fetch-max-age`. |
| `-` | `--wipe-preserve-cache` | off | Delete the active preserve cache file and exit. |
| `-` | `--preserve-cache-path` | `~/.config/delete-me-discord/preserve_cache.json` | Override the preserve cache path. |
| `-a` | `--fetch-max-age` | unlimited | Fetch only messages newer than this delta, for example `weeks=1,days=3` or `10d`. |
| `-m` | `--max-messages` | unlimited | Maximum number of messages to fetch per channel. |
| `-` | `--buffer-channel-messages` | off | Buffer one channel at a time before evaluation. Useful for per-channel progress and approximate execution estimates, at the cost of higher per-channel memory usage. |
| `-r` | `--max-retries` | `5` | Maximum retry attempts for rate-limited or retryable API requests. |
| `-b` | `--retry-time-buffer` | `25 35` | Extra wait after rate limits. One value is fixed, two values define a random range in seconds. |
| `-f` | `--fetch-sleep-time` | `0.2 0.4` | Sleep between fetch requests. One value is fixed, two values define a random range in seconds. |
| `-s` | `--delete-sleep-time` | `1.5 2` | Sleep between deletions. One value is fixed, two values define a random range in seconds. |
| `-l` | `--log-level` | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |
| `-` | `--json` | off | Emit JSON output for logs and discovery output. |
| `-v` | `--version` | n/a | Show the version and exit. |

**Time delta formats**
- Legacy key/value: `weeks=2,days=3,hours=1` (units must be unique; no negatives).
- Compact suffix: `2w3d4h5m6s` (units: w/d/h/m/s; unique; no negatives).
- A plain `0` or `0.0` is treated as zero duration.

**Dry-run and cache note**
- `--dry-run` uses a separate preserve cache file by appending `.dryrun.json` to the active cache path.
- `--wipe-preserve-cache` operates on the active cache path too, so with `--dry-run` it wipes the dry-run cache, not the normal one.

**Buffered mode note**
- `--buffer-channel-messages` buffers one channel at a time before evaluating deletions. This enables per-channel buffering/timing output and an approximate execution estimate.
- Buffered mode still respects fetch limits such as `--max-messages` and `--fetch-max-age`. It does not fetch the full channel history unless your other flags already require that.
- `est. execute time` is only a rough estimate for planned delete/reaction actions. It does not include fetch time, network jitter, or retries.
- In `--dry-run`, logs can also show `scan time` and `est. total time`. `scan time` is the actual dry-run scan time, while `est. total time` is `scan time + est. execute time`.

**Preserve cache (what is stored)**  
- When enabled, the cache records the IDs of deletable messages preserved in a run by `preserve-n` or `preserve-last`. If `--delete-reactions` is set, IDs of preserved messages that contain your reactions are also recorded.  
- On subsequent runs, cached IDs are refetched and merged into the stream so preserved items remain visible even if they fall outside `fetch-max-age` (which can happen when `--preserve-n` is enabled).

## Contributing

Contributions are welcome. Open an issue or pull request with improvements or bug fixes.

## License

This project is licensed under the [MIT License](LICENSE). You are free to use, modify, and distribute this software as per the terms of the license.
