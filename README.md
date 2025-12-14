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

- Rolling retention (preview):  
  `delete-me-discord --preserve-last "weeks=2" --preserve-n 20 --dry-run`  
  Drop `--dry-run` to keep recent messages (last 20 + last 2 weeks), expire older.

- Initial purge, then fast daily retention runs:  
  First run without `--fetch-max-age` to clear old history. For daily runs, set `--fetch-max-age` to your preserve window + 1 day (e.g., `--preserve-last "weeks=2"` + `--fetch-max-age "weeks=2,days=1"`), so you only fetch the recent slice while keeping your preservation buffer.

### Command-Line Options

- `-g, --list-guilds`: List guild IDs/names, then exit.
- `-c, --list-channels`: List channels (grouped by guild/category/parent + DMs), then exit.
- `-d, --dry-run`: Simulate deletions; no changes made.
- `-R, --delete-reactions`: Remove your reactions on messages encountered once the deletion window is reached (older than the cutoff and past the preserve-n threshold).
- `-i, --include-ids`: Channel/guild/parent IDs to include. If omitted, all IDs are eligible except those in `--exclude-ids`. Channel/category (parent) includes punch through higher-level excludes (category/guild). Example: `--include-ids 123 456`
- `-x, --exclude-ids`: Channel/guild/parent IDs to exclude. Example: `--exclude-ids 789`
- `-p, --preserve-last`: Keep messages/reactions newer than this delta (default `weeks=2`, e.g., `weeks=1,days=3`).
- `-n, --preserve-n`: Always keep the last N of your messages (default `12`).
- `-a, --fetch-max-age`: Only fetch newer than this delta (e.g., `weeks=1`). Default: no max age.
- `-m, --max-messages`: Max messages to fetch per channel (default: no limit).
- `-r, --max-retries`: Retry count for API requests (default `5`).
- `-b, --retry-time-buffer`: Extra wait after rate limits (default `25 35` seconds).
- `-f, --fetch-sleep-time`: Sleep between fetch requests (default `0.2 0.4` seconds).
- `-s, --delete-sleep-time`: Sleep between deletions (default `1.5 2` seconds).
- `-l, --log-level`: `DEBUG`|`INFO`|`WARNING`|`ERROR`|`CRITICAL` (default `INFO`).
- `-v, --version`: Show the version number and exit.

## Contributing

Contributions are welcome. Open an issue or pull request with improvements or bug fixes.

## License

This project is licensed under the [MIT License](LICENSE). You are free to use, modify, and distribute this software as per the terms of the license.
