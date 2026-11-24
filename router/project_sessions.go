package main

import (
	"bytes"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"time"
)

func validateProjectSessions(c Config) error {
	if !c.ProjectSessions {
		return nil
	}
	if len(c.Routes) == 0 {
		return errors.New("project routes required")
	}
	seen := map[string]bool{}
	for _, route := range c.Routes {
		entry, ok := catalogTarget(c, route)
		if !ok || !entry.ProjectSession || seen[route.ProjectID] || route.Phrase == "открой чат" || route.Phrase == "закрой чат" {
			return errors.New("invalid project session route")
		}
		seen[route.ProjectID] = true
	}
	return nil
}

func (r *Router) projectRoute(c Config, key string) (Route, error) {
	if key == "открой чат" {
		var last struct {
			Project string `json:"project_id"`
		}
		if e := readJSON(r.dir+"/project-last.json", &last); e != nil {
			return Route{}, errors.New("last project unavailable")
		}
		key = last.Project
	}
	for _, route := range c.Routes {
		if route.Phrase == key || route.ProjectID == key {
			return route, nil
		}
	}
	return Route{}, errors.New("project command not configured")
}

// This is an identity ledger, NOT a full transcript backup and never permits cloud deletion.
func (r *Router) saveSessionIdentity(chat map[string]json.RawMessage) error {
	id := rawString(chat["id"])
	if !uuid.MatchString(id) {
		return errors.New("invalid managed identity")
	}
	if e := os.MkdirAll(r.dir+"/session-identities", 0700); e != nil {
		return e
	}
	path := r.dir + "/session-identities/" + id + ".json"
	var previous map[string]json.RawMessage
	if e := readJSON(path, &previous); e == nil {
		oldCID, newCID := rawString(previous["conversation_id"]), rawString(chat["conversation_id"])
		if oldCID != "" && oldCID != newCID {
			return errors.New("session ledger identity conflict")
		}
		if rawString(chat["parent_message_id"]) == "" && rawString(previous["parent_message_id"]) != "" {
			chat["parent_message_id"] = previous["parent_message_id"]
		}
	} else if !os.IsNotExist(e) {
		return e
	}
	return atomicWrite(path, encoded(chat))
}

func (r *Router) prepareProjectDrafts() error {
	c, e := loadConfig(r.dir + "/routes.json")
	if e != nil || !c.ProjectSessions {
		return e
	}
	f, e := r.switchMutex()
	if e != nil {
		return nil
	}
	defer unlock(f)
	// Recovery of an interrupted registration owns settings before any draft rotation.
	if _, e := os.Stat(r.dir + "/pending.json"); e == nil {
		return nil
	} else if !os.IsNotExist(e) {
		return e
	}
	_, e = r.prepareProjectsLocked()
	return e
}

func (r *Router) prepareProjectsLocked() (Config, error) {
	return r.prepareProjectTargetsLocked(false)
}

func freshProjectChat(chat map[string]json.RawMessage, project string) bool {
	var managed bool
	json.Unmarshal(chat["project_session"], &managed)
	return managed && rawString(chat["gizmo_id"]) == project && rawString(chat["conversation_id"]) == "" && rawString(chat["voice_session_id"]) == "" && rawString(chat["parent_message_id"]) == ""
}

func newProjectID() (string, error) {
	var random [16]byte
	if _, e := rand.Read(random[:]); e != nil {
		return "", e
	}
	random[6] = (random[6] & 15) | 64
	random[8] = (random[8] & 63) | 128
	return fmt.Sprintf("%x-%x-%x-%x-%x", random[0:4], random[4:6], random[6:8], random[8:10], random[10:16]), nil
}

// Keep selection fast; native preparation only edits idle in-memory registrations.
const projectPoolSize = 8

func (r *Router) replenishProjectPool(force bool) error {
	f, e := r.switchMutex()
	if e != nil {
		if force {
			return e
		}
		return nil
	}
	defer unlock(f)
	return r.replenishProjectPoolLocked(force)
}

func (r *Router) replenishProjectPoolLocked(force bool) error {
	if _, e := os.Stat(r.dir + "/pending.json"); e == nil {
		return errors.New("registration recovery pending")
	} else if !os.IsNotExist(e) {
		return e
	}
	c, e := loadConfig(r.dir + "/routes.json")
	if e != nil || !c.ProjectSessions {
		return e
	}
	var s Status
	if e = r.request("GET", "/api/status", nil, &s); e != nil {
		r.projectIdleSince = time.Time{}
		return e
	}
	if !s.Connected || s.Phase != "idle" || s.Native.Mode != "idle" || (s.Error != "" && !force) {
		r.projectIdleSince = time.Time{}
		if force {
			return errors.New("pool preparation requires idle voice")
		}
		return nil
	}
	if r.projectIdleSince.IsZero() {
		r.projectIdleSince = time.Now()
	}
	doc, chats, e := rawSettings(r.voiceDir + "/state/settings.json")
	if e != nil {
		return e
	}
	counts := map[string]int{}
	for _, route := range c.Routes {
		for _, chat := range chats {
			loaded, ok := findChat(s.Settings, rawString(chat["id"]))
			if ok && loaded.Project == route.ProjectID && loaded.Conversation == "" && freshProjectChat(chat, route.ProjectID) {
				counts[route.ProjectID]++
			}
		}
	}
	if e = atomicWrite(r.runtimeFile("project-pool.json"), encoded(map[string]any{"updated_unix": time.Now().Unix(), "loaded_empty": counts, "target_per_project": projectPoolSize})); e != nil {
		return e
	}
	low := false
	for _, route := range c.Routes {
		if counts[route.ProjectID] < projectPoolSize {
			low = true
		}
	}
	if !force && (!low || time.Now().Before(r.projectRefillAfter)) {
		return nil
	}
	if !hotPreparation(s) {
		r.projectRefillAfter = time.Now().Add(time.Minute)
		return errors.New("native hot preparation unavailable; voice restart disabled")
	}
	kept, e := r.pruneProjectRegistrations(chats, rawString(doc["selected"]), rawString(doc["previous"]))
	if e != nil {
		return e
	}
	known := map[string]map[string]json.RawMessage{}
	for _, chat := range kept {
		known[rawString(chat["id"])] = chat
	}
	var entries []CatalogEntry
	for _, route := range c.Routes {
		entry, _ := catalogTarget(c, route)
		// The active route must be fresh; rotate consumed routes before replenishment.
		if entry.ConversationID != "" {
			return errors.New("project route requires rotation")
		}
		n := 0
		for _, chat := range kept {
			if freshProjectChat(chat, route.ProjectID) {
				n++
			}
		}
		if existing, ok := known[entry.ChatID]; !ok {
			entries = append(entries, entry)
			known[entry.ChatID] = nil
			n++
		} else if !freshProjectChat(existing, route.ProjectID) {
			return errors.New("project route already consumed")
		} else if _, loaded := findChat(s.Settings, entry.ChatID); !loaded {
			entries = append(entries, entry)
		}
		for n < projectPoolSize {
			entry.ChatID, e = newProjectID()
			if e != nil {
				return e
			}
			entries = append(entries, entry)
			known[entry.ChatID] = nil
			n++
		}
	}
	if len(entries) == 0 {
		return nil
	}
	if len(known) > 64 {
		return errors.New("insufficient native capacity for project pool")
	}
	r.projectRefillAfter = time.Now().Add(time.Minute)
	add := make([]map[string]json.RawMessage, 0, len(entries))
	for _, entry := range entries {
		raw, err := r.preparedChat(entry)
		if err != nil {
			return err
		}
		add = append(add, raw)
	}
	remove := []string{}
	for _, chat := range chats {
		if _, ok := known[rawString(chat["id"])]; !ok {
			remove = append(remove, rawString(chat["id"]))
		}
	}
	var reply struct {
		OK bool `json:"ok"`
	}
	postError := r.request("POST", "/api/command", map[string]any{"action": "prepare_chats", "expected_revision": s.Revision, "add": add, "remove": remove}, &reply)
	// An uncertain POST is reconciled by readback, never retried or recovered by restart.
	var after Status
	if e = r.request("GET", "/api/status", nil, &after); e != nil {
		return e
	}
	_, current, e := rawSettings(r.voiceDir + "/state/settings.json")
	if e != nil {
		return e
	}
	if after.PID != s.PID {
		return errors.New("native process changed during preparation")
	}
	byID := map[string]map[string]json.RawMessage{}
	for _, chat := range current {
		byID[rawString(chat["id"])] = chat
	}
	for _, entry := range entries {
		loaded, ok := findChat(after.Settings, entry.ChatID)
		if !ok || loaded.Project != entry.ProjectID || !freshProjectChat(byID[entry.ChatID], entry.ProjectID) {
			if postError != nil {
				return postError
			}
			return errors.New("hot preparation not confirmed")
		}
	}
	for _, id := range remove {
		_, loaded := findChat(after.Settings, id)
		if _, exists := byID[id]; exists || loaded {
			return errors.New("local retirement not confirmed")
		}
	}
	r.projectRefillAfter = time.Time{}
	r.count("project_pool_replenished")
	return nil
}

func hotPreparation(s Status) bool {
	for _, feature := range s.Features {
		if feature == "prepare_chats_v1" && s.Revision != "" && s.PID > 0 {
			return true
		}
	}
	return false
}

func (r *Router) prepareProjectTargetsLocked(explicitOpen bool) (Config, error) {
	path := r.dir + "/routes.json"
	c, e := loadConfig(path)
	if e != nil || !c.ProjectSessions {
		return c, e
	}
	var status Status
	if e = r.request("GET", "/api/status", nil, &status); e != nil {
		return c, e
	}
	allowed := status.Phase == "idle" && status.Native.Mode == "idle"
	if explicitOpen {
		allowed = allowed || status.Phase == "active" && status.Native.Mode == "chat"
	}
	if !allowed || !status.Connected || (status.Error != "" && !(explicitOpen && status.Phase == "idle")) {
		return c, nil
	}
	_, chats, e := rawSettings(r.voiceDir + "/state/settings.json")
	if e != nil {
		return c, e
	}
	before, e := os.ReadFile(path)
	if e != nil {
		return c, e
	}
	var doc map[string]json.RawMessage
	if e = json.Unmarshal(before, &doc); e != nil {
		return c, e
	}
	var routes, catalog []map[string]json.RawMessage
	if e = json.Unmarshal(doc["routes"], &routes); e != nil {
		return c, e
	}
	if e = json.Unmarshal(doc["catalog"], &catalog); e != nil {
		return c, e
	}
	changed := false
	assigned := map[string]bool{}
	for _, route := range c.Routes {
		assigned[route.ChatID] = true
	}
	for i, route := range c.Routes {
		var used map[string]json.RawMessage
		for _, chat := range chats {
			if rawString(chat["id"]) == route.ChatID {
				used = chat
				break
			}
		}
		if used == nil || (rawString(used["conversation_id"]) == "" && rawString(used["voice_session_id"]) == "") {
			continue
		}
		if rawString(used["gizmo_id"]) != route.ProjectID || (route.ConversationID != "" && rawString(used["conversation_id"]) != route.ConversationID) {
			return c, errors.New("project draft identity mismatch")
		}
		if e = r.saveSessionIdentity(used); e != nil {
			return c, e
		}
		id := ""
		for _, chat := range chats {
			candidate := rawString(chat["id"])
			loaded, ok := findChat(status.Settings, candidate)
			if ok && loaded.Project == route.ProjectID && loaded.Conversation == "" && !assigned[candidate] && freshProjectChat(chat, route.ProjectID) {
				id = candidate
				break
			}
		}
		if id == "" {
			id, e = newProjectID()
			if e != nil {
				return c, e
			}
		}
		assigned[id] = true
		for _, entry := range catalog {
			if rawString(entry["chat_id"]) == route.ChatID {
				entry["chat_id"] = encoded(id)
				entry["conversation_id"] = encoded("")
				entry["parent_message_id"] = encoded("")
				delete(entry, "voice_session_id")
				delete(entry, "account_id")
			}
		}
		routes[i]["chat_id"] = encoded(id)
		routes[i]["conversation_id"] = encoded("")
		delete(routes[i], "parent_message_id")
		delete(routes[i], "voice_session_id")
		changed = true
	}
	if !changed {
		return c, nil
	}
	doc["routes"] = encoded(routes)
	doc["catalog"] = encoded(catalog)
	var next Config
	if e = json.Unmarshal(encoded(doc), &next); e != nil {
		return c, e
	}
	if e = validateCatalog(next); e != nil {
		return c, e
	}
	if e = validateProjectSessions(next); e != nil {
		return c, e
	}
	current, e := os.ReadFile(path)
	if e != nil {
		return c, e
	}
	if !bytes.Equal(current, before) {
		return c, errors.New("routes changed during preparation")
	}
	if e = atomicWrite(path, encoded(doc)); e != nil {
		return c, e
	}
	r.count("project_drafts_prepared")
	return next, nil
}

// Only local registrations owned by this mode are pruned; cloud dialogs are untouched.
func (r *Router) pruneProjectRegistrations(chats []map[string]json.RawMessage, protected ...string) ([]map[string]json.RawMessage, error) {
	c, e := loadConfig(r.dir + "/routes.json")
	if e != nil {
		return nil, e
	}
	if !c.ProjectSessions {
		return chats, nil
	}
	keep := map[string]bool{}
	for _, id := range protected {
		keep[id] = true
	}
	for _, entry := range c.Catalog {
		keep[entry.ChatID] = true
	}
	result := make([]map[string]json.RawMessage, 0, len(chats))
	for _, chat := range chats {
		var managed bool
		json.Unmarshal(chat["project_session"], &managed)
		if managed && !keep[rawString(chat["id"])] && !freshProjectChat(chat, rawString(chat["gizmo_id"])) {
			if e = r.saveSessionIdentity(chat); e != nil {
				return nil, e
			}
		} else {
			result = append(result, chat)
		}
	}
	return result, nil
}

func (r *Router) openProjectLocked(c Config, key string, ev *Event) (OpenResult, error) {
	route, e := r.projectRoute(c, key)
	if e != nil {
		return OpenResult{}, e
	}
	if ev != nil && !r.currentProject(*ev) {
		return OpenResult{}, errors.New("stale project event")
	}
	var s Status
	if e = r.request("GET", "/api/status", nil, &s); e != nil {
		return OpenResult{}, e
	}
	if !s.Connected || (s.Error != "" && s.Phase != "idle") || (s.Phase != "idle" && s.Phase != "active") {
		return OpenResult{}, errors.New("voice unavailable")
	}
	if ev != nil && ev.At.Before(r.projectOpenedAt) && r.projectOpenedID == route.ProjectID && s.Phase == "active" {
		selected, ok := findChat(s.Settings, s.Settings.Selected)
		if ok && selected.Project == route.ProjectID {
			r.count("project_commands_coalesced")
			return r.result(selected.ID)
		}
	}
	if ev != nil && s.Native.Mode == "idle" {
		if e = ev.Conn.SetWriteDeadline(time.Now().Add(time.Second)); e != nil {
			return OpenResult{}, e
		}
		packet := cancelPacket(ev.ID)
		for len(packet) > 0 {
			n, err := ev.Conn.Write(packet)
			if err != nil {
				return OpenResult{}, err
			}
			if n == 0 {
				return OpenResult{}, errors.New("queue clear failed")
			}
			packet = packet[n:]
		}
	}
	// Let guarded registration own stopping and restoring an already active session.
	c, e = r.prepareProjectTargetsLocked(true)
	if e != nil {
		return OpenResult{}, e
	}
	route, e = r.projectRoute(c, route.ProjectID)
	if e != nil {
		return OpenResult{}, e
	}
	if route.ConversationID != "" {
		return OpenResult{}, errors.New("fresh project draft unavailable")
	}
	if ev != nil {
		r.mu.Lock()
		same := r.latest == ev.ID
		r.mu.Unlock()
		if !same {
			return OpenResult{}, errors.New("project event superseded")
		}
	}
	_, loaded := findChat(s.Settings, route.ChatID)
	started := time.Now()
	result, e := r.activateLocked(c, route, ev)
	log.Printf("project activation loaded=%t elapsed_ms=%d success=%t", loaded, time.Since(started).Milliseconds(), e == nil)
	if e != nil {
		return result, e
	}
	if e = atomicWrite(r.dir+"/project-last.json", encoded(map[string]string{"project_id": route.ProjectID})); e != nil {
		return result, e
	}
	r.projectOpenedAt = time.Now()
	r.projectOpenedID = route.ProjectID
	r.projectIdleSince = time.Time{}
	r.count("project_sessions_opened")
	return result, nil
}
