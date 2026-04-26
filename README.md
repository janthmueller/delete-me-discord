# Delete Me Discord
[![PyPI Latest Release](https://img.shields.io/pypi/v/delete-me-discord.svg)](https://pypi.org/project/delete-me-discord/)
[![Pepy Total Downloads](https://img.shields.io/pepy/dt/delete-me-discord)](https://pepy.tech/project/delete-me-discord)
[![GitHub License](https://img.shields.io/github/license/janthmueller/delete-me-discord)](https://github.com/janthmueller/delete-me-discord/blob/main/LICENSE)

**Delete Me Discord** is a command-line tool for deleting your own Discord messages and reactions with explicit filters and retention controls.

> ⚠️ Using automated tools on Discord may violate Discord’s [Terms of Service](https://discord.com/terms). Use at your own risk.

## What It Does

- Deletes your own messages in DMs and guild text channels.
- Optionally deletes your own reactions too.
- Lets you scope runs by channel, guild, or parent/category ID.
- Supports dry-run previews before making changes.
- Supports rolling cleanup with time- and count-based preservation.
- Can operate in a buffered per-channel mode for clearer timing output in manual runs.

## Installation

Choose one:

- Python installs require Python 3.10 or newer.
- `pipx install delete-me-discord`
- `pip install delete-me-discord`
- Download a prebuilt binary from the [latest release](https://github.com/janthmueller/delete-me-discord/releases/latest)

## Quick Start

Export your token first:

```bash
export DISCORD_TOKEN=<your_token>
```

You can obtain your token using [this guide](https://github.com/victornpb/undiscord/wiki/authToken).

Then discover targets safely:

```bash
delete-me-discord --list-guilds
delete-me-discord --list-channels
```

Preview a full wipe of one channel before executing it:

```bash
delete-me-discord --include-ids <channel_id> --delete-reactions --preserve-n 0 --preserve-last 0 --dry-run
```

Drop `--dry-run` only after the plan looks right.

If you want to share logs or screenshots, add `--redact-sensitive`. For example, `--redact-sensitive 0 4` keeps only the last 4 characters of IDs while fully hiding names and local paths.

## Common Use Cases

Full channel cleanup:

```bash
delete-me-discord --include-ids <channel_id> --delete-reactions --preserve-n 0 --preserve-last 0 --dry-run
```

Rolling retention:

```bash
delete-me-discord --preserve-last "weeks=2" --preserve-n 20 --fetch-max-age "weeks=2,days=1" --preserve-cache --dry-run
```

## Documentation

The full documentation lives at:

- [janthmueller.github.io/delete-me-discord](https://janthmueller.github.io/delete-me-discord/)

Useful entry points:

- [First Run](https://janthmueller.github.io/delete-me-discord/getting-started/first-run/)
- [Full Channel Cleanup](https://janthmueller.github.io/delete-me-discord/guides/full-channel-cleanup/)
- [Rolling Retention](https://janthmueller.github.io/delete-me-discord/guides/rolling-retention/)
- [CLI Options](https://janthmueller.github.io/delete-me-discord/reference/cli-options/)
- [Log Output](https://janthmueller.github.io/delete-me-discord/reference/log-output/)

For GitHub Actions usage, see the example workflow repo:

- [delete-me-discord-workflow](https://github.com/janthmueller/delete-me-discord-workflow)

## Contributing

Contributions are welcome. Open an issue or pull request with improvements or bug fixes.

## License

This project is licensed under the [MIT License](LICENSE). You are free to use, modify, and distribute this software as per the terms of the license.
