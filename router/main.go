package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf8"
)

const base = "/data/opt/kas-command-router"
const voice = "/data/opt/alisa-standalone"
const switchLock = "/tmp/kas-control-switch.lock"
const maxFrame = 256 * 1024

var validID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)
var uuid = regexp.MustCompile(`^[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}$`)

type Route struct {
	Phrase         string `json:"phrase"`
	ChatID         string `json:"chat_id"`
	ProjectID      string `json:"project_id"`
	ConversationID string `json:"conversation_id"`
}
type Config struct {
	Version int            `json:"version"`
	Routes  []Route        `json:"routes"`
	Catalog []CatalogEntry `json:"catalog"`
}
type Chat struct {
	ID           string `json:"id"`
	Project      string `json:"gizmo_id"`
	Conversation string `json:"conversation_id"`
	Parent       string `json:"parent_message_id"`
}
type Settings struct {
	Version  int    `json:"version"`
	Selected string `json:"selected"`
	Chats    []Chat `json:"chats"`
}
type Status struct {
	Phase     string `json:"phase"`
	Error     string `json:"error"`
	Connected bool   `json:"agent_connected"`
	Native    struct {
		Mode string `json:"mode"`
	} `json:"native"`
	Settings Settings `json:"settings"`
}
type Event struct {
	ID, Text string
	At       time.Time
	Conn     net.Conn
}

// Store canonical Latin/Cyrillic phrases; allow either user-facing wake word.
func normalize(s string) string {
	if len(s) > 1024 || !utf8.ValidString(s) {
		return ""
	}
	var b strings.Builder
	space := true
	for _, c := range s {
		if c >= 'А' && c <= 'Я' {
			c += 32
		}
		if c == 'Ё' || c == 'ё' {
			c = 'е'
		}
		if c >= 'A' && c <= 'Z' {
			c += 32
		}
		if c >= 'а' && c <= 'я' || c >= 'a' && c <= 'z' || c >= '0' && c <= '9' {
			b.WriteRune(c)
			space = false
		} else if !space {
			b.WriteByte(' ')
			space = true
		}
	}
	return strings.TrimSuffix(strings.TrimPrefix(strings.TrimPrefix(strings.TrimSpace(b.String()), "алиса "), "яндекс "), " пожалуйста")
}
func readJSON(path string, dst any) error {
	f, e := os.Open(path)
	if e != nil {
		return e
	}
	defer f.Close()
	b, e := io.ReadAll(io.LimitReader(f, 512*1024+1))
	if e != nil {
		return e
	}
	if len(b) > 512*1024 {
		return errors.New("file too large")
	}
	return json.Unmarshal(b, dst)
}
func loadConfig(path string) (Config, error) {
	var c Config
	e := readJSON(path, &c)
	if e != nil {
		return c, e
	}
	if c.Version != 1 || len(c.Routes) > 64 {
		return c, errors.New("invalid route schema")
	}
	seen := map[string]bool{}
	for i := range c.Routes {
		r := &c.Routes[i]
		r.Phrase = normalize(r.Phrase)
		if r.Phrase == "" || seen[r.Phrase] || !validID.MatchString(r.ChatID) || !validID.MatchString(r.ProjectID) || !strings.HasPrefix(r.ProjectID, "g-p-") || (r.ConversationID != "" && !validID.MatchString(r.ConversationID)) {
			return c, errors.New("invalid or duplicate route")
		}
		seen[r.Phrase] = true
	}
	if e := validateCatalog(c); e != nil {
		return c, e
	}
	for i := range c.Routes {
		if v, ok := catalogTarget(c, c.Routes[i]); ok {
			c.Routes[i].ConversationID = v.ConversationID
		}
	}
	return c, nil
}
func lock(path string) (*os.File, error) {
	f, e := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return nil, e
	}
	if e = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); e != nil {
		f.Close()
		return nil, e
	}
	return f, nil
}
func unlock(f *os.File) { syscall.Flock(int(f.Fd()), syscall.LOCK_UN); f.Close() }
func atomicWrite(path string, b []byte) error {
	f, e := os.CreateTemp(filepath.Dir(path), ".router-")
	// Use the same directory so rename is atomic on the station filesystem.
	if e != nil {
		return e
	}
	name := f.Name()
	defer os.Remove(name)
	if e = f.Chmod(0600); e == nil {
		_, e = f.Write(b)
	}
	if e == nil {
		e = f.Sync()
	}
	ce := f.Close()
	if e == nil {
		e = ce
	}
	if e != nil {
		return e
	}
	return os.Rename(name, path)
}

// Only open aliases can be delegated. Close/new/previous keep their existing semantics.
func filteredCommands(original []byte, c Config) ([]byte, error) {
	if len(original) == 0 || len(original) >= 4096 {
		return nil, errors.New("invalid native commands file")
	}
	phrases := map[string]bool{}
	for _, r := range c.Routes {
		phrases[r.Phrase] = true
	}
	var out strings.Builder
	for _, line := range strings.Split(strings.TrimSuffix(string(original), "\n"), "\n") {
		if len(line) < 3 || line[1] != '\t' || line[0] < '1' || line[0] > '4' {
			return nil, errors.New("invalid native command")
		}
		if phrases[normalize(line[2:])] {
			if line[0] != '1' {
				return nil, errors.New("route conflicts with reserved native command")
			}
			continue
		}
		out.WriteString(line)
		out.WriteByte('\n')
	}
	return []byte(out.String()), nil
}

type field struct {
	n    uint64
	b    []byte
	wire uint64
}

func fields(b []byte) (map[uint64]field, error) {
	m := map[uint64]field{}
	for len(b) > 0 {
		key, n := binary.Uvarint(b)
		if n <= 0 || key>>3 == 0 {
			return nil, errors.New("bad protobuf key")
		}
		b = b[n:]
		f := field{wire: key & 7}
		switch f.wire {
		case 0:
			v, n := binary.Uvarint(b)
			if n <= 0 {
				return nil, errors.New("bad varint")
			}
			f.n = v
			b = b[n:]
		case 2:
			l, n := binary.Uvarint(b)
			if n <= 0 || l > uint64(len(b)-n) {
				return nil, errors.New("bad protobuf length")
			}
			b = b[n:]
			f.b = b[:int(l)]
			b = b[int(l):]
		case 1:
			if len(b) < 8 {
				return nil, io.ErrUnexpectedEOF
			}
			b = b[8:]
		case 5:
			if len(b) < 4 {
				return nil, io.ErrUnexpectedEOF
			}
			b = b[4:]
		default:
			return nil, errors.New("bad protobuf wire")
		}
		if _, exists := m[key>>3]; exists {
			return nil, errors.New("duplicate protobuf field")
		}
		m[key>>3] = f
	}
	return m, nil
}
func event(b []byte) (Event, bool) {
	outer, e := fields(b)
	if e != nil || outer[253].n != 3 {
		return Event{}, false
	}
	s, e := fields(outer[252].b)
	if e != nil || s[1].n != 3 {
		return Event{}, false
	}
	r, e := fields(s[15].b)
	id := string(s[7].b)
	if e != nil || r[2].n != 1 || r[4].n != 1 || !uuid.MatchString(id) || len(r[1].b) > 1024 {
		return Event{}, false
	}
	text := normalize(string(r[1].b))
	return Event{ID: id, Text: text, At: time.Now()}, text != ""
}
func number(b []byte, key, v uint64) []byte {
	b = binary.AppendUvarint(b, key<<3)
	return binary.AppendUvarint(b, v)
}
func blob(b []byte, key uint64, v []byte) []byte {
	b = binary.AppendUvarint(b, key<<3|2)
	b = binary.AppendUvarint(b, uint64(len(v)))
	return append(b, v...)
}
func cancelPacket(id string) []byte {
	method := blob(number(nil, 1, 6), 10, []byte(id))
	remote := blob(blob(nil, 7, []byte("AliceCapability")), 8, method)
	envelope := number(blob(nil, 252, remote), 253, 210)
	b := binary.LittleEndian.AppendUint32(nil, uint32(len(envelope)))
	return append(b, envelope...)
}
func readFrame(r io.Reader) ([]byte, error) {
	var header [4]byte
	if _, e := io.ReadFull(r, header[:]); e != nil {
		return nil, e
	}
	n := binary.LittleEndian.Uint32(header[:])
	if n == 0 || n > maxFrame {
		return nil, errors.New("frame size")
	}
	b := make([]byte, n)
	_, e := io.ReadFull(r, b)
	return b, e
}

type Router struct {
	dir, voiceDir, api string
	original           []byte
	client             *http.Client
	mu                 sync.Mutex
	ready              bool
	latest             string
	applied            string
	counters           map[string]uint64
	lockPath           string
	backupRoot         string
}

func (r *Router) switchMutex() (*os.File, error) {
	path := r.lockPath
	if path == "" {
		path = switchLock
	}
	return lock(path)
}

func (r *Router) count(name string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.counters == nil {
		r.counters = map[string]uint64{}
	}
	r.counters[name]++
}
func (r *Router) connection(connected bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.ready = connected
	r.latest = ""
}
func (r *Router) recognizing(id string) { r.mu.Lock(); defer r.mu.Unlock(); r.latest = id }

func (r *Router) syncCommands() error {
	f, e := r.switchMutex()
	if e != nil {
		return e
	}
	defer unlock(f)
	c, e := loadConfig(r.dir + "/routes.json")
	if e != nil {
		return e
	}
	b, e := filteredCommands(r.original, c)
	if e != nil {
		return e
	}
	current, e := os.ReadFile(r.voiceDir + "/commands.txt")
	if e != nil {
		return e
	}
	if !bytes.Equal(current, b) {
		if e = atomicWrite(r.voiceDir+"/commands.txt", b); e != nil {
			return e
		}
	}
	raw, e := os.ReadFile(r.dir + "/routes.json")
	if e != nil {
		return e
	}
	r.mu.Lock()
	r.applied = fmt.Sprintf("%x", sha256.Sum256(raw))
	r.mu.Unlock()
	return nil
}
func (r *Router) request(method, path string, body any, dst any) error {
	var auth struct {
		Key string `json:"key"`
	}
	if e := readJSON(r.voiceDir+"/state/ui-auth.json", &auth); e != nil || auth.Key == "" {
		return errors.New("control key unavailable")
	}
	var data []byte
	var e error
	if body != nil {
		data, e = json.Marshal(body)
		if e != nil {
			return e
		}
	}
	req, e := http.NewRequest(method, r.api+path, bytes.NewReader(data))
	if e != nil {
		return e
	}
	req.Header.Set("X-Standalone-Key", auth.Key)
	req.Header.Set("Content-Type", "application/json")
	res, e := r.client.Do(req)
	if e != nil {
		return errors.New("control request failed; outcome may be unknown")
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		return errors.New("control rejected request")
	}
	return json.NewDecoder(io.LimitReader(res.Body, 512*1024)).Decode(dst)
}
func matches(s Settings, route Route, requireConversation bool) bool {
	for _, c := range s.Chats {
		if c.ID == route.ChatID && c.Project == route.ProjectID && (!requireConversation || c.Conversation == route.ConversationID) {
			return true
		}
	}
	return false
}
func (r *Router) current(ev Event) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.ready && r.latest == ev.ID && time.Since(ev.At) < 3*time.Second
}
func (r *Router) dispatch(ev Event) error {
	if !r.current(ev) {
		return errors.New("stale event")
	}
	f, e := r.switchMutex()
	if e != nil {
		return errors.New("switch busy; event dropped")
	}
	defer unlock(f)
	if e = r.recoverLocked(); e != nil {
		return e
	}
	c, e := loadConfig(r.dir + "/routes.json")
	if e != nil {
		return e
	}
	var target *Route
	for i := range c.Routes {
		if c.Routes[i].Phrase == ev.Text {
			target = &c.Routes[i]
			break
		}
	}
	if target == nil {
		return nil
	}
	expected, e := filteredCommands(r.original, c)
	if e != nil {
		return e
	}
	actual, e := os.ReadFile(r.voiceDir + "/commands.txt")
	if e != nil || !bytes.Equal(actual, expected) {
		return errors.New("native aliases not synchronized")
	}
	_, e = r.activateLocked(c, *target, &ev)
	return e
}

func (r *Router) listen(queue chan Event) {
	seen := map[string]bool{}
	var order []string
	for {
		conn, e := net.DialTimeout("tcp", "127.0.0.1:9875", 3*time.Second)
		if e != nil {
			time.Sleep(time.Second)
			continue
		}
		observed := map[string]time.Time{}
		r.connection(true)
		for {
			b, e := readFrame(conn)
			if e != nil {
				break
			}
			outer, _ := fields(b)
			s, _ := fields(outer[252].b)
			id := string(s[7].b)
			if outer[253].n == 3 && (s[1].n == 1 || s[1].n == 2) && uuid.MatchString(id) {
				r.count("recognition_states")
				r.recognizing(id)
				observed[id] = time.Now()
			}
			for key, at := range observed {
				if time.Since(at) > 30*time.Second {
					delete(observed, key)
				}
			}
			ev, ok := event(b)
			if !ok || seen[ev.ID] {
				continue
			}
			r.count("finals")
			seen[ev.ID] = true
			order = append(order, ev.ID)
			if len(order) > 256 {
				delete(seen, order[0])
				order = order[1:]
			}
			// A BUSY snapshot after reconnect is not a fresh spoken request.
			if _, ok := observed[ev.ID]; !ok {
				r.count("rejected_missing_fresh_uuid")
				continue
			}
			delete(observed, ev.ID)
			r.count("fresh_finals")
			ev.Conn = conn
			r.mu.Lock()
			r.latest = ev.ID
			r.mu.Unlock()
			select {
			case queue <- ev:
			default:
				select {
				case <-queue:
				default:
				}
				queue <- ev
			}
		}
		conn.Close()
		r.connection(false)
		time.Sleep(time.Second)
	}
}
func main() {
	log.SetFlags(log.Ldate | log.Ltime | log.LUTC)
	if len(os.Args) > 1 && os.Args[1] == "validate" {
		_, e := loadConfig(base + "/routes.json")
		if e != nil {
			log.Fatal(e)
		}
		return
	}
	r := Router{dir: base, voiceDir: voice, api: "http://127.0.0.1:18080", client: &http.Client{Timeout: 3 * time.Second, Transport: &http.Transport{Proxy: nil}, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
	if len(os.Args) >= 3 && len(os.Args) <= 4 && os.Args[1] == "restore" {
		held := len(os.Args) == 4 && os.Args[3] == "--held-lock"
		if len(os.Args) == 4 && !held {
			log.Fatal("invalid restore option")
		}
		if e := r.restoreBackup(os.Args[2], held); e != nil {
			log.Fatal(e)
		}
		fmt.Println("KAS_RESTORED")
		return
	}
	if len(os.Args) == 3 && os.Args[1] == "open" {
		result, e := r.open(os.Args[2])
		if e != nil {
			_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"ok": false, "error": e.Error(), "code": "KAS_ERROR:ACTIVATION_FAILED"})
			os.Exit(1)
		}
		_ = json.NewEncoder(os.Stdout).Encode(result)
		return
	}
	if len(os.Args) == 2 && os.Args[1] == "recover" {
		f, e := r.switchMutex()
		if e != nil {
			log.Fatal(e)
		}
		defer unlock(f)
		if e = r.recoverLocked(); e != nil {
			log.Fatal(e)
		}
		return
	}
	if len(os.Args) != 1 {
		log.Fatal("usage: router [validate | recover | open CHAT_ID]")
	}
	parent := os.Getppid()
	if parent == 1 {
		log.Fatal("run through supervise.sh")
	}
	go func() {
		for {
			time.Sleep(time.Second)
			if os.Getppid() != parent {
				os.Exit(1)
			}
		}
	}()
	original, e := os.ReadFile(base + "/commands.original")
	if e != nil {
		log.Fatal("missing supervisor command backup")
	}
	r.original = original
	singleton, e := lock("/tmp/kas-command-router-worker.lock")
	if e != nil {
		log.Fatal("worker already running")
	}
	defer unlock(singleton)
	f, e := r.switchMutex()
	if e != nil {
		log.Fatal(e)
	}
	e = r.recoverLocked()
	unlock(f)
	if e != nil {
		log.Fatal(e)
	}
	if e = r.syncCommands(); e != nil {
		log.Fatal(e)
	}
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGINT)
	go func() { <-signals; os.Exit(0) }()
	go func() {
		for {
			time.Sleep(time.Second)
			e := r.syncCommands()
			r.mu.Lock()
			connected := r.ready
			applied := r.applied
			counts := make(map[string]uint64, len(r.counters))
			for k, v := range r.counters {
				counts[k] = v
			}
			r.mu.Unlock()
			lastError := ""
			if e != nil {
				lastError = "configuration not applied or switch busy"
			}
			b, _ := json.Marshal(map[string]any{"connected": connected, "ready": connected && e == nil, "worker_pid": os.Getpid(), "updated_unix": time.Now().Unix(), "config_sha256": applied, "last_error": lastError, "counters": counts, "features": []string{"catalog", "lazy_registration"}})
			_ = atomicWrite(base+"/status.json", b)
		}
	}()
	queue := make(chan Event, 1)
	go r.listen(queue)
	log.Print("router ready")
	for ev := range queue {
		if e = r.dispatch(ev); e != nil {
			log.Print(fmt.Sprintf("route not completed: %s", e))
		}
	}
}
