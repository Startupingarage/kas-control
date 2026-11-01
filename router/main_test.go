package main

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestNormalize(t *testing.T) {
	for a, b := range map[string]string{"Алиса, открой ЁЖ! пожалуйста": "открой еж", "Яндекс, КАС-2": "кас 2", "капля🙂": "капля", "   ": ""} {
		if got := normalize(a); got != b {
			t.Fatalf("%q -> %q want %q", a, got, b)
		}
	}
}
func TestReconnectAndNewRecognitionInvalidateQueuedFinal(t *testing.T) {
	r := Router{ready: true, latest: "old"}
	ev := Event{ID: "old", At: time.Now()}
	if !r.current(ev) {
		t.Fatal("fresh final rejected")
	}
	r.connection(false)
	r.connection(true)
	if r.current(ev) {
		t.Fatal("old connection final survived reconnect")
	}
	r.recognizing("old")
	if !r.current(ev) {
		t.Fatal("fixture not current")
	}
	r.recognizing("new")
	if r.current(ev) {
		t.Fatal("new recognition failed to supersede prior final")
	}
}
func TestConfigAndAliases(t *testing.T) {
	c := Config{Version: 1, Routes: []Route{{Phrase: "открой кас", ChatID: "local", ProjectID: "g-p-test", ConversationID: "cloud"}}}
	orig := []byte("1\tоткрой кас\n3\tзакрой чат\n1\tоткрой чат\n")
	got, e := filteredCommands(orig, c)
	if e != nil || string(got) != "3\tзакрой чат\n1\tоткрой чат\n" {
		t.Fatal(string(got), e)
	}
	c.Routes[0].Phrase = "закрой чат"
	if _, e = filteredCommands(orig, c); e == nil {
		t.Fatal("reserved conflict accepted")
	}
	p := filepath.Join(t.TempDir(), "routes.json")
	c.Routes = append(c.Routes, c.Routes[0])
	b, _ := json.Marshal(c)
	os.WriteFile(p, b, 0600)
	if _, e = loadConfig(p); e == nil {
		t.Fatal("duplicate accepted")
	}
}
func finalFrame(state uint64) []byte {
	req := number(number(blob(nil, 1, []byte("открой кас")), 2, 1), 4, 1)
	s := blob(blob(number(nil, 1, state), 7, []byte("00000000-0000-4000-8000-000000000001")), 15, req)
	return number(blob(nil, 252, s), 253, 3)
}
func TestWire(t *testing.T) {
	ev, ok := event(finalFrame(3))
	if !ok || ev.Text != "открой кас" {
		t.Fatal(ev, ok)
	}
	if _, ok = event(finalFrame(1)); ok {
		t.Fatal("early final accepted")
	}
	for _, b := range [][]byte{{0}, {0x80}, {10, 255, 255}, append(finalFrame(3), 0)} {
		if _, ok = event(b); ok {
			t.Fatal("malformed accepted")
		}
	}
	hdr := binary.LittleEndian.AppendUint32(nil, maxFrame+1)
	if _, e := readFrame(bytes.NewReader(hdr)); e == nil {
		t.Fatal("oversize accepted")
	}
	p := cancelPacket(ev.ID)
	o, e := fields(p[4:])
	if e != nil || o[253].n != 210 {
		t.Fatal(e)
	}
	remote, _ := fields(o[252].b)
	m, _ := fields(remote[8].b)
	if string(remote[7].b) != "AliceCapability" || m[1].n != 6 || string(m[10].b) != ev.ID {
		t.Fatal("wrong cancellation")
	}
}
func TestDispatchTrustBoundaries(t *testing.T) {
	dir := t.TempDir()
	os.Mkdir(filepath.Join(dir, "state"), 0700)
	orig := []byte("1\tоткрой кас\n3\tзакрой чат\n")
	route := Route{Phrase: "открой кас", ChatID: "local", ProjectID: "g-p-test", ConversationID: "cloud"}
	c := Config{Version: 1, Routes: []Route{route}}
	b, _ := json.Marshal(c)
	os.WriteFile(dir+"/routes.json", b, 0600)
	filtered, _ := filteredCommands(orig, c)
	os.WriteFile(dir+"/commands.txt", filtered, 0600)
	os.WriteFile(dir+"/state/ui-auth.json", []byte(`{"key":"test"}`), 0600)
	settings := Settings{Version: 1, Chats: []Chat{{ID: "local", Project: "g-p-test", Conversation: "cloud", Parent: "parent"}}}
	b, _ = json.Marshal(settings)
	os.WriteFile(dir+"/state/settings.json", b, 0600)
	var posted int
	mode := "chat"
	project := "g-p-test"
	selected := ""
	phase := "active"
	failPost := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.Header.Get("X-Standalone-Key") != "test" {
			t.Error("missing auth")
		}
		if req.Method == "POST" {
			posted++
			var v map[string]string
			json.NewDecoder(req.Body).Decode(&v)
			if v["action"] != "select" || v["chat_id"] != "local" {
				t.Error(v)
			}
			if failPost {
				w.WriteHeader(500)
				return
			}
			selected = "local"
			settings.Selected = "local"
			b, _ := json.Marshal(settings)
			os.WriteFile(dir+"/state/settings.json", b, 0600)
			phase = "active"
			mode = "chat"
			json.NewEncoder(w).Encode(map[string]bool{"ok": true})
			return
		}
		var s Status
		s.Connected = true
		s.Phase = phase
		s.Native.Mode = mode
		s.Settings = Settings{Selected: selected, Chats: []Chat{{ID: "local", Project: project}}}
		json.NewEncoder(w).Encode(s)
	}))
	defer server.Close()
	r := Router{dir: dir, voiceDir: dir, api: server.URL, original: orig, client: server.Client(), ready: true, latest: "id", lockPath: dir + "/switch.lock"}
	ev := Event{ID: "id", Text: "открой кас", At: time.Now()}
	if e := r.dispatch(ev); e != nil || posted != 1 {
		t.Fatal(e, posted)
	}
	if e := r.dispatch(ev); e != nil || posted != 1 {
		t.Fatal("already active interrupted", e, posted)
	}
	project = "g-p-wrong"
	ev.At = time.Now()
	if e := r.dispatch(ev); e == nil || posted != 1 {
		t.Fatal("cross-project selection", e, posted)
	}
	project = "g-p-test"
	phase = "connecting"
	if e := r.dispatch(ev); e == nil {
		t.Fatal("transition accepted")
	}
	phase = "active"
	ev.At = time.Now().Add(-4 * time.Second)
	if e := r.dispatch(ev); e == nil {
		t.Fatal("stale accepted")
	}
	ev.At = time.Now()
	r.latest = "other"
	if e := r.dispatch(ev); e == nil {
		t.Fatal("superseded accepted")
	}
	r.latest = "id"
	selected = "other"
	failPost = true
	if e := r.dispatch(ev); e == nil || posted != 2 {
		t.Fatal("failed POST retry", e, posted)
	}
	failPost = false
	mode = "idle"
	if e := r.dispatch(ev); e == nil {
		t.Fatal("inconsistent native mode accepted")
	}
	phase = "idle"
	a, z := net.Pipe()
	defer a.Close()
	defer z.Close()
	ev.Conn = a
	ev.At = time.Now()
	cleared := make(chan bool, 1)
	go func() { p, e := readFrame(z); cleared <- e == nil && strings.Contains(string(p), "AliceCapability") }()
	if e := r.dispatch(ev); e != nil {
		t.Fatal(e)
	}
	if !<-cleared {
		t.Fatal("idle queue not cleared")
	}
}
