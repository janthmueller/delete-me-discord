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

v3 is a major release because cleanup discovery now includes accessible active
and archived Discord threads by default. A command that previously visited only the
supported non-thread channels can therefore inspect and delete from a larger
scope after upgrading.

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

### Replace partial ID suffixes with complete IDs

v3 removes the implicit unique-suffix fallback from `--include-ids` and
`--exclude-ids`. Every scope value must now be a complete decimal Discord ID
that identifies an accessible supported guild, category, channel, thread
parent, or thread.

```bash
# v2 shorthand; no longer accepted
dmd clean --include-ids 490059 --dry-run

# v3 exact scope
dmd clean --include-ids 123456789012490059 --dry-run
```

Use `dmd list guilds` or `dmd list channels` to obtain complete IDs. Profiles
created through the v2 profile commands already contain resolved complete IDs
and require no change. Replace suffixes only in hand-written configuration or
scripts. Old shorthand is treated as an exact ID and fails preflight when
Discord cannot resolve it.

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

### Archived content is included by default

Thread listing and content cleanup both include archived threads. The explicit
active-only equivalent is:

```bash
dmd clean -x archived
```

Selecting archived threads discovers, buffers, and plans archived content,
activates it only when at least one eligible message or reaction action exists,
and attempts to restore the archived state in a `finally` block. The default
continues for unlocked threads even when restoration rights cannot be
established. Use `--skip-unrestorable-threads` for a stricter policy:

```bash
dmd clean --include <thread-id> --dry-run

dmd clean --include <thread-id> --skip-unrestorable-threads --dry-run
```

Dry-run never changes thread state. Creator ownership or effective
`MANAGE_THREADS` lets DMD reasonably expect to re-archive the thread. Locked
threads always require effective `MANAGE_THREADS`.

Long-running plans can outlive Discord's auto-archive timer. A delete rejected
with Discord code `50083` triggers one exact channel refresh. For each detected
likely automatic archive, DMD reopens and retries that blocked action once when
the thread remains archived, lock/pin state and `auto_archive_duration` are
unchanged, the thread is not pinned, and the best available activity baseline
has reached the configured interval within a 30-second tolerance. For a thread
DMD opened, that baseline is the local monotonic activation time. For an
initially active thread, DMD compares the refreshed archive event timestamp
with the latest initial status-change or initial/refreshed last-message
timestamp. The new activation resets the timer baseline, so another reopen is
possible only after another full interval. Early archives, changed state,
missing activity evidence, malformed state, and a second immediate `50083`
stop the remaining thread actions instead of fighting an external archive.

### Thread containers remain protected by default

Default cleanup removes the authenticated user's messages and reactions inside
active threads but never deletes the thread object. Archived content follows
the separate policy above. Container deletion is available only through an
explicit mode:

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
`delete_me_discord/discord/channel_types.py`. The model distinguishes:

- root message channels: `DM`, `GroupDM`
- direct guild message channels: `GuildText`, `GuildAnnouncement`,
  `GuildVoice`, `GuildStageVoice`
- thread channels: `AnnouncementThread`, `PublicThread`, `PrivateThread`
- thread containers: `GuildForum`, `GuildMedia`
- structural/non-message types such as categories and directories

`GuildMedia` is an optional Discord channel type that acts as a container for
threads rather than a normal message history. If Discord exposes an existing
media channel, DMD discovers and cleans its supported threads normally; the
media parent itself is not a separate message-cleanup target. Discord may not
yet expose this type for every guild or account.

Discovery, exact-ID preflight, display, and cleanup use the same definitions.
This removes the previous duplicated `{0, 1, 3}` channel-type checks and makes
support for announcement, voice/stage chat, forum/media posts, and threads
consistent throughout the application.

### ScopeFilter is the shared policy

`delete_me_discord/scope/selectors.py` classifies the compact `-i/--include`
and `-x/--exclude` values into complete IDs, canonical channel types, the
`threads` group, and `active`/`archived` thread states. Existing structured
profile fields remain supported, and `--include-ids`/`--exclude-ids` remain CLI
aliases.

`delete_me_discord/scope/filter.py` converts those selectors and profile fields
into typed positive and negative channel/thread-state policy. The same filter
is passed through eager inventory collection, listing, and incremental
cleanup.

ID filtering follows nearest-target precedence:

1. channel or thread
2. thread parent
3. category
4. guild

For example, an included thread can override an excluded guild, while an
explicit exclusion on that thread still wins. Exact included cleanup leaves
(DMs, Group DMs, message channels, and threads) also override broad type and
state exclusions. Parent, category, and guild includes do not override filters
for their descendants.

Thread-type conflicts have their own specificity order:

1. exact thread ID
2. concrete thread type
3. the `threads` group

A concrete include therefore overrides `-x threads`, while a concrete
exclusion overrides `-i threads`. Exclusion wins when both sides name the same
concrete type. Thread states are orthogonal filters: `active` or `archived`
narrows the surviving thread types but does not override `-x threads`.

`delete_me_discord/scope/rules.py` owns this policy independently from API
discovery. If any include ID exists, unmatched channels default to excluded.
With excludes only, unmatched channels retain the default included state.

The resulting selection is order-independent:

```text
eligible
∩ (((hierarchical ID scope ∩ positive attributes) - negative attributes)
   ∪ exact included leaves)
- exact excluded leaves
```

Examples:

```bash
# One category, excluding voice and stage chat.
dmd clean -i <category-id> -x GuildVoice GuildStageVoice

# Exclude a guild, scoop one category back in, then remove one child.
dmd clean -x <guild-id> <channel-id> -i <category-id>

# Exclude threads globally but retain one exact thread.
dmd clean -x threads -i <thread-id>

# Exclude the broad group but retain public threads.
dmd clean -x threads -i PublicThread

# Include the broad group except public threads.
dmd clean -i threads -x PublicThread
```

The filter also determines the cheapest required discovery mode:

- `none`: make no thread-search requests
- `active`: request active threads only
- `all`: request active and archived threads, then apply exact type/state filters

Parents are skipped when none of their possible thread types remain eligible.
For example, excluding `AnnouncementThread` avoids searches under announcement
channels without disabling public/private thread searches elsewhere.

Exact-ID preflight now retains the resolved channel payloads in
`ScopeDiscoverySeed`. An exact thread include is injected beneath its fetched
parent with guild and permission context, so it does not require a thread
search request. Broad text/forum/media parent, category, guild, type, and state
selectors still use paginated thread discovery.

## Thread discovery pipeline

### Inventory and incremental cleanup

`dmd list channels` builds a complete `ScopeInventory` because rendering the
tree requires all selected channels and threads. Cleanup does not build a
global inventory, with or without explicit ID filters.

Explicit IDs first pass through `delete_me_discord/scope/resolver.py`. Preflight
loads the current guild and DM lists once, recognizes those IDs directly, and
uses `GET /channels/{id}` only for remaining category, channel, thread-parent,
or thread IDs. Every ID is validated before mutation. The guild and DM results
are then reused as a discovery seed rather than fetched again.

Cleanup advances one guild and one thread parent at a time. When include IDs
exist, the validated nodes form a safe guild allowlist; unrelated guilds cannot
match because unmatched nodes already default to excluded. Exclude-only scopes
still traverse every guild because a more-specific include is not available to
change their default. For every eligible thread-capable parent cleanup calls:

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

One parent's complete paginated thread result is normalized before cleanup
mutates that parent. Cleanup then processes the parent followed by its child
threads before discovering the next parent. This preserves hierarchy and
avoids a discovery/delete race without retaining every guild's thread result
in memory. The same nearest-target rules are applied only after each channel's
guild, category, and immediate parent IDs have been normalized.

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
the remaining guild work. These skips are diagnostic output, visible at the
established high-verbosity level rather than in normal output.

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

Without `--buffer-per-channel`, each fetched message is evaluated and acted on
before the next message is requested from the iterator. Buffered mode still
materializes exactly one channel and builds its complete plan before actions
begin. Owned-thread `self-only` scans and `all --dry-run` impact reporting also
force complete thread-history scans where their safety contract requires it.

Archived threads are handled specially. Discord's application API
documentation permits deleting messages there, but live user-account endpoints
rejected every tested direct archived-thread mutation with Discord code `50083`.
DMD therefore no longer emits one failing request per message. Archived
results force a buffered plan, PATCH the thread active only for a non-empty
plan, perform normal message and reaction cleanup, and then attempt to restore
the archived state. Internally, `temporary` requires known restoration rights,
while `allow-active` implements the default best-effort policy.

The preflight compares `owner_id` with the authenticated user and resolves
effective `MANAGE_THREADS` from the partial guild permission value, current
member role IDs, and parent-channel overwrites. Guild owners and administrators
bypass overwrites. Unknown permission data fails closed in `temporary`; the
default internal `allow-active` mode can continue for unlocked threads and
reports when the thread remains active. The public
`--skip-unrestorable-threads` flag selects the strict behavior.

Restorable transitions are recorded before activation in an owner-only local
journal. Restoration runs in `finally`, and the next non-dry cleanup retries any
entry left by an interrupted process. The journal is cleared only after the
thread is archived again or Discord reports that it is absent.

Message and reaction deletes preserve Discord code `50083` as a distinct
internal outcome. The cleaner uses it to refresh thread state, retry the blocked
action once per detected likely auto-archive event, or terminate the buffered
plan with an exact count of remaining actions. A thread confirmed archived
during that check is not sent a redundant final archive update. The same
recovery now applies when a thread started active: its initial
`archive_timestamp`, initial and refreshed `last_message_id`, configured
duration, lock state, and pin state provide a fail-closed inactivity-deadline
check before any reactivation.

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
is retained in the scan, logged whenever encountered, and treated as
non-deletable unless DMD knows that type is safe to delete.

## Request scheduler

The old pattern of sleeping unconditionally after fetches and deletions has
been replaced by `DiscordRequestScheduler` in
`delete_me_discord/discord/rate_limits.py`.

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

Network failures use exponential route backoff capped at 30 seconds with full
jitter. For Discord responses, DMD uses the longest valid relative
`retry_after`, numeric `Retry-After`, or `X-RateLimit-Reset-After` deadline and
adds the configured safety jitter. Standard HTTP-date `Retry-After` and absolute
`X-RateLimit-Reset` values are accepted only when no relative deadline exists,
avoiding unnecessary sensitivity to local clock skew. Server errors and hintless
`429` responses use full-jitter exponential backoff when no usable deadline
exists. Every HTTP request has a 10-second connect timeout and a 30-second read
timeout. Detailed wait/retry messages use the diagnostic logging level.

HTTP `408 Request Timeout` responses enter the same bounded transient retry
path as network failures and server errors. DMD only sends idempotent `GET` and
`DELETE` requests, so repeating these requests preserves the intended final
state.

### Delete outcomes are explicit

Message, reaction, and owned-thread delete operations return one of three
outcomes instead of a boolean:

- `deleted`: Discord confirmed that the request deleted the resource
- `absent`: Discord returned `404`, confirming the desired absent state without
  claiming which request or actor caused it
- `failed`: Discord confirmed that the operation could not be completed, such
  as a `403` permission response or invalid local reaction data

An `absent` result is terminal and is not reported as a failure. This handles
the ambiguous retry case where Discord applies a DELETE but its response is
lost, then answers the retry with `404`. Execution summaries report deleted,
absent, and failed counts separately. Retry exhaustion and unexpected protocol
responses still raise and abort instead of being flattened into `failed`.

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

## Planned work before the v3 release

The current `v3` branch is an integration checkpoint, not the final release
candidate. The following work is planned before merging it to `main`. This
section is forward-looking: items remain unimplemented until their changes and
tests land on the branch.

### Phase 1: long-running live Discord suite

Build an opt-in integration suite around multiple dedicated test accounts and
isolated fixture guilds. The initial M0 harness and its orchestrator now live
under `tests/live/`. They provide a default-off pytest
gate, strict loading from owner-only local `TOKEN_*` secrets or environment
variables, read-only account validation, and a private token-free run ledger
with run-ID ownership checks. Token values, credential-key names, account names,
and Discord IDs are never emitted to logs, exceptions, reports, test IDs, or CI
artifacts; output uses only ordinal handles such as `account-1`. The ignored
owner-only ledger may retain Discord IDs needed for ownership checks, but never
credential names or values. The suite does not run in the ordinary pull-request
gate. Fixture requests are serialized through a global process lock and a
central conservative scheduler: reads wait 1-2 seconds and mutations wait 3-6
seconds. The pinned user client owns Discord bucket and `retry_after` handling;
the harness adds no second retry loop. Uncertain creates are reconciled by
deterministic name, and duplicate matches stop the run.

Discord no longer documents whole-guild creation or deletion for applications.
The fixture controller consequently isolates an exact-commit `discord.py-self`
adapter in the `tests/live` uv project from DMD's API v10 cleanup
client. The adapter performs static login and fixture REST operations without
starting a Gateway connection. Its boundary is unit-tested, response bodies
remain private, and upstream contract drift fails closed before later fixture
phases run. A dedicated nested lockfile freezes this test tool while the
installable library root remains intentionally lock-free. On Linux, the Nix
development shell exposes the C++ runtime needed by the adapter's native
transport wheel without adding it to DMD's runtime closure.

M1 now also includes a resumable topology builder. Two persistent ledger-owned
guilds receive deterministic categories, message-bearing channel types,
Community configuration, member and permission roles, restricted-channel
overwrites, and all three non-owner fixture memberships. All four user-client
adapters share one scheduler, and invite inspection and acceptance are paced as
separate REST calls. Channels and roles are written to the private ledger after
each successful or reconciled mutation. A repeated run verifies resource ID,
name, kind, parent, permissions, guild, and ownership instead of recreating
them. Whole-guild teardown marks verified nested channels and roles terminal
with their parent guild.

The live run also verified the current Discord capability boundary: Forum,
Announcement, Stage, Voice, and Text fixtures were created, while Discord
rejected `GUILD_MEDIA` with error `50024` (`Cannot execute action on this
channel type`). That result is retained as an explicit unsupported capability,
so future runs do not retry it indefinitely or claim coverage that Discord did
not provide.

The current live ledger also contains a validated 216-message matrix across
nine scopes and active/archived public, announcement, private, and forum thread
fixtures. Both tested fixture accounts received HTTP 403 when the harness
probed Super Reactions. An isolated destructive check used ordinary DMD message
cleanup on a forum post containing only its starter message: the message was
deleted, while an independent API probe confirmed that the post's
`PublicThread` container remained present. Removing that container therefore
remains a separate, explicit owned-thread operation.

A guarded destructive contract runner now covers every available
message-bearing channel type without multiplying the retention-policy unit
matrix. Its preparation stage independently observes tracked subject messages,
foreign messages, subject reactions, foreign reactions, channel type, and
thread archive state, then requires an exact redacted DMD preview. Execution is
a separate opt-in command. It revalidates and previews each scope immediately
before cleanup, checkpoints scopes individually, and independently requires
subject messages and mutable subject reactions to be absent while foreign
messages, foreign reactions, and the channel/thread container remain. The live
matrix passed GuildText, GuildAnnouncement, GuildVoice chat, GuildStageVoice
chat, DM, GroupDM, and every active PublicThread, AnnouncementThread,
PrivateThread, and Forum-post form. Its first archived run recorded HTTP 400
rejection with messages, reactions, and containers unchanged. A subsequent
guarded replay processed those same archived fixtures through exact archived
thread selectors, removed subject messages and reactions, and independently
confirmed that foreign content and every
container remained while all four threads returned to archived state. The
restoration journal was empty after execution. `GUILD_MEDIA` stays an explicit
unsupported result rather than a false pass.

A separate isolated archived-thread race matrix now provides live transition
evidence. Its preview gate independently verified one target message, one
foreign message, one target reaction, and one foreign reaction in each of six
archived threads. Execution passed ordinary temporary restoration,
likely-auto-archive recovery with a controlled monotonic deadline, and locked
cleanup by an account with `MANAGE_THREADS`. Real manager-driven early archive,
lock-change, and second-archive transitions stopped cleanup without removing
the target artifacts. All foreign artifacts remained, final archive and lock
states matched policy, the retry stayed bounded, the dedicated restoration
journal was empty, and the ledger relocked. A later redacted read-only probe
confirmed that both exact channel lookup and thread search provide
`last_message_id`, `archive_timestamp`, `auto_archive_duration`, `archived`, and
`locked` for the live fixture; its exact-channel `last_message_id` matched the
newest fetched message. This validates the recovery input contract but does not
replace a future one-hour observation of Discord's natural auto-archive timer.

Discord may require an interactive CAPTCHA for user-account invite acceptance.
The suite treats that as a manual security boundary rather than attempting to
bypass it. A dedicated command can write two short-lived, usage-limited invite
links to an ignored owner-only state file without exposing codes or identities
in output. After each missing account accepts both links through its isolated
browser session, the normal topology command reconciles and continues.

The account topology, fixture matrix, safety model, milestones, and open
decisions are tracked in `LIVE_TEST_SUITE_PLAN.md`.

The fixture builder should create and evolve representative Discord state over
an extended run, potentially several hours, through an independent setup client
whose scheduler and rate-limit contracts are tested separately from the
application cleanup client. Fixtures should cover:

- guild text, announcement, forum, media, voice, and stage chat
- active and archived public, private, announcement, forum, and media threads
- ordinary and unknown message types, attachments, replies, polls, and system
  messages that are actually deletable by their author
- normal and Super Reactions, including both variants of the same emoji and a
  deleted custom emoji whose name is null
- ordinary-member and moderator permission boundaries for owned-thread deletion
- inaccessible, concurrently removed, and already absent resources

Every live scenario should have four explicit phases: fixture creation, dry-run
assertions, destructive execution, and postcondition verification. Fixture IDs
and ownership must be recorded in a per-run ledger so an interrupted run can be
resumed or cleaned up safely. Destructive cases require an explicit opt-in and
must refuse to operate on resources not created for that run. A concurrency lock
must prevent two destructive suites from sharing the same account or guild.

The dry-run and execution phases must evaluate the same planner output. Most
coverage should first prove that dry-run finds the intended artifacts without
mutation; a smaller but representative set should then perform real message,
reaction, and owned-thread cleanup and verify Discord's resulting state.

### Phase 2: persisted cleanup plans

Add a two-phase cleanup mode that records an exact non-mutating plan for later
execution:

```console
dmd clean --plan cleanup-plan.json [normal scope and retention options]
dmd clean --apply-plan cleanup-plan.json
```

`--plan` should use the same discovery and decision engine as `--dry-run`, but
write a versioned machine-readable file instead of relying on human-readable
terminal output. It must not mutate Discord. The plan should contain the
authenticated user ID, creation time, effective cleanup settings, schema
version, summary counts, and only the identifiers required for each selected
message, normal reaction, Super Reaction, and thread context. It must never
contain the token, message content, usernames, guild names, or channel names.
Because Discord IDs and timestamps are still sensitive metadata, the file must
be written atomically with owner-only permissions.

`--apply-plan` should verify the schema and authenticated account, then execute
only the recorded actions. It must not silently rediscover or append new
targets. Already absent artifacts should remain successful idempotent outcomes;
permission changes, inaccessible channels, malformed entries, and failed thread
activation or restoration must be reported explicitly. Archived-thread actions
should reuse the existing restoration coordinator and journal.

The original retention and time-window rules should not be re-evaluated while
applying a valid plan: the persisted plan is the reviewed decision made at
creation time. Execution should instead display the plan age and warn when it
may be stale. Tests must cover tampering, account mismatch, schema evolution,
partial execution, replay, already absent artifacts, changed permissions,
archived-thread transitions, atomic writes, and the absence of private content
or credentials in both the file and logs.

`--plan` and `--dry-run` should be mutually exclusive. `--apply-plan` should
also reject discovery, scope, retention, and ordinary planning options whose
meaning would be ambiguous once the target set has already been frozen.

### Phase 3: optional QR account login

Prototype `dmd login --qr` as a safer convenience layer over the existing token
and keyring flow. The current token prompt, `DISCORD_TOKEN`, and system-keyring
storage remain supported. QR approval should happen in the official Discord
mobile client so DMD never receives the account password or MFA secret.

The goal is for a successful QR flow to obtain the same Discord account token
that users can currently provide manually, validate it through `/users/@me`, and
store it with the existing `KeyringTokenStore`. Later runs should load that token
from the keyring without requiring another QR login, and logout should remove the
local token as it does today.

The exact remote-auth protocol, temporary handshake data, session lifecycle,
dependencies, and failure behavior still require research and live validation.
They must not be treated as settled design decisions in this migration plan. The
eventual implementation must still:

- never log tokens, passwords, MFA codes, QR payloads, or authentication request
  bodies
- fail closed when Discord requests CAPTCHA or an unsupported verification step
  and never integrate a CAPTCHA-solving service
- refuse plaintext credential fallback when the system keyring is unavailable
- retain token entry as the recovery path when QR login is unavailable
- use recorded protocol transcripts for deterministic tests before exercising
  the flow against dedicated live accounts

This must be a clean-room implementation. DMD is MIT licensed, while current
[Discordo](https://github.com/ayn2op/discordo) and
[Concord](https://github.com/chojs23/concord) releases are GPL and
[Endcord](https://github.com/sparklost/endcord) is source-available, so those
projects may inform expected behavior but their authentication code must not be
copied into DMD.

Discord's [documented OAuth2 scopes](https://docs.discord.com/developers/topics/oauth2)
do not provide the unrestricted historical message and reaction access required
by DMD. QR login therefore does not turn DMD into an official integration. The
command and documentation must retain an explicit warning that
[Discord prohibits automated normal-user accounts](https://support.discord.com/hc/en-us/articles/115002192352-Automated-User-Accounts-Self-Bots)
and may terminate accounts using unofficial automation.

Email/password login, MFA handling, device verification, and account recovery
are not v3 requirements. They would make DMD process primary account credentials
through a private, CAPTCHA-sensitive protocol and remain post-v3 research unless
a substantially safer supported flow becomes available.

### Phase 4: search-backed own-message discovery

Add message search as an alternative discovery strategy when
`--keep-reactions` is enabled. In that mode DMD only needs the authenticated
user's messages, so walking every message merely to inspect reaction ownership
is unnecessary. Search must not be used for normal reaction-cleanup runs because
search results omit reaction data and cannot discover the user's reactions on
messages authored by other users.

Search is a candidate source of message IDs, not immediate deletion authority.
Each candidate must be re-fetched before planning or mutation so current author,
message type, timestamp, channel state, retention rules, and access are checked
through the same path as traversal results. The implementation must also:

- handle Discord's `202` indexing responses through the request scheduler
- deduplicate results and partition searches by time or snowflake boundaries
  instead of relying on the capped offset range for large histories
- preserve scope filters, thread exclusions, `--fetch-within`,
  `--max-messages`, keep rules, dry-run output, and deletion ordering
- fall back to channel traversal when search is unavailable or cannot prove the
  required completeness
- compare search and traversal plans against the same live fixtures before
  search becomes the default for eligible runs

Search may eventually make preserve-cache unnecessary for search-backed,
own-message-only runs because retained messages can be rediscovered on the next
run. That is a hypothesis, not yet a migration decision. The cache must remain
available until live tests establish search completeness across guilds, DMs,
Group DMs, private threads, archived threads, indexing delays, and long
histories. It may remain useful as a fallback even after search ships.

### Phase 5: batched retention and reaction traversal

Reaction cleanup still needs channel history traversal. Incremental retention
also has a concrete inefficiency today: every preserved cached ID that is not in
the main stream is re-fetched with its own `around=<id>&limit=1` request.

Replace that point-fetch loop with a per-channel window planner. It should keep
cached IDs in descending snowflake order, group nearby targets, and fetch
bounded `around`, `before`, or `after` history pages so one response can satisfy
multiple cached IDs. Sparse targets should retain a point-fetch fallback when a
larger history page would cost more. Snowflake timestamps can propose windows,
but the planner must adapt to actual message density rather than assuming that
time proximity guarantees page proximity.

This optimization must preserve the current merge contract: newest-to-oldest
ordering, deduplication against the main stream, exact retention decisions,
lazy per-channel processing, and graceful handling of deleted or inaccessible
messages. Tests and live metrics should compare API request count and elapsed
time for sparse, clustered, and mixed cache layouts before selecting the default
window policy.

### Release readiness gate

The v3 release candidate is ready to merge only after:

1. the normal cross-platform `Test` workflow passes on the final branch SHA
2. the live suite passes its dry-run, destructive, permission, and recovery
   scenarios against isolated Discord fixtures
3. persisted cleanup plans preserve planner equivalence, privacy, account
   binding, idempotent replay, and archived-thread recovery, if included in v3
4. QR authentication preserves token redaction, keyring-only storage, CAPTCHA
   failure, and existing token-login behavior, if it is included in v3
5. search-backed discovery has plan-equivalence coverage and a traversal
   fallback, if it is included in v3
6. batched cache/history fetching preserves existing retention semantics, if it
   is included in v3
7. the migration guide, user documentation, artifact audit, and release notes
   describe the final behavior rather than planned behavior

## Operational constraints

- The default v3 scope is intentionally broader. Upgrade notes and the release
  commit must call out thread inclusion as a breaking change.
- Thread discovery cost scales with the number of visible thread-capable parent
  channels and result pages. Cleanup starts processing before every parent has
  been searched, including with explicit ID filters. Exact-ID preflight still
  completes before mutation, while only channel-tree listings complete thread
  discovery first. `--exclude-threads` is the explicit fast path.
- Incremental cleanup is not transactional. If a fatal API or discovery error
  occurs under a later parent, channels processed earlier in the run remain
  changed. Use a dry-run first; per-parent permission failures continue to be
  skipped rather than aborting the run.
- DMD can only discover threads visible to the authenticated user. Per-parent
  permission failures are skipped and do not imply that no thread exists.
- Archived content is included by default but activated only for a non-empty
  cleanup plan. Activation changes archive timestamps and is best-effort rather
  than atomic. `--skip-unrestorable-threads` requires a reasonable restoration
  guarantee before scanning.
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
| CLI and orchestration | `delete_me_discord/cli/commands.py`, `delete_me_discord/cli/parser.py`, `delete_me_discord/cli/logging.py` |
| Profiles and effective settings | `delete_me_discord/config/models.py`, `delete_me_discord/config/schema.py`, `delete_me_discord/config/profiles.py` |
| Channel model and filters | `delete_me_discord/discord/channel_types.py`, `delete_me_discord/scope/filter.py` |
| Scope IDs, rules, and inventory | `delete_me_discord/scope/resolver.py`, `delete_me_discord/scope/rules.py`, `delete_me_discord/scope/inventory.py` |
| Thread API, archived-state policy, and rate scheduling | `delete_me_discord/discord/client.py`, `delete_me_discord/discord/transport.py`, `delete_me_discord/cleanup/threads.py`, `delete_me_discord/cleanup/thread_recovery.py`, `delete_me_discord/cleanup/thread_session.py`, `delete_me_discord/discord/rate_limits.py` |
| Listing and rendering | `delete_me_discord/discovery/service.py`, `delete_me_discord/discovery/renderers.py` |
| Cleanup semantics | `delete_me_discord/cleanup/service.py`, `delete_me_discord/cleanup/planner.py`, `delete_me_discord/cleanup/executor.py`, `delete_me_discord/cleanup/thread_deletion.py`, `delete_me_discord/cleanup/models.py` |
| Durable local state | `delete_me_discord/storage.py`, `delete_me_discord/auth/keyring.py`, `delete_me_discord/cleanup/preserve_cache.py` |
| Version and packaging | `delete_me_discord/_version.py`, `pyproject.toml`, `MANIFEST.in`, `delete_me_discord.spec`, `tools/verify_distribution.py` |
| CI/CD | `.github/workflows/test.yml`, `.github/workflows/release.yml`, `.github/workflows/docs.yml`, `.github/workflows/pyinstaller.yml` |
| Development environment | `flake.nix`, `flake.lock` |
| Live Discord validation | `tests/live/`, `tests/test_live_suite.py` |
| Documentation | `README.md`, `MIGRATION_V3.md`, `DISCORD_USER_ARTIFACT_AUDIT.md`, `LIVE_TEST_SUITE_PLAN.md`, `docs/src/content/docs/`, `docs/package.json`, `docs/pnpm-workspace.yaml` |
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
