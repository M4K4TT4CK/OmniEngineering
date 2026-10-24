// Command notesapi is a small REST API for personal notes with user accounts.
package main

import (
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/crypto/bcrypt"
	_ "modernc.org/sqlite"
)

const (
	rateLimit    = 60
	rateWindow   = time.Minute
	tokenTTL     = 24 * time.Hour
	maxBodyBytes = 1 << 20
)

const schema = `
CREATE TABLE IF NOT EXISTS users (
	id            INTEGER PRIMARY KEY AUTOINCREMENT,
	username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
	password_hash TEXT NOT NULL,
	created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
	hash       TEXT PRIMARY KEY,
	user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
	expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
	id         INTEGER PRIMARY KEY AUTOINCREMENT,
	user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
	title      TEXT NOT NULL,
	body       TEXT NOT NULL,
	created_at TEXT NOT NULL,
	updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_user_updated ON notes(user_id, updated_at);
`

// limiter is a fixed-window rate limiter keyed by an arbitrary string.
type limiter struct {
	mu        sync.Mutex
	limit     int
	window    time.Duration
	now       func() time.Time
	entries   map[string]*window
	lastPurge time.Time
}

type window struct {
	start time.Time
	count int
}

func newLimiter(limit int, w time.Duration, now func() time.Time) *limiter {
	return &limiter{limit: limit, window: w, now: now, entries: map[string]*window{}}
}

// allow records a request and reports whether it is within the limit.
// When denied, it also returns how long until the window resets.
func (l *limiter) allow(key string) (bool, time.Duration) {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := l.now()
	if now.Sub(l.lastPurge) > l.window {
		for k, e := range l.entries {
			if now.Sub(e.start) >= l.window {
				delete(l.entries, k)
			}
		}
		l.lastPurge = now
	}
	e, ok := l.entries[key]
	if !ok || now.Sub(e.start) >= l.window {
		e = &window{start: now}
		l.entries[key] = e
	}
	if e.count >= l.limit {
		return false, e.start.Add(l.window).Sub(now)
	}
	e.count++
	return true, 0
}

type App struct {
	db      *sql.DB
	userLim *limiter // authenticated requests, keyed by user id
	ipLim   *limiter // signup/login, keyed by client IP
	now     func() time.Time
}

func NewApp(db *sql.DB) (*App, error) {
	// A single connection keeps SQLite writes serialized and makes
	// in-memory databases behave consistently.
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(schema); err != nil {
		return nil, err
	}
	now := time.Now
	return &App{
		db:      db,
		userLim: newLimiter(rateLimit, rateWindow, now),
		ipLim:   newLimiter(rateLimit, rateWindow, now),
		now:     now,
	}, nil
}

func (a *App) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /signup", a.limitIP(a.signup))
	mux.HandleFunc("POST /login", a.limitIP(a.login))
	mux.HandleFunc("POST /logout", a.authed(a.logout))
	mux.HandleFunc("POST /notes", a.authed(a.createNote))
	mux.HandleFunc("GET /notes", a.authed(a.listNotes))
	mux.HandleFunc("GET /notes/{id}", a.authed(a.getNote))
	mux.HandleFunc("PUT /notes/{id}", a.authed(a.updateNote))
	mux.HandleFunc("DELETE /notes/{id}", a.authed(a.deleteNote))
	return mux
}

// ---- helpers ----

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func readJSON(w http.ResponseWriter, r *http.Request, v any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid JSON body")
		return false
	}
	return true
}

func (a *App) ts() string { return a.now().UTC().Format(time.RFC3339Nano) }

func hashToken(tok string) string {
	sum := sha256.Sum256([]byte(tok))
	return hex.EncodeToString(sum[:])
}

func newToken() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

func serverErr(w http.ResponseWriter, err error) {
	log.Printf("internal error: %v", err)
	writeErr(w, http.StatusInternalServerError, "internal error")
}

func tooMany(w http.ResponseWriter, retry time.Duration) {
	secs := int(retry.Seconds()) + 1
	w.Header().Set("Retry-After", strconv.Itoa(secs))
	writeErr(w, http.StatusTooManyRequests, "rate limit exceeded")
}

// ---- middleware ----

// limitIP throttles unauthenticated endpoints per client IP to slow
// password guessing and signup spam. X-Forwarded-For is deliberately ignored.
func (a *App) limitIP(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		host, _, err := net.SplitHostPort(r.RemoteAddr)
		if err != nil {
			host = r.RemoteAddr
		}
		if ok, retry := a.ipLim.allow(host); !ok {
			tooMany(w, retry)
			return
		}
		next(w, r)
	}
}

type userHandler func(w http.ResponseWriter, r *http.Request, userID int64, tokenHash string)

// authed validates the bearer token, then applies the per-user rate limit.
func (a *App) authed(next userHandler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		const prefix = "Bearer "
		h := r.Header.Get("Authorization")
		if len(h) <= len(prefix) || !strings.EqualFold(h[:len(prefix)], prefix) {
			writeErr(w, http.StatusUnauthorized, "missing or invalid token")
			return
		}
		th := hashToken(strings.TrimSpace(h[len(prefix):]))
		var uid int64
		var exp string
		err := a.db.QueryRow(`SELECT user_id, expires_at FROM tokens WHERE hash = ?`, th).Scan(&uid, &exp)
		if errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusUnauthorized, "missing or invalid token")
			return
		} else if err != nil {
			serverErr(w, err)
			return
		}
		if t, err := time.Parse(time.RFC3339Nano, exp); err != nil || !a.now().Before(t) {
			a.db.Exec(`DELETE FROM tokens WHERE hash = ?`, th)
			writeErr(w, http.StatusUnauthorized, "token expired")
			return
		}
		if ok, retry := a.userLim.allow(strconv.FormatInt(uid, 10)); !ok {
			tooMany(w, retry)
			return
		}
		next(w, r, uid, th)
	}
}

// ---- auth handlers ----

type credentials struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

// dummyHash is compared against when a user doesn't exist, so login timing
// doesn't reveal which usernames are registered.
var dummyHash, _ = bcrypt.GenerateFromPassword([]byte("dummy-password"), bcrypt.DefaultCost)

func (a *App) issueToken(userID int64) (string, string, error) {
	tok, err := newToken()
	if err != nil {
		return "", "", err
	}
	exp := a.now().Add(tokenTTL).UTC().Format(time.RFC3339Nano)
	a.db.Exec(`DELETE FROM tokens WHERE expires_at < ?`, a.ts())
	_, err = a.db.Exec(`INSERT INTO tokens (hash, user_id, expires_at) VALUES (?, ?, ?)`, hashToken(tok), userID, exp)
	return tok, exp, err
}

func validUsername(u string) bool {
	if len(u) < 3 || len(u) > 32 {
		return false
	}
	for _, c := range u {
		switch {
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9', c == '_', c == '-', c == '.':
		default:
			return false
		}
	}
	return true
}

func (a *App) signup(w http.ResponseWriter, r *http.Request) {
	var c credentials
	if !readJSON(w, r, &c) {
		return
	}
	if !validUsername(c.Username) {
		writeErr(w, http.StatusBadRequest, "username must be 3-32 characters: letters, digits, '_', '-', '.'")
		return
	}
	// bcrypt only considers the first 72 bytes.
	if len(c.Password) < 8 || len(c.Password) > 72 {
		writeErr(w, http.StatusBadRequest, "password must be 8-72 bytes")
		return
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(c.Password), bcrypt.DefaultCost)
	if err != nil {
		serverErr(w, err)
		return
	}
	res, err := a.db.Exec(`INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)`,
		c.Username, string(hash), a.ts())
	if err != nil {
		if strings.Contains(err.Error(), "UNIQUE") {
			writeErr(w, http.StatusConflict, "username already taken")
			return
		}
		serverErr(w, err)
		return
	}
	uid, _ := res.LastInsertId()
	tok, exp, err := a.issueToken(uid)
	if err != nil {
		serverErr(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"token": tok, "expires_at": exp})
}

func (a *App) login(w http.ResponseWriter, r *http.Request) {
	var c credentials
	if !readJSON(w, r, &c) {
		return
	}
	var uid int64
	var hash string
	err := a.db.QueryRow(`SELECT id, password_hash FROM users WHERE username = ?`, c.Username).Scan(&uid, &hash)
	if errors.Is(err, sql.ErrNoRows) {
		bcrypt.CompareHashAndPassword(dummyHash, []byte(c.Password))
		writeErr(w, http.StatusUnauthorized, "invalid username or password")
		return
	} else if err != nil {
		serverErr(w, err)
		return
	}
	if bcrypt.CompareHashAndPassword([]byte(hash), []byte(c.Password)) != nil {
		writeErr(w, http.StatusUnauthorized, "invalid username or password")
		return
	}
	tok, exp, err := a.issueToken(uid)
	if err != nil {
		serverErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"token": tok, "expires_at": exp})
}

func (a *App) logout(w http.ResponseWriter, r *http.Request, _ int64, th string) {
	if _, err := a.db.Exec(`DELETE FROM tokens WHERE hash = ?`, th); err != nil {
		serverErr(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// ---- note handlers ----

type Note struct {
	ID        int64  `json:"id"`
	Title     string `json:"title"`
	Body      string `json:"body"`
	CreatedAt string `json:"created_at"`
	UpdatedAt string `json:"updated_at"`
}

type noteInput struct {
	Title string `json:"title"`
	Body  string `json:"body"`
}

func (n noteInput) validate() string {
	switch {
	case strings.TrimSpace(n.Title) == "":
		return "title is required"
	case len(n.Title) > 200:
		return "title must be at most 200 bytes"
	case len(n.Body) > 100_000:
		return "body must be at most 100000 bytes"
	}
	return ""
}

type scanner interface{ Scan(...any) error }

func scanNote(s scanner) (Note, error) {
	var n Note
	err := s.Scan(&n.ID, &n.Title, &n.Body, &n.CreatedAt, &n.UpdatedAt)
	return n, err
}

const noteCols = `id, title, body, created_at, updated_at`

func noteID(w http.ResponseWriter, r *http.Request) (int64, bool) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil {
		writeErr(w, http.StatusNotFound, "note not found")
		return 0, false
	}
	return id, true
}

func (a *App) createNote(w http.ResponseWriter, r *http.Request, uid int64, _ string) {
	var in noteInput
	if !readJSON(w, r, &in) {
		return
	}
	if msg := in.validate(); msg != "" {
		writeErr(w, http.StatusBadRequest, msg)
		return
	}
	ts := a.ts()
	res, err := a.db.Exec(`INSERT INTO notes (user_id, title, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?)`,
		uid, in.Title, in.Body, ts, ts)
	if err != nil {
		serverErr(w, err)
		return
	}
	id, _ := res.LastInsertId()
	writeJSON(w, http.StatusCreated, Note{ID: id, Title: in.Title, Body: in.Body, CreatedAt: ts, UpdatedAt: ts})
}

func likeEscape(s string) string {
	return strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(s)
}

// listNotes supports ?q= (substring search of title and body, case-insensitive
// for ASCII), ?limit= (default 50, max 100) and ?offset=.
func (a *App) listNotes(w http.ResponseWriter, r *http.Request, uid int64, _ string) {
	qs := r.URL.Query()
	limit, offset := 50, 0
	if v := qs.Get("limit"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 100 {
			writeErr(w, http.StatusBadRequest, "limit must be between 1 and 100")
			return
		}
		limit = n
	}
	if v := qs.Get("offset"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 0 {
			writeErr(w, http.StatusBadRequest, "offset must be a non-negative integer")
			return
		}
		offset = n
	}
	query := `SELECT ` + noteCols + ` FROM notes WHERE user_id = ?`
	args := []any{uid}
	if q := qs.Get("q"); q != "" {
		query += ` AND (title LIKE ? ESCAPE '\' OR body LIKE ? ESCAPE '\')`
		pat := "%" + likeEscape(q) + "%"
		args = append(args, pat, pat)
	}
	query += ` ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?`
	args = append(args, limit, offset)

	rows, err := a.db.Query(query, args...)
	if err != nil {
		serverErr(w, err)
		return
	}
	defer rows.Close()
	notes := []Note{}
	for rows.Next() {
		n, err := scanNote(rows)
		if err != nil {
			serverErr(w, err)
			return
		}
		notes = append(notes, n)
	}
	if err := rows.Err(); err != nil {
		serverErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, notes)
}

func (a *App) getNote(w http.ResponseWriter, r *http.Request, uid int64, _ string) {
	id, ok := noteID(w, r)
	if !ok {
		return
	}
	n, err := scanNote(a.db.QueryRow(`SELECT `+noteCols+` FROM notes WHERE id = ? AND user_id = ?`, id, uid))
	if errors.Is(err, sql.ErrNoRows) {
		writeErr(w, http.StatusNotFound, "note not found")
		return
	} else if err != nil {
		serverErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, n)
}

func (a *App) updateNote(w http.ResponseWriter, r *http.Request, uid int64, _ string) {
	id, ok := noteID(w, r)
	if !ok {
		return
	}
	var in noteInput
	if !readJSON(w, r, &in) {
		return
	}
	if msg := in.validate(); msg != "" {
		writeErr(w, http.StatusBadRequest, msg)
		return
	}
	n, err := scanNote(a.db.QueryRow(
		`UPDATE notes SET title = ?, body = ?, updated_at = ? WHERE id = ? AND user_id = ? RETURNING `+noteCols,
		in.Title, in.Body, a.ts(), id, uid))
	if errors.Is(err, sql.ErrNoRows) {
		writeErr(w, http.StatusNotFound, "note not found")
		return
	} else if err != nil {
		serverErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, n)
}

func (a *App) deleteNote(w http.ResponseWriter, r *http.Request, uid int64, _ string) {
	id, ok := noteID(w, r)
	if !ok {
		return
	}
	res, err := a.db.Exec(`DELETE FROM notes WHERE id = ? AND user_id = ?`, id, uid)
	if err != nil {
		serverErr(w, err)
		return
	}
	if n, _ := res.RowsAffected(); n == 0 {
		writeErr(w, http.StatusNotFound, "note not found")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func main() {
	path := os.Getenv("DB_PATH")
	if path == "" {
		path = "notes.db"
	}
	addr := os.Getenv("ADDR")
	if addr == "" {
		addr = ":8080"
	}
	db, err := sql.Open("sqlite", "file:"+path+"?_pragma=foreign_keys(1)&_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)")
	if err != nil {
		log.Fatal(err)
	}
	app, err := NewApp(db)
	if err != nil {
		log.Fatal(err)
	}
	srv := &http.Server{
		Addr:              addr,
		Handler:           app.Routes(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      15 * time.Second,
	}
	log.Printf("listening on %s (db: %s)", addr, path)
	log.Fatal(srv.ListenAndServe())
}
