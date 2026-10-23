package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync"
	"testing"
)

const testSecret = "test-secret"

func newTestService(t *testing.T, path string) *Service {
	t.Helper()
	svc, err := Open(path, []byte(testSecret))
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	return svc
}

func sign(body []byte) string {
	mac := hmac.New(sha256.New, []byte(testSecret))
	mac.Write(body)
	return hex.EncodeToString(mac.Sum(nil))
}

func postEvent(t *testing.T, h http.Handler, ev Event) *httptest.ResponseRecorder {
	t.Helper()
	body, err := json.Marshal(ev)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	return postRaw(h, body, sign(body))
}

func postRaw(h http.Handler, body []byte, sig string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/webhooks", bytes.NewReader(body))
	req.Header.Set(signatureHeader, sig)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func getOrder(h http.Handler, id string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/orders/"+id, nil))
	return rec
}

func decodeOrder(t *testing.T, rec *httptest.ResponseRecorder) Order {
	t.Helper()
	var o Order
	if err := json.NewDecoder(rec.Body).Decode(&o); err != nil {
		t.Fatalf("decode order: %v", err)
	}
	return o
}

func decodeResponse(t *testing.T, rec *httptest.ResponseRecorder) webhookResponse {
	t.Helper()
	var r webhookResponse
	if err := json.NewDecoder(rec.Body).Decode(&r); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return r
}

func TestPaymentSucceededMarksOrderPaid(t *testing.T) {
	svc := newTestService(t, filepath.Join(t.TempDir(), "test.db"))
	defer svc.Close()
	h := svc.Routes()

	rec := postEvent(t, h, Event{ID: "evt-1", Type: "payment.succeeded", OrderID: "ord-1", Amount: 1500})
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rec.Code)
	}
	if r := decodeResponse(t, rec); r.Duplicate || r.Ignored {
		t.Fatalf("response = %+v, want applied", r)
	}

	o := decodeOrder(t, getOrder(h, "ord-1"))
	if o.Status != "paid" || o.Amount != 1500 {
		t.Fatalf("order = %+v, want status paid amount 1500", o)
	}
}

func TestRedeliveryIsNotReapplied(t *testing.T) {
	svc := newTestService(t, filepath.Join(t.TempDir(), "test.db"))
	defer svc.Close()
	h := svc.Routes()

	paid := Event{ID: "evt-1", Type: "payment.succeeded", OrderID: "ord-1", Amount: 1500}
	postEvent(t, h, paid)
	postEvent(t, h, Event{ID: "evt-2", Type: "refund.created", OrderID: "ord-1", Amount: 1500})

	// The provider redelivers evt-1 after the refund; it must not flip the order back to paid.
	rec := postEvent(t, h, paid)
	if r := decodeResponse(t, rec); !r.Duplicate {
		t.Fatalf("response = %+v, want duplicate", r)
	}
	if o := decodeOrder(t, getOrder(h, "ord-1")); o.Status != "refunded" {
		t.Fatalf("status = %q, want refunded", o.Status)
	}
}

func TestConcurrentDeliveriesApplyOnce(t *testing.T) {
	svc := newTestService(t, filepath.Join(t.TempDir(), "test.db"))
	defer svc.Close()
	h := svc.Routes()

	const deliveries = 20
	ev := Event{ID: "evt-1", Type: "payment.failed", OrderID: "ord-1", Amount: 900}
	results := make([]webhookResponse, deliveries)
	var wg sync.WaitGroup
	for i := range deliveries {
		wg.Add(1)
		go func() {
			defer wg.Done()
			body, _ := json.Marshal(ev)
			rec := postRaw(h, body, sign(body))
			if rec.Code != http.StatusOK {
				t.Errorf("status = %d, want 200", rec.Code)
				return
			}
			json.NewDecoder(rec.Body).Decode(&results[i])
		}()
	}
	wg.Wait()

	applied := 0
	for _, r := range results {
		if !r.Duplicate {
			applied++
		}
	}
	if applied != 1 {
		t.Fatalf("applied %d times, want exactly 1", applied)
	}
}

func TestAppliedStateSurvivesReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "test.db")
	ev := Event{ID: "evt-1", Type: "payment.succeeded", OrderID: "ord-1", Amount: 1500}

	svc := newTestService(t, path)
	postEvent(t, svc.Routes(), ev)
	svc.Close()

	svc = newTestService(t, path)
	defer svc.Close()
	rec := postEvent(t, svc.Routes(), ev)
	if r := decodeResponse(t, rec); !r.Duplicate {
		t.Fatalf("response after restart = %+v, want duplicate", r)
	}
}

func TestInvalidSignatureRejected(t *testing.T) {
	svc := newTestService(t, filepath.Join(t.TempDir(), "test.db"))
	defer svc.Close()
	h := svc.Routes()

	body, _ := json.Marshal(Event{ID: "evt-1", Type: "payment.succeeded", OrderID: "ord-1", Amount: 1500})
	rec := postRaw(h, body, "deadbeef")
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", rec.Code)
	}
	if rec := getOrder(h, "ord-1"); rec.Code != http.StatusNotFound {
		t.Fatalf("order lookup status = %d, want 404 (no state change)", rec.Code)
	}
}

func TestSignatureMustCoverBody(t *testing.T) {
	svc := newTestService(t, filepath.Join(t.TempDir(), "test.db"))
	defer svc.Close()
	h := svc.Routes()

	signed, _ := json.Marshal(Event{ID: "evt-1", Type: "payment.failed", OrderID: "ord-1", Amount: 100})
	tampered, _ := json.Marshal(Event{ID: "evt-1", Type: "payment.succeeded", OrderID: "ord-1", Amount: 100})
	if rec := postRaw(h, tampered, sign(signed)); rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", rec.Code)
	}
}

func TestMalformedEventRejected(t *testing.T) {
	svc := newTestService(t, filepath.Join(t.TempDir(), "test.db"))
	defer svc.Close()
	h := svc.Routes()

	body := []byte(`{"id": "evt-1", "type": `)
	if rec := postRaw(h, body, sign(body)); rec.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", rec.Code)
	}
}

func TestUnknownEventTypeIgnored(t *testing.T) {
	svc := newTestService(t, filepath.Join(t.TempDir(), "test.db"))
	defer svc.Close()
	h := svc.Routes()

	rec := postEvent(t, h, Event{ID: "evt-1", Type: "payout.created", OrderID: "ord-1", Amount: 100})
	if r := decodeResponse(t, rec); !r.Ignored {
		t.Fatalf("response = %+v, want ignored", r)
	}
	if rec := getOrder(h, "ord-1"); rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404 for ignored event", rec.Code)
	}
}

func TestUnknownOrderReturnsNotFound(t *testing.T) {
	svc := newTestService(t, filepath.Join(t.TempDir(), "test.db"))
	defer svc.Close()

	if rec := getOrder(svc.Routes(), "missing"); rec.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", rec.Code)
	}
}
