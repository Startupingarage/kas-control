package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
	"unicode/utf8"
)

type CatalogEntry struct {
	ChatID         string `json:"chat_id"`
	ProjectID      string `json:"project_id"`
	ConversationID string `json:"conversation_id"`
	ParentID       string `json:"parent_message_id"`
	Title          string `json:"title"`
	ProjectName    string `json:"project_name"`
	ProjectSession bool   `json:"project_session,omitempty"`
}
type OpenResult struct {
	OK             bool   `json:"ok"`
	ChatID         string `json:"chat_id"`
	ConversationID string `json:"conversation_id"`
	Phase          string `json:"phase"`
}
type journal struct {
	Backup       string            `json:"backup"`
	Active       bool              `json:"active"`
	Fingerprints map[string]string `json:"fingerprints"`
	Target       string            `json:"target"`
}

func (r *Router) backups() string {
	if r.backupRoot != "" {
		return r.backupRoot
	}
	return "/data/opt/kas-control-backups"
}

func validateCatalog(c Config) error {
	if len(c.Catalog) > 64 {
		return errors.New("catalog limit")
	}
	seen := map[string]CatalogEntry{}
	for _, v := range c.Catalog {
		if !uuid.MatchString(v.ChatID) || !validID.MatchString(v.ProjectID) || !strings.HasPrefix(v.ProjectID, "g-p-") || v.Title == "" || len(v.Title) > 120 || !utf8.ValidString(v.Title) || strings.ContainsAny(v.Title, "\r\n\t") {
			return errors.New("invalid catalog entry")
		}
		if (v.ConversationID == "" && v.ParentID != "") || (v.ConversationID != "" && !validID.MatchString(v.ConversationID)) || (v.ParentID != "" && !validID.MatchString(v.ParentID)) {
			return errors.New("invalid catalog conversation")
		}
		if _, ok := seen[v.ChatID]; ok {
			return errors.New("duplicate catalog id")
		}
		seen[v.ChatID] = v
	}
	for _, v := range c.Routes {
		entry, ok := seen[v.ChatID]
		if ok && (entry.ProjectID != v.ProjectID || (v.ConversationID != "" && entry.ConversationID != v.ConversationID)) {
			return errors.New("route/catalog mismatch")
		}
		if !ok && v.ConversationID == "" {
			return errors.New("draft requires catalog entry")
		}
	}
	return nil
}
func catalogTarget(c Config, route Route) (CatalogEntry, bool) {
	for _, v := range c.Catalog {
		if v.ChatID == route.ChatID {
			return v, true
		}
	}
	return CatalogEntry{}, false
}
func findChat(s Settings, id string) (Chat, bool) {
	for _, v := range s.Chats {
		if v.ID == id {
			return v, true
		}
	}
	return Chat{}, false
}
func (r *Router) fingerprints() (map[string]string, error) {
	result := map[string]string{}
	for _, name := range []string{"bin/alisa-standalone", "runtime.env"} {
		f, e := os.Open(r.voiceDir + "/" + name)
		if e != nil {
			return nil, e
		}
		h := sha256.New()
		_, e = io.Copy(h, f)
		f.Close()
		if e != nil {
			return nil, e
		}
		result[name] = fmt.Sprintf("%x", h.Sum(nil))
	}
	return result, nil
}
func (r *Router) checkFingerprints(want map[string]string) error {
	got, e := r.fingerprints()
	if e != nil {
		return e
	}
	for name, hash := range want {
		if got[name] != hash {
			return errors.New("voice binary/runtime changed; manual recovery required")
		}
	}
	return nil
}
func (r *Router) run(action string) error {
	// run.sh itself allows twenty seconds for stop; leave time for its cleanup and exit.
	timeout := 20 * time.Second
	if action == "stop" {
		timeout = 35 * time.Second
	}
	return r.runTimed(action, timeout)
}
func (r *Router) runTimed(action string, timeout time.Duration) error {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, r.voiceDir+"/run.sh", action)
	cmd.Stdout = io.Discard
	cmd.Stderr = io.Discard
	if e := cmd.Run(); e != nil {
		if ctx.Err() == context.DeadlineExceeded {
			return errors.New("voice lifecycle " + action + " timed out")
		}
		var exit *exec.ExitError
		if errors.As(e, &exit) {
			return fmt.Errorf("voice lifecycle %s exited %d", action, exit.ExitCode())
		}
		return errors.New("voice lifecycle " + action + " could not start")
	}
	return nil
}
func (r *Router) awaitReady() error {
	deadline := time.Now().Add(25 * time.Second)
	for time.Now().Before(deadline) {
		var s Status
		if r.request("GET", "/api/status", nil, &s) == nil && s.Connected && s.Phase == "idle" && s.Error == "" {
			return nil
		}
		time.Sleep(250 * time.Millisecond)
	}
	return errors.New("voice restart not ready")
}
func (r *Router) selectTarget(id string) error {
	var reply struct {
		OK bool `json:"ok"`
	}
	if e := r.request("POST", "/api/command", map[string]string{"action": "select", "chat_id": id}, &reply); e != nil {
		return e
	}
	if !reply.OK {
		return errors.New("select rejected")
	}
	deadline := time.Now().Add(35 * time.Second)
	for time.Now().Before(deadline) {
		time.Sleep(250 * time.Millisecond)
		var s Status
		if e := r.request("GET", "/api/status", nil, &s); e != nil {
			return e
		}
		if s.Error != "" {
			return errors.New("voice connection failed")
		}
		if s.Settings.Selected == id && s.Phase == "active" && s.Connected {
			return nil
		}
	}
	return errors.New("selection outcome not confirmed")
}
func rawSettings(path string) (map[string]json.RawMessage, []map[string]json.RawMessage, error) {
	var doc map[string]json.RawMessage
	if e := readJSON(path, &doc); e != nil {
		return nil, nil, e
	}
	var version int
	var chats []map[string]json.RawMessage
	if json.Unmarshal(doc["version"], &version) != nil || version != 1 || json.Unmarshal(doc["chats"], &chats) != nil || len(chats) > 64 {
		return nil, nil, errors.New("unsupported settings")
	}
	return doc, chats, nil
}
func rawString(v json.RawMessage) string { var s string; _ = json.Unmarshal(v, &s); return s }
func encoded(v any) json.RawMessage      { b, _ := json.Marshal(v); return b }

func (r *Router) preparedChat(v CatalogEntry) (map[string]json.RawMessage, error) {
	entry := map[string]json.RawMessage{"id": encoded(v.ChatID), "gizmo_id": encoded(v.ProjectID), "conversation_id": encoded(v.ConversationID), "parent_message_id": encoded(v.ParentID), "title": encoded(v.Title), "account_id": encoded(""), "confirmed": encoded(false)}
	if v.ProjectSession {
		entry["project_session"] = encoded(true)
	}
	return entry, nil
}
func (r *Router) rollbackPending(j journal) error {
	if e := r.checkFingerprints(j.Fingerprints); e != nil {
		return e
	}
	if e := r.run("stop"); e != nil {
		return e
	}
	if e := r.captureKnownParent(j.Target); e != nil {
		r.count("parent_capture_failures")
		log.Print("catalog parent refresh failed during recovery; continuing settings restore")
	}
	b, e := os.ReadFile(j.Backup + "/settings.json")
	if e != nil {
		return e
	}
	if e = atomicWrite(r.voiceDir+"/state/settings.json", b); e != nil {
		return e
	}
	if e = r.run("start"); e != nil {
		return e
	}
	if e = r.awaitReady(); e != nil {
		return e
	}
	if j.Active {
		var old Settings
		if e = readJSON(j.Backup+"/settings.json", &old); e != nil {
			return e
		}
		if old.Selected == "" {
			return errors.New("previous active chat missing")
		}
		if e = r.selectTarget(old.Selected); e != nil {
			return e
		}
	}
	if e = r.checkFingerprints(j.Fingerprints); e != nil {
		return e
	}
	return os.Remove(r.dir + "/pending.json")
}

// Only an identity already saved after successful activation may be refreshed during rollback.
func (r *Router) captureKnownParent(id string) error {
	c, e := loadConfig(r.dir + "/routes.json")
	if e != nil {
		return e
	}
	var disk Settings
	if e = readJSON(r.voiceDir+"/state/settings.json", &disk); e != nil {
		return e
	}
	chat, ok := findChat(disk, id)
	if !ok || chat.Conversation == "" {
		return nil
	}
	for _, entry := range c.Catalog {
		if entry.ChatID == id && entry.ProjectID == chat.Project && entry.ConversationID == chat.Conversation {
			return r.persistIdentity(Route{ChatID: id, ProjectID: chat.Project, ConversationID: chat.Conversation})
		}
	}
	return nil
}
func (r *Router) recoverLocked(manual bool) error {
	var j journal
	e := readJSON(r.dir+"/pending.json", &j)
	if os.IsNotExist(e) {
		return nil
	}
	if e != nil {
		return e
	}
	if filepath.Dir(j.Backup) != filepath.Clean(r.backups()) || len(j.Fingerprints) != 2 {
		return errors.New("invalid activation recovery journal")
	}
	if !manual {
		c, err := loadConfig(r.dir + "/routes.json")
		if err != nil {
			return err
		}
		if c.ProjectSessions {
			return errors.New("project registration requires manual recovery; voice restart disabled")
		}
	}
	return r.rollbackPending(j)
}
func (r *Router) register(v CatalogEntry, s Status) (err error) {
	return r.registerEntries([]CatalogEntry{v}, s, false)
}

// Preparation loads local slots without opening a voice session or creating cloud dialogs.
func (r *Router) registerEntries(entries []CatalogEntry, s Status, prepareOnly bool) (err error) {
	if len(entries) == 0 {
		return errors.New("registration entries required")
	}
	if prepareOnly && (!s.Connected || s.Phase != "idle" || s.Native.Mode != "idle" || s.Error != "") {
		return errors.New("preparation requires idle voice")
	}
	v := entries[0]
	_, preflight, e := rawSettings(r.voiceDir + "/state/settings.json")
	if e != nil {
		return e
	}
	if v.ProjectSession {
		preflight, e = r.pruneProjectRegistrations(preflight)
		if e != nil {
			return e
		}
	}
	known := map[string]bool{}
	for _, chat := range preflight {
		known[rawString(chat["id"])] = true
	}
	for _, entry := range entries {
		known[entry.ChatID] = true
	}
	if len(known) > 64 {
		return errors.New("chat limit")
	}
	hashes, e := r.fingerprints()
	if e != nil {
		return e
	}
	if e = os.MkdirAll(r.backups(), 0700); e != nil {
		return e
	}
	var suffix [4]byte
	if _, e = rand.Read(suffix[:]); e != nil {
		return e
	}
	backup := filepath.Join(r.backups(), time.Now().Format("20060102-150405")+fmt.Sprintf("-%x", suffix))
	if e = os.Mkdir(backup, 0700); e != nil {
		return e
	}
	before, e := os.ReadFile(r.voiceDir + "/state/settings.json")
	if e != nil {
		return e
	}
	if e = atomicWrite(backup+"/settings.json", before); e != nil {
		return e
	}
	j := journal{Backup: backup, Active: s.Phase == "active", Fingerprints: hashes, Target: v.ChatID}
	if e = atomicWrite(backup+"/activation.json", encoded(j)); e != nil {
		return e
	}
	checks := ""
	for _, name := range []string{"bin/alisa-standalone", "runtime.env"} {
		checks += hashes[name] + "  " + r.voiceDir + "/" + name + "\n"
	}
	if e = atomicWrite(backup+"/voice.sha256", []byte(checks)); e != nil {
		return e
	}
	script := "#!/bin/sh\nset -eu\nexec /data/opt/kas-command-router/router restore '" + backup + "' \"$@\"\n"
	if e = atomicWrite(backup+"/restore.sh", []byte(script)); e != nil {
		return e
	}
	if e = atomicWrite(r.dir+"/pending.json", encoded(j)); e != nil {
		return e
	}
	if prepareOnly {
		var current Status
		e = r.request("GET", "/api/status", nil, &current)
		if e != nil || !current.Connected || current.Phase != "idle" || current.Native.Mode != "idle" || current.Error != "" {
			// No lifecycle action yet; leave a newly active conversation alone.
			if cleanup := os.Remove(r.dir + "/pending.json"); cleanup != nil {
				return cleanup
			}
			return errors.New("voice no longer idle for preparation")
		}
	}
	stage := "stop"
	defer func() {
		if err != nil {
			cause := err
			if recovery := r.rollbackPending(j); recovery != nil {
				err = fmt.Errorf("activation failed at %s (%v); recovery incomplete: %w", stage, cause, recovery)
			} else {
				err = fmt.Errorf("activation failed at %s (%v); previous settings restored", stage, cause)
			}
		}
	}()
	if err = r.run("stop"); err != nil {
		return err
	}
	// The stopped daemon has flushed the final parent; do not reuse the pre-stop snapshot.
	stage = "fresh settings snapshot"
	fresh, e := os.ReadFile(r.voiceDir + "/state/settings.json")
	if e != nil {
		return e
	}
	doc, chats, e := rawSettings(r.voiceDir + "/state/settings.json")
	if e != nil {
		return e
	}
	if err = atomicWrite(backup+"/settings.json", fresh); err != nil {
		return err
	}
	if v.ProjectSession {
		chats, err = r.pruneProjectRegistrations(chats)
		if err != nil {
			return err
		}
	}
	for _, entry := range entries {
		exists := false
		for _, chat := range chats {
			if rawString(chat["id"]) == entry.ChatID {
				if rawString(chat["gizmo_id"]) != entry.ProjectID || (entry.ConversationID != "" && rawString(chat["conversation_id"]) != entry.ConversationID) || (prepareOnly && !freshProjectChat(chat, entry.ProjectID)) {
					return errors.New("disk chat identity mismatch")
				}
				exists = true
			}
		}
		if !exists {
			if len(chats) >= 64 {
				return errors.New("chat limit")
			}
			raw, e := r.preparedChat(entry)
			if e != nil {
				return e
			}
			chats = append(chats, raw)
		}
	}
	doc["chats"] = encoded(chats)
	selectedExists := false
	for _, chat := range chats {
		if rawString(chat["id"]) == rawString(doc["selected"]) {
			selectedExists = true
		}
	}
	if (!prepareOnly || !selectedExists) && rawString(doc["selected"]) != v.ChatID {
		doc["previous"] = doc["selected"]
		doc["selected"] = encoded(v.ChatID)
	}
	stage = "settings write"
	if err = atomicWrite(r.voiceDir+"/state/settings.json", encoded(doc)); err != nil {
		return err
	}
	stage = "voice fingerprint verification"
	if err = r.checkFingerprints(hashes); err != nil {
		return err
	}
	stage = "start"
	if err = r.run("start"); err != nil {
		return err
	}
	stage = "await ready"
	if err = r.awaitReady(); err != nil {
		return err
	}
	var loaded Status
	stage = "loaded target verification"
	if err = r.request("GET", "/api/status", nil, &loaded); err != nil {
		return err
	}
	for _, entry := range entries {
		chat, ok := findChat(loaded.Settings, entry.ChatID)
		if !ok || chat.Project != entry.ProjectID || (prepareOnly && chat.Conversation != "") {
			return errors.New("registered target not loaded")
		}
	}
	stage = "select"
	if !prepareOnly {
		if err = r.selectTarget(v.ChatID); err != nil {
			return err
		}
	}
	stage = "final fingerprint verification"
	if err = r.checkFingerprints(hashes); err != nil {
		return err
	}
	stage = "backup publication"
	if err = atomicWrite(r.backups()+"/LATEST", []byte(backup+"\n")); err != nil {
		return err
	}
	return os.Remove(r.dir + "/pending.json")
}
func (r *Router) restoreBackup(path string, held bool) error {
	if filepath.Dir(path) != filepath.Clean(r.backups()) {
		return errors.New("invalid backup path")
	}
	if !held {
		f, e := r.switchMutex()
		if e != nil {
			return e
		}
		defer unlock(f)
	}
	if e := r.recoverLocked(true); e != nil {
		return e
	}
	var j journal
	if e := readJSON(path+"/activation.json", &j); e != nil {
		return e
	}
	if j.Backup != path {
		return errors.New("backup journal mismatch")
	}
	if e := atomicWrite(r.dir+"/pending.json", encoded(j)); e != nil {
		return e
	}
	return r.rollbackPending(j)
}
func (r *Router) result(id string) (OpenResult, error) {
	var disk Settings
	if e := readJSON(r.voiceDir+"/state/settings.json", &disk); e != nil {
		return OpenResult{}, e
	}
	v, ok := findChat(disk, id)
	if !ok || disk.Selected != id {
		return OpenResult{}, errors.New("selected settings not confirmed")
	}
	return OpenResult{OK: true, ChatID: id, ConversationID: v.Conversation, Phase: "active"}, nil
}

// Keep successful draft identity outside settings so restoring a binding cannot create a second chat.
// The caller holds the shared switch lock; raw JSON preserves unrelated UI/configuration fields.
func (r *Router) persistIdentity(target Route) error {
	_, chats, e := rawSettings(r.voiceDir + "/state/settings.json")
	if e != nil {
		return e
	}
	var cid, parent string
	for _, chat := range chats {
		if rawString(chat["id"]) == target.ChatID {
			if rawString(chat["gizmo_id"]) != target.ProjectID {
				return errors.New("realized project mismatch")
			}
			cid = rawString(chat["conversation_id"])
			parent = rawString(chat["parent_message_id"])
			break
		}
	}
	if cid == "" {
		return nil
	}
	if !validID.MatchString(cid) || (parent != "" && !validID.MatchString(parent)) || (target.ConversationID != "" && target.ConversationID != cid) {
		return errors.New("active chat identity not ready; configuration unchanged")
	}
	path := r.dir + "/routes.json"
	before, e := os.ReadFile(path)
	if e != nil {
		return e
	}
	var doc map[string]json.RawMessage
	if e = json.Unmarshal(before, &doc); e != nil {
		return e
	}
	var catalog, routes []map[string]json.RawMessage
	if raw, ok := doc["catalog"]; ok {
		if e = json.Unmarshal(raw, &catalog); e != nil {
			return e
		}
	}
	if e = json.Unmarshal(doc["routes"], &routes); e != nil {
		return e
	}
	changed := false
	for _, list := range [][]map[string]json.RawMessage{catalog, routes} {
		for _, row := range list {
			if rawString(row["chat_id"]) != target.ChatID {
				continue
			}
			if rawString(row["project_id"]) != target.ProjectID || (rawString(row["conversation_id"]) != "" && rawString(row["conversation_id"]) != cid) {
				return errors.New("configuration identity changed during activation")
			}
			rowParent := parent
			if rowParent == "" {
				rowParent = rawString(row["parent_message_id"])
			}
			if rawString(row["conversation_id"]) != cid || rawString(row["parent_message_id"]) != rowParent {
				row["conversation_id"] = encoded(cid)
				row["parent_message_id"] = encoded(rowParent)
				changed = true
			}
		}
	}
	if !changed {
		return nil
	}
	if _, ok := doc["catalog"]; ok {
		doc["catalog"] = encoded(catalog)
	}
	doc["routes"] = encoded(routes)
	after := encoded(doc)
	var valid Config
	if e = json.Unmarshal(after, &valid); e != nil {
		return e
	}
	if e = validateCatalog(valid); e != nil {
		return e
	}
	current, e := os.ReadFile(path)
	if e != nil {
		return e
	}
	if !bytes.Equal(before, current) {
		return errors.New("configuration changed during identity save")
	}
	return atomicWrite(path, after)
}
func (r *Router) activateLocked(c Config, target Route, ev *Event) (OpenResult, error) {
	var s Status
	if e := r.request("GET", "/api/status", nil, &s); e != nil {
		return OpenResult{}, e
	}
	var disk Settings
	if e := readJSON(r.voiceDir+"/state/settings.json", &disk); e != nil {
		return OpenResult{}, e
	}
	// Native retains a failed connection error until the next explicit select clears it.
	if !s.Connected || (s.Error != "" && !(c.ProjectSessions && s.Phase == "idle")) || (s.Phase != "idle" && s.Phase != "active") || (s.Native.Mode != "idle" && s.Native.Mode != "chat") || (s.Phase == "idle") != (s.Native.Mode == "idle") {
		return OpenResult{}, errors.New("voice unavailable or in transition")
	}
	entry, hasCatalog := catalogTarget(c, target)
	diskChat, onDisk := findChat(disk, target.ChatID)
	loadedChat, loaded := findChat(s.Settings, target.ChatID)
	if (onDisk && (diskChat.Project != target.ProjectID || (target.ConversationID != "" && diskChat.Conversation != target.ConversationID))) || (loaded && loadedChat.Project != target.ProjectID) {
		return OpenResult{}, errors.New("target identity mismatch")
	}
	if (!onDisk || !loaded) && !hasCatalog {
		return OpenResult{}, errors.New("target not loaded; catalog registration required")
	}
	fresh := r.current
	if c.ProjectSessions {
		fresh = r.currentProject
	}
	if ev != nil && !fresh(*ev) {
		return OpenResult{}, errors.New("stale event")
	}
	if loaded && onDisk && s.Phase == "active" && s.Settings.Selected == target.ChatID {
		r.count("already_active")
		if e := r.persistIdentity(target); e != nil {
			return OpenResult{}, e
		}
		return r.result(target.ChatID)
	}
	if ev != nil && s.Native.Mode == "idle" && !c.ProjectSessions {
		ev.Conn.SetWriteDeadline(time.Now().Add(time.Second))
		packet := cancelPacket(ev.ID)
		for len(packet) > 0 {
			n, e := ev.Conn.Write(packet)
			if e != nil {
				return OpenResult{}, errors.New("queue clear failed")
			}
			if n == 0 {
				return OpenResult{}, io.ErrShortWrite
			}
			packet = packet[n:]
		}
	}
	if ev != nil && !fresh(*ev) {
		return OpenResult{}, errors.New("superseded event")
	}
	// The fresh command authorizes this bounded operation; reconnects caused by registration are expected.
	var e error
	if !loaded || !onDisk {
		if c.ProjectSessions {
			if !hotPreparation(s) {
				return OpenResult{}, errors.New("native hot preparation unavailable; voice restart disabled")
			}
			if s.Phase == "active" {
				return OpenResult{}, errors.New("prepared project pool exhausted; current conversation preserved until close")
			}
			if e = r.replenishProjectPoolLocked(true); e == nil {
				if ev != nil && !fresh(*ev) {
					return OpenResult{}, errors.New("superseded event")
				}
				e = r.selectTarget(target.ChatID)
			}
		} else {
			e = r.register(entry, s)
		}
	} else {
		r.mu.Lock()
		recognized := r.latest
		r.mu.Unlock()
		e = r.selectTarget(target.ChatID)
		// CLI has no live recognition epoch and cannot safely undo a user's close.
		if e != nil && c.ProjectSessions && ev != nil && s.Phase == "active" {
			r.mu.Lock()
			sameRequest := r.latest == recognized
			r.mu.Unlock()
			var now Status
			if sameRequest && (ev == nil || fresh(*ev)) && r.request("GET", "/api/status", nil, &now) == nil && now.Connected {
				if now.Phase == "active" && now.Settings.Selected == target.ChatID && now.Error == "" {
					e = nil
				} else if now.Phase == "idle" && now.Native.Mode == "idle" && now.Error != "" {
					previous, oldOK := findChat(disk, s.Settings.Selected)
					loadedPrevious, loadedOK := findChat(now.Settings, s.Settings.Selected)
					// Recover only a confirmed failure, never an uncertain connection or a user close.
					r.mu.Lock()
					sameRequest = r.latest == recognized
					r.mu.Unlock()
					if sameRequest && (ev == nil || fresh(*ev)) && oldOK && loadedOK && loadedPrevious.Project == previous.Project {
						if recovery := r.selectTarget(previous.ID); recovery != nil {
							e = fmt.Errorf("project selection failed; previous session recovery failed: %w", recovery)
						} else {
							e = fmt.Errorf("project selection failed (%v); previous session restored", e)
						}
					}
				}
			}
		}
	}
	if e != nil {
		return OpenResult{}, e
	}
	if e = r.persistIdentity(target); e != nil {
		return OpenResult{}, e
	}
	r.count("selected_confirmed")
	return r.result(target.ChatID)
}
func (r *Router) open(id string) (OpenResult, error) {
	if !validID.MatchString(id) {
		return OpenResult{}, errors.New("invalid chat id")
	}
	f, e := r.switchMutex()
	if e != nil {
		return OpenResult{}, errors.New("switch busy")
	}
	defer unlock(f)
	if e = r.recoverLocked(false); e != nil {
		return OpenResult{}, e
	}
	c, e := loadConfig(r.dir + "/routes.json")
	if e != nil {
		return OpenResult{}, e
	}
	for _, v := range c.Catalog {
		if v.ChatID == id {
			if c.ProjectSessions {
				return r.openProjectLocked(c, v.ProjectID, nil)
			}
			return r.activateLocked(c, Route{ChatID: id, ProjectID: v.ProjectID, ConversationID: v.ConversationID}, nil)
		}
	}
	for _, v := range c.Routes {
		if v.ChatID == id {
			return r.activateLocked(c, v, nil)
		}
	}
	return OpenResult{}, errors.New("chat not configured")
}
