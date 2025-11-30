# Delete Me Discord
[![PyPI Latest Release](https://img.shields.io/pypi/v/delete-me-discord.svg)](https://pypi.org/project/delete-me-discord/)
[![Pepy Total Downloads](https://img.shields.io/pepy/dt/delete-me-discord)](https://pepy.tech/project/delete-me-discord)
[![GitHub License](https://img.shields.io/github/license/janthmueller/delete-me-discord)](https://github.com/janthmueller/delete-me-discord/blob/main/LICENSE)

**Delete Me Discord** is a command-line tool to delete your own Discord messages (and reactions) with filters and rolling retention controls.

> ⚠️ Using automated tools on Discord may violate Discord’s [Terms of Service](https://discord.com/terms). Use at your own risk.

**TL;DR**
- Deletes your messages/reactions in bulk.
- Filters by time/count and include/exclude (channels, categories/parents*, guilds).
- Dry-run + discovery helpers (`--list-guilds`, `--list-channels`) to target safely.

*\*Discord’s API uses `parent_id` for categories; we use “category” and “parent” interchangeably.*

**Who it’s for (and not)**
- For cleaning up your own messages. Not for deleting others’ messages.
## Features

- **Time-Based Deletion:** Delete messages and reactions older than a specified time delta.
- **Count-Based Preservation:** Preserve a certain number of recent messages regardless of their age.
- **Selective Channel Processing:** Include or exclude specific channels, guilds, or parent categories.
- **Dry Run Mode:** Simulate deletions without actually removing any messages.
- **Robust Logging:** Track the script's actions and troubleshoot issues effectively.
- **Rate Limit Handling:** Automatically handles Discord API rate limits with customizable retry strategies.

## Table of Contents

- [Installation](#installation)
- [Usage](#usage)
  - [Basic Command](#basic-command)
  - [Quick Start](#quick-start)
  - [Command-Line Options](#command-line-options)
- [Configuration](#configuration)
- [Contributing](#contributing)
- [License](#license)

## Installation

You can install `delete-me-discord` using `pip`. Ensure you have Python 3.6 or higher installed.

### Using `pip`

```bash
pip install delete-me-discord
```


## Usage

After installation, you can execute the script directly from the command line using the `delete-me-discord` command.

### Basic Command

```bash
delete-me-discord --preserve-n 10 --preserve-last "weeks=1,days=3"
```

Deletes messages older than 1 week and 3 days while keeping at least 10 messages per channel.

### Quick Start

- Export your token first:  
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

<details>
<summary>Full option reference</summary>

- `--list-guilds`: List guild IDs/names, then exit.
- `--list-channels`: List channels (grouped by guild/category/parent + DMs), then exit.
- `--dry-run`: Simulate deletions; no changes made.
- `--delete-reactions`: Remove your reactions on messages encountered.
- `--include-ids`: Channel/guild/parent IDs to include. If omitted, all IDs are eligible except those in `--exclude-ids`. Channel/category (parent) includes punch through higher-level excludes (category/guild). Example: `--include-ids 123 456`
- `--exclude-ids`: Channel/guild/parent IDs to exclude. Example: `--exclude-ids 789`
- `--preserve-last`: Keep messages/reactions newer than this delta (default `weeks=2`, e.g., `weeks=1,days=3`).
- `--preserve-n`: Always keep the last N messages (default `12`).
- `--fetch-max-age`: Only fetch newer than this delta (e.g., `weeks=1`). Default: no max age.
- `--max-messages`: Max messages to fetch per channel (default: no limit).
- `--max-retries`: Retry count for API requests (default `5`).
- `--retry-time-buffer`: Extra wait after rate limits (default `25 35` seconds).
- `--fetch-sleep-time`: Sleep between fetch requests (default `0.2 0.4` seconds).
- `--delete-sleep-time`: Sleep between deletions (default `1.5 2` seconds).
- `--log-level`: `DEBUG`|`INFO`|`WARNING`|`ERROR`|`CRITICAL` (default `INFO`).
- `--version`: Show the version number and exit.

</details>

## Configuration

Before using `delete-me-discord`, set up your Discord credentials via environment variables (avoid passing tokens on the command line):

- **`DISCORD_TOKEN`**: Your Discord authorization token. See [this guide](https://github.com/victornpb/undiscord/wiki/authToken) to obtain your token.
- **`DISCORD_USER_ID`** (optional): Your Discord user ID. This ID is used to target messages authored by you. If not provided, the tool will resolve it automatically using your token. You can obtain it by enabling Developer Mode in Discord and right-clicking your username to copy the ID.

**Security Note:**
Never share your authorization token. Sharing it will allow others to access your account and perform actions on your behalf.

## Contributing

Contributions are welcome—open an issue or pull request with improvements or bug fixes.

## License

This project is licensed under the [MIT License](LICENSE). You are free to use, modify, and distribute this software as per the terms of the license.
