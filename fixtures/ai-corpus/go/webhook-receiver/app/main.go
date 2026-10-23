// Command webhookreceiver receives signed payment events, stores them in
// SQLite and forwards successful payments to a per-merchant callback URL.
package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

const (
	maxBodyBytes     = 1 << 20
	successEventType = "payment.succeeded"
)

const schema = `
CREATE TABLE IF NOT EXISTS merchants (
	id           TEXT PRIMARY KEY,
	callback_url TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
	id              TEXT PRIMARY KEY,
	merchant_id     TEXT NOT NULL,
	type            TEXT NOT NULL,
	payload         TEXT NOT NULL,
	received_at     INTEGER NOT NULL,
	delivery_status TEXT NOT NULL DEFAULT 'none', -- none | pending | delivered | failed
	delivery_tries  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_merchant_idx ON events (merchant_id, received_at DESC);
`

// Server holds the receiver's dependencies.
type Server struct {
	db         *sql.DB
	secret     []byte
	adminToken string
	client     *http.Client
	attempts   int           // max forward attempts per event
	backoff    time.Duration // base delay between attempts (multiplied by attempt number)
	wg         sync.WaitGroup
}

func NewServer(db *sql.DB, secret, adminToken string) (*Server, error) {
	if secret == "" || adminToken == "" {
		return nil, errors.New("secret and admin token must be set")
	}
	if _, err := db.Exec(schema); err != nil {
		return nil, err
	}
	return &Server{
		db:         db,
		secret:     []byte(secret),
		adminToken: adminToken,
		client: &http.Client{
			Timeout: 10 * time.Second,
			// Do not follow redirects: they could bounce the event to an unvetted host.
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
		attempts: 3,
		backoff:  2 * time.Second,
	}, nil
}

func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /webhooks", s.handleWebhook)
	mux.HandleFunc("PUT /merchants/{id}", s.admin(s.handlePutMerchant))
	mux.HandleFunc("GET /merchants/{id}/events", s.admin(s.handleListEvents))
	return mux
}

// sign returns the hex HMAC-SHA256 of body.
func sign(secret, body []byte) string {
	m := hmac.New(sha256.New, secret)
	m.Write(body)
	return hex.EncodeToString(m.Sum(nil))
}

// validSignature checks a hex signature (optionally prefixed "sha256=") in constant time.
func validSignature(secret, body []byte, header string) bool {
	got, err := hex.DecodeString(strings.TrimPrefix(strings.TrimSpace(header), "sha256="))
	if err != nil {
		return false
	}
	want, _ := hex.DecodeString(sign(secret, body))
	return hmac.Equal(got, want)
}

type event struct {
	ID         string `json:"id"`
	Type       string `json:"type"`
	MerchantID string `json:"merchant_id"`
}

func (s *Server) handleWebhook(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBodyBytes))
	if err != nil {
		http.Error(w, "cannot read body", http.StatusRequestEntityTooLarge)
		return
	}
	if !validSignature(s.secret, body, r.Header.Get("X-Signature")) {
		http.Error(w, "invalid signature", http.StatusUnauthorized)
		return
	}
	var ev event
	if err := json.Unmarshal(body, &ev); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	if ev.ID == "" || ev.Type == "" || ev.MerchantID == "" || len(ev.ID) > 255 || len(ev.MerchantID) > 255 {
		http.Error(w, "id, type and merchant_id are required", http.StatusBadRequest)
		return
	}

	var callback string
	if ev.Type == successEventType {
		err := s.db.QueryRow(`SELECT callback_url FROM merchants WHERE id = ?`, ev.MerchantID).Scan(&callback)
		if err != nil && !errors.Is(err, sql.ErrNoRows) {
			log.Printf("merchant lookup: %v", err)
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}
	}
	status := "none"
	if callback != "" {
		status = "pending"
	}

	res, err := s.db.Exec(
		`INSERT INTO events (id, merchant_id, type, payload, received_at, delivery_status)
		 VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING`,
		ev.ID, ev.MerchantID, ev.Type, string(body), time.Now().Unix(), status)
	if err != nil {
		log.Printf("store event: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	// Providers retry; a duplicate is acknowledged but not stored or forwarded again.
	if n, _ := res.RowsAffected(); n == 0 {
		w.WriteHeader(http.StatusOK)
		return
	}
	if callback != "" {
		s.wg.Add(1)
		go func() {
			defer s.wg.Done()
			s.forward(ev.ID, callback, body)
		}()
	}
	w.WriteHeader(http.StatusAccepted)
}

// forward POSTs the original body to the merchant, retrying with linear backoff,
// and records the outcome. The body is re-signed so the merchant can verify it.
func (s *Server) forward(eventID, callback string, body []byte) {
	status := "failed"
	tries := 0
	for tries < s.attempts {
		if tries > 0 {
			time.Sleep(s.backoff * time.Duration(tries))
		}
		tries++
		req, err := http.NewRequest(http.MethodPost, callback, bytes.NewReader(body))
		if err != nil {
			break
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-Signature", sign(s.secret, body))
		resp, err := s.client.Do(req)
		if err != nil {
			log.Printf("forward %s attempt %d: %v", eventID, tries, err)
			continue
		}
		io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<16))
		resp.Body.Close()
		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			status = "delivered"
			break
		}
		log.Printf("forward %s attempt %d: status %d", eventID, tries, resp.StatusCode)
	}
	if _, err := s.db.Exec(`UPDATE events SET delivery_status = ?, delivery_tries = ? WHERE id = ?`,
		status, tries, eventID); err != nil {
		log.Printf("record delivery for %s: %v", eventID, err)
	}
}

// admin guards management endpoints with a bearer token.
func (s *Server) admin(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		tok := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if subtle.ConstantTimeCompare([]byte(tok), []byte(s.adminToken)) != 1 {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		next(w, r)
	}
}

func (s *Server) handlePutMerchant(w http.ResponseWriter, r *http.Request) {
	var in struct {
		CallbackURL string `json:"callback_url"`
	}
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16)).Decode(&in); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	u, err := url.Parse(in.CallbackURL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		http.Error(w, "callback_url must be an absolute http(s) URL", http.StatusBadRequest)
		return
	}
	_, err = s.db.Exec(`INSERT INTO merchants (id, callback_url) VALUES (?, ?)
		ON CONFLICT(id) DO UPDATE SET callback_url = excluded.callback_url`,
		r.PathValue("id"), in.CallbackURL)
	if err != nil {
		log.Printf("put merchant: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

type eventView struct {
	ID             string          `json:"id"`
	Type           string          `json:"type"`
	ReceivedAt     time.Time       `json:"received_at"`
	DeliveryStatus string          `json:"delivery_status"`
	DeliveryTries  int             `json:"delivery_tries"`
	Payload        json.RawMessage `json:"payload"`
}

func (s *Server) handleListEvents(w http.ResponseWriter, r *http.Request) {
	limit := 50
	if v := r.URL.Query().Get("limit"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 200 {
			http.Error(w, "limit must be between 1 and 200", http.StatusBadRequest)
			return
		}
		limit = n
	}
	rows, err := s.db.Query(
		`SELECT id, type, received_at, delivery_status, delivery_tries, payload
		 FROM events WHERE merchant_id = ? ORDER BY received_at DESC, rowid DESC LIMIT ?`,
		r.PathValue("id"), limit)
	if err != nil {
		log.Printf("list events: %v", err)
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	out := []eventView{}
	for rows.Next() {
		var v eventView
		var ts int64
		var payload string
		if err := rows.Scan(&v.ID, &v.Type, &ts, &v.DeliveryStatus, &v.DeliveryTries, &payload); err != nil {
			log.Printf("scan event: %v", err)
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}
		v.ReceivedAt = time.Unix(ts, 0).UTC()
		v.Payload = json.RawMessage(payload)
		out = append(out, v)
	}
	if err := rows.Err(); err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(out)
}

func main() {
	secret := os.Getenv("WEBHOOK_SECRET")
	adminToken := os.Getenv("ADMIN_TOKEN")
	dbPath := envOr("DB_PATH", "events.db")
	addr := envOr("LISTEN_ADDR", ":8080")

	db, err := sql.Open("sqlite", "file:"+dbPath+"?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)")
	if err != nil {
		log.Fatal(err)
	}
	db.SetMaxOpenConns(1)
	srv, err := NewServer(db, secret, adminToken)
	if err != nil {
		log.Fatalf("WEBHOOK_SECRET and ADMIN_TOKEN must be set: %v", err)
	}
	log.Printf("listening on %s", addr)
	log.Fatal((&http.Server{
		Addr:              addr,
		Handler:           srv.Routes(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
	}).ListenAndServe())
}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
