# Architecture Roadmap

This document tracks the v3 structural refactor. It describes the intended
ownership boundaries and migration order so the project does not stop halfway
between the original flat package and the target package layout.

The target is deliberately practical rather than absolute. Small, focused
modules may remain at package root when another package would add indirection
without clarifying ownership.

## Principles

- Finish structural work before adding planned v3 features such as persisted
  cleanup plans or message-search discovery.
- Preserve behavior while moving code. Each refactor checkpoint must pass the
  existing unit, live-harness unit, packaging, and Nix checks.
- Keep Discord transport, cleanup policy, execution, presentation, and CLI
  composition separate.
- Keep cleanup planning deterministic and independent from API calls, logging,
  storage, and execution.
- Use typed options and result models at module boundaries instead of mutable
  dictionaries.
- Do not retain compatibility shims solely for unreleased v3 internals. Keep a
  compatibility surface only when it is an intentional public Python API.
- Avoid moving a large function unchanged merely to satisfy the target tree.
  Split responsibilities before or during a module move.

## Target Package Layout

```text
delete_me_discord/
├── __init__.py             # Public package metadata only
├── __main__.py             # CLI entry point
├── _version.py
├── logging.py              # Application log levels and logger extensions
│
├── cli/
│   ├── parser.py           # Argument definitions
│   ├── commands.py         # Command dispatch
│   └── logging.py          # Verbosity and output formatting
│
├── config/
│   ├── models.py           # Typed effective settings
│   ├── schema.py           # Defaults and validation
│   └── profiles.py         # Profile loading, migration, persistence
│
├── discord/
│   ├── client.py           # Discord endpoint operations
│   ├── transport.py        # HTTP requests, retries, error translation
│   ├── rate_limits.py      # Scheduler and endpoint policies
│   ├── errors.py           # HTTP and Discord failure types
│   ├── models.py           # Discord payload types and operation outcomes
│   ├── channel_types.py    # Channel enums, groups, and predicates
│   └── type_enums.py       # Message and reaction enums
│
├── cleanup/
│   ├── service.py          # Run and per-channel orchestration
│   ├── planner.py          # Pure retention and action decisions
│   ├── executor.py         # Message and reaction execution
│   ├── threads.py          # Archived state, permissions, and restoration
│   ├── thread_deletion.py  # Creator-owned thread deletion policy
│   ├── reporting.py        # Dry-run and execution summaries
│   ├── models.py           # Plans, actions, options, and statistics
│   └── preserve_cache.py
│
├── scope/
│   ├── selectors.py        # Parse user selectors
│   ├── rules.py            # Hierarchical ID semantics
│   ├── filter.py           # Channel type and thread-state filtering
│   ├── resolver.py         # Explicit-ID API validation
│   └── inventory.py        # Lazy and eager scope discovery
│
├── discovery/
│   ├── service.py          # Guild and channel listing
│   └── renderers.py        # Rich and JSON output
│
├── auth/
│   ├── service.py          # Authentication command behavior
│   └── keyring.py          # Credential persistence
│
├── privacy.py
└── storage.py
```

## Current State

The first cleanup refactor has established:

- `cleanup/planner.py` for pure message and reaction decisions.
- `cleanup/executor.py` for concrete or simulated actions.
- `cleanup/reporting.py` for channel and run reporting.
- `cleanup/models.py` for plans, policies, typed options, and typed statistics.
- A small run-level `clean_messages()` coordinator.
- A separate per-channel cleanup transaction.

`cleanup/service.py` now owns the cleanup facade, scope iteration, message
fetching and buffering, preserve-cache merging, and the per-channel
transaction. Archived-thread activation and restoration are isolated in
`cleanup/threads.py`, creator-owned thread deletion policy is isolated in
`cleanup/thread_deletion.py`, and cache persistence is isolated in
`cleanup/preserve_cache.py`. The former root cleanup implementation modules
have been removed.

Scope selector parsing, hierarchy rules, type/state filtering, explicit-ID
resolution, and lazy/eager inventory traversal now live under `scope/`. The
shared `should_include_channel()` policy moved out of `utils.py` with them.

Discord HTTP execution now lives in `discord/transport.py`; endpoint operations
live in `discord/client.py`. Rate-limit scheduling, response errors, payload
models, and Discord enums are colocated under `discord/`, and the former root
Discord implementation modules have been removed.

CLI construction, dispatch, verbosity, and output formatting now live under
`cli/`. The package root exports version metadata only, while installed scripts
and `python -m delete_me_discord` enter through the CLI package. Effective
settings, profile schema/defaults, and profile persistence now live under
`config/`; schema validation requires every cleanup default to be classified as
profile-supported or runtime-only.

## Refactor Phases

### Phase 1: Complete The Cleanup Package

- [x] Extract deterministic cleanup planning.
- [x] Extract action execution.
- [x] Extract cleanup reporting.
- [x] Introduce typed cleanup options and result statistics.
- [x] Separate run orchestration from per-channel processing.
- [x] Move cleanup orchestration to `cleanup/service.py`.
- [x] Move archived-thread activation and restoration to `cleanup/threads.py`.
- [x] Move owned-thread deletion coordination to
  `cleanup/thread_deletion.py`.
- [x] Move preserve-cache behavior to `cleanup/preserve_cache.py`.
- [x] Update callers and tests to import the cleanup package.
- [x] Remove root `cleaner.py`.

Completion criteria:

- Root has no cleanup implementation module.
- The cleanup service coordinates discovery, planning, execution, thread
  lifecycle, caching, and reporting through explicit collaborators.
- `cleanup/planner.py` remains API-free and side-effect-free.

### Phase 2: Consolidate Scope And Discovery

- [x] Create the `scope/` package.
- [x] Move selector parsing, hierarchy rules, type/state filters, explicit-ID
  resolution, and inventory discovery to their target modules.
- [x] Remove duplicated eager/lazy filtering decisions.
- [x] Create the `discovery/` package and move list rendering into it.
- [x] Remove the root scope and discovery implementation modules.

Completion criteria:

- One documented scope decision path is shared by list and clean commands.
- Explicit IDs, parent overrides, type exclusions, and thread states retain
  their current tested semantics.

### Phase 3: Split Discord Transport From Operations

- [x] Create `discord/transport.py` for requests, retries, error translation,
  and response decoding.
- [x] Create `discord/client.py` for endpoint-level operations.
- [x] Move rate-limit scheduling and policies under `discord/`.
- [x] Move payload definitions, outcomes, errors, and Discord enums under
  `discord/`.
- [x] Remove root `api.py`, `rate_limits.py`, `models.py`, `channel_types.py`,
  and `type_enums.py`.

Completion criteria:

- Endpoint methods contain no retry-loop implementation.
- Transport code contains no cleanup or discovery policy.
- Discord payloads are normalized at one boundary.

### Phase 4: Separate CLI And Configuration

- [x] Move argument construction to `cli/parser.py`.
- [x] Move command dispatch from root `__init__.py` to `cli/commands.py`.
- [x] Move shared log levels out of `utils.py` and CLI presentation to
  `cli/logging.py`.
- [x] Add a minimal `__main__.py`.
- [x] Reduce `__init__.py` to package metadata and intentional exports.
- [x] Split settings, schema/defaults, and profile persistence into `config/`.
- [x] Generate or validate CLI/profile defaults from one authoritative schema.
- [x] Remove root `options.py` and `app_config.py`.

Completion criteria:

- Importing `delete_me_discord` does not construct or run the CLI.
- CLI and profiles resolve to the same typed effective settings.
- Configuration defaults are not duplicated across parser and profile code.

### Phase 5: Finish Supporting Boundaries

- [ ] Split authentication commands from keyring persistence.
- [ ] Move every remaining `utils.py` helper to its owning module.
- [ ] Remove `utils.py`.
- [ ] Review whether `privacy.py` and `storage.py` remain clearer at root or
  belong under a small infrastructure package.
- [ ] Run a final import-cycle and package-surface audit.

## Deferred Features

The following remain design notes only until the structural phases above are
complete:

- Persisted cleanup plans and later plan execution.
- Search-based message discovery.
- Retention-cache batching improvements.
- Unofficial account login and QR research.
- Expanded live integration scenarios.

Feature designs belong in `MIGRATION_V3.md`; this file tracks only structural
ownership and refactor progress.
