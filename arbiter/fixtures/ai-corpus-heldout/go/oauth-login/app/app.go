package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"html/template"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
	"time"

	"golang.org/x/oauth2"
	_ "modernc.org/sqlite"
)

const (
	sessionCookie = "session"
	flowCookie    = "oauth_flow"
	flowTTL       = 10 * time.Minute
)

// Config holds the settings needed to talk to GitHub. The endpoint URLs
// default to github.com and only need overriding in tests.
type Config struct {
	ClientID     string
	ClientSecret string
	BaseURL      string // public URL of this app; the callback is BaseURL + "/auth/callback"
	AuthURL      string
	TokenURL     string
	APIBaseURL   string
	SessionTTL   time.Duration
}

type User struct {
	ID         int64
	GitHubID   int64
	Login      string
	Name       string
	Email      string
	AvatarURL  string
	ProfileURL string
	CreatedAt  time.Time
	UpdatedAt  time.Time
}

type githubProfile struct {
	ID        int64  `json:"id"`
	Login     string `json:"login"`
	Name      string `json:"name"`
	Email     string `json:"email"`
	AvatarURL string `json:"avatar_url"`
	HTMLURL   string `json:"html_url"`
}

type App struct {
	db         *sql.DB
	oauth      *oauth2.Config
	apiBase    string
	httpClient *http.Client
	origin     string // scheme://host of BaseURL, used for same-origin checks
	secure     bool   // set the Secure flag on cookies (BaseURL is https)
	sessionTTL time.Duration
}

var schema = []string{
	`CREATE TABLE IF NOT EXISTS users (
		id          INTEGER PRIMARY KEY,
		github_id   INTEGER NOT NULL UNIQUE,
		login       TEXT NOT NULL,
		name        TEXT NOT NULL DEFAULT '',
		email       TEXT NOT NULL DEFAULT '',
		avatar_url  TEXT NOT NULL DEFAULT '',
		profile_url TEXT NOT NULL DEFAULT '',
		created_at  INTEGER NOT NULL,
		updated_at  INTEGER NOT NULL
	)`,
	`CREATE TABLE IF NOT EXISTS sessions (
		token_hash TEXT PRIMARY KEY,
		user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
		expires_at INTEGER NOT NULL
	)`,
	`CREATE INDEX IF NOT EXISTS sessions_expires_at ON sessions(expires_at)`,
}

// OpenDB opens (or creates) the SQLite database at path and applies the schema.
func OpenDB(path string) (*sql.DB, error) {
	dsn := "file:" + path + "?_pragma=foreign_keys(1)&_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	for _, stmt := range schema {
		if _, err := db.Exec(stmt); err != nil {
			db.Close()
			return nil, fmt.Errorf("apply schema: %w", err)
		}
	}
	return db, nil
}

func NewApp(cfg Config, db *sql.DB) (*App, error) {
	base, err := url.Parse(cfg.BaseURL)
	if err != nil || (base.Scheme != "http" && base.Scheme != "https") || base.Host == "" {
		return nil, fmt.Errorf("invalid BaseURL %q", cfg.BaseURL)
	}
	if cfg.AuthURL == "" {
		cfg.AuthURL = "https://github.com/login/oauth/authorize"
	}
	if cfg.TokenURL == "" {
		cfg.TokenURL = "https://github.com/login/oauth/access_token"
	}
	if cfg.APIBaseURL == "" {
		cfg.APIBaseURL = "https://api.github.com"
	}
	if cfg.SessionTTL == 0 {
		cfg.SessionTTL = 7 * 24 * time.Hour
	}
	origin := base.Scheme + "://" + base.Host
	return &App{
		db: db,
		oauth: &oauth2.Config{
			ClientID:     cfg.ClientID,
			ClientSecret: cfg.ClientSecret,
			RedirectURL:  strings.TrimRight(cfg.BaseURL, "/") + "/auth/callback",
			Scopes:       []string{"read:user"},
			Endpoint: oauth2.Endpoint{
				AuthURL:   cfg.AuthURL,
				TokenURL:  cfg.TokenURL,
				AuthStyle: oauth2.AuthStyleInParams,
			},
		},
		apiBase:    strings.TrimRight(cfg.APIBaseURL, "/"),
		httpClient: &http.Client{Timeout: 10 * time.Second},
		origin:     origin,
		secure:     base.Scheme == "https",
		sessionTTL: cfg.SessionTTL,
	}, nil
}

func (a *App) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", a.handleHome)
	mux.HandleFunc("GET /login", a.handleLogin)
	mux.HandleFunc("GET /auth/callback", a.handleCallback)
	mux.HandleFunc("GET /profile", a.handleProfile)
	mux.HandleFunc("POST /logout", a.handleLogout)
	return securityHeaders(mux)
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("Content-Security-Policy", "default-src 'none'; img-src https:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Referrer-Policy", "same-origin")
		next.ServeHTTP(w, r)
	})
}

// --- handlers ---

func (a *App) handleHome(w http.ResponseWriter, r *http.Request) {
	user, err := a.currentUser(r)
	if err != nil {
		log.Printf("load session: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	render(w, homeTmpl, user)
}

func (a *App) handleLogin(w http.ResponseWriter, r *http.Request) {
	state, err := randomToken()
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	verifier := oauth2.GenerateVerifier()
	next := a.returnTo(r)

	// State, PKCE verifier and return path travel in one short-lived cookie.
	// The state and verifier are base64url, so "." is a safe separator.
	value := state + "." + verifier + "." + base64.RawURLEncoding.EncodeToString([]byte(next))
	a.setCookie(w, flowCookie, value, flowTTL)

	http.Redirect(w, r, a.oauth.AuthCodeURL(state, oauth2.S256ChallengeOption(verifier)), http.StatusFound)
}

func (a *App) handleCallback(w http.ResponseWriter, r *http.Request) {
	c, err := r.Cookie(flowCookie)
	a.clearCookie(w, flowCookie) // single use, whatever the outcome
	if err != nil {
		http.Error(w, "Login attempt expired or not started here. Please try again.", http.StatusBadRequest)
		return
	}
	parts := strings.Split(c.Value, ".")
	if len(parts) != 3 {
		http.Error(w, "Invalid login state.", http.StatusBadRequest)
		return
	}
	state, verifier := parts[0], parts[1]
	nextRaw, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		http.Error(w, "Invalid login state.", http.StatusBadRequest)
		return
	}

	q := r.URL.Query()
	if e := q.Get("error"); e != "" {
		log.Printf("github authorization error: %s", e)
		http.Error(w, "GitHub authorization was not completed.", http.StatusBadRequest)
		return
	}
	got := q.Get("state")
	if got == "" || subtle.ConstantTimeCompare([]byte(got), []byte(state)) != 1 {
		http.Error(w, "Invalid login state.", http.StatusBadRequest)
		return
	}
	code := q.Get("code")
	if code == "" {
		http.Error(w, "Missing authorization code.", http.StatusBadRequest)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	defer cancel()
	ctx = context.WithValue(ctx, oauth2.HTTPClient, a.httpClient)

	tok, err := a.oauth.Exchange(ctx, code, oauth2.VerifierOption(verifier))
	if err != nil {
		log.Printf("token exchange: %v", err)
		http.Error(w, "Could not complete login with GitHub.", http.StatusBadGateway)
		return
	}
	profile, err := a.fetchProfile(ctx, tok.AccessToken)
	if err != nil {
		log.Printf("fetch profile: %v", err)
		http.Error(w, "Could not load your GitHub profile.", http.StatusBadGateway)
		return
	}
	userID, err := a.upsertUser(ctx, profile)
	if err != nil {
		log.Printf("upsert user: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}

	// Always issue a fresh session token on login (prevents session fixation).
	if old, err := r.Cookie(sessionCookie); err == nil {
		_ = a.deleteSession(ctx, old.Value)
	}
	token, err := a.createSession(ctx, userID)
	if err != nil {
		log.Printf("create session: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	a.setCookie(w, sessionCookie, token, a.sessionTTL)
	http.Redirect(w, r, safePath(string(nextRaw)), http.StatusFound)
}

func (a *App) handleProfile(w http.ResponseWriter, r *http.Request) {
	user, err := a.currentUser(r)
	if err != nil {
		log.Printf("load session: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	if user == nil {
		http.Redirect(w, r, "/login?next="+url.QueryEscape(r.URL.RequestURI()), http.StatusFound)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	render(w, profileTmpl, user)
}

func (a *App) handleLogout(w http.ResponseWriter, r *http.Request) {
	// Cookies are SameSite=Lax, which already keeps them off cross-site POSTs;
	// this is a second line of defence against CSRF logouts.
	if o := r.Header.Get("Origin"); o != "" && o != a.origin {
		http.Error(w, "cross-origin request rejected", http.StatusForbidden)
		return
	}
	if r.Header.Get("Sec-Fetch-Site") == "cross-site" {
		http.Error(w, "cross-origin request rejected", http.StatusForbidden)
		return
	}
	if c, err := r.Cookie(sessionCookie); err == nil {
		if err := a.deleteSession(r.Context(), c.Value); err != nil {
			log.Printf("delete session: %v", err)
		}
	}
	a.clearCookie(w, sessionCookie)
	http.Redirect(w, r, "/", http.StatusSeeOther)
}

// --- GitHub ---

func (a *App) fetchProfile(ctx context.Context, accessToken string) (*githubProfile, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, a.apiBase+"/user", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+accessToken)
	req.Header.Set("Accept", "application/vnd.github+json")
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	resp, err := a.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("GET /user: status %d", resp.StatusCode)
	}
	var p githubProfile
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&p); err != nil {
		return nil, fmt.Errorf("decode /user: %w", err)
	}
	if p.ID == 0 || p.Login == "" {
		return nil, errors.New("GET /user: missing id or login")
	}
	return &p, nil
}

// --- persistence ---

func (a *App) upsertUser(ctx context.Context, p *githubProfile) (int64, error) {
	now := time.Now().Unix()
	var id int64
	err := a.db.QueryRowContext(ctx, `
		INSERT INTO users (github_id, login, name, email, avatar_url, profile_url, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(github_id) DO UPDATE SET
			login       = excluded.login,
			name        = excluded.name,
			email       = excluded.email,
			avatar_url  = excluded.avatar_url,
			profile_url = excluded.profile_url,
			updated_at  = excluded.updated_at
		RETURNING id`,
		p.ID, p.Login, p.Name, p.Email, p.AvatarURL, p.HTMLURL, now, now,
	).Scan(&id)
	return id, err
}

// createSession stores only a hash of the token, so a leaked database
// does not yield usable session cookies.
func (a *App) createSession(ctx context.Context, userID int64) (string, error) {
	token, err := randomToken()
	if err != nil {
		return "", err
	}
	now := time.Now()
	if _, err := a.db.ExecContext(ctx, `DELETE FROM sessions WHERE expires_at <= ?`, now.Unix()); err != nil {
		return "", err
	}
	_, err = a.db.ExecContext(ctx,
		`INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)`,
		hashToken(token), userID, now.Add(a.sessionTTL).Unix())
	return token, err
}

func (a *App) deleteSession(ctx context.Context, token string) error {
	_, err := a.db.ExecContext(ctx, `DELETE FROM sessions WHERE token_hash = ?`, hashToken(token))
	return err
}

// currentUser returns the logged-in user, or nil if there is no valid session.
func (a *App) currentUser(r *http.Request) (*User, error) {
	c, err := r.Cookie(sessionCookie)
	if err != nil || c.Value == "" {
		return nil, nil
	}
	var u User
	var created, updated int64
	err = a.db.QueryRowContext(r.Context(), `
		SELECT u.id, u.github_id, u.login, u.name, u.email, u.avatar_url, u.profile_url, u.created_at, u.updated_at
		FROM sessions s JOIN users u ON u.id = s.user_id
		WHERE s.token_hash = ? AND s.expires_at > ?`,
		hashToken(c.Value), time.Now().Unix(),
	).Scan(&u.ID, &u.GitHubID, &u.Login, &u.Name, &u.Email, &u.AvatarURL, &u.ProfileURL, &created, &updated)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	u.CreatedAt, u.UpdatedAt = time.Unix(created, 0).UTC(), time.Unix(updated, 0).UTC()
	return &u, nil
}

// --- helpers ---

// returnTo picks where to send the user after login: the "next" query
// parameter, else a same-origin Referer, else "/".
func (a *App) returnTo(r *http.Request) string {
	if next := r.URL.Query().Get("next"); next != "" {
		return safePath(next)
	}
	if ref, err := url.Parse(r.Referer()); err == nil && ref.Scheme+"://"+ref.Host == a.origin {
		p := safePath(ref.RequestURI())
		if !strings.HasPrefix(p, "/login") && !strings.HasPrefix(p, "/auth/") {
			return p
		}
	}
	return "/"
}

// safePath returns p if it is a local, path-absolute URL, otherwise "/".
// This prevents open redirects such as "//evil.com" or "https://evil.com".
func safePath(p string) string {
	if p == "" || p[0] != '/' || strings.HasPrefix(p, "//") || strings.Contains(p, `\`) {
		return "/"
	}
	for _, ch := range p {
		if ch < 0x20 || ch == 0x7f {
			return "/"
		}
	}
	u, err := url.Parse(p)
	if err != nil || u.Scheme != "" || u.Host != "" || u.User != nil {
		return "/"
	}
	return u.RequestURI()
}

func randomToken() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

func hashToken(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}

func (a *App) setCookie(w http.ResponseWriter, name, value string, ttl time.Duration) {
	http.SetCookie(w, &http.Cookie{
		Name:     name,
		Value:    value,
		Path:     "/",
		MaxAge:   int(ttl.Seconds()),
		HttpOnly: true,
		Secure:   a.secure,
		SameSite: http.SameSiteLaxMode, // Lax so the cookie survives the top-level redirect back from GitHub
	})
}

func (a *App) clearCookie(w http.ResponseWriter, name string) {
	http.SetCookie(w, &http.Cookie{
		Name:     name,
		Value:    "",
		Path:     "/",
		MaxAge:   -1,
		HttpOnly: true,
		Secure:   a.secure,
		SameSite: http.SameSiteLaxMode,
	})
}

func render(w http.ResponseWriter, t *template.Template, data any) {
	var buf bytes.Buffer
	if err := t.Execute(&buf, data); err != nil {
		log.Printf("render %s: %v", t.Name(), err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = buf.WriteTo(w)
}

// --- templates ---

var homeTmpl = template.Must(template.New("home").Parse(`<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>GitHub login demo</title></head>
<body>
<h1>GitHub login demo</h1>
{{if .}}
<p>Signed in as <strong>{{.Login}}</strong>. <a href="/profile">View your profile</a></p>
<form method="post" action="/logout"><button type="submit">Log out</button></form>
{{else}}
<p><a href="/login?next=/">Log in with GitHub</a></p>
{{end}}
</body>
</html>
`))

var profileTmpl = template.Must(template.New("profile").Parse(`<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{{.Login}} – profile</title></head>
<body>
<p><a href="/">Home</a></p>
<h1>Your profile</h1>
{{if .AvatarURL}}<img src="{{.AvatarURL}}" alt="Avatar of {{.Login}}" width="96" height="96">{{end}}
<dl>
  <dt>Username</dt><dd>{{.Login}}</dd>
  <dt>Name</dt><dd>{{if .Name}}{{.Name}}{{else}}<em>not set</em>{{end}}</dd>
  <dt>Email</dt><dd>{{if .Email}}{{.Email}}{{else}}<em>not public</em>{{end}}</dd>
  <dt>GitHub ID</dt><dd>{{.GitHubID}}</dd>
  {{if .ProfileURL}}<dt>GitHub page</dt><dd><a href="{{.ProfileURL}}" rel="noopener noreferrer">{{.ProfileURL}}</a></dd>{{end}}
  <dt>First login</dt><dd>{{.CreatedAt.Format "2006-01-02 15:04 MST"}}</dd>
  <dt>Last updated</dt><dd>{{.UpdatedAt.Format "2006-01-02 15:04 MST"}}</dd>
</dl>
<form method="post" action="/logout"><button type="submit">Log out</button></form>
</body>
</html>
`))
