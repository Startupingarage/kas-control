package main

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

func projectFixture(t *testing.T) (*Router, *Status, Config) {
	t.Helper()
	dir := t.TempDir()
	os.Mkdir(dir+"/state", 0700)
	atomicWrite(dir+"/state/ui-auth.json", encoded(map[string]string{"key": "test"}))
	a := "00000000-0000-4000-8000-000000000001"
	b := "00000000-0000-4000-8000-000000000002"
	c := Config{Version: 1, ProjectSessions: true, Routes: []Route{{Phrase: "готовка", ChatID: a, ProjectID: "g-p-cook"}, {Phrase: "голосовой чат", ChatID: b, ProjectID: "g-p-core"}}, Catalog: []CatalogEntry{{ChatID: a, ProjectID: "g-p-cook", Title: "Cooking", ProjectSession: true}, {ChatID: b, ProjectID: "g-p-core", Title: "Voice", ProjectSession: true}}}
	atomicWrite(dir+"/routes.json", encoded(c))
	doc := map[string]any{"version": 1, "voice": "orbit", "selected": a, "previous": "legacy", "chats": []map[string]any{{"id": a, "gizmo_id": "g-p-cook", "conversation_id": "cloud-one", "parent_message_id": "last-answer", "voice_session_id": "session-one", "project_session": true}, {"id": "legacy", "gizmo_id": "g-p-core", "conversation_id": "keep-me"}}}
	atomicWrite(dir+"/state/settings.json", encoded(doc))
	s := &Status{Phase: "idle", Connected: true}
	s.Native.Mode = "idle"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		if q.Method != "GET" {
			t.Error("preparation must not start voice")
			w.WriteHeader(500)
			return
		}
		json.NewEncoder(w).Encode(s)
	}))
	t.Cleanup(server.Close)
	return &Router{dir: dir, voiceDir: dir, api: server.URL, client: server.Client(), lockPath: dir + "/lock"}, s, c
}

func TestProjectSessionsOpenClosePrepareOpen(t *testing.T) {
	r := hotPoolFixture(t)
	if e := r.prepareProjectDrafts(); e != nil {
		t.Fatal(e)
	}
	c, _ := loadConfig(r.dir + "/routes.json")
	first, e := r.open(c.Routes[1].ChatID)
	if e != nil {
		t.Fatal(e)
	}
	atomicWrite(r.dir+"/phase", []byte("idle"))
	if e = r.prepareProjectDrafts(); e != nil {
		t.Fatal(e)
	}
	next, _ := loadConfig(r.dir + "/routes.json")
	route, e := r.projectRoute(next, "открой чат")
	if e != nil || route.ChatID == first.ChatID || route.ProjectID != "g-p-core" {
		t.Fatal(route, e)
	}
	second, e := r.open(route.ChatID)
	if e != nil || second.ConversationID == first.ConversationID {
		t.Fatal(second, e)
	}
	history, e := os.ReadFile(r.dir + "/session-identities/" + first.ChatID + ".json")
	if e != nil || !strings.Contains(string(history), first.ConversationID) {
		t.Fatal("missing prior identity", e)
	}
	assertNoLifecycle(t, r)
}

func TestProjectDraftRotationAndActiveGuard(t *testing.T) {
	r, s, c := projectFixture(t)
	before, _ := os.ReadFile(r.voiceDir + "/state/settings.json")
	s.Phase = "active"
	s.Native.Mode = "chat"
	if e := r.prepareProjectDrafts(); e != nil {
		t.Fatal(e)
	}
	unchanged, _ := loadConfig(r.dir + "/routes.json")
	if unchanged.Routes[0].ChatID != c.Routes[0].ChatID {
		t.Fatal("rotated active chat")
	}
	s.Phase = "idle"
	s.Native.Mode = "idle"
	if e := r.prepareProjectDrafts(); e != nil {
		t.Fatal(e)
	}
	next, e := loadConfig(r.dir + "/routes.json")
	if e != nil {
		t.Fatal(e)
	}
	if next.Routes[0].ChatID == c.Routes[0].ChatID || next.Routes[0].ConversationID != "" || next.Routes[1].ChatID != c.Routes[1].ChatID {
		t.Fatal("not a fresh project-only draft", next)
	}
	if next.Catalog[0].ParentID != "" || !next.Catalog[0].ProjectSession {
		t.Fatal("identity leaked")
	}
	history, _ := os.ReadFile(r.dir + "/session-identities/" + c.Routes[0].ChatID + ".json")
	if !strings.Contains(string(history), "cloud-one") || !strings.Contains(string(history), "last-answer") {
		t.Fatal("lost original identity", string(history))
	}
	after, _ := os.ReadFile(r.voiceDir + "/state/settings.json")
	if string(before) != string(after) {
		t.Fatal("background preparation touched voice settings")
	}
	if e := r.prepareProjectDrafts(); e != nil {
		t.Fatal(e)
	}
	again, _ := loadConfig(r.dir + "/routes.json")
	if again.Routes[0].ChatID != next.Routes[0].ChatID {
		t.Fatal("duplicate preparation")
	}
}

func TestProjectCommandFilteringAndLastProject(t *testing.T) {
	r, _, c := projectFixture(t)
	original := []byte("1\tоткрой чат\n1\tстарый вызов\n2\tновый чат\n3\tзакрой чат\n4\tпредыдущий чат\n")
	got, e := filteredCommands(original, c)
	if e != nil || string(got) != "3\tзакрой чат\n" {
		t.Fatal(string(got), e)
	}
	got, e = filteredCommands([]byte("3\tвыйди из чата\n"), c)
	if e != nil || string(got) != "3\tзакрой чат\n" {
		t.Fatal("canonical close missing", e)
	}
	atomicWrite(r.dir+"/project-last.json", encoded(map[string]string{"project_id": "g-p-core"}))
	route, e := r.projectRoute(c, "открой чат")
	if e != nil || route.ProjectID != "g-p-core" {
		t.Fatal(route, e)
	}
	route, e = r.projectRoute(c, "готовка")
	if e != nil || route.ProjectID != "g-p-cook" {
		t.Fatal(route, e)
	}
	if _, e = r.projectRoute(c, "старый вызов"); e == nil {
		t.Fatal("old command remained")
	}
}

func TestProjectPruneKeepsLegacyAndPrepared(t *testing.T) {
	r, _, c := projectFixture(t)
	if e := r.prepareProjectDrafts(); e != nil {
		t.Fatal(e)
	}
	next, _ := loadConfig(r.dir + "/routes.json")
	_, chats, _ := rawSettings(r.voiceDir + "/state/settings.json")
	fresh, _ := r.preparedChat(next.Catalog[0])
	chats = append(chats, fresh)
	kept, e := r.pruneProjectRegistrations(chats)
	if e != nil {
		t.Fatal(e)
	}
	if len(kept) != 2 || rawString(kept[0]["id"]) != "legacy" || rawString(kept[1]["id"]) != next.Routes[0].ChatID {
		t.Fatal("wrong pruning", kept)
	}
	if _, e = os.Stat(r.dir + "/session-identities/" + c.Routes[0].ChatID + ".json"); e != nil {
		t.Fatal(e)
	}
}

func TestProjectCancelledSessionBeforeConversationRotates(t *testing.T) {
	r, _, c := projectFixture(t)
	doc, chats, _ := rawSettings(r.dir + "/state/settings.json")
	chats[0]["conversation_id"] = encoded("")
	chats[0]["parent_message_id"] = encoded("")
	doc["chats"] = encoded(chats)
	atomicWrite(r.dir+"/state/settings.json", encoded(doc))
	if e := r.prepareProjectDrafts(); e != nil {
		t.Fatal(e)
	}
	next, e := loadConfig(r.dir + "/routes.json")
	if e != nil || next.Routes[0].ChatID == c.Routes[0].ChatID {
		t.Fatal("reused cancelled voice session", e)
	}
	fresh, _ := r.preparedChat(next.Catalog[0])
	if rawString(fresh["voice_session_id"]) != "" {
		t.Fatal("old SID copied")
	}
}

func TestProjectRotationUsesLoadedReserve(t *testing.T) {
	r, s, c := projectFixture(t)
	doc, chats, _ := rawSettings(r.dir + "/state/settings.json")
	reserve := c.Catalog[0]
	reserve.ChatID = "00000000-0000-4000-8000-000000000003"
	raw, _ := r.preparedChat(reserve)
	chats = append(chats, raw)
	doc["chats"] = encoded(chats)
	atomicWrite(r.dir+"/state/settings.json", encoded(doc))
	s.Settings.Chats = []Chat{{ID: reserve.ChatID, Project: reserve.ProjectID}}
	before, _ := os.ReadFile(r.dir + "/state/settings.json")
	if e := r.prepareProjectDrafts(); e != nil {
		t.Fatal(e)
	}
	next, e := loadConfig(r.dir + "/routes.json")
	if e != nil || next.Routes[0].ChatID != reserve.ChatID {
		t.Fatal("ignored loaded empty reserve", e)
	}
	after, _ := os.ReadFile(r.dir + "/state/settings.json")
	if string(before) != string(after) {
		t.Fatal("rotation must not restart or rewrite voice")
	}
}

func loadedPoolFixture(t *testing.T) *Router {
	t.Helper()
	r, _, _ := projectFixture(t)
	r.backupRoot = r.dir + "/backups"
	os.Mkdir(r.dir+"/bin", 0700)
	os.WriteFile(r.dir+"/bin/alisa-standalone", []byte("accepted voice"), 0600)
	os.WriteFile(r.dir+"/runtime.env", []byte("accepted audio"), 0600)
	os.WriteFile(r.dir+"/phase", []byte("idle"), 0600)
	b, _ := os.ReadFile(r.dir + "/state/settings.json")
	os.WriteFile(r.dir+"/loaded.json", b, 0600)
	os.WriteFile(r.dir+"/commands.txt", []byte("3\tзакрой чат\n"), 0600)
	r.original = []byte("1\tоткрой чат\n3\tзакрой чат\n")
	os.WriteFile(r.dir+"/run.sh", []byte(`#!/bin/sh
set -eu
B=$(dirname "$0")
echo "$1" >> "$B/actions"
echo idle > "$B/phase"
if [ "$1" = start ]; then
 if [ -f "$B/fail-load-once" ]; then rm "$B/fail-load-once"; else cp "$B/state/settings.json" "$B/loaded.json"; fi
fi
`), 0700)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, q *http.Request) {
		if q.Method == "POST" {
			var cmd map[string]string
			json.NewDecoder(q.Body).Decode(&cmd)
			if cmd["action"] != "select" {
				t.Error("unexpected mutation", cmd)
				w.WriteHeader(500)
				return
			}
			if _, e := os.Stat(r.dir + "/fail-select-once"); e == nil {
				os.Remove(r.dir + "/fail-select-once")
				atomicWrite(r.dir+"/phase", []byte("idle"))
				atomicWrite(r.dir+"/error", []byte("connection_failed"))
				json.NewEncoder(w).Encode(map[string]bool{"ok": true})
				return
			}
			doc, chats, e := rawSettings(r.dir + "/loaded.json")
			if e != nil {
				t.Error(e)
				w.WriteHeader(500)
				return
			}
			found := false
			for _, chat := range chats {
				if rawString(chat["id"]) == cmd["chat_id"] {
					allow, _ := os.ReadFile(r.dir + "/allow-restore")
					if !freshProjectChat(chat, rawString(chat["gizmo_id"])) && string(allow) != cmd["chat_id"] {
						t.Error("reused cloud or voice identity")
					}
					chat["conversation_id"] = encoded("cloud-" + cmd["chat_id"])
					chat["voice_session_id"] = encoded("sid-" + cmd["chat_id"])
					chat["parent_message_id"] = encoded("answer")
					found = true
				}
			}
			if !found {
				t.Error("selected slot not loaded")
				w.WriteHeader(500)
				return
			}
			doc["chats"] = encoded(chats)
			doc["selected"] = encoded(cmd["chat_id"])
			atomicWrite(r.dir+"/loaded.json", encoded(doc))
			atomicWrite(r.dir+"/state/settings.json", encoded(doc))
			atomicWrite(r.dir+"/phase", []byte("active"))
			os.Remove(r.dir + "/error")
			f, _ := os.OpenFile(r.dir+"/posts", os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
			f.WriteString("select\n")
			f.Close()
			json.NewEncoder(w).Encode(map[string]bool{"ok": true})
			return
		}
		s := Status{Connected: true}
		failure, _ := os.ReadFile(r.dir + "/error")
		s.Error = string(failure)
		readJSON(r.dir+"/loaded.json", &s.Settings)
		phase, _ := os.ReadFile(r.dir + "/phase")
		s.Phase = strings.TrimSpace(string(phase))
		s.Native.Mode = "idle"
		if s.Phase == "active" {
			s.Native.Mode = "chat"
		}
		json.NewEncoder(w).Encode(s)
	}))
	t.Cleanup(server.Close)
	r.api = server.URL
	r.client = server.Client()
	return r
}

func TestLoadedPoolThreeOpeningsAndBusyCommand(t *testing.T) {
	r := hotPoolFixture(t)
	if e := r.prepareProjectDrafts(); e != nil {
		t.Fatal(e)
	}
	if e := r.replenishProjectPool(true); e != nil {
		t.Fatal(e)
	}
	actions, _ := os.ReadFile(r.dir + "/actions")
	if len(actions) != 0 {
		t.Fatal(string(actions))
	}
	if _, e := os.Stat(r.dir + "/posts"); !os.IsNotExist(e) {
		t.Fatal("preparation opened voice")
	}
	_, chats, _ := rawSettings(r.dir + "/state/settings.json")
	if len(chats) != 2*projectPoolSize+2 {
		t.Fatal("pool not loaded", len(chats))
	}
	if e := r.replenishProjectPool(true); e != nil {
		t.Fatal(e)
	}
	seen := map[string]bool{}
	for i := 0; i < 3; i++ {
		c, _ := loadConfig(r.dir + "/routes.json")
		id := c.Routes[0].ChatID
		if seen[id] {
			t.Fatal("repeated chat")
		}
		seen[id] = true
		if i < 2 {
			result, e := r.open(id)
			if e != nil || result.ConversationID != "cloud-"+id {
				t.Fatal(result, e)
			}
		} else {
			held, e := r.switchMutex()
			if e != nil {
				t.Fatal(e)
			}
			r.connection(true)
			r.recognizing("queued")
			a, z := net.Pipe()
			defer a.Close()
			defer z.Close()
			go func() { readFrame(z) }()
			done := make(chan error, 1)
			go func() {
				done <- r.dispatch(Event{ID: "queued", Text: "готовка", At: time.Now().Add(-4 * time.Second), Conn: a})
			}()
			time.Sleep(150 * time.Millisecond)
			unlock(held)
			if e := <-done; e != nil {
				t.Fatal("queued command lost", e)
			}
			// A repeat spoken during connection must not consume a second reserve.
			r.recognizing("repeat")
			if e := r.dispatch(Event{ID: "repeat", Text: "готовка", At: r.projectOpenedAt.Add(-time.Second)}); e != nil {
				t.Fatal(e)
			}
		}
		if e := r.replenishProjectPool(true); e == nil {
			t.Fatal("preparation accepted active voice")
		}
		atomicWrite(r.dir+"/phase", []byte("idle"))
		if e := r.prepareProjectDrafts(); e != nil {
			t.Fatal(e)
		}
		after, _ := os.ReadFile(r.dir + "/actions")
		if string(after) != string(actions) {
			t.Fatal("restarted voice on a prepared opening")
		}
	}
	posts, _ := os.ReadFile(r.dir + "/posts")
	if string(posts) != "select\nselect\nselect\n" {
		t.Fatal(string(posts))
	}
	c, _ := loadConfig(r.dir + "/routes.json")
	last, e := r.projectRoute(c, "открой чат")
	if e != nil || last.ProjectID != "g-p-cook" {
		t.Fatal(last, e)
	}
	if r.counters["project_commands_waited"] != 1 || r.counters["project_commands_coalesced"] != 1 {
		t.Fatal(r.counters)
	}
}

func TestPoolFailedLoadRestoresSettingsWithoutVoice(t *testing.T) {
	r := hotPoolFixture(t)
	if e := r.prepareProjectDrafts(); e != nil {
		t.Fatal(e)
	}
	before, _ := os.ReadFile(r.dir + "/state/settings.json")
	os.WriteFile(r.dir+"/reject-prepare", nil, 0600)
	if e := r.replenishProjectPool(true); e == nil {
		t.Fatal("failed load accepted")
	}
	after, _ := os.ReadFile(r.dir + "/state/settings.json")
	if string(before) != string(after) {
		t.Fatal("settings not restored")
	}
	if _, e := os.Stat(r.dir + "/posts"); !os.IsNotExist(e) {
		t.Fatal("opened voice while recovering preparation")
	}
	if _, e := os.Stat(r.dir + "/pending.json"); !os.IsNotExist(e) {
		t.Fatal("pending journal left")
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(q *http.Request) (*http.Response, error) { return f(q) }

func TestProjectSupersededDuringFinalStatusDoesNotSelect(t *testing.T) {
	r := hotPoolFixture(t)
	if e := r.prepareProjectDrafts(); e != nil {
		t.Fatal(e)
	}
	if e := r.replenishProjectPool(true); e != nil {
		t.Fatal(e)
	}
	r.connection(true)
	r.recognizing("open")
	original := r.client.Transport
	gets := 0
	r.client.Transport = roundTripFunc(func(q *http.Request) (*http.Response, error) {
		if q.Method == "GET" {
			gets++
			if gets == 3 {
				r.recognizing("close")
			}
		}
		return original.RoundTrip(q)
	})
	a, z := net.Pipe()
	defer a.Close()
	defer z.Close()
	go func() { readFrame(z) }()
	if e := r.dispatch(Event{ID: "open", Text: "готовка", At: time.Now(), Conn: a}); e == nil {
		t.Fatal("superseded operation accepted")
	}
	if _, e := os.Stat(r.dir + "/posts"); !os.IsNotExist(e) {
		t.Fatal("superseded command selected voice")
	}
}

func TestLoadedSelectionFailureCanRecover(t *testing.T) {
	for _, active := range []bool{false, true} {
		t.Run(fmt.Sprint(active), func(t *testing.T) {
			r := hotPoolFixture(t)
			if e := r.prepareProjectDrafts(); e != nil {
				t.Fatal(e)
			}
			if e := r.replenishProjectPool(true); e != nil {
				t.Fatal(e)
			}
			c, _ := loadConfig(r.dir + "/routes.json")
			oldID := ""
			if active {
				previous, e := r.open(c.Routes[0].ChatID)
				if e != nil {
					t.Fatal(e)
				}
				oldID = previous.ChatID
				os.WriteFile(r.dir+"/allow-restore", []byte(oldID), 0600)
			}
			os.WriteFile(r.dir+"/fail-select-once", nil, 0600)
			r.connection(true)
			r.recognizing("open-second")
			ev := Event{ID: "open-second", Text: "голосовой чат", At: time.Now()}
			if !active {
				a, z := net.Pipe()
				defer a.Close()
				defer z.Close()
				go func() { readFrame(z) }()
				ev.Conn = a
			}
			if e := r.dispatch(ev); e == nil {
				t.Fatal("failed select accepted")
			}
			var s Status
			r.request("GET", "/api/status", nil, &s)
			if active && (s.Phase != "active" || s.Settings.Selected != oldID || s.Error != "") {
				t.Fatal("active conversation not restored", s.Phase, s.Error)
			}
			// An explicit next command can clear a stale native connection error.
			c, _ = loadConfig(r.dir + "/routes.json")
			if _, e := r.open(c.Routes[1].ChatID); e != nil {
				t.Fatal("connection error poisoned subsequent command", e)
			}
			actions, _ := os.ReadFile(r.dir + "/actions")
			if len(actions) != 0 {
				t.Fatal("recovery restarted voice")
			}
		})
	}
}
