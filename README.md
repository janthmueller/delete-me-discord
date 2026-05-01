# Delete Me Discord
[![PyPI Latest Release](https://img.shields.io/pypi/v/delete-me-discord.svg)](https://pypi.org/project/delete-me-discord/)
[![Pepy Total Downloads](https://img.shields.io/pepy/dt/delete-me-discord)](https://pepy.tech/project/delete-me-discord)
[![GitHub License](https://img.shields.io/github/license/janthmueller/delete-me-discord)](https://github.com/janthmueller/delete-me-discord/blob/main/LICENSE)

**Delete Me Discord** is a CLI for deleting your own Discord messages and reactions with explicit filters and retention rules. The installed command is available as `delete-me-discord` and the shorter alias `dmd`.

> ⚠️ Using automated tools on Discord may violate Discord’s [Terms of Service](https://discord.com/terms). Use at your own risk.

It is built around a cautious workflow:

- discover targets first
- preview with `dmd clean --dry-run`
- only then execute

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

Preview one channel safely:

```bash
dmd clean --include-ids <channel_id> --dry-run
```

Then rerun without `--dry-run` when the plan looks right.

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
