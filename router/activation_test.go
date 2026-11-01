package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

func TestLifecycleErrorsAreBoundedAndSecretFree(t *testing.T) {
	dir := t.TempDir()
	r := Router{voiceDir: dir}
	os.WriteFile(dir+"/run.sh", []byte("#!/bin/sh\necho SECRET >&2\nexit 7\n"), 0700)
	if e := r.run("start"); e == nil || e.Error() != "voice lifecycle start exited 7" {
		t.Fatal(e)
	}
	os.WriteFile(dir+"/run.sh", []byte("#!/bin/sh\nexec sleep 5\n"), 0700)
	if e := r.runTimed("stop", 20*time.Millisecond); e == nil || e.Error() != "voice lifecycle stop timed out" {
		t.Fatal(e)
	}
}

func TestFullSettingsRegistrationNeverStopsVoice(t *testing.T) {
	dir := t.TempDir()
	os.Mkdir(dir+"/state", 0700)
	chats := []map[string]string{}
	for i := 0; i < 64; i++ {
		chats = append(chats, map[string]string{"id": fmt.Sprintf("existing-%d", i)})
	}
	atomicWrite(dir+"/state/settings.json", encoded(map[string]any{"version": 1, "voice": "orbit", "chats": chats}))
	r := Router{dir: dir, voiceDir: dir, backupRoot: dir + "/backups"}
	e := r.register(CatalogEntry{ChatID: "absent"}, Status{})
	if e == nil || e.Error() != "chat limit" {
		t.Fatal(e)
	}
	for _, path := range []string{"pending.json", "backups", "actions"} {
		if _, e = os.Stat(dir + "/" + path); !os.IsNotExist(e) {
			t.Fatal("side effect on full settings", path)
		}
	}
}

func TestCatalogDraftCanonicalization(t *testing.T) {
	dir := t.TempDir()
	entry := CatalogEntry{ChatID: "00000000-0000-4000-8000-000000000001", ProjectID: "g-p-test", ConversationID: "cloud", ParentID: "parent", Title: "Prepared"}
	cfg := Config{Version: 1, Catalog: []CatalogEntry{entry}, Routes: []Route{{Phrase: "открой подготовленный", ChatID: entry.ChatID, ProjectID: entry.ProjectID}}}
	if e := atomicWrite(dir+"/routes.json", encoded(cfg)); e != nil {
		t.Fatal(e)
	}
	got, e := loadConfig(dir + "/routes.json")
	if e != nil || got.Routes[0].ConversationID != "cloud" {
		t.Fatal(got, e)
	}
	cfg.Routes[0].ProjectID = "g-p-other"
	atomicWrite(dir+"/routes.json", encoded(cfg))
	if _, e = loadConfig(dir + "/routes.json"); e == nil {
		t.Fatal("cross-project catalog accepted")
	}
}
func TestPersistIdentityPreservesConfigurationAndRejectsUncertainIdentity(t *testing.T) {
	dir := t.TempDir()
	os.Mkdir(dir+"/state", 0700)
	id := "00000000-0000-4000-8000-000000000001"
	catalog := map[string]any{"chat_id": id, "project_id": "g-p-test", "conversation_id": "", "parent_message_id": "", "title": "Prepared", "project_name": "Name", "ui_future": "retain"}
	route := map[string]any{"chat_id": id, "project_id": "g-p-test", "conversation_id": "", "phrase": "открой кас", "ui_color": "green"}
	other := map[string]any{"chat_id": "other", "project_id": "g-p-other", "conversation_id": "other-cloud", "phrase": "открой другой", "unrelated": true}
	config := map[string]any{"version": 1, "catalog": []any{catalog}, "routes": []any{route, other}, "future": map[string]any{"preserved": true}}
	atomicWrite(dir+"/routes.json", encoded(config))
	initial, _ := os.ReadFile(dir + "/routes.json")
	r := Router{dir: dir, voiceDir: dir}
	target := Route{ChatID: id, ProjectID: "g-p-test"}
	settings := Settings{Version: 1, Selected: id, Chats: []Chat{{ID: id, Project: "g-p-test"}}}
	atomicWrite(dir+"/state/settings.json", encoded(settings))
	if e := r.persistIdentity(target); e != nil {
		t.Fatal(e)
	}
	unchanged, _ := os.ReadFile(dir + "/routes.json")
	if string(unchanged) != string(initial) {
		t.Fatal("empty identity persisted")
	}
	settings.Chats[0].Conversation = "realized"
	atomicWrite(dir+"/state/settings.json", encoded(settings))
	if e := r.persistIdentity(target); e != nil {
		t.Fatal("native CID before first parent rejected", e)
	}
	if saved, e := loadConfig(dir + "/routes.json"); e != nil || saved.Catalog[0].ConversationID != "realized" || saved.Catalog[0].ParentID != "" {
		t.Fatal(saved, e)
	}
	settings.Chats[0].Parent = "parent"
	atomicWrite(dir+"/state/settings.json", encoded(settings))
	if e := r.persistIdentity(target); e != nil {
		t.Fatal(e)
	}
	var got map[string]json.RawMessage
	readJSON(dir+"/routes.json", &got)
	if string(got["future"]) != "{\"preserved\":true}" {
		t.Fatal("top-level metadata lost")
	}
	var rows []map[string]json.RawMessage
	json.Unmarshal(got["routes"], &rows)
	if rawString(rows[0]["ui_color"]) != "green" || rawString(rows[0]["conversation_id"]) != "realized" || string(encoded(rows[1])) != string(encoded(other)) {
		t.Fatal("route data lost")
	}
	json.Unmarshal(got["catalog"], &rows)
	if rawString(rows[0]["ui_future"]) != "retain" || rawString(rows[0]["project_name"]) != "Name" {
		t.Fatal("catalog metadata lost")
	}
}

func TestLazyRegistrationAndRollback(t *testing.T) {
	for _, fail := range []bool{false, true} {
		t.Run(map[bool]string{false: "draft_reopen", true: "rollback"}[fail], func(t *testing.T) {
			dir := t.TempDir()
			os.Mkdir(dir+"/state", 0700)
			os.Mkdir(dir+"/bin", 0700)
			write := func(path string, b []byte) {
				t.Helper()
				if e := os.WriteFile(dir+path, b, 0700); e != nil {
					t.Fatal(e)
				}
			}
			original := []byte(`{"version":1,"voice":"orbit","selected":"old","previous":"","future":{"retained":true},"chats":[{"id":"old","gizmo_id":"g-p-old","conversation_id":"old-cloud","parent_message_id":"before-stop","title":"old"}]}`)
			write("/state/settings.json", original)
			write("/loaded.json", original)
			write("/state/ui-auth.json", []byte(`{"key":"test"}`))
			write("/bin/alisa-standalone", []byte("accepted-binary"))
			write("/runtime.env", []byte("accepted-runtime"))
			write("/phase", []byte("active"))
			write("/run.sh", []byte(`#!/bin/sh
set -eu
B=$(dirname "$0")
echo "$1" >> "$B/actions"
case "$1" in
stop) sed -e 's/before-stop/after-stop/g' -e 's/"parent_message_id":""/"parent_message_id":"server-parent"/g' "$B/state/settings.json" > "$B/state/temp"; mv "$B/state/temp" "$B/state/settings.json"; echo idle > "$B/phase";;
start) cp "$B/state/settings.json" "$B/loaded.json"; echo idle > "$B/phase";;
*) exit 1;;
esac
`))
			newID := "00000000-0000-4000-8000-000000000001"
			entry := CatalogEntry{ChatID: newID, ProjectID: "g-p-new", Title: "Prepared"}
			cfg := Config{Version: 1, Catalog: []CatalogEntry{entry}, Routes: []Route{{Phrase: "открой подготовленный", ChatID: newID, ProjectID: "g-p-new"}}}
			write("/routes.json", encoded(cfg))
			write("/commands.txt", []byte("3\tзакрой чат\n"))
			posts := 0
			created := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				if req.Method == "POST" {
					posts++
					var v map[string]string
					json.NewDecoder(req.Body).Decode(&v)
					if fail && v["chat_id"] == newID {
						w.WriteHeader(500)
						return
					}
					doc, chats, e := rawSettings(dir + "/state/settings.json")
					if e != nil {
						t.Error(e)
						w.WriteHeader(500)
						return
					}
					doc["selected"] = encoded(v["chat_id"])
					for _, chat := range chats {
						if rawString(chat["id"]) == v["chat_id"] && rawString(chat["conversation_id"]) == "" {
							created++
							chat["conversation_id"] = encoded("server-created")
							chat["parent_message_id"] = encoded("")
						}
					}
					doc["chats"] = encoded(chats)
					atomicWrite(dir+"/state/settings.json", encoded(doc))
					atomicWrite(dir+"/loaded.json", encoded(doc))
					atomicWrite(dir+"/phase", []byte("active"))
					json.NewEncoder(w).Encode(map[string]bool{"ok": true})
					return
				}
				var s Status
				s.Connected = true
				readJSON(dir+"/loaded.json", &s.Settings)
				phase, _ := os.ReadFile(dir + "/phase")
				s.Phase = strings.TrimSpace(string(phase))
				s.Native.Mode = "idle"
				if s.Phase == "active" {
					s.Native.Mode = "chat"
				}
				json.NewEncoder(w).Encode(s)
			}))
			defer server.Close()
			r := Router{dir: dir, voiceDir: dir, api: server.URL, client: server.Client(), lockPath: dir + "/lock", backupRoot: dir + "/backups", original: []byte("3\tзакрой чат\n")}
			if e := r.syncCommands(); e != nil {
				t.Fatal(e)
			}
			unchanged, _ := os.ReadFile(dir + "/state/settings.json")
			if string(unchanged) != string(original) || posts != 0 {
				t.Fatal("configuration activated voice")
			}
			if _, e := os.Stat(dir + "/actions"); !os.IsNotExist(e) {
				t.Fatal("management invoked runner")
			}
			result, e := r.open(newID)
			if fail {
				if e == nil {
					t.Fatal("failed selection accepted")
				}
				if !strings.Contains(e.Error(), "failed at select (control rejected request)") || !strings.Contains(e.Error(), "previous settings restored") {
					t.Fatal("lost original failure", e)
				}
				var restored Settings
				readJSON(dir+"/state/settings.json", &restored)
				if restored.Selected != "old" || len(restored.Chats) != 1 {
					t.Fatal("rollback failed", restored)
				}
				if posts != 2 {
					t.Fatal("unexpected retries", posts)
				}
				return
			}
			if e != nil || result.ConversationID != "server-created" {
				t.Fatal(result, e)
			}
			initialCatalog, e := loadConfig(dir + "/routes.json")
			if e != nil || initialCatalog.Catalog[0].ConversationID != "server-created" || initialCatalog.Catalog[0].ParentID != "" {
				t.Fatal("first active CID without parent not saved", initialCatalog, e)
			}
			doc, chats, e := rawSettings(dir + "/state/settings.json")
			if e != nil || rawString(doc["voice"]) != "orbit" || string(doc["future"]) != "{\"retained\":true}" || rawString(chats[0]["parent_message_id"]) != "after-stop" {
				t.Fatal("settings lost", e, string(encoded(doc)))
			}
			actions, _ := os.ReadFile(dir + "/actions")
			if string(actions) != "stop\nstart\n" {
				t.Fatal("unexpected restart", string(actions))
			}
			result, e = r.open(newID)
			if e != nil || posts != 1 || result.ConversationID != "server-created" {
				t.Fatal("draft recreated", e, posts, result)
			}
			actions2, _ := os.ReadFile(dir + "/actions")
			if string(actions2) != string(actions) {
				t.Fatal("draft reopen restarted")
			}
			latest, _ := os.ReadFile(dir + "/backups/LATEST")
			if e = r.restoreBackup(strings.TrimSpace(string(latest)), false); e != nil {
				t.Fatal("manual rollback", e)
			}
			var restored Settings
			readJSON(dir+"/state/settings.json", &restored)
			if restored.Selected != "old" || len(restored.Chats) != 1 {
				t.Fatal(restored)
			}
			saved, e := loadConfig(dir + "/routes.json")
			if e != nil || saved.Catalog[0].ConversationID != "server-created" || saved.Catalog[0].ParentID != "server-parent" || saved.Routes[0].ConversationID != "server-created" {
				t.Fatal("realized identity not persisted", saved, e)
			}
			result, e = r.open(newID)
			if e != nil || result.ConversationID != "server-created" || created != 1 {
				t.Fatal("restore then reopen created duplicate", result, e, created)
			}
			latest, _ = os.ReadFile(dir + "/backups/LATEST")
			atomicWrite(dir+"/routes.json", []byte("corrupt catalog"))
			if e = r.restoreBackup(strings.TrimSpace(string(latest)), false); e != nil {
				t.Fatal("optional catalog failure blocked rollback", e)
			}
			readJSON(dir+"/state/settings.json", &restored)
			phase, _ := os.ReadFile(dir + "/phase")
			if restored.Selected != "old" || strings.TrimSpace(string(phase)) != "active" || r.counters["parent_capture_failures"] != 1 {
				t.Fatal("recovery did not restart voice after catalog failure", restored, string(phase), r.counters)
			}
		})
	}
}
