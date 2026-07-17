# Live Discord Test Suite Plan

Status: M1 bootstrap and the initial M2 volume gate are complete. The opt-in test gate, strict local secret
loading, identity-free output, read-only account validation, private run ledger,
global process lock, shared conservative request pacing, resumable guild and
topology bootstrap, CAPTCHA-safe membership handoff, and verified recursive
guild teardown are implemented and validated live with four dedicated accounts.
Discord rejected the documented-in-development `GUILD_MEDIA` fixture channel
with error `50024`; that capability is recorded explicitly in the private
ledger and is not treated as a successful channel. The fixture adapter now has
an opt-in content phase for resumable messages, reactions, and public threads;
the live content, redacted dry-run gate, isolated destructive smoke, and
216-message volume matrix are now validated. The active/archived public,
announcement, private, and forum thread matrix also passes the expanded
redacted dry-run. Super Reaction probes were rejected with HTTP 403 for both
tested fixture accounts, and the unavailable Media capability remains recorded
explicitly. An isolated destructive smoke confirmed that ordinary cleanup can
delete a forum starter message while its `PublicThread` container remains
present. The guarded destructive contract matrix now passes every available
direct message-bearing type and active thread form while preserving tracked
foreign messages, foreign reactions, and containers. All four archived thread
forms consistently rejected direct message deletion with HTTP 400 and were
verified unchanged. The guarded replay now passes those same Public,
Announcement, Private, and Forum fixtures through temporary activation: subject
messages and reactions are removed, foreign messages and reactions remain, each
container remains present, and every thread is independently observed as
archived again. No restoration-journal entries remain after the run.

This document defines the long-running live test suite planned before the v3
release. It complements `MIGRATION_V3.md` and
`DISCORD_USER_ARTIFACT_AUDIT.md`. Unit tests remain the primary fast correctness
gate; this suite validates Discord behavior that mocks and public documentation
cannot prove.

## Goals

The suite should prove that DMD behaves correctly against real Discord state
with multiple interacting users. It must cover both discovery and mutation:

- create representative channels, threads, messages, and reactions
- let several accounts interact with the same messages and containers
- allow state to evolve over a longer run rather than testing only fresh data
- verify dry-run selection and impact reporting before any destructive action
- execute a smaller representative cleanup set and verify the final Discord
  state independently
- retain enough state to recover and clean up after an interrupted run
- produce useful timing and request-count data for later search and retention
  optimizations

The suite is not intended to automate account registration, solve CAPTCHA, load
test public communities, or run against personal guilds and conversations.

## Safety invariants

These requirements apply before the first destructive live test is enabled:

1. All accounts and guilds are dedicated fixtures. No personal account or guild
   may be accepted by the destructive runner.
2. Every generated resource carries a unique run ID in its name or ledger
   metadata, for example `dmd-live-20260712-ab12`.
3. The ignored, owner-only fixture ledger records generic fixture roles and the
   Discord IDs required for ownership checks. It is private runtime state, not a
   report or CI artifact, and never records credential key names, tokens,
   passwords, MFA values, or QR payloads.
4. Tokens are supplied through `TOKEN_*` environment variables or the local
   `tests/live/secrets.env` file. The file must be ignored by Git, owner-readable
   only (`0600` on POSIX), and parsed as data rather than sourced as shell code.
   The `TOKEN_*` key names and values are both confidential. Command-line token
   arguments remain forbidden. Moving the local file into a dedicated
   live-suite keyring is a later hardening step.
5. Logs, exceptions, pytest IDs, snapshots, and CI artifacts must be checked for
   token, credential-key, account-name, and Discord-ID leakage before live
   execution is enabled. Local and CI output use the same redaction policy and
   identify credentials only as `account-1`, `account-2`, and so on. Live
   pytest runs reject `--showlocals` so traceback locals cannot bypass this
   policy.
6. The normal pull-request and push workflows never run the live suite. It is a
   local or explicitly dispatched workflow with protected secrets.
7. All accounts use one stable network egress endpoint for the complete run. It
   may be the normal local network or one fixed VPN endpoint. Rotating VPN
   locations, per-account proxies, and endpoint changes during a run are
   forbidden. The endpoint is for privacy and reproducibility, never for
   bypassing Discord limits or enforcement.
8. A global suite lock prevents two runs from mutating the same account or guild.
9. Dry-run verification must pass before the destructive phase can unlock.
10. The destructive runner refuses any resource that is absent from the current
   run ledger or whose observed owner/guild does not match the ledger.
11. Teardown is idempotent and can resume from a partially completed ledger.
12. The suite must not deliberately spam endpoints, force account restrictions,
    or repeatedly provoke rate limits. Natural scheduler behavior is observed;
    synthetic 408/429/5xx cases remain deterministic unit tests.
13. Fixture operations are globally serialized and use randomized conservative
    spacing: 1-2 seconds for reads and 3-6 seconds for mutations. The pinned user
    client observes Discord's rate-limit buckets and `retry_after` values and may
    repeat narrowly selected transient failures. The harness adds no second
    retry loop; uncertain creates are reconciled by deterministic name and any
    duplicate match stops the run.
14. Whole-guild creation and deletion use an exact-commit `discord.py-self`
    adapter from the isolated `tests/live` uv project because Discord removed
    those operations from the documented app API. The adapter logs in without
    starting a Gateway connection. Client or HTTP contract drift stops the run
    for reconciliation; it must not be papered over with repeated writes or
    unredacted response bodies.

## Account topology

### Recommended baseline: four accounts

| Alias | Default role | Purpose |
| --- | --- | --- |
| `owner` | Guild owner and fixture controller | Creates guilds, channels, roles, and recovery state. It is never the account being cleaned. |
| `subject` | Ordinary member | Primary DMD account. Creates the messages, reactions, and threads that cleanup should find. |
| `peer_a` | Ordinary member | Creates foreign messages and reactions, participates in DMs/private threads, and provides mixed ownership. |
| `peer_b` | Ordinary member or scenario moderator | Provides a second independent foreign author/reactor, permission contrast, Group DM membership, and controlled race actions. |

The `owner` account must remain outside the cleanup target so it can recover
permissions, inspect failures, and tear down the fixture even when a destructive
scenario fails. `subject`, `peer_a`, and `peer_b` can receive different roles in
separate guilds. These names are generic fixture roles, not Discord names or
credential-key suffixes; their role assignments remain stable for the run.

At least `subject` and one peer should be capable of creating Super Reactions if
the full burst matrix is required. If those capabilities are unavailable, burst
scenarios are reported as unsupported rather than silently passing. This is a
capability requirement, not automatically a reason to add another account.

### Minimum: three accounts

Three accounts can cover the basic ownership boundary:

- one guild owner/controller
- one cleanup subject
- one foreign peer

That is enough for own-versus-foreign messages, reactions, DMs, private threads,
and permission changes. It is not the preferred baseline because exact
multi-user reaction counts and two-foreign-author scenarios would need to reuse
the guild owner as test content. Doing that mixes administrator identity into
ordinary-member assertions and weakens teardown isolation.

### Why more than four is not initially required

Four accounts already provide one isolated controller, one cleanup subject, and
two independent foreign actors. Additional accounts add scale and parallelism,
but no new ownership semantic required by the current DMD feature set. Add a
fifth account only for a concrete capability gap, replacement account, or future
parallel execution plan. Do not add accounts merely to increase fixture volume.

## Guild and private-channel topology

The controller should create at least two disposable guilds so permission tests
cannot corrupt the main content matrix:

### Matrix guild

- `owner` owns the guild and fixture roles
- `subject`, `peer_a`, and `peer_b` are ordinary members
- text, announcement, forum, media, voice, and stage chat are represented where
  the account and guild expose those types
- public, private, announcement, forum, and media threads are represented in
  active and archived states
- messages and reactions from all three non-controller actors are interleaved

### Permission guild

- owned-thread deletion is exercised without and with the relevant permission
- channel visibility and history access are granted and revoked deliberately
- `subject` can be temporarily promoted without changing the matrix guild
- expected `403`, fallback, and recovery paths remain isolated from other cases

### Optional cross-guild fixture

A third guild may be added for announcement following/crosspost observations or
multi-guild search pagination. It is not necessary for the first live milestone.

Private fixtures should include:

- a one-to-one DM between `subject` and `peer_a`
- a Group DM containing `subject`, both peers, and optionally `owner`
- private threads where membership differs between the subject and peers

## Scenario model

Each scenario is an object graph, not only a CLI command. Its ledger entry must
record:

- scenario ID and run ID
- generic fixture roles and their expected guild permissions
- container IDs and channel/thread types
- message IDs, authors, timestamps, types, and expected deletability
- reaction emoji, normal/burst variant, and reacting fixture roles
- expected dry-run actions and expected foreign-content impact
- whether destructive execution is permitted
- expected post-execution state
- teardown state and any observed Discord limitation

### Mixed message ownership

- `subject`, `peer_a`, and `peer_b` send interleaved messages in one channel
- several subject messages sit directly next to foreign messages in history
- replies cross author boundaries in both directions
- attachments, polls, voice messages, and supported system-message cases are
  added where the clients can create them reliably
- keep-count and keep-within boundaries select subject messages without changing
  foreign messages
- deleted or inaccessible messages exercise the explicit `absent` outcome

### Shared reaction state

- all three actors react to the same subject-authored message with the same
  normal emoji
- the subject reacts to peer-authored messages so reaction cleanup must traverse
  foreign content
- normal and Super Reaction variants of the same emoji coexist where supported
- multiple foreign normal and burst reactions remain attached to a subject
  message that is selected for deletion
- dry-run reports exact foreign reaction impact at scan time
- keeping reactions suppresses reaction actions without suppressing subject
  message deletion

### Thread ownership and impact

- a subject-owned thread contains only subject messages
- a subject-owned thread contains messages from both peers
- a peer-owned thread contains subject messages and reactions
- a subject-owned thread contains only subject messages but foreign reactions
- `self-only` and `all` modes produce different plans for the same fixture
- active, archived, public, private, announcement, forum, and media thread forms
  are covered where Discord exposes them
- deletion without permission fails and falls back to ordinary content cleanup
- permission is granted in a separate case and container deletion is verified
- a controlled peer action between scan and delete records the known race rather
  than pretending the operation is atomic

### Channel coverage

For every channel type classified as message-bearing, prove all of the following
that Discord permits in that container:

1. subject message discovery
2. foreign message preservation
3. subject reaction discovery on a peer message
4. dry-run action reporting
5. real deletion and independent absence verification

Unsupported creation or mutation must be recorded as a capability result, not
converted into a passing test.

### Retention and preserve-cache layouts

Prepare cache/history cases that can later benchmark the batched window planner:

- several preserved IDs clustered in one 100-message history window
- sparse preserved IDs far apart in one channel
- cached IDs overlapping the normal fetch stream
- cached IDs for messages deleted by another account
- mixed clustered and sparse IDs across several channels
- a fetch cutoff that excludes old history while cache entries reintroduce kept
  messages for evaluation

The initial live suite may use the current point-fetch implementation. It must
capture request counts and elapsed time so the future implementation can prove
that batching improves work without changing decisions.

### Search equivalence fixtures

Once message search is implemented for `--keep-reactions`, the same seeded state
must be processed through both search and traversal:

- compare normalized candidate IDs and final planned actions
- include recently created messages while indexing may still be incomplete
- include old and high-volume histories that need search partitioning
- include guild channels, threads, DMs, and Group DMs as supported
- require traversal fallback whenever search cannot prove completeness

Search equivalence is not part of the first live milestone, but the fixture
ledger must retain enough metadata to add it without rebuilding the suite.

## Per-scenario lifecycle

Every scenario follows the same state machine:

1. **Create** - create containers, roles, and initial messages from the declared
   fixture roles.
2. **Interact** - add replies, reactions, thread membership, and foreign content
   from the peer accounts.
3. **Mature** - allow configured time to pass and apply supported state changes
   such as archive, permission, or visibility transitions.
4. **Snapshot** - independently fetch the expected object graph and freeze the
   pre-cleanup ledger state.
5. **Dry run** - run the installed `dmd` CLI as `subject` and compare structured
   actions and impact with the scenario expectations.
6. **Unlock** - permit destructive execution only when the dry-run comparison
   passes exactly and the run ID still matches every target.
7. **Execute** - run the same settings without dry-run for scenarios explicitly
   marked destructive.
8. **Verify** - use `owner` or a peer observer to fetch postconditions rather
   than trusting the subject process or its local state.
9. **Teardown** - delete generated resources in reverse dependency order and
   mark each ledger item complete.

The long-running orchestrator may pause between phases for minutes or hours. It
must persist only the token-free, owner-only ledger and be able to resume at
every numbered boundary.

## Assertions and evidence

The suite should assert behavior at three levels:

### Structured DMD output

- selected channel/thread and scope reason
- planned message, reaction, and owned-thread actions
- foreign message and reaction impact
- concrete `deleted`, `absent`, and `failed` execution outcomes
- retry/wait telemetry without sensitive IDs or tokens
- final per-channel and total summaries

Text scraping is too fragile for a release gate. If current JSON logging cannot
express these values reliably, add stable structured event fields before making
the live suite authoritative.

### Discord postconditions

- deleted messages and threads return an absent result
- kept subject messages remain accessible
- foreign messages remain unless an explicit owned-thread `all` case deletes the
  shared container
- subject reactions are removed only when configured
- foreign reactions remain unless their containing message or thread is deleted
- failed permission cases leave the container and foreign content intact while
  ordinary fallback cleanup follows its configured scope

### Operational evidence

- API request count by policy and normalized route family
- scheduler wait count and total wait time
- scenario and phase elapsed time
- search-versus-traversal candidate and action counts when available
- point-fetch-versus-window-fetch counts when batching is implemented

## Harness shape

The intended repository layout is:

```text
tests/live/
  README.md
  conftest.py
  live_suite.py
  secrets.env.example
  fixtures/
  scenarios/
  test_accounts.py
  test_discovery.py
  test_dry_run.py
  test_cleanup.py
```

Implementation expectations:

- mark all tests with `pytest.mark.live`
- skip collection or execution unless an explicit live-suite switch is present
- keep fixture construction separate from DMD production API wrappers so setup
  bugs do not automatically validate cleanup bugs
- centralize fixture pacing, honor Discord rate limits, and prohibit parallel
  account traffic during the initial milestones
- invoke the installed CLI for end-to-end assertions
- expose phase commands such as `create`, `interact`, `snapshot`, `dry-run`,
  `execute`, `verify`, `teardown`, and `resume`
- support selecting one scenario while developing without weakening the global
  destructive guard
- serialize destructive phases initially; parallelism is future work

The ordinary `Test` workflow must continue to pass without tokens and without
network access to Discord.

## Milestones

### M0: safety and contracts

- [x] Define the ledger schema and run-ID ownership checks
- [x] Define secret loading and prove logs remain redacted
- [x] Add the live marker and default skip guard
- [x] Define stable structured output needed by assertions
- [x] Add an idempotent empty-run teardown test

### M1: account and guild bootstrap

- [x] Validate all configured accounts through `/users/@me` without identity output
- [x] Create the matrix and permission guilds
- [x] Implement resumable roles, memberships, permissions, Community, and channel topology
- [x] Complete live membership and topology reconciliation
- [x] Resume bootstrap from an interrupted ledger
- [x] Implement verified, resumable recursive teardown for both controller-owned guilds

### M2: multi-user dry-run suite

- [x] Add opt-in content seeding for subject/peer messages, a foreign reaction,
  and a public thread
- [x] Add a resumable 216-message volume matrix across nine scopes with
  deterministic varied content, multi-account authorship, cross-account
  reactions, and conservative jittered pacing
- [x] Live-validate interleaved messages from subject and both peers
- [x] Live-validate normal reactions and record HTTP 403 for capability-gated
  Super Reactions on both tested fixture accounts
- [x] Live-validate active and archived public, announcement, private, and forum
  thread cases; retain Media as explicitly unavailable with Discord code 50024
- [x] Assert exact redacted DMD action counts across every available
  message-bearing channel and thread form
- [ ] Assert detailed messages, reactions, impact, and keep decisions
- [x] Run the suite across every supported message-bearing channel type;
  retain Media as an explicit unsupported capability

### M3: destructive verification

- [x] Delete one isolated representative subject message
- [x] Verify `deleted` and resumable `absent` outcomes
- [x] Delete an isolated forum starter message and verify that its thread
  container remains present
- [x] Delete representative reaction sets from every mutable channel type
- [x] Verify temporary archived-thread activation, content cleanup, and
  restoration across public, announcement, private, and forum forms
- [x] Deterministically verify likely auto-archive resume, early external
  archive termination, unchanged/changed lock state, and bounded message and
  reaction retries with a controlled clock
- [ ] Verify creator, non-owner, moderator, locked, and interrupted-restoration
  archived-thread permission boundaries
- [x] Exercise real mid-clean archive and lock transitions against Discord,
  including a bounded second-archive retry
- [ ] Observe Discord's natural auto-archive timer over a full one-hour window;
  keep the deterministic clock suite as the release gate
- [ ] Verify permission `failed` outcomes
- [ ] Verify owned-thread `self-only`, `all`, and fallback behavior
- [x] Prove tracked foreign messages and reactions remain outside explicit
  shared-container deletion
- [ ] Recover and tear down after a deliberately interrupted execution

### M4: long-running and performance fixtures

- [ ] Evolve fixture state over a multi-hour run
- [ ] Capture route, request-count, wait, and elapsed-time baselines
- [ ] Add clustered and sparse preserve-cache layouts
- [ ] Add search/traversal equivalence when message search exists
- [ ] Add window-fetch comparisons when retention batching exists

### M5: release gate

- [ ] Run the complete suite from a clean installed v3 build
- [ ] Archive only redacted reports and the non-secret scenario manifest
- [ ] Record unsupported Discord capabilities explicitly
- [ ] Update migration, user docs, and artifact audit with observed behavior
- [ ] Require a final successful live run ID in the v3 release checklist

## Open decisions

- Which supplied accounts can create normal and Super Reaction variants?
- Should tokens be resolved through one live-suite keyring service or through
  separate DMD config-path keyring entries?
- Which Discord client/API surface should create fixtures independently from the
  production cleanup client?
- Which structured JSON fields are missing from current DMD output?
- Which archive and permission transitions can be created directly, and which
  need elapsed time or manual confirmation?
- Should successful runs tear down immediately or retain a guild briefly for
  forensic inspection?
- What is the maximum supported run duration and resume window?

Resolve these questions during M0/M1. They do not change the recommended
four-account baseline.
