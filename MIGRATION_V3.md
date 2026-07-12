# Delete Me Discord v3 Migration

- Status: draft, living document
- Migration branch: `v3`
- Compared with: `origin/main` at `125ac29fc63904fccbbd494b61473b15d110840a`
- Baseline date: 2026-07-11
- Last reviewed: 2026-07-12

This document records the functional and structural migration from the current
v2 code on `origin/main` to the v3 work on this branch. Keep it updated when the
v3 behavior, public CLI, architecture, packaging, or delivery flow changes. If
the branch is rebased onto a newer `origin/main`, update the baseline commit and
review the comparison again.

## Release intent

v3 is a major release because cleanup discovery now includes all accessible
active and archived Discord threads by default. A command that previously
visited only the supported non-thread channels can therefore inspect and delete
from a larger scope after upgrading.

The eventual commit reaching `main` must preserve a Conventional Commit major
marker, for example `feat!: include threads in default cleanup discovery`, or a
`BREAKING CHANGE:` footer. Without that marker, Semantic Release will not infer
`3.0.0` from the implementation alone.

The release also introduces explicit channel/thread filters, centralized
request scheduling, safer state-file writes, more reliable package and binary
builds, Super Reaction cleanup, opt-in creator-owned thread deletion, and a
gated CI/CD chain.

## User migration summary

### Review the expanded default scope

Both of these commands now discover threads by default:

```bash
dmd list channels
dmd clean
```

The default cleanup scope consists of:

- guild text and announcement channels
- text chat attached to guild voice and stage voice channels
- DMs and Group DMs
- accessible active and archived announcement, public, and private threads
- forum and media posts, represented by Discord as public threads

Forum and media channels are containers. They can be selected as thread
parents, but cleanup runs against their discovered thread posts rather than the
container itself.

To retain the pre-v3 behavior of not discovering threads, add:

```bash
dmd clean --exclude-threads
```

The same option provides a faster channel listing when thread information is
not needed:

```bash
dmd list channels --exclude-threads
```

### Replace inclusion-style thread options

Thread discovery is now controlled with exclusions:

```bash
# Omit archived threads but keep active threads.
dmd clean --exclude-thread-states archived

# Omit one exact Discord channel type.
dmd clean --exclude-channel-types PrivateThread

# Omit several exact types.
dmd clean --exclude-channel-types GuildVoice PrivateThread

# Omit every thread type.
dmd clean --exclude-threads
```

The accepted values can be inspected locally without a Discord token:

```bash
dmd list channel-types
dmd list thread-states
```

Both value-list commands support `--json`.

### Existing profiles are migrated in memory

The current profile fields are:

- `exclude_channel_types`
- `exclude_thread_states`
- `exclude_threads`

Legacy thread fields are accepted and translated when a profile is loaded:

| Legacy value | Effective v3 value |
| --- | --- |
| `threads: "none"` | `exclude_threads: true` |
| `threads: "active"` | `exclude_thread_states: ["archived"]` |
| `threads: "all"` | no thread exclusion |
| `include_threads: false` | `exclude_threads: true` |
| `include_threads: true` | active threads only |
| `include_archived_threads: true` | active and archived threads |

Updating a legacy profile rewrites it using the current fields. A profile that
mixes legacy and current thread fields is rejected because its intent would be
ambiguous.

Profiles that never defined thread behavior inherit the v3 default and include
all accessible threads. Add `exclude_threads=true` to profiles that must retain
the old scope.

### Thread containers remain protected by default

Default cleanup removes the authenticated user's messages and reactions inside
threads but never deletes the thread object. Container deletion is available
only through an explicit mode:

```bash
# Require a complete scan with no messages from other or unknown authors.
dmd clean --include-ids <thread-id> --delete-owned-threads self-only --dry-run

# Permit deletion even when the thread contains other users' messages.
dmd clean --include-ids <thread-id> --delete-owned-threads all --dry-run
```

Both modes require the thread's `owner_id` to match the authenticated account,
and Discord still requires `MANAGE_THREADS`. A rejected deletion falls back to
ordinary own-message and own-reaction cleanup. Successful deletion removes the
whole shared thread and overrides retention for that thread. Even `self-only`
can remove reactions from other users and has an unavoidable scan/delete race,
so `none` remains the default and narrow dry-runs are required operationally.

### Retry timing terminology changed

`--retry-safety-jitter` is the current option name. The old
`--retry-time-buffer` spelling remains an alias.

The default changed from a large 25-35 second buffer to a 0.1-0.3 second safety
jitter. The application now honors Discord's actual retry delay and adds only
this small jitter; it no longer treats the configured value as the primary
wait.

Named request policies can be overridden per invocation:

```bash
dmd list channels --request-interval thread-search=0.5,0.8
dmd clean --request-interval fetch=0.3 --request-interval delete=2,2.5
```

Supported policies and defaults are:

| Policy | Default minimum interval |
| --- | --- |
| `read` | 0.1-0.25 seconds |
| `fetch` | 0.2-0.4 seconds |
| `delete` | 1.5-2.0 seconds |
| `thread-search` | 0.2-0.4 seconds |

`--fetch-sleep-time` and `--delete-sleep-time` remain supported, but now define
minimum intervals in the scheduler. An explicit `--request-interval` for the
same policy takes precedence.

### Authentication precedence changed

Token resolution is now:

1. explicit `--token`
2. `DISCORD_TOKEN`
3. operating-system keyring
4. legacy token in `config.json`

This makes a shell or CI environment override stored local credentials without
having to remove them.

## Thread and channel model

### Centralized Discord channel types

Discord channel type numbers and display names now live in
`delete_me_discord/channel_types.py`. The model distinguishes:

- root message channels: `DM`, `GroupDM`
- direct guild message channels: `GuildText`, `GuildAnnouncement`,
  `GuildVoice`, `GuildStageVoice`
- thread channels: `AnnouncementThread`, `PublicThread`, `PrivateThread`
- thread containers: `GuildForum`, `GuildMedia`
- structural/non-message types such as categories and directories

Discovery, selector resolution, display, and cleanup use the same definitions.
This removes the previous duplicated `{0, 1, 3}` channel-type checks and makes
support for announcement, voice/stage chat, forum/media posts, and threads
consistent throughout the application.

### ScopeFilter is the shared policy

`delete_me_discord/scope_filter.py` converts CLI/profile names into typed
channel and thread-state exclusions. The same filter is passed through
inventory collection, selector resolution, listing, and cleanup.

Type and thread-state exclusions always take precedence over ID includes. This
prevents an explicitly included channel ID from accidentally overriding a
global safety exclusion such as `--exclude-threads`.

ID filtering follows nearest-target precedence:

1. channel or thread
2. thread parent
3. category
4. guild

For example, an included thread can override an excluded guild, while an
explicit exclusion on that thread still wins.

The filter also determines the cheapest required discovery mode:

- `none`: make no thread-search requests
- `active`: request active threads only
- `all`: request active and archived threads, then apply exact type/state filters

Parents are skipped when none of their possible thread types remain eligible.
For example, excluding `AnnouncementThread` avoids searches under announcement
channels without disabling public/private thread searches elsewhere.

## Thread discovery pipeline

### Inventory collection

`ScopeInventory` first loads guilds, root channels, and each guild's channels.
For every eligible thread-capable parent it then calls:

```text
GET /channels/{channel_id}/threads/search
```

Parent/type relationships are explicit:

| Parent type | Possible discovered thread types |
| --- | --- |
| `GuildText` | `PublicThread`, `PrivateThread` |
| `GuildAnnouncement` | `AnnouncementThread` |
| `GuildForum` | `PublicThread` |
| `GuildMedia` | `PublicThread` |

The endpoint is part of Discord's user-facing API behavior rather than the
documented bot API used by conventional bot integrations. That matches DMD's
existing user-token model, but it remains an integration surface that may need
maintenance if Discord changes its client API.

### Pagination and response handling

Each search requests 25 threads, newest first. When Discord returns
`has_more: true`, the last thread ID becomes the next `max_id` cursor. IDs are
deduplicated across pages. Pagination stops when:

- `has_more` is false
- Discord returns an empty page
- the cursor is absent or no longer advances
- the response is malformed and the request fails explicitly

When only active threads are wanted, the request sends `archived=false`. To
obtain all accessible threads, the archived filter is omitted and the returned
objects are filtered locally.

Discord may return `202` with code `110000` while its search index is being
built. DMD schedules a retry instead of treating that response as success.
`429`, transient network errors, and server errors also enter the centralized
retry scheduler.

A `403` or `404` for one parent skips that parent's threads without discarding
the rest of the guild inventory. These skips are diagnostic output, visible at
the established high-verbosity level rather than in normal output.

### Thread hierarchy and display

Discovered threads are normalized with their guild, immediate parent, and
category IDs. The Rich channel tree nests a thread below its visible parent.
If a parent is unavailable or filtered out, the thread remains visible at the
category level with parent context.

JSON channel output remains structurally compatible: channel entries stay flat
within their category and expose `parent_id`, `parent_name`, and `archived`
metadata.

### Cleanup behavior

Threads are ordinary message channels once discovered, so existing message
fetching, retention rules, dry-run planning, preserve-cache behavior, and
message deletion apply to them.

Archived threads are handled specially: Discord permits deleting the user's
messages there but restricts other mutations. DMD therefore skips reaction
removal in archived threads instead of failing the channel.

Reaction ownership now distinguishes Discord's `me` and `me_burst` fields.
Normal and Super Reactions are planned and deleted independently, including
when both variants use the same emoji. Deleted custom emoji with a null name use
the current Discord client identifier form `null:{id}`.

Optional thread-container deletion retains `owner_id` and `message_count` from
discovery. `self-only` performs an unbounded full-history author scan and uses
that buffered history for ordinary cleanup if deletion is ineligible. A real
`all` execution skips author eligibility checks by design. Both modes use the
existing request scheduler, dry-run reporting, and permission-failure fallback.

Dry-run impact reporting now retains Discord reaction `count_details` and
derives the exact foreign normal/Super Reaction instances removed as a cascade
of deleting an own message or owned thread. Missing, inconsistent, or incomplete
count data is reported as `unknown`, never estimated. `all` execution still
deletes directly, while `all --dry-run` performs a complete scan so the shared
message and reaction impact is visible first.

An unknown future Discord message type no longer aborts message collection. It
is retained in the scan, logged once, and treated as non-deletable unless DMD
knows that type is safe to delete.

## Request scheduler

The old pattern of sleeping unconditionally after fetches and deletions has
been replaced by `DiscordRequestScheduler` in
`delete_me_discord/rate_limits.py`.

The scheduler tracks in-memory timing state for:

- Discord global limits
- normalized HTTP routes
- Discord bucket IDs combined with their major channel/guild/webhook resource
- route families across different major resource IDs
- application policies such as fetch, delete, and thread search

Before every request, it computes all applicable deadlines and sleeps once
until the latest deadline. Successful response headers update bucket state.
`429` responses update global, bucket, route, or route-family state according to
the returned scope. User-scoped limits can teach a minimum interval for the same
normalized endpoint across different channel IDs.

This timing model has two practical effects:

- a retry delay is remembered and enforced when the affected request is tried again
- there is no unconditional sleep after the final API call in a sequence

Network failures use exponential route backoff capped at 30 seconds. Server
errors prefer response retry headers and otherwise use the same backoff. Every
HTTP request now has a 10-second connect timeout and a 30-second read timeout.
Detailed wait/retry messages use the diagnostic logging level.

## State and configuration reliability

### Atomic JSON writes

Auth config, profiles, and preserve-cache files now share
`atomic_write_json()` from `delete_me_discord/storage.py`. It writes a temporary
file, flushes and fsyncs it, applies owner-only file permissions, and atomically
replaces the target. A failed write leaves the previous file intact.

The preserve cache is saved after every completed channel rather than only at
the end of the whole run. A long cleanup interrupted later therefore retains
the completed channel progress.

### Input validation

Retry counts must be non-negative. Timing ranges must contain one or two finite,
non-negative, ordered values. Named request policies reject unknown or duplicate
overrides. Profile channel types and thread states are validated against the
same canonical values used by the CLI.

## Versioning and distribution

### Single source version at runtime

Runtime version lookup no longer depends on installed package metadata or
`setuptools_scm`. `delete_me_discord/_version.py` contains the importable
version, while `pyproject.toml` remains package metadata.

Python Semantic Release is configured to update both locations in the release
commit:

```toml
version_toml = ["pyproject.toml:project.version"]
version_variables = ["delete_me_discord/_version.py:__version__"]
```

This keeps source runs, installed wheels, and frozen binaries on the same
version. The current development snapshot still says `2.2.5`; the semantic
release commit for the breaking release is expected to move it to v3.

### Clean Python distributions

Package discovery now explicitly includes only `delete_me_discord*`.
`MANIFEST.in` prunes docs, tests, tools, scripts, build output, and caches from
the source distribution. `tools/verify_distribution.py` validates wheel/sdist
contents and metadata version during CI and release builds.

The project license metadata now uses the SPDX string form required by modern
setuptools, removing the deprecated TOML table and license classifier.

### Frozen binaries

The PyInstaller specification explicitly collects `charset_normalizer`
submodules and its mypyc binary modules. This prevents frozen builds from
passing compilation but failing at runtime when `requests` imports the optimized
charset implementation.

Binary builds use clean PyInstaller output and verify that the executable
version exactly matches the release tag before uploading artifacts. Manual
binary builds require an existing tag and build that tag rather than an
arbitrary branch state.

### Nix development flow

The flake now tests the Python package with `pytestCheckHook`, uses the renamed
`tests/` directory, and provides the build, packaging, lint, and release-audit
tools needed by the repository. Node.js moves to 24.

Entering the development shell no longer creates or activates `.venv`.
Instead, uv is pointed at the Nix Python and Python downloads are disabled. The
repository intentionally ignores `uv.lock`; installable package constraints in
`pyproject.toml` remain the Python dependency contract rather than locking one
application environment.

The Python test directory has been renamed from `test/` to the conventional
`tests/` path across packaging, Nix, and CI.

## Documentation toolchain

The Astro docs project now installs `@astrojs/check` and TypeScript explicitly,
so `pnpm check` is reproducible in CI. The PostCSS override moved to
`docs/pnpm-workspace.yaml`, which is the supported pnpm location, and the lockfile
was regenerated for the resulting workspace configuration.

User documentation has been updated for the expanded channel support, default
thread discovery, exclusion filters, profile fields, request policies, logging,
archived-thread limitations, Super Reactions, and destructive owned-thread
deletion modes.

`DISCORD_USER_ARTIFACT_AUDIT.md` records the audited channel surface, adjacent
Discord user artifacts, ownership and permission boundaries, known cleanup
gaps, and the recommended boundary between core communication cleanup and
future opt-in resource management.

## CI/CD migration

### Pull request and main gate

The `Test` workflow is now the common gate for pull requests and pushes to
`main` or the long-lived `v3` integration branch. This lets every v3 checkpoint
run the complete gate without opening or merging a pull request. It runs:

- pytest on Ubuntu and Windows for Python 3.10 through 3.14
- Ruff and a 90 percent coverage floor
- Astro type/content checks and a production docs build
- `pip-audit` for Python dependencies
- `pnpm audit --audit-level high` for docs dependencies
- wheel, sdist, installed-wheel, and PyInstaller smoke validation
- a Nix package build and test

The old standalone security workflow was removed because its audits now run in
the common gate. A final `build` job succeeds only when every required job has
succeeded, providing one stable aggregate check in addition to the matrix jobs.

### Release chain

The release path is now:

1. A push to `main` runs `Test`.
2. `Release` starts only after that workflow succeeds.
3. A guard verifies that the tested SHA is still the current `main` SHA.
4. Python Semantic Release computes the version from conventional commits,
   writes both version files, pushes its release commit/tag, and publishes the
   GitHub release.
5. The Python distributions are rebuilt and validated, then published to PyPI.
6. The published GitHub release triggers tagged Linux, macOS, and Windows
   PyInstaller builds.

The release workflow checks out and authenticates with `GH_PAT`, not the default
workflow token, because Semantic Release must push its version commit and tag.
The PAT actor must remain on the `main` ruleset bypass list; this is repository
configuration outside the tracked files.

Release runs are serialized and are not cancelled when a newer push appears.
The SHA guard makes an older successful test run a no-op once `main` has moved.
The `v3` branch itself cannot publish a release: downstream `workflow_run`
events are filtered to `main`, the release guard checks `main` again, and
Semantic Release configuration targets `main`. It is an integration branch
until the breaking change is merged.

### Documentation deployment

Pull requests validate docs only inside `Test`; they never deploy them.

After a successful `Test` run caused by a push to `main`, the `Docs` workflow
checks out that tested SHA, checks and builds Astro, and deploys the artifact to
GitHub Pages. Completed tests on `v3` do not trigger this downstream workflow.
Manual deployment is allowed only from `main`. Pages deployments share one
concurrency group so a stale deployment cannot race the latest build.

## Operational constraints

- The default v3 scope is intentionally broader. Upgrade notes and the release
  commit must call out thread inclusion as a breaking change.
- Thread discovery cost scales with the number of visible thread-capable parent
  channels and result pages. `--exclude-threads` is the explicit fast path.
- DMD can only discover threads visible to the authenticated user. Per-parent
  permission failures are skipped and do not imply that no thread exists.
- The thread search endpoint follows Discord client behavior and is less stable
  than a documented public bot endpoint.
- Super Reaction removal also follows a typed route observed in Discord's
  current web client and may need maintenance if that client API changes.
- Thread-container deletion is never enabled by default. Creator attribution
  does not imply `MANAGE_THREADS`, and even `self-only` cannot make its
  scan/delete sequence atomic or preserve other users' reactions.
- Rate-limit state is process-local. A new DMD process starts without learned
  route-family intervals and learns again from Discord responses.
- Release ruleset bypass, PyPI credentials, Pages configuration, and GitHub
  environments remain external repository settings and must stay aligned with
  the tracked workflows.

## Change map

| Area | Primary files |
| --- | --- |
| CLI and orchestration | `delete_me_discord/__init__.py`, `delete_me_discord/options.py` |
| Profiles and effective settings | `delete_me_discord/app_config.py` |
| Channel model and filters | `delete_me_discord/channel_types.py`, `delete_me_discord/scope_filter.py` |
| Inventory and selectors | `delete_me_discord/scope_inventory.py`, `delete_me_discord/scope_selectors.py` |
| Thread API and rate scheduling | `delete_me_discord/api.py`, `delete_me_discord/rate_limits.py` |
| Listing and rendering | `delete_me_discord/discovery.py`, `delete_me_discord/discovery_renderers.py` |
| Cleanup semantics | `delete_me_discord/cleaner.py`, `delete_me_discord/models.py` |
| Durable local state | `delete_me_discord/storage.py`, `delete_me_discord/auth.py`, `delete_me_discord/preserve_cache.py` |
| Version and packaging | `delete_me_discord/_version.py`, `pyproject.toml`, `MANIFEST.in`, `delete_me_discord.spec`, `tools/verify_distribution.py` |
| CI/CD | `.github/workflows/test.yml`, `.github/workflows/release.yml`, `.github/workflows/docs.yml`, `.github/workflows/pyinstaller.yml` |
| Development environment | `flake.nix`, `flake.lock` |
| Documentation | `README.md`, `MIGRATION_V3.md`, `DISCORD_USER_ARTIFACT_AUDIT.md`, `docs/src/content/docs/`, `docs/package.json`, `docs/pnpm-workspace.yaml` |
| Tests | `tests/` |

## Keeping this document current

When v3 changes, update the relevant section here in the same change. Compare
the branch against the recorded baseline with:

```bash
git diff --stat 125ac29fc63904fccbbd494b61473b15d110840a
git diff --name-status 125ac29fc63904fccbbd494b61473b15d110840a
```

After rebasing v3 onto a newer `origin/main`, replace the baseline hash and
review the full document against the new diff.
