package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"testing"
	"time"

	"golang.org/x/crypto/bcrypt"
)

func newTestServer(t *testing.T) *Server {
	t.Helper()
	store, err := OpenStore(filepath.Join(t.TempDir(), "test.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { store.Close() })
	s, err := NewServer(store, bcrypt.MinCost)
	if err != nil {
		t.Fatalf("new server: %v", err)
	}
	return s
}

func call(t *testing.T, s *Server, method, path, token string, body any) (int, map[string]any) {
	t.Helper()
	var rdr io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		rdr = bytes.NewReader(b)
	}
	req := httptest.NewRequest(method, path, rdr)
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	rec := httptest.NewRecorder()
	s.ServeHTTP(rec, req)
	var out map[string]any
	if rec.Body.Len() > 0 {
		if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
			t.Fatalf("%s %s: decode response %q: %v", method, path, rec.Body.String(), err)
		}
	}
	return rec.Code, out
}

func mustStatus(t *testing.T, want, got int, out map[string]any) {
	t.Helper()
	if got != want {
		t.Fatalf("status = %d, want %d (body: %v)", got, want, out)
	}
}

const password = "correct horse battery"

func signup(t *testing.T, s *Server, org, email string) (token string, userID int64) {
	t.Helper()
	code, out := call(t, s, "POST", "/signup", "", map[string]string{
		"org_name": org, "email": email, "password": password,
	})
	mustStatus(t, http.StatusCreated, code, out)
	return out["token"].(string), int64(out["user"].(map[string]any)["id"].(float64))
}

func invite(t *testing.T, s *Server, adminToken, email, role string) (token string, userID int64) {
	t.Helper()
	code, out := call(t, s, "POST", "/invites", adminToken, map[string]string{"email": email, "role": role})
	mustStatus(t, http.StatusCreated, code, out)
	code, out = call(t, s, "POST", "/invites/accept", "", map[string]string{
		"token": out["invite_token"].(string), "password": password,
	})
	mustStatus(t, http.StatusCreated, code, out)
	return out["token"].(string), int64(out["user"].(map[string]any)["id"].(float64))
}

func createNote(t *testing.T, s *Server, token, title, body string) int64 {
	t.Helper()
	code, out := call(t, s, "POST", "/notes", token, map[string]string{"title": title, "body": body})
	mustStatus(t, http.StatusCreated, code, out)
	return int64(out["id"].(float64))
}

func listNotes(t *testing.T, s *Server, token, query string) []any {
	t.Helper()
	path := "/notes"
	if query != "" {
		path += "?q=" + url.QueryEscape(query)
	}
	code, out := call(t, s, "GET", path, token, nil)
	mustStatus(t, http.StatusOK, code, out)
	return out["notes"].([]any)
}

func TestSignupAndLogin(t *testing.T) {
	s := newTestServer(t)
	signup(t, s, "Acme", "Alice@Example.com")

	code, out := call(t, s, "POST", "/login", "", map[string]string{"email": "alice@example.com", "password": password})
	mustStatus(t, http.StatusOK, code, out)
	token := out["token"].(string)

	code, out = call(t, s, "GET", "/me", token, nil)
	mustStatus(t, http.StatusOK, code, out)
	if out["email"] != "alice@example.com" || out["role"] != "admin" {
		t.Fatalf("unexpected /me: %v", out)
	}

	code, out = call(t, s, "POST", "/login", "", map[string]string{"email": "alice@example.com", "password": "wrong-password"})
	mustStatus(t, http.StatusUnauthorized, code, out)
	code, out = call(t, s, "POST", "/login", "", map[string]string{"email": "nobody@example.com", "password": password})
	mustStatus(t, http.StatusUnauthorized, code, out)

	code, out = call(t, s, "POST", "/signup", "", map[string]string{"org_name": "Other", "email": "alice@example.com", "password": password})
	mustStatus(t, http.StatusConflict, code, out)
}

func TestSignupValidation(t *testing.T) {
	s := newTestServer(t)
	cases := []map[string]string{
		{"org_name": "", "email": "a@example.com", "password": password},
		{"org_name": "X", "email": "not-an-email", "password": password},
		{"org_name": "X", "email": "a@example.com", "password": "short"},
	}
	for _, c := range cases {
		code, out := call(t, s, "POST", "/signup", "", c)
		mustStatus(t, http.StatusBadRequest, code, out)
	}
	code, out := call(t, s, "POST", "/signup", "", map[string]any{"org_name": "X", "email": "a@example.com", "password": password, "role": "admin"})
	mustStatus(t, http.StatusBadRequest, code, out)
}

func TestAuthRequired(t *testing.T) {
	s := newTestServer(t)
	code, out := call(t, s, "GET", "/notes", "", nil)
	mustStatus(t, http.StatusUnauthorized, code, out)
	code, out = call(t, s, "GET", "/notes", "bogus", nil)
	mustStatus(t, http.StatusUnauthorized, code, out)
}

func TestLogoutAndSessionExpiry(t *testing.T) {
	s := newTestServer(t)
	token, _ := signup(t, s, "Acme", "alice@example.com")

	code, out := call(t, s, "POST", "/logout", token, nil)
	mustStatus(t, http.StatusNoContent, code, out)
	code, out = call(t, s, "GET", "/me", token, nil)
	mustStatus(t, http.StatusUnauthorized, code, out)

	code, out = call(t, s, "POST", "/login", "", map[string]string{"email": "alice@example.com", "password": password})
	mustStatus(t, http.StatusOK, code, out)
	token = out["token"].(string)
	s.now = func() time.Time { return time.Now().Add(sessionTTL + time.Minute) }
	code, out = call(t, s, "GET", "/me", token, nil)
	mustStatus(t, http.StatusUnauthorized, code, out)
}

func TestRolePermissions(t *testing.T) {
	s := newTestServer(t)
	admin, _ := signup(t, s, "Acme", "admin@example.com")
	editor, _ := invite(t, s, admin, "editor@example.com", RoleEditor)
	viewer, _ := invite(t, s, admin, "viewer@example.com", RoleViewer)

	// Editors have full CRUD on notes.
	id := createNote(t, s, editor, "Plan", "draft")
	notePath := fmt.Sprintf("/notes/%d", id)
	code, out := call(t, s, "PUT", notePath, editor, map[string]string{"title": "Plan v2", "body": "final"})
	mustStatus(t, http.StatusOK, code, out)
	if out["title"] != "Plan v2" || out["body"] != "final" {
		t.Fatalf("update not applied: %v", out)
	}

	// Viewers can read but not write.
	code, out = call(t, s, "GET", notePath, viewer, nil)
	mustStatus(t, http.StatusOK, code, out)
	if len(listNotes(t, s, viewer, "")) != 1 {
		t.Fatal("viewer should see the note")
	}
	code, out = call(t, s, "POST", "/notes", viewer, map[string]string{"title": "x", "body": "y"})
	mustStatus(t, http.StatusForbidden, code, out)
	code, out = call(t, s, "PUT", notePath, viewer, map[string]string{"title": "x", "body": "y"})
	mustStatus(t, http.StatusForbidden, code, out)
	code, out = call(t, s, "DELETE", notePath, viewer, nil)
	mustStatus(t, http.StatusForbidden, code, out)

	// Only admins manage users.
	for _, tok := range []string{editor, viewer} {
		code, out = call(t, s, "POST", "/invites", tok, map[string]string{"email": "x@example.com", "role": "viewer"})
		mustStatus(t, http.StatusForbidden, code, out)
		code, out = call(t, s, "GET", "/users", tok, nil)
		mustStatus(t, http.StatusForbidden, code, out)
		code, out = call(t, s, "PATCH", "/users/1", tok, map[string]string{"role": "admin"})
		mustStatus(t, http.StatusForbidden, code, out)
	}

	// Admins can write notes too.
	code, out = call(t, s, "DELETE", notePath, admin, nil)
	mustStatus(t, http.StatusNoContent, code, out)
	code, out = call(t, s, "GET", notePath, editor, nil)
	mustStatus(t, http.StatusNotFound, code, out)
}

func TestTenantIsolation(t *testing.T) {
	s := newTestServer(t)
	adminA, adminAID := signup(t, s, "Acme", "a@acme.test")
	adminB, _ := signup(t, s, "Globex", "b@globex.test")

	id := createNote(t, s, adminA, "Secret plan", "secret contents")
	notePath := fmt.Sprintf("/notes/%d", id)

	code, out := call(t, s, "GET", notePath, adminB, nil)
	mustStatus(t, http.StatusNotFound, code, out)
	code, out = call(t, s, "PUT", notePath, adminB, map[string]string{"title": "pwned", "body": ""})
	mustStatus(t, http.StatusNotFound, code, out)
	code, out = call(t, s, "DELETE", notePath, adminB, nil)
	mustStatus(t, http.StatusNotFound, code, out)
	if n := len(listNotes(t, s, adminB, "")); n != 0 {
		t.Fatalf("org B sees %d notes from org A", n)
	}
	if n := len(listNotes(t, s, adminB, "secret")); n != 0 {
		t.Fatalf("org B search finds %d notes from org A", n)
	}

	// Org B's admin cannot see or change org A's users.
	code, out = call(t, s, "PATCH", fmt.Sprintf("/users/%d", adminAID), adminB, map[string]string{"role": "viewer"})
	mustStatus(t, http.StatusNotFound, code, out)
	code, out = call(t, s, "GET", "/users", adminB, nil)
	mustStatus(t, http.StatusOK, code, out)
	if users := out["users"].([]any); len(users) != 1 {
		t.Fatalf("org B lists %d users, want 1", len(users))
	}

	code, out = call(t, s, "GET", notePath, adminA, nil)
	mustStatus(t, http.StatusOK, code, out)
	if out["title"] != "Secret plan" {
		t.Fatalf("org A note was modified: %v", out)
	}
}

func TestInviteFlow(t *testing.T) {
	s := newTestServer(t)
	admin, _ := signup(t, s, "Acme", "admin@example.com")

	code, out := call(t, s, "POST", "/invites", admin, map[string]string{"email": "new@example.com", "role": "superuser"})
	mustStatus(t, http.StatusBadRequest, code, out)
	code, out = call(t, s, "POST", "/invites", admin, map[string]string{"email": "admin@example.com", "role": "viewer"})
	mustStatus(t, http.StatusConflict, code, out)

	code, out = call(t, s, "POST", "/invites", admin, map[string]string{"email": "new@example.com", "role": "editor"})
	mustStatus(t, http.StatusCreated, code, out)
	inviteToken := out["invite_token"].(string)

	code, out = call(t, s, "POST", "/invites/accept", "", map[string]string{"token": inviteToken, "password": password})
	mustStatus(t, http.StatusCreated, code, out)
	user := out["user"].(map[string]any)
	if user["role"] != "editor" || user["email"] != "new@example.com" {
		t.Fatalf("unexpected invited user: %v", user)
	}

	// Invites are single use.
	code, out = call(t, s, "POST", "/invites/accept", "", map[string]string{"token": inviteToken, "password": password})
	mustStatus(t, http.StatusNotFound, code, out)

	code, out = call(t, s, "POST", "/login", "", map[string]string{"email": "new@example.com", "password": password})
	mustStatus(t, http.StatusOK, code, out)

	// Invites expire.
	code, out = call(t, s, "POST", "/invites", admin, map[string]string{"email": "late@example.com", "role": "viewer"})
	mustStatus(t, http.StatusCreated, code, out)
	lateToken := out["invite_token"].(string)
	s.now = func() time.Time { return time.Now().Add(inviteTTL + time.Minute) }
	code, out = call(t, s, "POST", "/invites/accept", "", map[string]string{"token": lateToken, "password": password})
	mustStatus(t, http.StatusNotFound, code, out)
}

func TestChangeRole(t *testing.T) {
	s := newTestServer(t)
	admin, adminID := signup(t, s, "Acme", "admin@example.com")
	viewer, viewerID := invite(t, s, admin, "viewer@example.com", RoleViewer)

	code, out := call(t, s, "POST", "/notes", viewer, map[string]string{"title": "x", "body": ""})
	mustStatus(t, http.StatusForbidden, code, out)

	code, out = call(t, s, "PATCH", fmt.Sprintf("/users/%d", viewerID), admin, map[string]string{"role": "editor"})
	mustStatus(t, http.StatusOK, code, out)
	// The existing session picks up the new role immediately.
	createNote(t, s, viewer, "x", "")

	code, out = call(t, s, "PATCH", fmt.Sprintf("/users/%d", viewerID), admin, map[string]string{"role": "owner"})
	mustStatus(t, http.StatusBadRequest, code, out)

	// The last admin cannot demote themselves...
	code, out = call(t, s, "PATCH", fmt.Sprintf("/users/%d", adminID), admin, map[string]string{"role": "viewer"})
	mustStatus(t, http.StatusConflict, code, out)

	// ...but can once another admin exists.
	code, out = call(t, s, "PATCH", fmt.Sprintf("/users/%d", viewerID), admin, map[string]string{"role": "admin"})
	mustStatus(t, http.StatusOK, code, out)
	code, out = call(t, s, "PATCH", fmt.Sprintf("/users/%d", adminID), admin, map[string]string{"role": "viewer"})
	mustStatus(t, http.StatusOK, code, out)
	code, out = call(t, s, "GET", "/users", admin, nil)
	mustStatus(t, http.StatusForbidden, code, out)
}

func TestListAndSearch(t *testing.T) {
	s := newTestServer(t)
	admin, _ := signup(t, s, "Acme", "admin@example.com")
	createNote(t, s, admin, "Shopping list", "Milk and eggs")
	createNote(t, s, admin, "Budget", "Spent 100% of it")
	createNote(t, s, admin, "Ideas", "snake_case everywhere")

	if n := len(listNotes(t, s, admin, "")); n != 3 {
		t.Fatalf("list returned %d notes, want 3", n)
	}
	if notes := listNotes(t, s, admin, "MILK"); len(notes) != 1 || notes[0].(map[string]any)["title"] != "Shopping list" {
		t.Fatalf("case-insensitive body search failed: %v", notes)
	}
	if n := len(listNotes(t, s, admin, "budg")); n != 1 {
		t.Fatalf("title search returned %d notes, want 1", n)
	}
	// LIKE wildcards in the query are matched literally.
	if notes := listNotes(t, s, admin, "%"); len(notes) != 1 || notes[0].(map[string]any)["title"] != "Budget" {
		t.Fatalf("%% search should only match the literal percent: %v", notes)
	}
	if notes := listNotes(t, s, admin, "_"); len(notes) != 1 || notes[0].(map[string]any)["title"] != "Ideas" {
		t.Fatalf("_ search should only match the literal underscore: %v", notes)
	}
	if n := len(listNotes(t, s, admin, "nothing matches this")); n != 0 {
		t.Fatalf("search returned %d notes, want 0", n)
	}

	code, out := call(t, s, "GET", "/notes?limit=2", admin, nil)
	mustStatus(t, http.StatusOK, code, out)
	if n := len(out["notes"].([]any)); n != 2 {
		t.Fatalf("limit=2 returned %d notes", n)
	}
	code, out = call(t, s, "GET", "/notes?limit=2&offset=2", admin, nil)
	mustStatus(t, http.StatusOK, code, out)
	if n := len(out["notes"].([]any)); n != 1 {
		t.Fatalf("offset=2 returned %d notes", n)
	}
	code, out = call(t, s, "GET", "/notes?limit=1000", admin, nil)
	mustStatus(t, http.StatusBadRequest, code, out)
}

func TestNoteValidation(t *testing.T) {
	s := newTestServer(t)
	admin, _ := signup(t, s, "Acme", "admin@example.com")
	code, out := call(t, s, "POST", "/notes", admin, map[string]string{"title": "   ", "body": "x"})
	mustStatus(t, http.StatusBadRequest, code, out)
	code, out = call(t, s, "GET", "/notes/abc", admin, nil)
	mustStatus(t, http.StatusNotFound, code, out)
	code, out = call(t, s, "PUT", "/notes/999", admin, map[string]string{"title": "x", "body": "y"})
	mustStatus(t, http.StatusNotFound, code, out)
}
