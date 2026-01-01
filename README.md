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
- GitHub Actions example: see the workflow repo at <https://github.com/janthmueller/delete-me-discord-workflow>.

*\*Discord’s API uses `parent_id` for categories; we use “category” and “parent” interchangeably.*

## Installation

You can install `delete-me-discord` using `pip`. Ensure you have Python 3.7 or higher installed:

```bash
pip install delete-me-discord
```

## Usage

After installation, you can execute the script directly from the command line using the `delete-me-discord` command.

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

- `-g, --list-guilds`: List guild IDs/names, then exit.
- `-c, --list-channels`: List channels (grouped by guild/category/parent + DMs), then exit.
- `-d, --dry-run`: Simulate deletions; no changes made.
- `-R, --delete-reactions`: Remove your reactions on messages encountered once the deletion window is reached (older than the cutoff and past the preserve-n threshold).
- `-i, --include-ids`: Channel/guild/parent IDs to include. If omitted, all IDs are eligible except those in `--exclude-ids`. Channel/category (parent) includes punch through higher-level excludes (category/guild). Example: `--include-ids 123 456`
- `-x, --exclude-ids`: Channel/guild/parent IDs to exclude. Example: `--exclude-ids 789`
- `-p, --preserve-last`: Keep messages/reactions newer than this delta (default `weeks=2`, e.g., `weeks=1,days=3` or `1w3d`).
- `-n, --preserve-n`: Always keep the last N messages (default `12`).
- `--preserve-n-mode`: How to count the last N messages to keep: `mine` (only your deletable messages; default) or `all` (the last N messages in the channel, any author).
- `--preserve-cache`: Enable cache to refetch preserved messages between runs (useful with `--preserve-n` + `--fetch-max-age`).
- `--wipe-preserve-cache`: Delete the preserve cache file and exit.
- `--preserve-cache-path`: Override preserve cache path (default `~/.config/delete-me-discord/preserve_cache.json`).
- `-a, --fetch-max-age`: Only fetch newer than this delta (e.g., `weeks=1,days=3` or `10d`). Default: no max age.
- `-m, --max-messages`: Max messages to fetch per channel (default: no limit).
- `-r, --max-retries`: Retry count for API requests (default `5`).
- `-b, --retry-time-buffer`: Extra wait after rate limits (default `25 35` seconds; one number = fixed, two = random range).
- `-f, --fetch-sleep-time`: Sleep between fetch requests (default `0.2 0.4` seconds; one number = fixed, two = random range).
- `-s, --delete-sleep-time`: Sleep between deletions (default `1.5 2` seconds; one number = fixed, two = random range).
- `-l, --log-level`: `DEBUG`|`INFO`|`WARNING`|`ERROR`|`CRITICAL` (default `INFO`).
- `-v, --version`: Show the version number and exit.

**Time delta formats**
- Legacy key/value: `weeks=2,days=3,hours=1` (units must be unique; no negatives).
- Compact suffix: `2w3d4h5m6s` (units: w/d/h/m/s; unique; no negatives).
- A plain `0` or `0.0` is treated as zero duration.

**Preserve cache (what is stored)**  
- When enabled, the cache records the IDs of deletable messages preserved in a run by `preserve-n` or `preserve-last`. If `--delete-reactions` is set, IDs of preserved messages that contain your reactions are also recorded.  
- On subsequent runs, cached IDs are refetched and merged into the stream so preserved items remain visible even if they fall outside `fetch-max-age` (which can happen when `--preserve-n` is enabled).

## Contributing

Contributions are welcome. Open an issue or pull request with improvements or bug fixes.

## License

This project is licensed under the [MIT License](LICENSE). You are free to use, modify, and distribute this software as per the terms of the license.
