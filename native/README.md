# Hot preparation of project chats

`patches/alisa-voice-0.3.10.patch` adds the prepared-slot command and passes the
selected project ID to the native session request. [Build and install from a
clean checkout](../docs/ALISA_VOICE_0310.md). The older
`patches/0001-hot-project-slots.patch` targets the original prototype snapshot.

## Contract

The existing authenticated local control API advertises
`features: ["prepare_chats_v1"]`, a process `pid`, and `settings_revision` in
`GET /api/status`. The revision is SHA-256 of the canonical private settings JSON,
computed before private fields are removed from the public status.

Send once to `POST /api/command`:

```json
{
  "action": "prepare_chats",
  "expected_revision": "<revision from status>",
  "add": [{
    "id": "00000000-0000-4000-8000-000000000001",
    "gizmo_id": "g-p-example",
    "title": "Prepared conversation",
    "project_session": true,
    "conversation_id": "",
    "parent_message_id": "",
    "voice_session_id": "",
    "account_id": "",
    "confirmed": false
  }],
  "remove": []
}
```

The client accepts this only while connected and fully idle: no voice call,
queued request, unfinished request handler, teardown or shutdown. It verifies
the expected revision and equality of disk and memory settings under the
settings lock. Additions must be blank project slots with unique UUIDs and known
fields. At most 64 local registrations may exist. Removal is limited to used,
managed local slots; selected, previous, blank and unmanaged entries are protected.
No cloud chat is deleted by this command. A cloud conversation and voice session
are allocated later, through the existing selection path.

The replacement settings are written atomically before in-memory publication.
Unknown existing settings and all voice fields are retained. On an uncertain
HTTP outcome the router reads back status and settings rather than retrying the
POST. A process change invalidates confirmation.

## Router and archive

Project routing keeps eight blank loaded slots per configured project and
replenishes any deficit on the next idle check after a close. Successful refill
does not delay the next one; failures retain the one-minute backoff. Closed conversation identities are journaled
before local retirement. The archive worker remains independent: it copies and
verifies full transcripts before deleting their cloud source conversations.

An explicit idle opening can replenish an exhausted pool despite a retained
connection error; selection clears that error. Background preparation does not
ignore errors. If an active conversation has no prepared target, opening fails
without closing it; refill waits for the normal close. A pending legacy recovery
journal blocks automatic project routing rather than restarting voice. Explicit
`router recover` / `router restore` retain their documented manual behavior.

## Tests and deployment boundary

`tests/hot_slots.cpp` uses the real settings transaction. It checks persistence,
ownership, protected fields, stale revisions, external edits and failed writes.
`tests/daemon_hot_slots.cpp` uses the real daemon with fake network, audio and
station interfaces. Its generated `daemon_under_test.hpp` is `daemon.hpp` with
only the includes of `voice_call.hpp`, `children.hpp` and `http_server.hpp` removed.
It checks queued, connecting, cancelled and active-call guards.

Router tests exercise repeated opening/closing/refilling, identity retention,
unknown replies, old clients, exhausted reserves and pending recovery. Run them
with `go test -race -count=1 ./...` in `router/`. Archive regressions are covered by
`python -m unittest test_voice_archive test_archive_worker`.

## Source compatibility

The 0.3.10 patch targets upstream commit
`24806ecaea2258bbaa0c779ebceb9fdfeebc66fb`. Its four headers cover the settings
transaction, daemon lifecycle guard, and project session metadata. Idle is
published after call teardown so the router can replenish the reserve promptly.

A client already advertising `prepare_chats_v1` can be used as-is. On a client
without the capability, installation requires one controlled native update with
a rollback. Normal pool refill then runs within the existing client process.

Before migration, verify an idle session, no pending recovery, sufficient device
space, candidate hashes and rollback. Preserve the accepted executable and router
as separate files. Rollback restores those executables while retaining current
settings, conversation identities and archive work; never restore a stale copy
of settings over conversations created after migration. Live voice, interruption
and face acceptance require a spoken test after the authorized migration.
