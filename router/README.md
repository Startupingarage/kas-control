# Station command router

## Project-session mode

An opt-in `project_sessions: true` configuration interprets every configured route
as a project command. Each route has one `catalog` entry marked
`project_session: true`. Only the configured project phrases and the generic
`открой чат` alias open conversations; native aliases are replaced by the common
`3\tзакрой чат` close command. The generic alias resolves `project-last.json`.
Phrases are stored without a wake word.

After a confirmed idle state, a consumed draft (cloud conversation ID **or voice
session ID**) rotates to a new local ID with empty cloud/parent/session identity.
Rotation first selects an unused slot already loaded in the native client. Eight
empty slots per project are registered with the idle-only `prepare_chats` command.
Ordinary openings use `select`. After a session closes, any reserve below eight
is replenished on the next idle check, without restarting the native process.
Confirmed success clears the retry delay, so consecutive closes can refill
immediately; an already full pool makes no preparation POST. Failed preparation has a
one-minute backoff. A finite pool can still be exhausted by rapid repeated calls;
an explicit idle opening can refill it immediately. An active conversation is
preserved if its requested target is unavailable. No voice
session or cloud conversation is opened by preloading. Cloud CID and voice SID
are assigned by Voice at first activation; server connection latency remains.

Obsolete local registrations marked `project_session` are pruned only after their
identities are durably recorded in private `session-identities/*.json`. Unmanaged
registrations, voice parameters and unknown settings fields are retained.
**This ledger is not a dialog backup.** The router never deletes cloud chats.
The separate [Windows archive worker](../docs/VOICE_ARCHIVE.md) copies full
dialogues to the project backup chat and verifies them before source deletion.
If that worker is unavailable or copying fails, original cloud chats remain.

`router prepare-projects` rotates drafts and forces idle pool replenishment once.
The worker checks rotation and low-reserve replenishment every two seconds.
`project-pool.json` reports counts of empty registrations loaded in the client.
`router open CHAT_ID` uses the fresh
project route when this mode is enabled. Startup retains existing supervisor
and fresh-recognition protections. Pending legacy recovery blocks project
routing until an explicit manual recovery; it cannot silently restart voice.

Prerequisites, device access guides and related projects:
[station preparation](../docs/PREREQUISITES.md). This router assumes an already
working root/SSH connection and the compatible native voice interfaces described there.

Separate standard-library Go process for Linux ARM64. It does not modify the
accepted voice binary, runtime environment, microphone, TTS, focus or display.
It consumes exact recognized commands and submits the existing `select` control
operation. Legacy fixed-chat activation can register a prepared local chat by
restarting the same voice executable; project-session mode has no such fallback. It
does not fabricate cloud conversation IDs or log spoken text, IDs or auth keys.

`routes.json`: version 1, at most 64 routes, each with `phrase`, `chat_id`
(local settings ID), `project_id` and `conversation_id`. Optional `catalog` holds
at most 64 entries: `chat_id` (UUID), `project_id`, `conversation_id`,
`parent_message_id`, `title`, `project_name`. Empty conversation/parent denotes
a prepared draft. A blank draft route resolves through its current catalog
entry. Legacy routes must identify existing native chats. Optional UI fields are
ignored. UTF-8 phrases normalize Latin/Russian case, ё to е, punctuation and
spaces, one leading Алиса/Яндекс and trailing пожалуйста. Duplicate normalized
phrases are rejected. Empty routes disables routing.

Write configuration atomically under `/tmp/kas-control-switch.lock`, then release
the lock and wait for `status.json`: `ready=true`, `connected=true`, matching
`config_sha256` and recent `updated_unix`. `last_error` is a nonsecret diagnostic.
Status can be temporarily not ready while a switch holds the lock. Bounded
in-memory `counters` expose `recognition_states`, `finals`, `fresh_finals`,
`rejected_missing_fresh_uuid`, `already_active`, and `selected_confirmed`, with
no recognized text or account identifiers. They reset with the worker process.
`features` contains `catalog` and `lazy_registration` for controller compatibility.

The supervisor owns `commands.original`, captured once before filtering.
Only native action 1 aliases matching routes are removed. Native close/new/
previous collisions are rejected. Do not edit `commands.txt` independently while
this supervisor owns it. Worker failure restores the exact original file before
restart. Supervisor termination stops the worker and restores it. Supervisor
SIGKILL makes the worker exit within one second; restoration then requires
supervisor restart or rollback. Simultaneous process death cannot restore a file
without a surviving process; next boot/start restores it before routing.

Frame parsing rejects malformed/oversized protobuf, request IDs are deduplicated,
and a BUSY final is fresh only after observing LISTENING/RECOGNIZING with the same
UUID on that TCP connection. This deliberately drops reconnect snapshots and
requests first seen mid-recognition. Legacy commands older than three seconds
are dropped. Project commands wait for the lifecycle lock for up to 45 seconds,
only while still the latest request. A repeat received during the same project
activation coalesces. One latest request is queued. Loaded target selection requires both
loaded local ID/project and on-disk conversation identity. The native queue is cancelled
only in native idle mode, scoped to that exact UUID. Chat mode already clears
the native queue. A POST is never retried; the resulting selected ID/active phase
is observed for up to 35 seconds. A loaded active target is not reopened.

`router open CHAT_ID` explicitly activates a configured catalog/route target and
returns JSON `{ok,chat_id,conversation_id,phase}`. Errors return `ok:false` and
`code:KAS_ERROR:ACTIVATION_FAILED`. CLI and spoken commands share the same lock
and activation logic. In legacy fixed-chat mode, a missing loaded target is lazily registered: save recovery
journal, stop the unchanged `run.sh`, read freshly flushed settings, preserve
unknown fields and voice, append the target if absent, restart the same `run.sh`,
then select once and observe. Existing draft local IDs always reuse native CID
and parent; an empty catalog CID does not create another entry. Active state/CID
does not prove a conversation has appeared in the cloud list before a first message.
After a confirmed activation, a nonempty native CID and available parent are saved into
the catalog and matching routes under the same switch lock. Unrelated config
fields remain intact; a changed config or conflicting identity rejects the save.
Thus restoring the previous native binding does not turn that prepared chat back
into a new draft on its next explicit open. Native may assign a CID before the
first utterance and leave parent empty; that active state is valid. A later parent
is refreshed after graceful stop before restoring settings, only for an identity
already saved after successful activation. A known parent is never replaced with
an empty one. No identity is invented, and an uncertain POST does not promote it.

Registration backups are compatible with the controller's existing recovery:
`/data/opt/kas-control-backups/YYYYMMDD-HHMMSS-hex8` contains `settings.json`,
`voice.sha256`, `restore.sh`, and `activation.json`; `LATEST` changes on success.
The voice binary/runtime hashes must stay unchanged. Failed registration attempts
restore the previous settings and active/idle state without repeating the failed
select. In fixed-chat mode, a durable `pending.json` enables startup recovery or `router recover`
after worker death; configuration polling itself never performs voice recovery.
`router restore BACKUP [--held-lock]` supports existing recovery scripts.
Failed/unpersisted draft CIDs are not cached separately or treated as cloud chats.

Project-session mode uses the native `prepare_chats_v1` capability instead of
stop/start registration. It maintains eight blank loaded slots per project;
used local entries are retired only after their identities reach the archive
ledger. Selected/previous entries are retained. Old clients and pending legacy
recovery fail without a restart fallback. An exhausted reserve never causes an
active conversation to close; the pool can refill after the normal close. The
native transaction, candidate deployment boundary and tests are described in
[`native/README.md`](../native/README.md). The existing loaded selection path
remains unchanged. Cloud transcript archival/deletion is a separate worker.

## Build and test

Run from the `router` directory with Go 1.22 or newer on Linux.

```sh
go test -race -count=1 ./...
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -buildvcs=false -trimpath -ldflags='-s -w' -o router-arm64 .
```

## Install on a prepared station

The Linux ARM64 release archive contains `router-arm64`, `supervise.sh`,
`rollback.sh` and `install.sh`. Copy those four files to a staging directory on
your station, then run `sh install.sh` there. The installer is for first
installation and expects the `/etc/init.d` boot layout. Back up any existing
router and its configuration before a separate upgrade.

For a fresh station that should use project commands, create
`/data/opt/kas-command-router/routes.json` with the following initial content
before installing. The installer preserves an existing configuration file.

```json
{"version":1,"project_sessions":true,"routes":[],"catalog":[]}
```

This example starts with zero routes. Keep a copy of your current routes before
migrating a station with fixed-chat bindings. After installation, check that
`/data/opt/kas-command-router/status.json` reports `ready` and `connected`.
Then assign the phrases in [Kas Control](../docs/GETTING_STARTED.md).

The independent hook is `S99zkasroutes`; no voice startup file is changed.
For rollback remove that exact hook, then run the installed `rollback.sh`.
Keep the directory and baseline for review/recovery. This restores native aliases
but does not change the last selected chat. No voice restart or APK installation.

Host tests do not prove real spoken-command timing. A passive live frame capture
must confirm UUID continuity before enabling routes; final device acceptance
requires the user's spoken command and selected-chat observation.
