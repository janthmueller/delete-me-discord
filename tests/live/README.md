# Live Discord tests

This directory contains opt-in tests against dedicated Discord accounts and
fixtures. Ordinary `pytest`, pull-request, and push workflows collect these
tests but skip them without making network requests.

## Local secrets

Copy `secrets.env.example` to `secrets.env`, fill the four generic fixture-role
entries, and restrict the file to its owner:

```bash
chmod 600 tests/live/secrets.env
```

`secrets.env` is ignored by Git. The harness parses it as data and never sources
it as shell code. `TOKEN_*` environment variables override matching entries and
can be used instead. Both the variable names and values are confidential. They
must never appear in command arguments, logs, ledgers, snapshots, reports, test
IDs, or exception representations.

Account checks are always reported as `account-1`, `account-2`, and so on. This
redaction is identical for local runs and CI; there is no verbose identity mode.
The owner-only ignored ledger may contain Discord IDs needed for ownership
checks, but never credential key names or token values.

Live pytest runs reject `--showlocals` because traceback locals could expose
tokens or Discord IDs even when normal logging is redacted.

## First commands

Validate all four accounts with read-only `/users/@me` requests:

```bash
uv run python tests/live/live_suite.py accounts
```

Run the equivalent opt-in pytest check:

```bash
uv run pytest -q tests/live --live-discord
```

Create the initial token-free, private run ledger after validation:

```bash
uv run python tests/live/live_suite.py init
uv run python tests/live/live_suite.py status
```

Create or reconcile the isolated matrix and permission guilds after copying the
run ID from `status`:

```bash
uv run --project tests/live python -m tests.live.live_suite bootstrap \
  --confirm-run-id '<current-run-id>'
```

Bootstrap is resumable. Each successful guild creation is written to the
owner-only ledger immediately. If Discord accepted a create request but the
process stopped before that write, the next run recovers only an owned guild
whose deterministic name exactly matches the current run. A global non-blocking
lock prevents concurrent harness processes from sharing fixture state.

Create or reconcile the complete M1 topology after guild bootstrap:

```bash
uv run --project tests/live python -m tests.live.live_suite topology \
  --confirm-run-id '<current-run-id>'
```

This creates deterministic categories, text, announcement, forum, media,
voice, and stage channels; enables Community on the matrix guild; creates the
member, thread-manager, and restricted-reader roles; joins the three non-owner
accounts; and assigns the expected roles. Every channel and role is recorded
immediately and is verified by ID, name, type, parent, permissions, guild, and
run ownership on later invocations.

Discord may reject the documented-but-still-developing `GUILD_MEDIA` type with
error `50024` (`Cannot execute action on this channel type`). The harness records
that exact capability as `unsupported` in the private ledger and continues;
other channel creation errors remain fatal. The live fixture therefore covers
Forum plus all currently creatable message-bearing guild channel types.

`GUILD_MEDIA` is an optional thread-container type, not a normal message
channel. If a guild already has one and Discord returns it during discovery,
DMD can process its supported threads; the live fixture does not require the
parent channel to be creatable.

Discord can require a CAPTCHA when a dedicated account accepts an invite. The
harness does not attempt to bypass that control. If topology reports this
condition, prepare two short-lived manual links:

```bash
uv run --project tests/live python -m tests.live.live_suite membership-invites \
  --confirm-run-id '<current-run-id>'
```

The command writes links to the ignored owner-only
`tests/live/state/membership-invites.json` file and never prints them. Open each
guild link once in each missing non-owner account's browser container, complete
Discord's normal confirmation or CAPTCHA, and rerun `topology`. A link is valid
for one hour by default and its use limit equals the number of missing members.

Guild creation and whole-guild deletion are user-client operations that are no
longer part of Discord's documented application API. The independent fixture
client therefore uses an exact-commit `discord.py-self` dependency from the
isolated `tests/live` uv project, while normal DMD cleanup remains on API v10.
Its dedicated lockfile freezes the complete live-tool dependency graph without
changing the intentionally lock-free installable library root.
The Nix development shell exposes the C++ runtime required by the client's
native transport wheel; other supported platforms use their normal system
runtime.
It performs static login and REST fixture operations without starting a Gateway
connection. The library owns Discord's user-client transport and rate-limit
protocol; the harness still owns serialization, conservative request spacing,
ledger reconciliation, and write safety. Adapter contracts are covered offline,
and any upstream drift stops the bootstrap; the harness adds no independent
write retry. The harness never emits Discord response text; a failure may expose
only HTTP status and a non-sensitive numeric Discord error code.

Seed the resumable multi-user smoke fixtures after topology completes:

```bash
uv run --project tests/live python -m tests.live.live_suite content \
  --confirm-run-id '<current-run-id>'
```

This creates mixed guild, thread, DM, and Group DM content plus one isolated
subject message reserved for destructive verification. Discord can require an
API-side CAPTCHA for the first automated private message; the harness stops
without exposing challenge data and can resume after the conversation has been
established normally in the Discord client.

Verify every non-destructive scope and unlock exactly one destructive smoke:

```bash
uv run --project tests/live python -m tests.live.live_suite dry-run \
  --confirm-run-id '<current-run-id>'
```

The command captures and suppresses detailed DMD output, requires sensitive
redaction, and records the unlock only after GuildText, PublicThread, DM, and
Group DM previews all return valid summaries. Then run the isolated smoke:

```bash
uv run --project tests/live python -m tests.live.live_suite destructive-smoke \
  --confirm-run-id '<current-run-id>'
```

It rechecks that the isolated scope contains exactly one subject message,
deletes it, proves the scope is empty, records `deleted`, and relocks the run.
If a prior interrupted execution already removed the message, it records
`absent` after proving the same empty postcondition.

After the smoke phase, seed the broader M2 matrix:

```bash
uv run --project tests/live python -m tests.live.live_suite volume \
  --confirm-run-id '<current-run-id>'
```

The default creates 24 varied messages in each of nine scopes: GuildText,
Announcement, Voice chat, Stage chat, three public threads, DM, and Group DM.
Authorship cycles through the subject and both peers, and every fourth message
receives a cross-account reaction. Message text is varied deterministically so
an interrupted run resumes the same scenario, while writes use a globally
serialized random 4-12 second interval to avoid request bursts. The content is
explicitly identifiable as test-fixture data; pacing is not intended to conceal
automation.

For bounded local sessions, use `--max-new-mutations 24` and rerun the same
command until it reports `complete`. `--messages-per-scope`, `--delay-min`, and
`--delay-max` are configurable, but changing the message count after seeding has
started creates a different target matrix and should be avoided.

After the volume dry-run succeeds, seed the thread-form matrix:

```bash
uv run --project tests/live python -m tests.live.live_suite thread-matrix \
  --confirm-run-id '<current-run-id>'
```

The default creates active and archived forms of a regular PublicThread,
AnnouncementThread, PrivateThread, and Forum post, plus the same two Media post
forms when the guild exposes a creatable Media parent. Each thread receives 12
interleaved messages from the subject and both peers. All three actors apply the
same normal reaction to one subject message, while the subject and one peer
react to the same foreign message. This covers both exact foreign-reaction
impact and standalone subject-reaction cleanup.

The harness also attempts one Super Reaction from the subject and one peer. A
supported write is recorded as fixture content; an HTTP 400 or 403 is recorded
as an explicit account capability result. Use `--no-super-reactions` to avoid
that optional probe. Private-thread membership is established before content is
sent, and archived fixtures are archived only after all interactions complete.

This phase uses the same 4-12 second global pacing and supports
`--max-new-mutations`, `--messages-per-thread`, `--delay-min`, and `--delay-max`.
After it reports `complete`, rerun `dry-run`. The expanded gate rechecks all nine
volume scopes and every available thread fixture. It requires subject reactions
to be discoverable in every thread form. Archived fixture previews use
an exact archived thread selector; dry-run never changes thread state, but
its plan includes the activation and restoration that a real cleanup would need.

Run the read-only structured planner contract at any later phase while
destructive execution is locked:

```bash
uv run --project tests/live python -m tests.live.live_suite planner-contract \
  --confirm-run-id '<current-run-id>'
```

The harness independently reads the mixed-ownership public-thread fixture,
reproduces `--keep-last 4 --keep-last-scope mine`, and compares DMD's redacted
JSON action, keep-decision, summary, and foreign-reaction-impact events exactly.
Only scalar counts and action categories are retained; raw log messages and
Discord identifiers are discarded. The command does not mutate Discord.

After the expanded dry-run passes, prepare one isolated forum starter-message
deletion:

```bash
uv run --project tests/live python -m tests.live.live_suite forum-starter-smoke \
  --confirm-run-id '<current-run-id>'
```

Preparation creates a dedicated subject-owned forum post containing only its
starter message, independently verifies both resources, and requires DMD's
ordinary dry-run to plan exactly one message deletion and no owned-thread
deletion. It then unlocks only that recorded fixture. Execute the previewed
operation with:

```bash
uv run --project tests/live python -m tests.live.live_suite forum-starter-smoke \
  --confirm-run-id '<current-run-id>' \
  --execute
```

Execution rechecks the preview, performs normal message cleanup, and probes the
starter message and forum thread independently. The ledger records whether the
message was `deleted` or already `absent` and whether Discord left the post
container `present` or removed it as a cascade. The command always relocks
destructive execution before returning.

The current validated fixture result is `message=deleted, container=present`:
ordinary DMD cleanup removed the forum starter message, while Discord retained
the post's `PublicThread` container.

The final per-channel contract is another explicit two-stage operation. First
prepare exact previews for every available message-bearing type:

```bash
uv run --project tests/live python -m tests.live.live_suite \
  destructive-contract-matrix \
  --confirm-run-id '<current-run-id>'
```

The preparation stage independently reads each tracked scope, checks subject
and foreign message authorship, and requires both a subject reaction to remove
and a foreign reaction to preserve. It may add a missing reaction fixture to an
active scope, but does not remove content. DMD's redacted dry-run counts must
exactly match the independently observed state before the ledger unlocks.

Execute the verified matrix separately:

```bash
uv run --project tests/live python -m tests.live.live_suite \
  destructive-contract-matrix \
  --confirm-run-id '<current-run-id>' \
  --execute
```

Every scope is previewed again immediately before execution and checkpointed
separately. Independent postcondition reads require all tracked subject
messages to be absent, all tracked foreign messages and foreign reactions to
remain, subject reactions to be removed in mutable scopes, and every
channel/thread container to remain. Archived scopes run DMD with
an exact archived thread selector: preview must include the tracked subject
messages and reactions, execution must remove them, and the independent
postcondition must still observe the thread as archived. This supersedes the
first live baseline where direct mutations on archived PublicThread,
AnnouncementThread, PrivateThread, and Forum-post fixtures returned HTTP 400.
Available coverage is GuildText, GuildAnnouncement, GuildVoice chat,
GuildStageVoice chat, DM, GroupDM, and active/archived PublicThread,
AnnouncementThread, PrivateThread, and Forum-post forms. Media posts remain an
explicit unsupported capability until Discord permits creation in a fixture
guild.

The current validated archived result is successful for all four available
forms: subject messages and reactions were removed after temporary activation,
tracked foreign messages and reactions remained, every thread container
remained present, every thread was independently observed as archived again,
and the recovery journal had no pending entries.

Archived-thread state races use one final isolated two-stage matrix. Prepare six
dedicated public threads with:

```bash
uv run --project tests/live python -m tests.live.live_suite \
  archived-thread-race-matrix \
  --confirm-run-id '<current-run-id>'
```

Each thread contains one target message, one foreign message, one target
reaction on the foreign message, and one foreign reaction. Preparation resets
each pending fixture to its exact archived/lock state, independently verifies
the four artifacts, and requires DMD's redacted dry-run to plan exactly one
message deletion and one reaction removal. It then unlocks only this matrix.

Execute the immediately revalidated scenarios separately:

```bash
uv run --project tests/live python -m tests.live.live_suite \
  archived-thread-race-matrix \
  --confirm-run-id '<current-run-id>' \
  --execute
```

The matrix covers ordinary temporary activation/restoration, a locked thread
cleaned by the account with `MANAGE_THREADS`, an early external archive that
must stop cleanup, an archive accompanied by a lock change that must stop
cleanup, a likely auto-archive that may reopen once, and a second immediate
archive that must not create an unbounded retry loop. The race hook changes
real Discord thread state immediately before the real DMD mutation. For the
likely-auto case it advances only DMD's injected monotonic test clock to the
configured one-hour deadline; it does not claim that Discord's natural timer
was observed for an hour.

Independent postcondition reads require successful cases to remove only target
artifacts, interrupted cases to retain them, all foreign content to remain,
every thread to finish archived with the expected lock state, and the dedicated
restoration journal to be empty. Every scenario is checkpointed separately.
Any error relocks the ledger and requires a fresh preview before execution can
resume.

The current validated result passed all six scenarios. Ordinary temporary
cleanup, likely-auto-archive recovery, and locked-manager cleanup removed the
target message and reaction. Early external archive, lock change, and a second
immediate archive stopped cleanup with the target artifacts intact. Every
foreign message and reaction remained, every thread finished archived with the
expected lock state, the second archive produced no retry loop, the journal was
empty, and the ledger relocked after verification.

Remove the two verified run-owned guilds in reverse order:

```bash
uv run --project tests/live python -m tests.live.live_suite teardown \
  --confirm-run-id '<current-run-id>'
```

Teardown is idempotent. It treats already absent guilds as complete and refuses
to delete an observed guild unless its ID, ownership flag, deterministic name,
fixture key, and run ledger all agree. Deleting a verified parent guild also
marks its recorded channels and roles terminal because Discord removes those
resources atomically with the guild. Content-destructive tests require the
explicit dry-run unlock described above.

## Request pacing

All harness operations across all four clients share one serialized scheduler.
The wrapper waits a randomized 1-2 seconds before reads and 3-6 seconds before
mutations, including the first operation in a process. Invite inspection and
acceptance are paced as separate REST operations. The pinned user client
additionally observes Discord's rate-limit buckets and `retry_after` values
internally. It can repeat a 429 or a narrow set of transient transport and
gateway failures; the wrapper adds no second retry loop.

The harness does not parallelize fixture traffic or rotate network identities.
If a create result is uncertain, the next invocation reconciles Discord state
against the private ledger and deterministic fixture names. More than one
matching owned guild is treated as ambiguity and stops the run.
