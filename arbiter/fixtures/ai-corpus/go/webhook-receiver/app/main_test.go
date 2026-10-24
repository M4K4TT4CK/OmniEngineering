package main

import (
	"database/sql"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const (
	testSecret = "whsec_test"
	testAdmin  = "admin-token"
)

func newTestServer(t *testing.T) (*Server, http.Handler) {
	t.Helper()
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	db.SetMaxOpenConns(1) // each connection to :memory: is its own database
	t.Cleanup(func() { db.Close() })
	s, err := NewServer(db, testSecret, testAdmin)
	if err != nil {
		t.Fatal(err)
	}
	s.backoff = time.Millisecond
	return s, s.Routes()
}

func do(h http.Handler, method, path, body string, headers map[string]string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func postEvent(h http.Handler, body string) *httptest.ResponseRecorder {
	return do(h, "POST", "/webhooks", body, map[string]string{"X-Signature": sign([]byte(testSecret), []byte(body))})
}

func setCallback(t *testing.T, h http.Handler, merchant, cb string) {
	t.Helper()
	rec := do(h, "PUT", "/merchants/"+merchant, `{"callback_url":"`+cb+`"}`,
		map[string]string{"Authorization": "Bearer " + testAdmin})
	if rec.Code != http.StatusNoContent {
		t.Fatalf("set callback: %d %s", rec.Code, rec.Body)
	}
}

func listEvents(t *testing.T, h http.Handler, merchant string) []eventView {
	t.Helper()
	rec := do(h, "GET", "/merchants/"+merchant+"/events", "", map[string]string{"Authorization": "Bearer " + testAdmin})
	if rec.Code != 200 {
		t.Fatalf("list: %d %s", rec.Code, rec.Body)
	}
	var out []eventView
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestSignatureVerification(t *testing.T) {
	_, h := newTestServer(t)
	body := `{"id":"e1","type":"payment.failed","merchant_id":"m1"}`
	good := sign([]byte(testSecret), []byte(body))
	bad := sign([]byte("wrong"), []byte(body))

	cases := []struct {
		name, sig string
		want      int
	}{
		{"valid", good, http.StatusAccepted},
		{"valid with prefix", "sha256=" + good, http.StatusAccepted},
		{"missing", "", http.StatusUnauthorized},
		{"wrong secret", bad, http.StatusUnauthorized},
		{"not hex", "zzzz", http.StatusUnauthorized},
	}
	for _, c := range cases {
		rec := do(h, "POST", "/webhooks", body, map[string]string{"X-Signature": c.sig})
		if rec.Code != c.want {
			t.Errorf("%s: got %d, want %d", c.name, rec.Code, c.want)
		}
	}
}

func TestTamperedBodyRejected(t *testing.T) {
	_, h := newTestServer(t)
	orig := `{"id":"e1","type":"payment.failed","merchant_id":"m1"}`
	sig := sign([]byte(testSecret), []byte(orig))
	rec := do(h, "POST", "/webhooks", strings.Replace(orig, "failed", "succeeded", 1), map[string]string{"X-Signature": sig})
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("got %d", rec.Code)
	}
	if n := len(listEvents(t, h, "m1")); n != 0 {
		t.Fatalf("stored %d events", n)
	}
}

func TestInvalidPayloads(t *testing.T) {
	_, h := newTestServer(t)
	for _, body := range []string{`not json`, `{}`, `{"id":"e1","type":"x"}`} {
		if rec := postEvent(h, body); rec.Code != http.StatusBadRequest {
			t.Errorf("%q: got %d", body, rec.Code)
		}
	}
}

func TestSuccessfulPaymentForwarded(t *testing.T) {
	var got struct {
		body, sig string
	}
	var calls int32
	cb := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		got.body, got.sig = string(b), r.Header.Get("X-Signature")
		atomic.AddInt32(&calls, 1)
	}))
	defer cb.Close()

	s, h := newTestServer(t)
	setCallback(t, h, "m1", cb.URL)
	body := `{"id":"e1","type":"payment.succeeded","merchant_id":"m1","amount":100}`
	if rec := postEvent(h, body); rec.Code != http.StatusAccepted {
		t.Fatalf("got %d", rec.Code)
	}
	s.wg.Wait()

	if calls != 1 || got.body != body {
		t.Fatalf("calls=%d body=%q", calls, got.body)
	}
	if !validSignature([]byte(testSecret), []byte(got.body), got.sig) {
		t.Error("forwarded request has invalid signature")
	}
	evs := listEvents(t, h, "m1")
	if len(evs) != 1 || evs[0].DeliveryStatus != "delivered" || evs[0].DeliveryTries != 1 {
		t.Fatalf("unexpected events: %+v", evs)
	}
}

func TestOtherEventsAndUnknownMerchantsNotForwarded(t *testing.T) {
	var calls int32
	cb := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { atomic.AddInt32(&calls, 1) }))
	defer cb.Close()

	s, h := newTestServer(t)
	setCallback(t, h, "m1", cb.URL)
	postEvent(h, `{"id":"e1","type":"payment.failed","merchant_id":"m1"}`)
	postEvent(h, `{"id":"e2","type":"payment.succeeded","merchant_id":"unknown"}`)
	s.wg.Wait()

	if calls != 0 {
		t.Fatalf("forwarded %d events", calls)
	}
	if evs := listEvents(t, h, "unknown"); len(evs) != 1 || evs[0].DeliveryStatus != "none" {
		t.Fatalf("unexpected events: %+v", evs)
	}
}

func TestDuplicateEventIgnored(t *testing.T) {
	var calls int32
	cb := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { atomic.AddInt32(&calls, 1) }))
	defer cb.Close()

	s, h := newTestServer(t)
	setCallback(t, h, "m1", cb.URL)
	body := `{"id":"e1","type":"payment.succeeded","merchant_id":"m1"}`
	if rec := postEvent(h, body); rec.Code != http.StatusAccepted {
		t.Fatalf("first: %d", rec.Code)
	}
	if rec := postEvent(h, body); rec.Code != http.StatusOK {
		t.Fatalf("duplicate: %d", rec.Code)
	}
	s.wg.Wait()
	if calls != 1 {
		t.Fatalf("forwarded %d times", calls)
	}
	if n := len(listEvents(t, h, "m1")); n != 1 {
		t.Fatalf("stored %d events", n)
	}
}

func TestForwardRetriesThenFails(t *testing.T) {
	var calls int32
	cb := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer cb.Close()

	s, h := newTestServer(t)
	setCallback(t, h, "m1", cb.URL)
	postEvent(h, `{"id":"e1","type":"payment.succeeded","merchant_id":"m1"}`)
	s.wg.Wait()

	if int(calls) != s.attempts {
		t.Fatalf("calls=%d, want %d", calls, s.attempts)
	}
	evs := listEvents(t, h, "m1")
	if len(evs) != 1 || evs[0].DeliveryStatus != "failed" || evs[0].DeliveryTries != s.attempts {
		t.Fatalf("unexpected events: %+v", evs)
	}
}

func TestListEvents(t *testing.T) {
	_, h := newTestServer(t)
	for _, id := range []string{"e1", "e2", "e3"} {
		postEvent(h, `{"id":"`+id+`","type":"payment.failed","merchant_id":"m1"}`)
	}
	postEvent(h, `{"id":"other","type":"payment.failed","merchant_id":"m2"}`)

	evs := listEvents(t, h, "m1")
	if len(evs) != 3 || evs[0].ID != "e3" || evs[2].ID != "e1" {
		t.Fatalf("unexpected order/content: %+v", evs)
	}
	rec := do(h, "GET", "/merchants/m1/events?limit=2", "", map[string]string{"Authorization": "Bearer " + testAdmin})
	var limited []eventView
	json.Unmarshal(rec.Body.Bytes(), &limited)
	if len(limited) != 2 {
		t.Fatalf("limit not applied: %d", len(limited))
	}
	rec = do(h, "GET", "/merchants/m1/events?limit=abc", "", map[string]string{"Authorization": "Bearer " + testAdmin})
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("bad limit: %d", rec.Code)
	}
}

func TestAdminAuthRequired(t *testing.T) {
	_, h := newTestServer(t)
	for _, hdr := range []map[string]string{nil, {"Authorization": "Bearer nope"}} {
		if rec := do(h, "GET", "/merchants/m1/events", "", hdr); rec.Code != http.StatusUnauthorized {
			t.Errorf("list with %v: %d", hdr, rec.Code)
		}
		if rec := do(h, "PUT", "/merchants/m1", `{"callback_url":"http://x.test"}`, hdr); rec.Code != http.StatusUnauthorized {
			t.Errorf("put with %v: %d", hdr, rec.Code)
		}
	}
}

func TestInvalidCallbackURLRejected(t *testing.T) {
	_, h := newTestServer(t)
	for _, u := range []string{"", "ftp://example.com", "/relative", "javascript:alert(1)"} {
		rec := do(h, "PUT", "/merchants/m1", `{"callback_url":"`+u+`"}`, map[string]string{"Authorization": "Bearer " + testAdmin})
		if rec.Code != http.StatusBadRequest {
			t.Errorf("%q: got %d", u, rec.Code)
		}
	}
}
