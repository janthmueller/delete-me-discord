# Discord User Artifact and Removal Audit

- Status: living audit
- Audit branch: `v3`
- Last reviewed: 2026-07-12
- Product scope: Discord user content and interactions relevant to cleanup

This document answers three separate questions:

1. Does DMD know every current Discord channel type that can carry messages or
   reactions?
2. Which other artifacts can a Discord user create or cause, and can that user
   remove them?
3. Which of those operations belong in DMD without violating its current
   philosophy?

The inventory covers user-visible Discord artifacts and state with a plausible
cleanup use case. It does not treat Discord's internal analytics, anti-abuse
records, backups, billing records, or legal records as application-manageable
artifacts. Those limits are called out separately.

## Product philosophy used for this audit

DMD currently describes itself as a tool for deleting **your own messages and
reactions** with explicit scope, retention rules, and a dry-run-first workflow.
The safest extension of that philosophy is:

- prove that an artifact belongs to the authenticated user
- avoid elevated moderator permissions for core cleanup
- never remove another user's content by default; require an explicit shared-container opt-in
- discover and preview an action before executing it
- make shared-container and administrator actions separate and explicitly opted in
- continue past inaccessible resources while reporting that the scan was incomplete
- never promise complete erasure when Discord access or copying semantics prevent it

These rules are more important than whether Discord happens to expose a delete
endpoint. A user may have created a shared object without being its sole owner,
and a delete endpoint may remove other users' content.

### Ownership and deletion-cascade boundary

DMD separates three questions that must not be collapsed into one broad
"delete everything I can" switch:

1. **Discovery scope:** where should DMD look?
2. **Ownership evidence:** which artifact is attributed to the authenticated user?
3. **Cascade impact:** which independently authored artifacts disappear with it?

The product boundary is based on independent authorship, not only containment
or API permission:

| Target artifact | Ownership evidence | Foreign cascade | Product policy |
| --- | --- | --- | --- |
| Current user's reaction or Super Reaction | `me` / `me_burst` | None | Core cleanup, enabled by default |
| Current user's message | matching `author_id` | Reactions attached by other users; these cannot exist without the message | Core cleanup, enabled by default; report the reaction impact in dry-run |
| Creator-owned thread with no foreign messages | matching `owner_id` plus complete author scan | Other users' reactions attached to the creator's messages | Explicit container opt-in; not default |
| Creator-owned thread with foreign messages | matching `owner_id` | Independently authored messages, attachments, polls, replies, and reactions | Stronger explicit opt-in only |
| Guild channel or category | Administrative permission; no durable creator-ownership contract | Potentially substantial shared content and structure | Outside ordinary cleanup |
| Guild | `owner_id`, but community-level ownership | Entire community, configuration, and every member's content | Outside DMD cleanup |

A reaction is independently attributable as an interaction, but it is
lifecycle-dependent on its parent message. Deleting an own message therefore
unavoidably removes foreign reactions and remains a personal-content action.
A message authored by someone else remains first-class content even when it is
inside a thread created by the authenticated user. Deleting that thread crosses
a materially stronger boundary and must remain opt-in.

Creation and authorization are not sufficient ownership policies. A user may
be allowed to delete a channel, category, thread, or guild without DMD treating
that object as part of their personal footprint. Future features should classify
their target in this table before adding CLI or profile controls. Shared
administrative resources belong behind a separate resource/admin command, if
they are ever supported at all.

### Platform and API constraint

DMD authenticates as a normal user and already warns that automated use may
violate Discord's [Terms of Service](https://discord.com/terms). Discord's
public developer documentation primarily describes app and bot integrations;
client routes such as thread search are a less stable implementation surface.

Every proposed private user endpoint therefore carries two independent risks:

- Discord may change its request or response shape without a compatibility promise.
- Expanding automated user-account behavior may increase policy/enforcement risk.

Prefer documented routes when they provide the required user-owned operation.
For client-only operations, require a captured live request, narrow tests,
graceful failure, redacted diagnostics, and an explicit maintenance note. This
audit describes technical feasibility, not permission from Discord to automate
the operation.

## Verdict

### Channel-type coverage

**Yes, the v3 channel model covers every currently documented Discord channel
type that can directly contain messages, plus every documented thread
container.** No additional public channel type needs to be added for ordinary
message or reaction cleanup as of this review.

That conclusion applies to the object model, not to discovery completeness.
DMD can still miss content in a known channel type when the channel or thread is
not returned to the current account, the user has lost access, a DM is closed,
or Discord's private thread-search behavior omits a result.

### Artifact coverage

**No, channels plus reactions are not the full set of removable user
artifacts.** This v3 branch now covers normal reactions, Super Reactions, and
explicit deletion of creator-owned thread containers. The remaining
highest-value gaps are:

1. Poll votes are own-user interactions, but DMD discards poll state and the
   public app API does not expose vote mutation.
2. Historical discovery is not complete for closed DMs, left guilds,
   inaccessible private threads, deleted channels, or copied messages.
3. Custom emoji, stickers, soundboard sounds, and scheduled events have useful
   creator attribution and conditional self-management permissions. They are
   plausible future resource-cleanup features, but not message cleanup.

A user-created thread or forum/media post is a channel object with an
`owner_id`. DMD can now delete that container only through an explicit
`--delete-owned-threads` mode. The default remains container-safe because
deletion requires `MANAGE_THREADS` and can remove other users' content.

## Channel surface audit

Discord's current [channel type table](https://docs.discord.com/developers/resources/channel#channel-object-channel-types)
defines the following relevant surfaces.

| ID | Discord type | Can contain message objects? | Can parent threads? | DMD v3 handling | Verdict |
| --- | --- | --- | --- | --- | --- |
| 0 | `GUILD_TEXT` | Yes | Public and private threads | Direct channel plus thread search | Covered |
| 1 | `DM` | Yes | No | Root message channel | Covered when returned |
| 2 | `GUILD_VOICE` | Yes, through voice-channel text chat | No documented thread creation | Direct guild message channel | Covered |
| 3 | `GROUP_DM` | Yes | No | Root message channel | Covered when returned |
| 4 | `GUILD_CATEGORY` | No | No | Structural parent only | Correctly excluded |
| 5 | `GUILD_ANNOUNCEMENT` | Yes | Announcement threads | Direct channel plus thread search | Covered |
| 6-9 | Not assigned in the public channel table | No supported surface to add | No | Unknown values are not selected | Correct |
| 10 | `ANNOUNCEMENT_THREAD` | Yes | It is already a thread | Discovered thread channel | Covered when visible |
| 11 | `PUBLIC_THREAD` | Yes | It is already a thread | Discovered thread channel | Covered when visible |
| 12 | `PRIVATE_THREAD` | Yes | It is already a thread | Discovered thread channel | Covered when visible/member/moderator |
| 13 | `GUILD_STAGE_VOICE` | Yes, through stage text chat | No documented thread creation | Direct guild message channel | Covered |
| 14 | `GUILD_DIRECTORY` | No | No | Structural type only | Correctly excluded |
| 15 | `GUILD_FORUM` | No direct messages; it only contains public-thread posts | Public threads | Search parent, clean returned posts | Covered |
| 16 | `GUILD_MEDIA` | No direct messages; it only contains public-thread posts | Public threads | Search parent, clean returned posts | Covered |

The implementation matches this table in
`delete_me_discord/channel_types.py`:

- `ROOT_MESSAGE_CHANNEL_TYPES` contains DM and Group DM.
- `GUILD_MESSAGE_CHANNEL_TYPES` contains text, announcement, voice, and stage.
- `THREAD_CHANNEL_TYPES` contains announcement, public, and private threads.
- `THREAD_PARENT_CHANNEL_TYPES` contains text, announcement, forum, and media.

Forum and media channels are containers, not message channels. Their posts are
`PUBLIC_THREAD` channel objects. Voice and stage channels are message-bearing
because Discord provides text chat on those channel types. Threads are not
documented as children of DM, Group DM, voice, or stage channels.

Reactions attach to message objects, so the same channel matrix covers normal
and Super Reactions. Archived threads are an important state exception: DMD's
live user-account tests observed Discord rejecting direct message and reaction
mutations until the thread was active. DMD includes archived content by default,
plans both action types, and activates the thread only for a non-empty plan.

### Existing automated coverage

The current suite already validates the internal topology rather than merely
checking display labels:

- `tests/test_channel_types.py` asserts the exact root, guild, thread, cleanup,
  and filterable channel sets.
- `tests/test_thread_inventory.py` asserts searches under text, announcement,
  forum, and media parents, including active, archived, public, private, and
  announcement thread objects.
- selector and discovery tests verify DM, Group DM, announcement, voice, stage,
  forum, and nested-thread inclusion and rendering.
- cleaner behavior tests verify that direct message channels and threads enter
  cleanup while forum containers do not.
- archived-thread tests verify default discovery, dry-run planning, guarded
  activation, own-message and own-reaction cleanup, restoration,
  locked-thread permissions, interrupted restoration recovery, likely
  auto-archive reopening for both message and reaction actions, early external
  archive termination, changed lock/duration rejection, and bounded retry
  behavior.
- reaction tests cover normal and burst ownership independently, both variants
  on one emoji, the typed burst route, and null-name custom emoji formatting.
- owned-thread tests cover the default-off contract, creator matching,
  `self-only` completeness and author checks, `all`, dry-run, permission
  failure fallback, and ordinary cleanup fallback.

Those tests prove DMD's classification and orchestration contract. They cannot
prove that a live Discord account can fetch and mutate each surface under every
permission combination, or that the private thread-search endpoint returns all
historical results. Those points remain integration tests below.

## Coverage is not completeness

Supporting every channel type does not prove that every historical channel
instance can be discovered.

| Limitation | Consequence for DMD |
| --- | --- |
| Current guild membership | `/users/@me/guilds` cannot provide ordinary access to every guild the user has left. |
| Channel permissions | Missing `VIEW_CHANNEL`, `READ_MESSAGE_HISTORY`, or, for voice channels, `CONNECT` prevents history retrieval. |
| Private-thread visibility | A private thread is visible only to members and users with `MANAGE_THREADS`. Losing parent access can also hide it. |
| Private user endpoint | DMD's `/channels/{id}/threads/search` route follows Discord client behavior, but is not a stable documented bot API contract. Its historical completeness must be tested live. |
| Closed DMs and Group DMs | The current root-channel response must not be assumed to be a complete historical DM index. Closing a DM hides the container without deleting its history. |
| Deleted or inaccessible containers | Knowing an old channel and message ID does not restore permission to delete from it. |
| Intentional error tolerance | DMD skips per-resource `403` and `404` responses so one inaccessible channel does not abort a long run. The resulting scan is useful but explicitly incomplete. |
| Copies outside the source | Forwarded snapshots, announcement crossposts, screenshots, quotes, notifications, and third-party archives are not all controlled by the original message author. |

Discord's [Data Package](https://support.discord.com/hc/en-us/articles/360004957991-Your-Discord-Data-Package)
is the strongest available historical inventory for messages sent by the user.
It groups sent messages by DM, Group DM, and guild channel and includes message
IDs, channel IDs, timestamps, content, and attachment links. It is still only a
snapshot, can take time to obtain, and cannot restore access. It also does not
serve as a complete reaction inventory.

The documented [Search Guild Messages](https://docs.discord.com/developers/resources/message#search-guild-messages)
endpoint is useful as a future acceleration path for own-message discovery, but
is not a full replacement for channel traversal:

- search results omit the `reactions` key
- indexing may return `202` and may return fewer results for old messages
- result totals can be inaccurate while messages change
- offset pagination is capped, so time/ID partitioning would be required for
  very large histories
- it cannot discover reactions the user placed on other users' messages

It would fit best when reaction removal is disabled, with direct message fetches
used to verify each candidate before deletion.

## Message-contained artifacts

A Discord message is a container for many kinds of user content. Deleting the
message is normally the correct and only cleanup operation for the contained
artifact.

| User artifact | Stored on | Removal semantics | Current DMD status |
| --- | --- | --- | --- |
| Plain text, markdown, mentions, links, and TTS | Message | Delete the user's message | Covered |
| Reply | Message type/reference | Delete the reply message; the referenced message remains | Covered |
| File, image, video, audio, or GIF upload | Message attachment/embed | Delete the message; Discord notes cached uploads may take time to clear | Covered by message deletion |
| Voice message | Message with `IS_VOICE_MESSAGE` and one audio attachment | Cannot be edited; delete the message | Covered by message deletion |
| Locally captured Discord Clip after sharing | File attachment in a sent message | Delete the sent message; the original Clip is a separate local file | Shared copy covered |
| Sent sticker | `sticker_items` on a message | Delete the message; this does not delete the guild sticker asset | Covered by message deletion |
| Rich Presence/activity invite | Message activity/embed | Delete the message | Covered when user-authored/deletable |
| Shared client theme | `shared_client_theme` on a message | Delete the message | Covered by message deletion |
| Poll created by the user | `poll` nested on a message | The poll cannot be edited; deleting the entire message deletes it. Creator may also end it early. | Covered by message deletion, poll metadata discarded |
| Forward sent by the user | New message with a message snapshot | Delete the forward message | Covered when authored by current user |
| Original message later forwarded by someone else | Snapshot inside another user's message | Original author cannot retract or update that copy | Not removable by DMD |
| Announcement message published to followers | Source message plus crossposts | Deleting the source removes its content but leaves an `Original Message Deleted` notice in follower channels | Source covered; full retraction impossible |
| Slash/context command invocation | User-attributed command message or interaction metadata | Delete only if Discord represents it as a user-authored deletable message | Covered when author/type allow it |
| Bot/app response to a command | App or webhook-authored message | Belongs to the app, not the invoking user; app-specific deletion may exist | Correctly not treated as user's message |
| Ephemeral interaction response | App-owned ephemeral message | Not ordinary channel history; expires or is managed by the app | Outside DMD |
| Forum/media post starter | First message in a public thread plus the thread container | Delete own starter message; optionally delete a creator-owned container with `MANAGE_THREADS` | Message covered; container deletion is explicit opt-in |

### System-generated message artifacts

Discord actions can create system message objects. DMD handles these through
message type and author checks rather than assuming every returned object is
safe to delete.

The official message table currently marks these types non-deletable:

- `RECIPIENT_ADD`
- `RECIPIENT_REMOVE`
- `CALL`
- `CHANNEL_NAME_CHANGE`
- `CHANNEL_ICON_CHANGE`
- `THREAD_STARTER_MESSAGE`

`AUTO_MODERATION_ACTION` is deletable only with `MANAGE_MESSAGES`, so it does
not belong in self-service cleanup. Other documented types may be technically
deletable, but DMD still requires the authenticated user to be the returned
author before planning a delete.

`delete_me_discord/type_enums.py` also contains newer client-observed message
types from a non-official reference. Unknown future types are conservatively
retained and skipped. The official
[message type table](https://docs.discord.com/developers/resources/message#message-object-message-types)
should remain the primary source for deletion capability; client-only values
need individual live verification before being considered safe.

Deleting a system notice does not reverse the action that caused it. Important
examples are:

| Trigger | Possible message artifact | What deleting the notice does not do |
| --- | --- | --- |
| Pinning | `CHANNEL_PINNED_MESSAGE` | It does not unpin the referenced message. |
| Boosting | `GUILD_BOOST` and tier notices | It does not cancel or refund the boost. |
| Creating a thread | `THREAD_CREATED` | It does not delete the thread channel. |
| Stage activity | Stage start/end/speaker/topic notices | It does not change the current stage instance. |
| Ending a poll | `POLL_RESULT` | It does not remove the original poll message. |
| Running an app command | Command invocation and app response messages | It does not erase data retained by the app provider. |

## User interaction artifacts

These are actions the user applies to an object they may not own. They fit the
privacy goal better than shared-resource administration because removing them
does not remove another user's primary content.

| Interaction | Stored on | Can the user remove it? | Current DMD status | Recommendation |
| --- | --- | --- | --- | --- |
| Normal reaction | Reaction entry on a message, `me=true` | Yes, through Delete Own Reaction while the message/thread is mutable | Covered directly in active threads and through guarded archived-thread activation | Keep in core |
| Super Reaction | Same reaction entry, `me_burst=true`, type `BURST` | Discord's client uses a typed own-reaction removal route | Represented separately from normal reactions; archived cleanup uses the same guarded activation | Keep in core and monitor the client-only route |
| Reaction using a deleted custom emoji | Reaction emoji may have an ID but a null name | Current client formats the route identifier as `null:id` | Covered by route formatting and fixtures | Keep the null-name fixture |
| Poll vote | Poll results contain `me_voted`; vote belongs to the voter | Client allows `Remove Vote` while the poll is open. Public app API says apps cannot vote and documents no vote-removal endpoint. | Not modeled | Candidate core interaction using a verified user endpoint only |
| Scheduled-event interest/RSVP | Scheduled event user/subscription | User can toggle interest in the client | Not modeled | Account-state feature, not message cleanup |
| Thread membership and notification settings | Thread member object | User can join/leave active threads and change notifications; archived state restricts changes | Not modeled | Optional account-state cleanup, not default |
| Message pin/unpin | Shared pin state on a channel message | Requires `PIN_MESSAGES` in guild channels; the pin object is not a reliable self-owned artifact | Not modeled | Keep out of default cleanup |
| Bookmark or reminder | Private account state pointing to a message | User can unsave, complete, or remove it in supported clients | Not modeled; feature is experimental | Separate account-hygiene scope only |
| Pending server member application | User submission to a guild | User can withdraw a pending application | Not modeled | Separate account-hygiene scope only |
| App command/modal input | Interaction payload delivered to a third-party app | Discord message cleanup cannot erase the app's external storage | Not modeled | Out of scope; direct user to app provider |

### Current reaction implementation findings

Discord's [reaction object](https://docs.discord.com/developers/resources/message#reaction-object)
reports normal ownership in `me` and burst ownership in `me_burst`. DMD models
both and creates one planned action per owned variant. If both flags are true
for the same emoji, both variants are removed independently.

The public Delete Own Reaction documentation describes the untyped normal
route. On 2026-07-12, the current official Discord web client bundle was
inspected before implementing burst removal. Its removal flow uses:

```text
DELETE /channels/{channel}/messages/{message}/reactions/{emoji}/@me/{type}
```

For a Super Reaction, `type` is `1` (`BURST`) and the request includes
`burst=true`. DMD preserves the documented untyped route for normal reactions
and uses the client-observed typed route only for burst reactions. The client
also formats a deleted custom emoji as `null:{id}`, which DMD now mirrors.

The burst route remains a client-only integration surface. Unit tests cover a
normal reaction, a burst reaction, both variants for one emoji, and a null-name
custom emoji. A Discord client change can still require maintenance, and an
actual account run remains the integration validation.

Dry-run cascade reporting uses `count_details.normal`, `count_details.burst`,
`me`, and `me_burst` to derive foreign reaction instances exactly at scan time.
It does not attempt to infer unique users. Missing or inconsistent fields, or an
incomplete enclosing thread scan, produce `unknown` rather than an estimate.

## Thread and forum/media ownership

Threads need two independent cleanup concepts:

1. content inside the thread
2. the thread channel object itself

Discord records the thread creator in `owner_id`. That does **not** grant the
creator an unconditional delete right. The
[Threads documentation](https://docs.discord.com/developers/topics/threads#editing-deleting-threads)
states that deleting a thread requires `MANAGE_THREADS`.

A creator without that permission can generally change creator-owned thread
properties such as its name, archive state, and auto-archive duration. Locked
and archived state add restrictions. Discord documents message deletion as the
one ordinary mutation allowed while archived, but the live user-account
endpoint returned code `50083`; DMD therefore uses explicit guarded activation
instead of relying on direct archived mutation.

Public threads created from an existing message share the source message ID and
can become orphaned when that source message is deleted. Forum and media posts
are public threads. Deleting the starter message can therefore leave an empty
or context-free post rather than deleting the thread.

### DMD policy for threads

The current default is correct:

- discover accessible threads
- delete the authenticated user's own messages inside active and archived threads
- remove the authenticated user's own normal and Super Reactions in those threads
- activate archived threads only after a non-empty cleanup plan is built
- attempt to restore every thread that DMD activates
- do not delete the thread container

The default best-effort policy can clean an unlocked thread when restoration
rights are unavailable or unknown. `--skip-unrestorable-threads` requires
creator attribution or effective `MANAGE_THREADS` before scanning. Restorable
transitions are journaled before activation, restoration runs in `finally`, and
interrupted restorations are retried on the next run. Locked threads still
require effective `MANAGE_THREADS`.

Creator-owned container deletion is now available only through
`--delete-owned-threads`:

- `none` is the default and never deletes a thread container.
- `self-only` requires matching `owner_id`, fetches unbounded thread history,
  proves the fetch completed, cross-checks `message_count`, and refuses container
  deletion when another or unknown author appears.
- `all` requires matching `owner_id` but intentionally permits deleting the
  complete shared thread without an author scan.

Both opt-in modes use Discord's thread delete endpoint, which still enforces
`MANAGE_THREADS`. DMD does not infer permission from creator ownership. A dry-run
can plan but cannot prove permission. If a real delete receives an unavailable
or missing-permission response, ordinary own-message/reaction cleanup follows
the selected archived-thread policy.

Successful container deletion supersedes retention, reaction-preservation,
fetch-boundary, and preserve-cache decisions for that thread. `self-only` is
safer than `all`, but not private-content-only: it can remove reactions placed by
other users on the creator's messages, and another message can race between the
completed scan and the delete request. The operation is explicit for precisely
those reasons.

An optional `archive owned empty threads` action could fit as an explicit
non-default action because a creator can often archive their own thread. It is
not deletion and another message can reopen it.

The implementation deliberately has no moderator override for another user's
thread. A future resource-management command can revisit that boundary, but it
must not weaken the creator match used by communication cleanup.

## Creator-attributed guild resources

Some guild resources have a real creator field and grant limited rights to that
creator. They are stronger future candidates than channels or roles, but they
still require guild permissions and affect shared server state.

| Resource | Creator attribution | Self-removal rule | Fit for DMD |
| --- | --- | --- | --- |
| Guild custom emoji | `emoji.user` | Creator needs `CREATE_GUILD_EXPRESSIONS` or `MANAGE_GUILD_EXPRESSIONS`; other users' emoji require manage permission | Separate opt-in resource module |
| Guild custom sticker | `sticker.user` | Same creator-specific expression permission model | Separate opt-in resource module |
| Guild soundboard sound | `sound.user` | Same creator-specific expression permission model | Separate opt-in resource module |
| Guild scheduled event | `creator_id` | Creator can modify/delete with `CREATE_EVENTS` or `MANAGE_EVENTS`, plus event/channel permissions; other creators' events require manage permission | Separate opt-in resource module |
| Application emoji | App-owned; uploader is recorded | Managed through the owning application/bot context | Developer tooling, not user cleanup |

These resources should not be folded into channel traversal. A future command
such as `dmd resources list` / `dmd resources clean` could inventory only
creator-matching resources, preview permission requirements, and default to no
deletion.

## Shared or administrator resources

The following objects can be caused or created by a user, but they are shared
server administration rather than self-owned communication. They do not belong
in DMD's default or core cleanup path.

| Resource | Why creator identity is insufficient | Removal boundary |
| --- | --- | --- |
| Guild invite | Invite has an `inviter`, but deletion requires `MANAGE_CHANNELS` or `MANAGE_GUILD` | Admin-only resource action |
| Webhook and webhook-authored messages | Webhook has a creator, but management requires `MANAGE_WEBHOOKS` or possession of its token; webhook messages are authored by the webhook | Integration management |
| Guild channel, category, forum, or media container | Public channel object has no self-ownership contract | `MANAGE_CHANNELS`; deleting removes shared content |
| Role and permission overwrite | No creator ownership contract | `MANAGE_ROLES`; affects other members |
| Forum/media tags and defaults | Parent-channel configuration, not a user's private artifact | `MANAGE_CHANNELS` / `MANAGE_THREADS` as applicable |
| Group DM name, icon, and recipients | Shared private-channel state; changing it affects every participant | Group DM owner/participant controls, not content cleanup |
| Pin state | Shared channel state and not reliably attributed to one user in the pin object | `PIN_MESSAGES`; do not infer ownership |
| Voice/stage channel status | Shared channel state without durable creator ownership | `SET_VOICE_CHANNEL_STATUS`, sometimes `MANAGE_CHANNELS` |
| Stage instance | Shared live-session object | Stage moderator permissions |
| Auto Moderation rule | May have creator metadata but is a guild policy | `MANAGE_GUILD` |
| Guild template | Has creator metadata but represents shared guild configuration | `MANAGE_GUILD` |
| Onboarding, Server Guide, rules, and default-channel configuration | Shared guild configuration without self-ownership | Server administration only |
| Server products and monetization configuration | Shared commercial configuration and financial obligations | Owner/admin and billing flows only |
| App/bot installation or integration | Installation modifies a guild and can grant broad permissions | Server owner or `MANAGE_GUILD` |
| Moderation action, timeout, ban, or guild report | Shared safety state and audit evidence | Moderator/admin flow; audit records are not user-cleanup targets |
| Guild/server | Owner can delete it, but that deletes the entire community | Explicit Discord server administration only |
| Audit-log entry | Immutable accountability record rather than authored content | No ordinary deletion API |

## Personal and account-state artifacts

These artifacts belong to the user more clearly, but are not communication
content. Adding all of them would turn DMD into a general Discord account
manager and significantly increase private-client API risk.

| Personal state | Typical removal/control | DMD recommendation |
| --- | --- | --- |
| Global profile: display name, avatar, banner, bio, pronouns, decorations, server tag | Clear or replace through account settings where supported | Out of message cleanup |
| Per-guild profile: nickname, avatar, banner, bio | Clear or replace while still a member and permitted | Separate account-hygiene tool |
| Friend, block, pending request, friend nickname, private note | Remove/unblock/cancel/clear in client | Separate account-hygiene tool |
| External account connection | Disconnect in account settings | Separate account-hygiene tool |
| Authorized app, user-installed app, OAuth grant, app role connection | Deauthorize/uninstall/delete connection | Separate security/account tool |
| Guild membership | Leave guild; owned guild must be transferred or deleted first | Does not delete prior messages |
| DM container | Close/hide and reopen later | Does not delete DM history |
| Group DM membership/ownership | Leave or manage recipients where allowed | Does not delete sent history |
| Thread membership and notification flags | Leave/change settings while state permits | Optional account-state module |
| Scheduled-event RSVP | Remove interest in client | Optional account-state module |
| Pending server member application | Withdraw in client | Optional account-state module |
| Member onboarding answers, selected channels, and selected roles | Change selections where the guild permits it | Optional account-state module, not authored content |
| Bookmark/reminder | Unsave, complete, or remove in client | Experimental; optional account-state module |
| Local Discord Clip | Delete from the local Clips library; shared copies are messages | Local filesystem concern, not API cleanup |
| Drafts, favorites, recent items, notification settings, and client preferences | Clear or reset where the client exposes controls | Settings cleanup, not content cleanup |
| Friend or Group DM invite link | Cancel, expire, close, or leave where the client permits | Private container/relationship management, not message cleanup |
| Third-party Activity or app state | Managed by the app provider and its own retention policy | Outside Discord message cleanup |
| Developer application, bot, team, commands, and application assets | Delete/manage in Developer Portal with owner/team authority | Separate developer tool |
| Nitro, boosts, subscriptions, purchases, and entitlements | Cancel or manage billing; transaction records may remain | Never automate as content cleanup |
| Trust and Safety reports, support tickets, appeals, and age-verification records | Provider/privacy process; retention rules apply | No ordinary DMD delete action |
| Discord account | Account deletion after transferring/deleting owned guilds | Anonymizes messages; it is not a substitute for message deletion |

Discord states that account deletion anonymizes the account across messages; it
does not say that all message bodies are removed. Users seeking content removal
should delete accessible content before deleting the account.

## Transient actions without a cleanup object

The following user actions generally do not leave a separately deletable
history object for DMD:

- typing indicators
- current presence and activity status
- current voice state, request-to-speak state, video, and screen share
- live soundboard playback
- an ongoing call or Activity session

Some actions can generate a system message, such as a call event. That system
message follows the message-type rules above and may be non-deletable even
though the live state is gone.

## Copies and retention outside user control

No implementation can promise complete erasure of all traces of a message:

- another user can forward a snapshot that does not update with the original
- announcement followers retain a deletion marker after a source is deleted
- bots and integrations may have copied content to external storage
- users may have quoted, screenshotted, downloaded, or notified on the content
- inaccessible guilds, channels, DMs, and threads cannot be mutated
- Discord may retain deleted content temporarily in caches, backups, or for
  legal and safety obligations

Discord's [retention explanation](https://support.discord.com/hc/en-us/articles/5431812448791-How-long-Discord-keeps-your-information)
explicitly distinguishes user-visible deletion from backup, legal, safety, and
other retention obligations. DMD should use wording such as `deleted accessible
message` rather than `erased every copy`.

## Fit with the current architecture

The existing channel inventory and message cleaner should remain the core. A
generic `delete any Discord object` abstraction would hide ownership and
permission differences that need to stay visible.

### Core communication cleanup

Continue extending the existing model incrementally:

- retain message `flags`, `poll`, `webhook_id`, and relevant reference fields
- keep `me_burst` and explicit reaction types in the planned-action pipeline
- add a poll-vote action only after the user endpoint is verified
- retain more thread lock and starter/source relationship data for reporting
- keep every action in the existing dry-run, logging, retention, and request-scheduler path

This keeps messages, reactions, and future poll votes under one principle: the
authenticated user can remove only their own content or interaction without
moderator authority.

### Discovery sources

Treat discovery as pluggable evidence rather than assuming one endpoint is
complete:

1. Current channel traversal remains the source that can inspect messages and
   reactions together.
2. Guild message search can accelerate own-message-only runs, but needs direct
   verification and cannot find reactions on other authors' messages.
3. A Data Package importer can seed historical channel/message IDs, especially
   for closed DMs, but every delete still needs a live access check.
4. Private thread search remains a client-API integration with explicit
   diagnostics and integration tests.

Candidate IDs from search or a data package should be re-fetched before action
so author, type, state, retention window, and current access are evaluated with
the same policy as ordinary traversal.

### Optional resource cleanup

If creator-owned guild resources are added later, use a separate inventory and
command boundary. Each resource adapter must expose:

- creator identity and confidence
- current permission requirement
- whether deletion affects other users
- whether deletion is reversible
- discovery source and completeness
- a concrete dry-run description

Emoji, stickers, soundboard sounds, and creator-owned scheduled events are the
only current guild resources that pass enough of this test to justify further
design. Threads need their own lifecycle policy because deletion can remove a
conversation.

## Recommended work order

### Priority 0: close correctness gaps

Completed on this branch: the current client request shape was captured, Super
Reaction actions were implemented, and null-name custom emoji route formatting
was corrected with fixtures. Remaining work:

1. Add live integration fixtures proving message fetch/delete and reaction
   behavior on each message-bearing channel type; the unit topology matrix is
   already covered.
2. Reconcile message deletion types with the official table and isolate
   client-only types behind explicit tests.

### Priority 1: improve discovery and lifecycle reporting

1. Add thread owner/starter/orphan information to inventory output without
   deleting containers.
2. Prototype a read-only Data Package importer and report which entries are
   currently actionable.
3. Evaluate author-filtered guild search only for modes that do not need full
   reaction traversal.
4. Live-test closed DM, left Group DM, private-thread, voice-chat, and stage-chat
   access boundaries.
5. Capture poll vote add/remove requests and decide whether the private API is
   stable enough for an own-vote cleanup action.

### Priority 2: explicitly separate adjacent resources

1. Design a read-only `resources list` inventory for creator-owned emoji,
   stickers, sounds, and scheduled events.
2. Consider archive-only handling for creator-owned empty threads.
3. Add deletion for other creator-owned resource types only after ownership,
   permission, third-party impact, and confirmation rules are represented in
   the plan.

Account settings, relationships, billing, developer applications, and shared
guild administration should remain outside DMD unless the product is
deliberately expanded and renamed around broader account hygiene.

## Live verification still required

Official documentation is sufficient for the channel matrix and most
permission boundaries. These client/user-token behaviors remain empirical and
must not be treated as settled until exercised against a disposable Discord
test account and guild:

- whether the observed typed burst route and documented normal route remove the
  intended variant when both variants use the same emoji
- live removal behavior for a custom emoji whose name is null
- creator-owned thread deletion with and without `MANAGE_THREADS`, including
  active, archived, text-thread, forum-post, and media-post cases
- the private user endpoint for adding/removing a poll vote
- completeness and pagination behavior of `/channels/{id}/threads/search`
- which closed DM and Group DM containers `/users/@me/channels` returns
- exact empty/orphan behavior after deleting text-thread and forum/media starter messages
- message-history requirements for voice and stage channel chat under real permissions

The integration account should have separate ordinary-member and moderator
roles so creator rights are not accidentally confused with elevated rights.

## Primary sources

### Discord developer documentation

- [Channels Resource](https://docs.discord.com/developers/resources/channel)
- [Threads](https://docs.discord.com/developers/topics/threads)
- [Message Resource](https://docs.discord.com/developers/resources/message)
- [Poll Resource](https://docs.discord.com/developers/resources/poll)
- [Permissions](https://docs.discord.com/developers/topics/permissions)
- [User Resource](https://docs.discord.com/developers/resources/user)
- [Emoji Resource](https://docs.discord.com/developers/resources/emoji)
- [Sticker Resource](https://docs.discord.com/developers/resources/sticker)
- [Soundboard Resource](https://docs.discord.com/developers/resources/soundboard)
- [Guild Scheduled Event Resource](https://docs.discord.com/developers/resources/guild-scheduled-event)
- [Invite Resource](https://docs.discord.com/developers/resources/invite)
- [Webhook Resource](https://docs.discord.com/developers/resources/webhook)
- [Stage Instance Resource](https://docs.discord.com/developers/resources/stage-instance)
- [Auto Moderation Resource](https://docs.discord.com/developers/resources/auto-moderation)
- [Guild Template Resource](https://docs.discord.com/developers/resources/guild-template)
- [Guild Resource](https://docs.discord.com/developers/resources/guild)

### Discord support documentation

- [Text Channels and Text Chat in Voice Channels](https://support.discord.com/hc/en-us/articles/4412085582359-Text-Channels-Text-Chat-In-Voice-Channels)
- [Stage Channels FAQ](https://support.discord.com/hc/en-us/articles/1500005513722-Stage-Channels-FAQ)
- [Threads FAQ](https://support.discord.com/hc/en-us/articles/4403205878423-Threads-FAQ)
- [Polls FAQ](https://support.discord.com/hc/en-us/articles/22163184112407-Polls-FAQ)
- [Reactions and Super Reactions FAQ](https://support.discord.com/hc/en-us/articles/12102061808663-Reactions-and-Super-Reactions-FAQ)
- [Voice Messages](https://support.discord.com/hc/en-us/articles/13091096725527-Voice-Messages)
- [Message Forwarding](https://support.discord.com/hc/en-us/articles/24640649961367-Message-Forwarding)
- [Announcement Channel FAQ](https://support.discord.com/hc/en-us/articles/360032008192-Announcement-Channel-FAQ)
- [Message Bookmarks and Reminders](https://support.discord.com/hc/en-us/articles/26442819646999-Message-Bookmarks-and-Reminders)
- [Server Member Applications](https://support.discord.com/hc/en-us/articles/29729107418519-Server-Member-Applications)
- [Clips](https://support.discord.com/hc/en-us/articles/16861982215703-Clips)
- [Your Discord Data Package](https://support.discord.com/hc/en-us/articles/360004957991-Your-Discord-Data-Package)
- [How to Delete Your Discord Account](https://support.discord.com/hc/en-us/articles/212500837-How-to-Delete-your-Discord-Account)
- [How Long Discord Keeps Your Information](https://support.discord.com/hc/en-us/articles/5431812448791-How-long-Discord-keeps-your-information)
