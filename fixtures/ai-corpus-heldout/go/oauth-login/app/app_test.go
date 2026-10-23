package main

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// fakeGitHub stands in for github.com's token endpoint and api.github.com.
type fakeGitHub struct {
	srv          *httptest.Server
	mu           sync.Mutex
	profile      map[string]any
	lastVerifier string
}

func newFakeGitHub(t *testing.T) *fakeGitHub {
	t.Helper()
	f := &fakeGitHub{profile: map[string]any{
		"id":         42,
		"login":      "octocat",
		"name":       "The Octocat",
		"email":      "octo@example.com",
		"avatar_url": "https://avatars.example.com/u/42",
		"html_url":   "https://github.com/octocat",
	}}
	mux := http.NewServeMux()
	mux.HandleFunc("POST /login/oauth/access_token", func(w http.ResponseWriter, r *http.Request) {
		_ = r.ParseForm()
		w.Header().Set("Content-Type", "application/json")
		if r.Form.Get("code") != "good-code" || r.Form.Get("client_id") != "cid" ||
			r.Form.Get("client_secret") != "secret" || r.Form.Get("code_verifier") == "" {
			w.WriteHeader(http.StatusBadRequest)
			io.WriteString(w, `{"error":"bad_verification_code"}`)
			return
		}
		f.mu.Lock()
		f.lastVerifier = r.Form.Get("code_verifier")
		f.mu.Unlock()
		io.WriteString(w, `{"access_token":"gho_test","token_type":"bearer","scope":"read:user"}`)
	})
	mux.HandleFunc("GET /user", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer gho_test" {
			http.Error(w, `{"message":"Bad credentials"}`, http.StatusUnauthorized)
			return
		}
		f.mu.Lock()
		defer f.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(f.profile)
	})
	f.srv = httptest.NewServer(mux)
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeGitHub) setProfile(key string, value any) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.profile[key] = value
}

type testEnv struct {
	t      *testing.T
	app    *App
	srv    *httptest.Server
	gh     *fakeGitHub
	client *http.Client
}

func newTestEnv(t *testing.T) *testEnv {
	t.Helper()
	gh := newFakeGitHub(t)
	db, err := OpenDB(filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })

	// The app needs its own URL for the redirect_uri, so start the server first.
	var handler http.Handler
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		handler.ServeHTTP(w, r)
	}))
	t.Cleanup(srv.Close)

	app, err := NewApp(Config{
		ClientID:     "cid",
		ClientSecret: "secret",
		BaseURL:      srv.URL,
		AuthURL:      gh.srv.URL + "/login/oauth/authorize",
		TokenURL:     gh.srv.URL + "/login/oauth/access_token",
		APIBaseURL:   gh.srv.URL,
	}, db)
	if err != nil {
		t.Fatal(err)
	}
	handler = app.Routes()

	return &testEnv{t: t, app: app, srv: srv, gh: gh, client: newClient(t)}
}

func newClient(t *testing.T) *http.Client {
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	return &http.Client{
		Jar:           jar,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
}

func (e *testEnv) do(c *http.Client, method, path string, header http.Header) *http.Response {
	e.t.Helper()
	req, err := http.NewRequest(method, e.srv.URL+path, nil)
	if err != nil {
		e.t.Fatal(err)
	}
	for k, v := range header {
		req.Header[k] = v
	}
	resp, err := c.Do(req)
	if err != nil {
		e.t.Fatal(err)
	}
	e.t.Cleanup(func() { resp.Body.Close() })
	return resp
}

func (e *testEnv) get(path string) *http.Response { return e.do(e.client, "GET", path, nil) }

// startLogin hits /login and returns the parsed GitHub authorize URL.
func (e *testEnv) startLogin(path string, header http.Header) *url.URL {
	e.t.Helper()
	resp := e.do(e.client, "GET", path, header)
	if resp.StatusCode != http.StatusFound {
		e.t.Fatalf("GET %s: status %d, want 302", path, resp.StatusCode)
	}
	u, err := url.Parse(resp.Header.Get("Location"))
	if err != nil {
		e.t.Fatal(err)
	}
	return u
}

// login runs the whole flow and returns the callback response.
func (e *testEnv) login(next string) *http.Response {
	e.t.Helper()
	auth := e.startLogin("/login?next="+url.QueryEscape(next), nil)
	return e.get("/auth/callback?code=good-code&state=" + url.QueryEscape(auth.Query().Get("state")))
}

func (e *testEnv) count(table string) int {
	e.t.Helper()
	var n int
	if err := e.app.db.QueryRow("SELECT COUNT(*) FROM " + table).Scan(&n); err != nil {
		e.t.Fatal(err)
	}
	return n
}

func readBody(t *testing.T, resp *http.Response) string {
	t.Helper()
	b, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestLoginRedirectsToGitHub(t *testing.T) {
	e := newTestEnv(t)
	u := e.startLogin("/login", nil)

	if got, want := u.Scheme+"://"+u.Host+u.Path, e.gh.srv.URL+"/login/oauth/authorize"; got != want {
		t.Errorf("authorize URL = %s, want %s", got, want)
	}
	q := u.Query()
	checks := map[string]string{
		"client_id":             "cid",
		"redirect_uri":          e.srv.URL + "/auth/callback",
		"response_type":         "code",
		"scope":                 "read:user",
		"code_challenge_method": "S256",
	}
	for k, want := range checks {
		if got := q.Get(k); got != want {
			t.Errorf("%s = %q, want %q", k, got, want)
		}
	}
	if len(q.Get("state")) < 32 {
		t.Errorf("state %q is too short", q.Get("state"))
	}
	if q.Get("code_challenge") == "" {
		t.Error("missing code_challenge")
	}
}

func TestFullLoginFlow(t *testing.T) {
	e := newTestEnv(t)
	auth := e.startLogin("/login?next="+url.QueryEscape("/profile?tab=1"), nil)
	resp := e.get("/auth/callback?code=good-code&state=" + url.QueryEscape(auth.Query().Get("state")))

	if resp.StatusCode != http.StatusFound || resp.Header.Get("Location") != "/profile?tab=1" {
		t.Fatalf("callback: status %d location %q, want 302 /profile?tab=1", resp.StatusCode, resp.Header.Get("Location"))
	}

	// PKCE: the verifier sent to the token endpoint must match the challenge.
	sum := sha256.Sum256([]byte(e.gh.lastVerifier))
	if got := base64.RawURLEncoding.EncodeToString(sum[:]); got != auth.Query().Get("code_challenge") {
		t.Errorf("code_verifier does not match code_challenge")
	}

	var sess *http.Cookie
	for _, c := range resp.Cookies() {
		if c.Name == sessionCookie {
			sess = c
		}
	}
	if sess == nil || !sess.HttpOnly || sess.SameSite != http.SameSiteLaxMode {
		t.Fatalf("session cookie missing or not HttpOnly/SameSite=Lax: %+v", sess)
	}

	resp = e.get("/profile")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("profile: status %d, want 200", resp.StatusCode)
	}
	body := readBody(t, resp)
	for _, want := range []string{"octocat", "The Octocat", "octo@example.com", "https://avatars.example.com/u/42"} {
		if !strings.Contains(body, want) {
			t.Errorf("profile page missing %q", want)
		}
	}
	if e.count("users") != 1 || e.count("sessions") != 1 {
		t.Errorf("users=%d sessions=%d, want 1 and 1", e.count("users"), e.count("sessions"))
	}
}

func TestLoginUpdatesExistingUser(t *testing.T) {
	e := newTestEnv(t)
	e.login("/")
	e.gh.setProfile("name", "Renamed Cat")
	e.gh.setProfile("login", "octocat2")
	e.login("/")

	if n := e.count("users"); n != 1 {
		t.Fatalf("users = %d, want 1 (same GitHub id must update, not insert)", n)
	}
	if n := e.count("sessions"); n != 1 {
		t.Errorf("sessions = %d, want 1 (old session should be replaced)", n)
	}
	var login, name string
	e.app.db.QueryRow("SELECT login, name FROM users WHERE github_id = 42").Scan(&login, &name)
	if login != "octocat2" || name != "Renamed Cat" {
		t.Errorf("user = %q/%q, want octocat2/Renamed Cat", login, name)
	}
}

func TestCallbackRejectsBadState(t *testing.T) {
	e := newTestEnv(t)
	e.startLogin("/login", nil)
	resp := e.get("/auth/callback?code=good-code&state=forged")
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status %d, want 400", resp.StatusCode)
	}
	if e.count("users") != 0 || e.count("sessions") != 0 {
		t.Error("bad state must not create a user or session")
	}
}

func TestCallbackStateIsSingleUse(t *testing.T) {
	e := newTestEnv(t)
	auth := e.startLogin("/login", nil)
	path := "/auth/callback?code=good-code&state=" + url.QueryEscape(auth.Query().Get("state"))
	if resp := e.get(path); resp.StatusCode != http.StatusFound {
		t.Fatalf("first callback: status %d, want 302", resp.StatusCode)
	}
	if resp := e.get(path); resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("replayed callback: status %d, want 400", resp.StatusCode)
	}
}

func TestCallbackWithoutLoginCookie(t *testing.T) {
	e := newTestEnv(t)
	resp := e.get("/auth/callback?code=good-code&state=anything")
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status %d, want 400", resp.StatusCode)
	}
}

func TestCallbackTokenExchangeFailure(t *testing.T) {
	e := newTestEnv(t)
	auth := e.startLogin("/login", nil)
	resp := e.get("/auth/callback?code=bad-code&state=" + url.QueryEscape(auth.Query().Get("state")))
	if resp.StatusCode != http.StatusBadGateway {
		t.Fatalf("status %d, want 502", resp.StatusCode)
	}
	if e.count("sessions") != 0 {
		t.Error("failed exchange must not create a session")
	}
}

func TestCallbackGitHubError(t *testing.T) {
	e := newTestEnv(t)
	auth := e.startLogin("/login", nil)
	resp := e.get("/auth/callback?error=access_denied&state=" + url.QueryEscape(auth.Query().Get("state")))
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status %d, want 400", resp.StatusCode)
	}
}

func TestProfileRequiresLogin(t *testing.T) {
	e := newTestEnv(t)
	resp := e.get("/profile")
	if resp.StatusCode != http.StatusFound || resp.Header.Get("Location") != "/login?next=%2Fprofile" {
		t.Fatalf("status %d location %q, want 302 /login?next=%%2Fprofile", resp.StatusCode, resp.Header.Get("Location"))
	}
}

func TestReturnsToProfileAfterBeingSentToLogin(t *testing.T) {
	e := newTestEnv(t)
	loginPath := e.get("/profile").Header.Get("Location")
	auth := e.startLogin(loginPath, nil)
	resp := e.get("/auth/callback?code=good-code&state=" + url.QueryEscape(auth.Query().Get("state")))
	if loc := resp.Header.Get("Location"); loc != "/profile" {
		t.Fatalf("location %q, want /profile", loc)
	}
}

func TestReturnToFromReferer(t *testing.T) {
	cases := []struct {
		name, referer, want string
	}{
		{"same origin", "/profile", "/profile"},
		{"other origin", "https://evil.example/profile", "/"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			e := newTestEnv(t)
			ref := tc.referer
			if strings.HasPrefix(ref, "/") {
				ref = e.srv.URL + ref
			}
			auth := e.startLogin("/login", http.Header{"Referer": {ref}})
			resp := e.get("/auth/callback?code=good-code&state=" + url.QueryEscape(auth.Query().Get("state")))
			if loc := resp.Header.Get("Location"); loc != tc.want {
				t.Fatalf("location %q, want %q", loc, tc.want)
			}
		})
	}
}

func TestOpenRedirectIsBlocked(t *testing.T) {
	for _, next := range []string{"//evil.example", "https://evil.example/", `/\evil.example`} {
		e := newTestEnv(t)
		resp := e.login(next)
		if loc := resp.Header.Get("Location"); loc != "/" {
			t.Errorf("next=%q redirected to %q, want /", next, loc)
		}
	}
}

func TestSafePath(t *testing.T) {
	cases := map[string]string{
		"":                       "/",
		"/":                      "/",
		"/profile":               "/profile",
		"/profile?tab=1":         "/profile?tab=1",
		"profile":                "/",
		"//evil.example":         "/",
		"///evil.example":        "/",
		`/\evil.example`:         "/",
		"https://evil.example":   "/",
		"javascript:alert(1)":    "/",
		"/\t/evil.example":       "/",
		"/ok\r\nSet-Cookie: x=1": "/",
	}
	for in, want := range cases {
		if got := safePath(in); got != want {
			t.Errorf("safePath(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestLogout(t *testing.T) {
	e := newTestEnv(t)
	e.login("/")

	resp := e.do(e.client, "POST", "/logout", nil)
	if resp.StatusCode != http.StatusSeeOther || resp.Header.Get("Location") != "/" {
		t.Fatalf("logout: status %d location %q, want 303 /", resp.StatusCode, resp.Header.Get("Location"))
	}
	if n := e.count("sessions"); n != 0 {
		t.Errorf("sessions = %d after logout, want 0", n)
	}
	if resp := e.get("/profile"); resp.StatusCode != http.StatusFound {
		t.Errorf("profile after logout: status %d, want 302", resp.StatusCode)
	}
}

func TestLogoutRejectsGETAndCrossOrigin(t *testing.T) {
	e := newTestEnv(t)
	e.login("/")

	if resp := e.get("/logout"); resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("GET /logout: status %d, want 405", resp.StatusCode)
	}
	resp := e.do(e.client, "POST", "/logout", http.Header{"Origin": {"https://evil.example"}})
	if resp.StatusCode != http.StatusForbidden {
		t.Errorf("cross-origin logout: status %d, want 403", resp.StatusCode)
	}
	if resp := e.get("/profile"); resp.StatusCode != http.StatusOK {
		t.Errorf("session should survive rejected logouts, profile status %d", resp.StatusCode)
	}
}

func TestExpiredSessionIsRejected(t *testing.T) {
	e := newTestEnv(t)
	e.login("/")
	if _, err := e.app.db.Exec("UPDATE sessions SET expires_at = 0"); err != nil {
		t.Fatal(err)
	}
	if resp := e.get("/profile"); resp.StatusCode != http.StatusFound {
		t.Fatalf("profile with expired session: status %d, want 302", resp.StatusCode)
	}
}

func TestSessionTokenNotStoredInPlaintext(t *testing.T) {
	e := newTestEnv(t)
	resp := e.login("/")
	var token string
	for _, c := range resp.Cookies() {
		if c.Name == sessionCookie {
			token = c.Value
		}
	}
	var stored string
	e.app.db.QueryRow("SELECT token_hash FROM sessions").Scan(&stored)
	if token == "" || stored == token || stored != hashToken(token) {
		t.Fatalf("session should be stored as hash of cookie token")
	}
}

func TestProfileEscapesHTML(t *testing.T) {
	e := newTestEnv(t)
	e.gh.setProfile("name", "<script>alert(1)</script>")
	e.login("/")
	body := readBody(t, e.get("/profile"))
	if strings.Contains(body, "<script>alert(1)</script>") {
		t.Fatal("profile name was not HTML-escaped")
	}
}

func TestHomePage(t *testing.T) {
	e := newTestEnv(t)
	if body := readBody(t, e.get("/")); !strings.Contains(body, "Log in with GitHub") {
		t.Error("anonymous home page should offer login")
	}
	e.login("/")
	if body := readBody(t, e.get("/")); !strings.Contains(body, "octocat") {
		t.Error("home page should show the signed-in user")
	}
	if resp := e.get("/nope"); resp.StatusCode != http.StatusNotFound {
		t.Errorf("unknown path: status %d, want 404", resp.StatusCode)
	}
}
