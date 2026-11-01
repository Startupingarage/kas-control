# Station command router

Prerequisites, device access guides and related projects:
[station preparation](../docs/PREREQUISITES.md). This router assumes an already
working root/SSH connection and the compatible native voice interfaces described there.

Separate standard-library Go process for Linux ARM64. It does not modify the
accepted voice binary, runtime environment, microphone, TTS, focus or display.
It consumes exact recognized commands and submits the existing `select` control
operation. Explicit activation can register a prepared local chat by restarting
the same voice executable; management alone never starts/reconnects voice. It
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
requests first seen mid-recognition. Pending requests older than three seconds
are dropped. One latest request is queued. Loaded target selection requires both
loaded local ID/project and on-disk conversation identity. The native queue is cancelled
only in native idle mode, scoped to that exact UUID. Chat mode already clears
the native queue. A POST is never retried; the resulting selected ID/active phase
is observed for up to 35 seconds. A loaded active target is not reopened.

`router open CHAT_ID` explicitly activates a configured catalog/route target and
returns JSON `{ok,chat_id,conversation_id,phase}`. Errors return `ok:false` and
`code:KAS_ERROR:ACTIVATION_FAILED`. CLI and spoken commands share the same lock
and activation logic. A missing loaded target is lazily registered: save recovery
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
select. A durable `pending.json` enables startup recovery or `router recover`
after worker death; configuration polling itself never performs voice recovery.
`router restore BACKUP [--held-lock]` supports existing recovery scripts.
Failed/unpersisted draft CIDs are not cached separately or treated as cloud chats.

Build in StationLab WSL:

```
/opt/station-go/go/bin/go test -race -count=1 ./...
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 /opt/station-go/go/bin/go build -buildvcs=false -trimpath -ldflags='-s -w' -o router-arm64 .
```

Upload `router-arm64`, `supervise.sh`, `rollback.sh`, `install.sh` to a private
staging directory. `sh install.sh` supports first installation only and requires
the verified boot directory `/etc/init.d`. Inspect actual boot
layout first; do not redirect installation into another tree speculatively.
The independent hook is `S99zkasroutes`; no voice startup file is changed.
For rollback remove that exact hook, then run the installed `rollback.sh`.
Keep the directory and baseline for review/recovery. This restores native aliases
but does not change the last selected chat. No voice restart or APK installation.

Host tests do not prove real spoken-command timing. A passive live frame capture
must confirm UUID continuity before enabling routes; final device acceptance
requires the user's spoken command and selected-chat observation.
