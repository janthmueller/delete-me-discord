# Profile Management Feature Plan

This document captures the next profile-related feature set after the initial
profile support shipped.

It is an internal planning note, not public documentation.

## Goal

Add basic profile management commands so users can inspect and maintain profile
entries without editing `config.json` by hand for every change.

This should stay focused on profile CRUD, not scheduling or daemon behavior.

## Scope

Planned commands:

- `dmd list profiles`
- `dmd profile show <name>`
- `dmd profile add <name> --set key=value [--set key=value ...]`
- `dmd profile update <name> --set key=value [--set key=value ...]`
- `dmd profile remove <name>`

Already implemented:

- `dmd list profiles`
- `dmd clean --profile <name>`

## Command semantics

### `dmd profile show <name>`

Show the stored profile exactly as it exists in config.

Output:

- normal mode: readable JSON object
- `--json`: raw JSON object

Behavior:

- fail if the profile does not exist

### `dmd profile add <name> --set key=value ...`

Create a new profile entry.

Behavior:

- fail if the profile already exists
- only accept fields that are valid profile fields
- write only explicitly provided fields from `--set`
- do not store omitted fields

### `dmd profile update <name> --set key=value ...`

Update an existing profile entry.

Behavior:

- fail if the profile does not exist
- only change explicitly provided fields from `--set`
- preserve all untouched fields

### `dmd profile remove <name>`

Remove an existing profile entry.

Behavior:

- fail if the profile does not exist
- remove only that profile entry
- preserve all other config content

## Command-level flags vs stored fields

The profile management commands should not reuse the full `clean` CLI flag
surface directly for stored values.

Reason:

- flags like `--json`, `--quiet`, or `-v` would become ambiguous
- they could either mean:
  - affect the `profile add/update/show/remove` command itself
  - or be written into the stored profile

To avoid this ambiguity:

- command-level behavior uses normal CLI flags
- stored profile values use `--set key=value`

Examples:

```bash
dmd profile add nightly-dms --json --set keep_within=2w --set verbose=1
```

Meaning:

- `--json` controls the output of this command
- `--set keep_within=2w` writes `keep_within`
- `--set verbose=1` writes `verbose`

## Allowed stored fields

`add` and `update` should only allow profile-allowed fields through `--set`.

Allowed:

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

Not allowed:

- `token`
- `config_path`
- `profile`
- `version`

## Field naming

Two naming conventions remain intentional:

- config/profile fields use `snake_case`
- CLI flags use `kebab-case`

Examples:

- config: `keep_last`, `preserve_cache_path`
- CLI: `--keep-last`, `--preserve-cache-path`

The management commands should keep this split. Internally they map CLI names
to profile field names.

For `--set`, the stored field names should be used directly:

- `--set keep_last=20`
- `--set keep_within=2w`
- `--set preserve_cache=true`

That avoids an unnecessary extra name mapping layer.

## Unset behavior

`update` needs a way to remove existing profile fields.

Use:

```bash
dmd profile update nightly-dms --unset fetch_within max_messages
```

Rules:

- `--unset` accepts one or more profile field names
- field names are the config names (`snake_case`)
- unknown field names are an error
- duplicates should be deduplicated
- a field may not be both set and unset in the same command

## `--set` value parsing

`--set` should parse values according to the target profile field.

Examples:

- `--set keep_last=20`
- `--set keep_within=2w`
- `--set preserve_cache=true`
- `--set verbose=2`
- `--set max_messages=none`

Rules:

- field names are the stored config names (`snake_case`)
- unknown fields are an error
- invalid values are validated the same way as profile values loaded from config
- `none` should be accepted for nullable fields such as:
  - `fetch_within`
  - `max_messages`
- `null` should not be supported

This is a hard requirement:

- `profile add` and `profile update` must not introduce a second, looser parsing path
- the same validation rules must apply whether a profile is:
  - written manually in `config.json`
  - or created/updated through CLI management commands

Examples:

- `--set keep_last=abc` must fail
- `--set keep_within=banana` must fail
- `--set preserve_cache=maybe` must fail
- `--set verbose=9` must fail

## Null behavior

Do not allow `null` as a stored value.

Rule:

- omitted field = no statement
- present field = explicit value

This remains consistent with the first profile version.

## Special value behavior

For nullable CLI options, follow the runtime convention already in place:

- `--fetch-within none`
- `--max-messages none`

This should also apply to profile management commands when setting those fields.

Stored config values should still be normal JSON values:

- strings for time deltas
- integers for message limits
- field removal via `--unset`, not `null`

## Config write rules

Management commands must preserve unrelated config content.

Examples:

- auth token must survive profile add/update/remove
- other profiles must survive profile add/update/remove
- formatting can be normalized on write

Expected write shape:

- JSON object
- `indent=2`
- `sort_keys=True`

## Validation

Validation should reuse the same profile rules as `dmd clean --profile`.

That means:

- one validation source of truth
- same error semantics for invalid values
- same allowed field set

## Suggested implementation order

1. Add config read/write helpers for the `profiles` object
2. Add `dmd profile show <name>`
3. Add `dmd profile add <name> ...`
4. Add `dmd profile update <name> ...`
5. Add `--unset` support
6. Add `dmd profile remove <name>`
7. Add docs and tests

## Test checklist

- show existing profile
- show missing profile -> error
- add new profile
- add duplicate profile -> error
- update existing profile
- update missing profile -> error
- remove existing profile
- remove missing profile -> error
- `--unset` removes fields
- `--unset` rejects unknown fields
- set and unset same field in one command -> error
- auth token survives all profile mutations
- unrelated profiles survive all profile mutations
