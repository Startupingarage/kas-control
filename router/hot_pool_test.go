package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"
)

func hotPoolFixture(t *testing.T) *Router {
	t.Helper()
	r := loadedPoolFixture(t)
	original := r.client.Transport
	r.client.Transport = roundTripFunc(func(q *http.Request) (*http.Response, error) {
		respond := func(value any, code int) (*http.Response, error) {
			return &http.Response{StatusCode: code, Header: http.Header{}, Body: io.NopCloser(bytes.NewReader(encoded(value)))}, nil
		}
		if q.Method == "GET" {
			response, err := original.RoundTrip(q)
			if err != nil {
				return response, err
			}
			var value map[string]any
			err = json.NewDecoder(response.Body).Decode(&value)
			response.Body.Close()
			if err != nil {
				return nil, err
			}
			value["pid"] = 123
			value["features"] = []string{"prepare_chats_v1"}
			b, _ := os.ReadFile(r.dir + "/loaded.json")
			value["settings_revision"] = string(b)
			return respond(value, 200)
		}
		body, _ := io.ReadAll(q.Body)
		q.Body = io.NopCloser(bytes.NewReader(body))
		var cmd map[string]json.RawMessage
		json.Unmarshal(body, &cmd)
		if rawString(cmd["action"]) == "close" {
			atomicWrite(r.dir+"/phase", []byte("idle"))
			return respond(map[string]bool{"ok": true}, 200)
		}
		if rawString(cmd["action"]) != "prepare_chats" {
			return original.RoundTrip(q)
		}
		before, _ := os.ReadFile(r.dir + "/loaded.json")
		if rawString(cmd["expected_revision"]) != string(before) {
			t.Error("stale preparation revision")
			return respond(nil, 400)
		}
		if _, err := os.Stat(r.dir + "/reject-prepare"); err == nil {
			return respond(nil, 400)
		}
		doc, chats, _ := rawSettings(r.dir + "/loaded.json")
		var add []map[string]json.RawMessage
		var remove []string
		json.Unmarshal(cmd["add"], &add)
		json.Unmarshal(cmd["remove"], &remove)
		kept := []map[string]json.RawMessage{}
		for _, chat := range chats {
			retired := false
			for _, id := range remove {
				if rawString(chat["id"]) == id {
					retired = true
					if _, err := os.Stat(r.dir + "/session-identities/" + id + ".json"); err != nil {
						t.Error("retired identity not journaled")
					}
					if id == rawString(doc["selected"]) || id == rawString(doc["previous"]) {
						t.Error("retired selected/previous")
					}
				}
			}
			if !retired {
				kept = append(kept, chat)
			}
		}
		doc["chats"] = encoded(append(kept, add...))
		atomicWrite(r.dir+"/loaded.json", encoded(doc))
		atomicWrite(r.dir+"/state/settings.json", encoded(doc))
		f, _ := os.OpenFile(r.dir+"/preparations", os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
		f.WriteString("prepare\n")
		f.Close()
		if _, err := os.Stat(r.dir + "/lost-prepare-reply"); err == nil {
			return respond(nil, 500)
		}
		return respond(map[string]bool{"ok": true}, 200)
	})
	return r
}

func assertNoLifecycle(t *testing.T, r *Router) {
	t.Helper()
	b, _ := os.ReadFile(r.dir + "/actions")
	if len(b) != 0 {
		t.Fatalf("voice lifecycle invoked: %q", b)
	}
}

func TestHotPoolRefillsAfterEveryClose(t *testing.T) {
	r := hotPoolFixture(t)
	if err := r.prepareProjectDrafts(); err != nil {
		t.Fatal(err)
	}
	if err := r.replenishProjectPool(false); err != nil {
		t.Fatal(err)
	}
	for cycle := 0; cycle < 2; cycle++ {
		c, _ := loadConfig(r.dir + "/routes.json")
		project := c.Routes[0].ProjectID
		result, err := r.open(c.Routes[0].ChatID)
		if err != nil {
			t.Fatal(err)
		}
		before, _ := os.ReadFile(r.dir + "/preparations")
		if err = r.replenishProjectPool(false); err != nil {
			t.Fatal(err)
		}
		during, _ := os.ReadFile(r.dir + "/preparations")
		if !bytes.Equal(before, during) {
			t.Fatal("prepared slots during active voice")
		}
		atomicWrite(r.dir+"/phase", []byte("idle"))
		if err = r.prepareProjectDrafts(); err != nil {
			t.Fatal(err)
		}
		if err = r.replenishProjectPool(false); err != nil {
			t.Fatal(err)
		}
		_, chats, _ := rawSettings(r.dir + "/state/settings.json")
		count := 0
		for _, chat := range chats {
			if freshProjectChat(chat, project) {
				count++
			}
		}
		if count != projectPoolSize {
			t.Fatalf("after close %d: reserve %d, want %d", cycle+1, count, projectPoolSize)
		}
		if _, err = os.Stat(r.dir + "/session-identities/" + result.ChatID + ".json"); err != nil {
			t.Fatal("closed session identity lost", err)
		}
		full, _ := os.ReadFile(r.dir + "/preparations")
		if err = r.replenishProjectPool(false); err != nil {
			t.Fatal(err)
		}
		again, _ := os.ReadFile(r.dir + "/preparations")
		if !bytes.Equal(full, again) {
			t.Fatal("redundant preparation while pool is full")
		}
		assertNoLifecycle(t, r)
	}
}

func TestHotPoolFailureBackoffClearsOnlyAfterConfirmedSuccess(t *testing.T) {
	r := hotPoolFixture(t)
	r.prepareProjectDrafts()
	os.WriteFile(r.dir+"/reject-prepare", nil, 0600)
	if err := r.replenishProjectPool(false); err == nil {
		t.Fatal("rejection was accepted")
	}
	if !r.projectRefillAfter.After(time.Now()) {
		t.Fatal("failure backoff missing")
	}
	os.Remove(r.dir + "/reject-prepare")
	if err := r.replenishProjectPool(false); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(r.dir + "/preparations"); !os.IsNotExist(err) {
		t.Fatal("retry ignored failure backoff")
	}
	if err := r.replenishProjectPool(true); err != nil {
		t.Fatal(err)
	}
	if !r.projectRefillAfter.IsZero() {
		t.Fatal("confirmed success kept the retry delay")
	}
	assertNoLifecycle(t, r)
}

func TestHotPoolRefillNoRestart(t *testing.T) {
	r := hotPoolFixture(t)
	if err := r.prepareProjectDrafts(); err != nil {
		t.Fatal(err)
	}
	if err := r.replenishProjectPool(true); err != nil {
		t.Fatal(err)
	}
	assertNoLifecycle(t, r)
	doc, chats, _ := rawSettings(r.dir + "/state/settings.json")
	if rawString(doc["voice"]) != "orbit" || rawString(doc["previous"]) != "legacy" {
		t.Fatal("voice/binding changed")
	}
	for _, p := range []string{"g-p-cook", "g-p-core"} {
		n := 0
		for _, c := range chats {
			if freshProjectChat(c, p) {
				n++
			}
		}
		if n != projectPoolSize {
			t.Fatal("wrong pool size", p, n)
		}
	}
}

func TestHotPoolRejectAndLostReplyNeverRestart(t *testing.T) {
	for _, flag := range []string{"reject-prepare", "lost-prepare-reply"} {
		t.Run(flag, func(t *testing.T) {
			r := hotPoolFixture(t)
			r.prepareProjectDrafts()
			os.WriteFile(r.dir+"/"+flag, nil, 0600)
			err := r.replenishProjectPool(true)
			if flag == "reject-prepare" && err == nil {
				t.Fatal("rejected preparation accepted")
			}
			assertNoLifecycle(t, r)
			os.Remove(r.dir + "/" + flag)
			if err := r.replenishProjectPool(true); err != nil {
				t.Fatal(err)
			}
			b, _ := os.ReadFile(r.dir + "/preparations")
			if strings.Count(string(b), "prepare\n") != 1 {
				t.Fatal("duplicate application", string(b))
			}
			assertNoLifecycle(t, r)
		})
	}
}

func TestOldClientNeverFallsBackToProjectRestart(t *testing.T) {
	r := loadedPoolFixture(t)
	r.prepareProjectDrafts()
	if err := r.replenishProjectPool(true); err == nil {
		t.Fatal("old client accepted")
	}
	assertNoLifecycle(t, r)
	c, _ := loadConfig(r.dir + "/routes.json")
	if _, err := r.open(c.Routes[0].ChatID); err == nil {
		t.Fatal("cold project activation accepted")
	}
	assertNoLifecycle(t, r)
}

func TestHotPoolManyCyclesKeepArchiveIdentities(t *testing.T) {
	r := hotPoolFixture(t)
	seen := map[string]bool{}
	for i := 0; i < 24; i++ {
		if err := r.prepareProjectDrafts(); err != nil {
			t.Fatal(err)
		}
		if err := r.replenishProjectPool(true); err != nil {
			t.Fatal(err)
		}
		c, _ := loadConfig(r.dir + "/routes.json")
		result, err := r.open(c.Routes[i%2].ChatID)
		if err != nil {
			t.Fatal(err)
		}
		if seen[result.ChatID] {
			t.Fatal("reused slot", result.ChatID)
		}
		seen[result.ChatID] = true
		atomicWrite(r.dir+"/phase", []byte("idle"))
		if err = r.prepareProjectDrafts(); err != nil {
			t.Fatal(err)
		}
		var identity map[string]json.RawMessage
		if err = readJSON(r.dir+"/session-identities/"+result.ChatID+".json", &identity); err != nil {
			t.Fatal(err)
		}
		if rawString(identity["conversation_id"]) != result.ConversationID {
			t.Fatal("archive identity lost")
		}
		_, chats, _ := rawSettings(r.dir + "/state/settings.json")
		if len(chats) > 2*projectPoolSize+3 {
			t.Fatal("unbounded registrations", len(chats))
		}
		assertNoLifecycle(t, r)
	}
}

func TestProjectPendingRecoveryNeverRestartsImplicitly(t *testing.T) {
	r := hotPoolFixture(t)
	backup := r.backups() + "/previous"
	os.MkdirAll(backup, 0700)
	before, _ := os.ReadFile(r.dir + "/state/settings.json")
	os.WriteFile(backup+"/settings.json", before, 0600)
	hashes, err := r.fingerprints()
	if err != nil {
		t.Fatal(err)
	}
	atomicWrite(r.dir+"/pending.json", encoded(journal{Backup: backup, Fingerprints: hashes}))
	c, _ := loadConfig(r.dir + "/routes.json")
	if _, err = r.open(c.Routes[0].ChatID); err == nil || !strings.Contains(err.Error(), "manual recovery") {
		t.Fatal("pending project recovery was not blocked", err)
	}
	assertNoLifecycle(t, r)
	after, _ := os.ReadFile(r.dir + "/state/settings.json")
	if !bytes.Equal(before, after) {
		t.Fatal("pending recovery rewrote settings")
	}
	if _, err = os.Stat(r.dir + "/pending.json"); err != nil {
		t.Fatal("journal lost", err)
	}
}

func TestHotPoolExplicitOpenClearsIdleErrorAfterExhaustion(t *testing.T) {
	r := hotPoolFixture(t)
	if err := r.prepareProjectDrafts(); err != nil {
		t.Fatal(err)
	}
	os.WriteFile(r.dir+"/error", []byte("connection_failed"), 0600)
	if err := r.replenishProjectPool(false); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(r.dir + "/preparations"); !os.IsNotExist(err) {
		t.Fatal("background preparation ignored error")
	}
	c, _ := loadConfig(r.dir + "/routes.json")
	if _, err := r.open(c.Routes[0].ChatID); err != nil {
		t.Fatal("explicit open stayed blocked", err)
	}
	var s Status
	r.request("GET", "/api/status", nil, &s)
	if s.Phase != "active" || s.Error != "" {
		t.Fatal("new call not ready", s.Phase, s.Error)
	}
	assertNoLifecycle(t, r)
}

func TestHotPoolExhaustionKeepsActiveConversation(t *testing.T) {
	r := hotPoolFixture(t)
	r.prepareProjectDrafts()
	os.WriteFile(r.dir+"/phase", []byte("active"), 0600)
	before, _ := os.ReadFile(r.dir + "/loaded.json")
	c, _ := loadConfig(r.dir + "/routes.json")
	if _, err := r.open(c.Routes[1].ChatID); err == nil {
		t.Fatal("missing target interrupted active call")
	}
	after, _ := os.ReadFile(r.dir + "/loaded.json")
	phase, _ := os.ReadFile(r.dir + "/phase")
	if !bytes.Equal(before, after) || string(phase) != "active" {
		t.Fatal("active conversation changed")
	}
	if _, err := os.Stat(r.dir + "/preparations"); !os.IsNotExist(err) {
		t.Fatal("prepared during conversation")
	}
	assertNoLifecycle(t, r)
}
