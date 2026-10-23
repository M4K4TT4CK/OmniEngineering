package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// newTestServer returns a shortener with an in-memory DB. allowPrivate lets it
// fetch titles from httptest servers on loopback.
func newTestServer(t *testing.T, allowPrivate bool) http.Handler {
	t.Helper()
	s, err := newServer("file::memory:", allowPrivate)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.db.Close() })
	return s.routes()
}

func newTarget(t *testing.T) *httptest.Server {
	t.Helper()
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, "<html><head><TITLE>\n  Hello &amp;  World </TITLE></head></html>")
	}))
	t.Cleanup(ts.Close)
	return ts
}

func do(h http.Handler, method, path, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func create(t *testing.T, h http.Handler, body string) map[string]any {
	t.Helper()
	rec := do(h, "POST", "/api/links", body)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create: status %d body %s", rec.Code, rec.Body)
	}
	var out map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestCreateRedirectStats(t *testing.T) {
	h := newTestServer(t, true)
	target := newTarget(t)

	out := create(t, h, fmt.Sprintf(`{"url":%q}`, target.URL))
	code := out["code"].(string)
	if len(code) != generatedLen {
		t.Errorf("generated code %q has unexpected length", code)
	}
	if out["title"] != "Hello & World" {
		t.Errorf("title = %q", out["title"])
	}
	if out["short_url"] != "http://example.com/"+code {
		t.Errorf("short_url = %v", out["short_url"])
	}

	for i := 0; i < 3; i++ {
		rec := do(h, "GET", "/"+code, "")
		if rec.Code != http.StatusFound || rec.Header().Get("Location") != target.URL {
			t.Fatalf("redirect: status %d location %q", rec.Code, rec.Header().Get("Location"))
		}
	}

	rec := do(h, "GET", "/api/links/"+code+"/stats", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("stats: status %d", rec.Code)
	}
	var st link
	if err := json.Unmarshal(rec.Body.Bytes(), &st); err != nil {
		t.Fatal(err)
	}
	if st.Clicks != 3 || st.Title != "Hello & World" || st.URL != target.URL || st.CreatedAt.IsZero() {
		t.Errorf("unexpected stats: %+v", st)
	}
}

func TestStatsDoNotCountAsClicks(t *testing.T) {
	h := newTestServer(t, true)
	target := newTarget(t)
	create(t, h, fmt.Sprintf(`{"url":%q,"alias":"abc"}`, target.URL))
	do(h, "GET", "/api/links/abc/stats", "")
	var st link
	json.Unmarshal(do(h, "GET", "/api/links/abc/stats", "").Body.Bytes(), &st)
	if st.Clicks != 0 {
		t.Errorf("clicks = %d, want 0", st.Clicks)
	}
}

func TestCustomAlias(t *testing.T) {
	h := newTestServer(t, true)
	target := newTarget(t)
	body := fmt.Sprintf(`{"url":%q,"alias":"my-link_1"}`, target.URL)

	if out := create(t, h, body); out["code"] != "my-link_1" {
		t.Errorf("code = %v", out["code"])
	}
	if rec := do(h, "POST", "/api/links", body); rec.Code != http.StatusConflict {
		t.Errorf("duplicate alias: status %d, want 409", rec.Code)
	}
	if rec := do(h, "GET", "/my-link_1", ""); rec.Code != http.StatusFound {
		t.Errorf("alias redirect: status %d", rec.Code)
	}
}

func TestValidation(t *testing.T) {
	h := newTestServer(t, true)
	cases := map[string]string{
		"not json":       `nope`,
		"empty url":      `{"url":""}`,
		"relative url":   `{"url":"/foo"}`,
		"ftp scheme":     `{"url":"ftp://example.com/x"}`,
		"javascript url": `{"url":"javascript:alert(1)"}`,
		"bad alias":      `{"url":"http://example.com","alias":"a/b"}`,
		"long alias":     `{"url":"http://example.com","alias":"` + strings.Repeat("a", 33) + `"}`,
		"reserved alias": `{"url":"http://example.com","alias":"api"}`,
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			if rec := do(h, "POST", "/api/links", body); rec.Code != http.StatusBadRequest {
				t.Errorf("status %d, want 400 (body %s)", rec.Code, rec.Body)
			}
		})
	}
}

func TestNotFound(t *testing.T) {
	h := newTestServer(t, true)
	for _, path := range []string{"/missing", "/api/links/missing/stats"} {
		if rec := do(h, "GET", path, ""); rec.Code != http.StatusNotFound {
			t.Errorf("%s: status %d, want 404", path, rec.Code)
		}
	}
}

func TestTitleFetchFailureStillCreates(t *testing.T) {
	h := newTestServer(t, true)
	// Port 1 on loopback: connection refused.
	out := create(t, h, `{"url":"http://127.0.0.1:1/"}`)
	if out["title"] != "" {
		t.Errorf("title = %q, want empty", out["title"])
	}
}

func TestPrivateAddressesNotFetched(t *testing.T) {
	h := newTestServer(t, false)
	hit := false
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hit = true
		fmt.Fprint(w, "<title>secret</title>")
	}))
	defer target.Close()

	out := create(t, h, fmt.Sprintf(`{"url":%q}`, target.URL))
	if hit || out["title"] != "" {
		t.Errorf("loopback target was fetched (hit=%v, title=%q)", hit, out["title"])
	}
}
