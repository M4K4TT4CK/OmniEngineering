package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func newTestApp(t *testing.T) (*App, http.Handler) {
	t.Helper()
	db, err := sql.Open("sqlite", "file::memory:?_pragma=foreign_keys(1)")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	app, err := NewApp(db)
	if err != nil {
		t.Fatal(err)
	}
	return app, app.Routes()
}

func do(t *testing.T, h http.Handler, method, path, token string, body any) *httptest.ResponseRecorder {
	t.Helper()
	var buf bytes.Buffer
	if body != nil {
		json.NewEncoder(&buf).Encode(body)
	}
	req := httptest.NewRequest(method, path, &buf)
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func decode[T any](t *testing.T, rec *httptest.ResponseRecorder) T {
	t.Helper()
	var v T
	if err := json.Unmarshal(rec.Body.Bytes(), &v); err != nil {
		t.Fatalf("decode %q: %v", rec.Body.String(), err)
	}
	return v
}

func signup(t *testing.T, h http.Handler, user string) string {
	t.Helper()
	rec := do(t, h, "POST", "/signup", "", credentials{user, "password123"})
	if rec.Code != http.StatusCreated {
		t.Fatalf("signup %s: %d %s", user, rec.Code, rec.Body)
	}
	return decode[map[string]any](t, rec)["token"].(string)
}

func expect(t *testing.T, rec *httptest.ResponseRecorder, code int) {
	t.Helper()
	if rec.Code != code {
		t.Fatalf("status = %d, want %d; body: %s", rec.Code, code, rec.Body)
	}
}

func TestSignupAndLogin(t *testing.T) {
	_, h := newTestApp(t)
	signup(t, h, "alice")

	expect(t, do(t, h, "POST", "/signup", "", credentials{"ALICE", "password123"}), http.StatusConflict)
	expect(t, do(t, h, "POST", "/signup", "", credentials{"x", "password123"}), http.StatusBadRequest)
	expect(t, do(t, h, "POST", "/signup", "", credentials{"bobby", "short"}), http.StatusBadRequest)

	rec := do(t, h, "POST", "/login", "", credentials{"alice", "password123"})
	expect(t, rec, http.StatusOK)
	tok := decode[map[string]any](t, rec)["token"].(string)
	expect(t, do(t, h, "GET", "/notes", tok, nil), http.StatusOK)

	expect(t, do(t, h, "POST", "/login", "", credentials{"alice", "wrongpass1"}), http.StatusUnauthorized)
	expect(t, do(t, h, "POST", "/login", "", credentials{"nobody", "password123"}), http.StatusUnauthorized)
}

func TestAuthRequired(t *testing.T) {
	_, h := newTestApp(t)
	expect(t, do(t, h, "GET", "/notes", "", nil), http.StatusUnauthorized)
	expect(t, do(t, h, "GET", "/notes", "bogus", nil), http.StatusUnauthorized)
}

func TestLogoutRevokesToken(t *testing.T) {
	_, h := newTestApp(t)
	tok := signup(t, h, "alice")
	expect(t, do(t, h, "POST", "/logout", tok, nil), http.StatusNoContent)
	expect(t, do(t, h, "GET", "/notes", tok, nil), http.StatusUnauthorized)
}

func TestTokenExpiry(t *testing.T) {
	app, h := newTestApp(t)
	tok := signup(t, h, "alice")
	app.now = func() time.Time { return time.Now().Add(tokenTTL + time.Minute) }
	expect(t, do(t, h, "GET", "/notes", tok, nil), http.StatusUnauthorized)
}

func TestNoteCRUD(t *testing.T) {
	_, h := newTestApp(t)
	tok := signup(t, h, "alice")

	rec := do(t, h, "POST", "/notes", tok, noteInput{"Groceries", "milk, eggs"})
	expect(t, rec, http.StatusCreated)
	n := decode[Note](t, rec)
	path := fmt.Sprintf("/notes/%d", n.ID)

	rec = do(t, h, "GET", path, tok, nil)
	expect(t, rec, http.StatusOK)
	if got := decode[Note](t, rec); got.Title != "Groceries" || got.Body != "milk, eggs" {
		t.Fatalf("unexpected note: %+v", got)
	}

	rec = do(t, h, "PUT", path, tok, noteInput{"Groceries v2", "milk"})
	expect(t, rec, http.StatusOK)
	if got := decode[Note](t, rec); got.Title != "Groceries v2" || got.Body != "milk" || got.ID != n.ID {
		t.Fatalf("unexpected update: %+v", got)
	}

	rec = do(t, h, "GET", "/notes", tok, nil)
	expect(t, rec, http.StatusOK)
	if list := decode[[]Note](t, rec); len(list) != 1 {
		t.Fatalf("list len = %d, want 1", len(list))
	}

	expect(t, do(t, h, "DELETE", path, tok, nil), http.StatusNoContent)
	expect(t, do(t, h, "GET", path, tok, nil), http.StatusNotFound)
	expect(t, do(t, h, "DELETE", path, tok, nil), http.StatusNotFound)
}

func TestNoteValidation(t *testing.T) {
	_, h := newTestApp(t)
	tok := signup(t, h, "alice")
	expect(t, do(t, h, "POST", "/notes", tok, noteInput{"  ", "x"}), http.StatusBadRequest)
	expect(t, do(t, h, "PUT", "/notes/abc", tok, noteInput{"t", "b"}), http.StatusNotFound)
	req := httptest.NewRequest("POST", "/notes", bytes.NewBufferString(`{"title":"a","extra":1}`))
	req.Header.Set("Authorization", "Bearer "+tok)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	expect(t, rec, http.StatusBadRequest)
}

func TestSearch(t *testing.T) {
	_, h := newTestApp(t)
	tok := signup(t, h, "alice")
	do(t, h, "POST", "/notes", tok, noteInput{"Shopping", "buy apples"})
	do(t, h, "POST", "/notes", tok, noteInput{"Work", "finish report"})
	do(t, h, "POST", "/notes", tok, noteInput{"100% done", "literal percent"})

	search := func(q string) []Note {
		rec := do(t, h, "GET", "/notes?q="+q, tok, nil)
		expect(t, rec, http.StatusOK)
		return decode[[]Note](t, rec)
	}
	if got := search("apple"); len(got) != 1 || got[0].Title != "Shopping" {
		t.Fatalf("body search: %+v", got)
	}
	if got := search("WORK"); len(got) != 1 || got[0].Title != "Work" {
		t.Fatalf("title search: %+v", got)
	}
	if got := search("%25"); len(got) != 1 || got[0].Title != "100% done" {
		t.Fatalf("wildcards must be escaped: %+v", got)
	}
	if got := search("zzz"); len(got) != 0 {
		t.Fatalf("expected no results: %+v", got)
	}

	rec := do(t, h, "GET", "/notes?limit=1", tok, nil)
	if got := decode[[]Note](t, rec); len(got) != 1 {
		t.Fatalf("limit: %+v", got)
	}
	expect(t, do(t, h, "GET", "/notes?limit=0", tok, nil), http.StatusBadRequest)
}

func TestUsersAreIsolated(t *testing.T) {
	_, h := newTestApp(t)
	alice := signup(t, h, "alice")
	bob := signup(t, h, "bobby")

	n := decode[Note](t, do(t, h, "POST", "/notes", alice, noteInput{"secret", "alice only"}))
	path := fmt.Sprintf("/notes/%d", n.ID)

	expect(t, do(t, h, "GET", path, bob, nil), http.StatusNotFound)
	expect(t, do(t, h, "PUT", path, bob, noteInput{"hacked", "x"}), http.StatusNotFound)
	expect(t, do(t, h, "DELETE", path, bob, nil), http.StatusNotFound)
	if list := decode[[]Note](t, do(t, h, "GET", "/notes?q=secret", bob, nil)); len(list) != 0 {
		t.Fatalf("bob sees alice's notes: %+v", list)
	}
	rec := do(t, h, "GET", path, alice, nil)
	expect(t, rec, http.StatusOK)
	if got := decode[Note](t, rec); got.Title != "secret" {
		t.Fatalf("alice's note was modified: %+v", got)
	}
}

func TestRateLimitPerUser(t *testing.T) {
	app, h := newTestApp(t)
	clock := time.Now()
	app.now = func() time.Time { return clock }
	app.userLim.now = app.now

	alice := signup(t, h, "alice")
	bob := signup(t, h, "bobby")

	for i := 0; i < rateLimit; i++ {
		expect(t, do(t, h, "GET", "/notes", alice, nil), http.StatusOK)
	}
	rec := do(t, h, "GET", "/notes", alice, nil)
	expect(t, rec, http.StatusTooManyRequests)
	if rec.Header().Get("Retry-After") == "" {
		t.Fatal("missing Retry-After header")
	}

	// Another user is unaffected.
	expect(t, do(t, h, "GET", "/notes", bob, nil), http.StatusOK)

	// The window resets after a minute.
	clock = clock.Add(rateWindow + time.Second)
	expect(t, do(t, h, "GET", "/notes", alice, nil), http.StatusOK)
}
