# Profiles Feature Plan

This document is an internal planning note for the next major feature after the
v2 CLI migration.

The goal is to make `delete-me-discord` more self-contained by allowing named
cleanup presets in the main config file. These presets are called **profiles**.

It is not public documentation yet.

## Goals

- Let users save reusable `clean` configurations under stable names.
- Reduce long, error-prone command lines.
- Establish the foundation for later scheduling or service-style execution.
- Keep the first implementation small and predictable.

## Core model

The main config file stays:

- `~/.config/delete-me-discord/config.json`

The file can contain:

- auth data
- profiles

Example:

```json
{
  "auth": {
    "token": "..."
  },
  "profiles": {
    "nightly-dms": {
      "include_ids": ["123456789012345678"],
      "keep_last": 0,
      "keep_last_scope": "all",
      "keep_within": "2w",
      "fetch_within": "2w1d",
      "preserve_cache": true
    }
  }
}
```

## First CLI surface

The first version should stay small:

- `dmd clean --profile <name>`
- `dmd list profiles`

Not in the first version:

- `profile init`
- `profile create`
- `profile edit`
- scheduling
- daemon/service mode

## Why `profile`

Use `profile`, not `config`.

Terminology:

- `config` = the whole file
- `profile` = one named cleanup preset inside that file

This keeps the naming clean:

- `--config-path` points to the file
- `--profile nightly-dms` selects one cleanup preset from that file

## Command behavior

### `dmd clean --profile <name>`

Loads the selected profile from `config.json`, merges it onto the normal
`clean` defaults, and then applies explicit CLI flags on top.

### `dmd list profiles`

Lists the names of available profiles from the config file.

For the first version, this can stay intentionally simple:

- one profile name per line in normal output
- JSON array in `--json`

## Merge order

This is the most important behavioral rule:

1. built-in parser defaults
2. selected profile values
3. explicit CLI flags

Meaning:

- a profile acts as a named preset
- the CLI still wins when the user wants a one-off override

Example:

```bash
dmd clean --profile nightly-dms --keep-last 50
```

This should:

- load `nightly-dms`
- then override its `keep_last` with `50`

## Config value presence

Profiles should not use `null` to mean “not set”.

Rule:

- missing field = the profile makes no statement about this option
- present field = the profile intentionally sets this option

Examples:

- omit `keep_within` if the profile does not want to override it
- omit `verbose` if the profile does not want to control output detail
- do not use `keep_within: null`
- do not use `verbose: null`

This keeps merge behavior simple:

- parser defaults apply first
- only present profile fields override those defaults
- explicit CLI flags can still override the resulting value

## What belongs in a profile

Profiles should describe a reusable **run preset** for `dmd clean`.

Allowed in the first version:

- `include_ids`
- `exclude_ids`
- `keep_last`
- `keep_last_scope`
- `keep_within`
- `fetch_within`
- `max_messages`
- `buffer_per_channel`
- `keep_reactions`
- `preserve_cache`
- `preserve_cache_path`
- `max_retries`
- `retry_time_buffer`
- `fetch_sleep_time`
- `delete_sleep_time`
- `dry_run`
- `quiet`
- `verbose`
- `json`
- `redact_sensitive`

Not allowed in the first version:

- `token`
- `config_path`
- `profile`
- `version`

Reason:

- profiles should be able to represent a complete typical `clean` run
- but they should not control auth source, config file selection, or CLI meta behavior

## Preserve cache behavior

Profiles should interact with preserve cache slightly differently from normal
ad hoc runs.

### Rule

If:

- a profile is active
- `preserve_cache` is enabled
- and no explicit preserve-cache path is provided

then a profile-specific cache path should be used automatically.

### Path precedence

1. explicit CLI `--preserve-cache-path`
2. `preserve_cache_path` from the selected profile
3. auto-derived profile cache path
4. normal global default path

### Auto-derived profile cache path

Preferred shape:

```text
~/.config/delete-me-discord/preserve-cache/<profile>.json
```

Examples:

- no profile:
  - `~/.config/delete-me-discord/preserve_cache.json`
- profile `nightly-dms`:
  - `~/.config/delete-me-discord/preserve-cache/nightly-dms.json`

### Why this exception is worth it

This is one of the few justified special cases because it prevents unrelated
profiles from silently sharing the same cache file.

Without it, profile-based repeated runs would easily step on each other.

## Dry-run behavior

`dry_run` may be stored in a profile.

This is useful for “safe preview” profiles that are intentionally meant to be
reused without immediately executing destructive actions.

## Output controls in profiles

Profiles may include:

- `quiet`
- `verbose`
- `json`
- `redact_sensitive`

### `verbose`

In profiles, `verbose` should be an integer level:

- `0` = default output
- `1` = equivalent to `-v`
- `2` = equivalent to `-vv`
- `3` = equivalent to `-vvv`

It is not a boolean in the config model.

### `quiet`

`quiet` remains a boolean:

- `true` = warnings/errors only
- `false` = normal verbosity behavior

### Conflict rule

If both are effectively set:

- `quiet=true` wins
- `verbose` is ignored for that run

Existing preserve-cache dry-run behavior should continue to apply:

- dry-run cache files use the `.dryrun.json` suffix

This should apply after the effective preserve-cache path has been resolved.

## Validation

The first implementation should validate profiles strictly.

Errors should cover:

- missing `profiles` object when `--profile` is used
- unknown profile name
- unknown fields inside a profile
- wrong field types
- invalid time delta strings
- invalid random range values
- conflicting output controls where resolution is ambiguous

The config should fail early with a clear message instead of silently ignoring
bad input.

## Suggested implementation order

1. Add config loading support for `profiles`
2. Define allowed profile fields and validation
3. Add `--profile` to `clean`
4. Merge profile values into `clean` args
5. Implement profile-specific preserve-cache path derivation
6. Add `dmd list profiles`
7. Add tests and docs

## Suggested tests

### Parser / orchestration

- `dmd clean --profile nightly-dms`
- explicit CLI flags override profile values
- missing profile name errors clearly

### Config validation

- unknown profile name
- invalid profile field
- invalid time delta in profile
- invalid random range in profile

### Preserve cache

- profile with `preserve_cache=true` and no path gets derived profile path
- CLI `--preserve-cache-path` overrides profile/default
- profile `preserve_cache_path` overrides auto-derived path
- dry-run still appends `.dryrun.json`

### Discovery

- `dmd list profiles`

## Out of scope for first version

- scheduling
- background daemon/service execution
- profile mutation commands
- global cleanup defaults outside profiles
- profile inheritance
- profile composition

## Likely next step after profiles

Once profiles are stable, the next logical layer is scheduling.

At that point:

- profile = what to run
- schedule = when to run

This is a better progression than jumping directly to a daemon.

## Implementation plan

The implementation should start from the abstract settings model, not from CLI
commands.

The key idea is:

- parser defaults already define the base shape
- a profile is a partial set of overrides for `clean`
- explicit CLI flags can override both

So the first real artifact should be an **effective clean settings** merge
layer.

### Phase 1: Define the settings model

Add one internal representation for the clean settings that profiles are
allowed to influence.

This should be independent from `argparse`.

Suggested shape:

- one typed dict / dataclass / small model for profile-supported `clean` fields
- one function that can normalize raw config JSON into that model

Responsibilities:

- hold only profile-allowed fields
- distinguish “field not present” from “field present with value”
- normalize time delta strings
- normalize random range arrays
- reject unknown keys

This phase should not yet change command behavior.

### Phase 2: Define merge semantics explicitly

Add a single merge function that produces **effective clean settings**.

Conceptually:

```text
effective_clean_settings =
  merge(
    parser_defaults,
    selected_profile_overrides,
    explicit_cli_overrides,
  )
```

Important constraint:

- explicit CLI overrides must only apply when the user actually set a flag
- a parser default must not accidentally mask a profile value

That means the implementation should detect which CLI values were explicitly
provided rather than blindly trusting the parsed namespace.

The cleanest route is likely:

- inspect raw argv for explicit flags
- or maintain a dedicated override map during parsing

The merge layer should be written once and then reused by:

- `dmd clean --profile ...`
- later scheduling
- later daemon/service mode, if it ever exists

### Phase 3: Add config loading helpers

Extend the config support around `config.json`.

Suggested responsibilities:

- load config root safely
- read `profiles`
- return one named profile
- validate shape and field types

Likely helper functions:

- `load_config(path)`
- `load_profiles(path)`
- `load_profile(path, name)`

These should live near auth/config code rather than inside the cleaner.

### Phase 4: Add CLI surface

Add the two first-profile entry points:

- `dmd clean --profile <name>`
- `dmd list profiles`

Parser changes:

- add `--profile` to `clean`
- extend `list` subcommands with `profiles`

Behavior:

- `dmd clean --profile x` loads and merges profile `x`
- `dmd list profiles` prints the configured profile names

### Phase 5: Resolve preserve-cache path

After the effective clean settings are known, resolve preserve-cache path with
the agreed precedence:

1. explicit CLI `--preserve-cache-path`
2. `preserve_cache_path` from the selected profile
3. auto-derived profile cache path
4. global default path

This should be a dedicated helper, not open-coded inside `_run_clean()`.

Suggested helper:

- `resolve_preserve_cache_path(profile_name, preserve_cache_enabled, explicit_path, profile_path)`

That helper should also apply the existing dry-run suffix rule after the final
base path is chosen.

### Phase 6: Wire clean execution through effective settings

Once the effective settings object exists:

- `_run_clean()` should stop manually reading every field from `args`
- instead it should read from the merged effective clean settings object

That is the main architectural payoff of the feature.

This also reduces the chance that profile support becomes a pile of one-off
`if args.profile` branches.

### Phase 7: Implement `list profiles`

Keep it intentionally small:

- normal output:
  - one profile name per line
- JSON output:
  - array of strings

No extra metadata needed in the first version.

### Phase 8: Tests

Recommended order:

1. unit tests for profile validation
2. unit tests for merge logic
3. unit tests for preserve-cache path resolution
4. parser/orchestration tests for `--profile`
5. parser/orchestration tests for `dmd list profiles`

Most important cases:

- profile overrides parser defaults
- explicit CLI flags override profile values
- omitted profile fields do not override defaults
- unknown profile fields fail loudly
- `verbose` accepts only `0..3`
- `quiet=true` wins over `verbose`
- profile-specific preserve cache path is derived correctly
- dry-run still appends `.dryrun.json`

### Phase 9: Docs

After implementation, update:

- `getting-started/authentication`
- `getting-started/first-run`
- `reference/cli-options`
- `reference/preserve-cache`
- maybe add a dedicated `guides/profiles` page

The docs should explain:

- what a profile is
- where it lives
- how `--profile` works
- how CLI flags override profile values
- how preserve-cache behaves with profiles

## Suggested implementation order in practice

If implemented as small commits, a clean order would be:

1. internal profile/config model + validation
2. merge layer for effective clean settings
3. `--profile` support in `clean`
4. preserve-cache path resolution for profiles
5. `dmd list profiles`
6. tests
7. docs

## Main architectural rule

Do not scatter profile logic across:

- parser code
- `_run_clean()`
- preserve cache handling
- cleaner setup

Instead:

- load profile once
- merge once
- resolve effective clean settings once
- then run the existing clean pipeline from that resolved object

If this rule is respected, the feature should stay manageable and provide a
clean base for later scheduling.
