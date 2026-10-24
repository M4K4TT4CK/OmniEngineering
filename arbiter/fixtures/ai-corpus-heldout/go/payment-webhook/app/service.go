package main

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

const (
	maxBodyBytes    = 1 << 20
	signatureHeader = "X-Webhook-Signature"
)

const schema = `
CREATE TABLE IF NOT EXISTS orders (
	id         TEXT PRIMARY KEY,
	amount     INTEGER NOT NULL,
	status     TEXT NOT NULL,
	updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_events (
	event_id     TEXT PRIMARY KEY,
	processed_at INTEGER NOT NULL
);
`

// statusByType maps each supported event type to the status it sets on the order.
var statusByType = map[string]string{
	"payment.succeeded": "paid",
	"payment.failed":    "failed",
	"refund.created":    "refunded",
}

// Event is the webhook body. Amount is in minor units (e.g. cents).
type Event struct {
	ID      string `json:"id"`
	Type    string `json:"type"`
	OrderID string `json:"order_id"`
	Amount  int64  `json:"amount"`
}

// Order is the order state returned by the lookup endpoint.
type Order struct {
	ID        string `json:"id"`
	Amount    int64  `json:"amount"`
	Status    string `json:"status"`
	UpdatedAt int64  `json:"updated_at"`
}

type webhookResponse struct {
	Duplicate bool `json:"duplicate,omitempty"`
	Ignored   bool `json:"ignored,omitempty"`
}

// Service applies signed payment webhooks to orders and serves order lookups.
type Service struct {
	db     *sql.DB
	secret []byte
}

// Open opens (creating if needed) the SQLite database at path.
func Open(path string, secret []byte) (*Service, error) {
	db, err := sql.Open("sqlite", path+"?_pragma=busy_timeout(5000)")
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	// SQLite permits one writer at a time; a single connection keeps that simple.
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(schema); err != nil {
		db.Close()
		return nil, fmt.Errorf("create schema: %w", err)
	}
	return &Service{db: db, secret: secret}, nil
}

func (s *Service) Close() error {
	return s.db.Close()
}

func (s *Service) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /webhooks", s.handleWebhook)
	mux.HandleFunc("GET /orders/{id}", s.handleOrder)
	return mux
}

func (s *Service) handleWebhook(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBodyBytes))
	if err != nil {
		http.Error(w, "unreadable or oversized body", http.StatusRequestEntityTooLarge)
		return
	}
	if !s.verify(body, r.Header.Get(signatureHeader)) {
		http.Error(w, "invalid signature", http.StatusUnauthorized)
		return
	}

	var ev Event
	if err := json.Unmarshal(body, &ev); err != nil || ev.ID == "" || ev.OrderID == "" {
		http.Error(w, "malformed event", http.StatusBadRequest)
		return
	}
	status, known := statusByType[ev.Type]
	if !known {
		// Acknowledge types we do not handle so the provider stops retrying them.
		writeJSON(w, http.StatusOK, webhookResponse{Ignored: true})
		return
	}

	applied, err := s.apply(r.Context(), ev, status)
	if err != nil {
		http.Error(w, "could not apply event", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, webhookResponse{Duplicate: !applied})
}

// verify checks the HMAC-SHA256 of the raw body against the header value,
// which is hex, optionally prefixed with "sha256=".
func (s *Service) verify(body []byte, header string) bool {
	got, err := hex.DecodeString(strings.TrimPrefix(header, "sha256="))
	if err != nil {
		return false
	}
	mac := hmac.New(sha256.New, s.secret)
	mac.Write(body)
	return hmac.Equal(got, mac.Sum(nil))
}

// apply records the event ID and updates the order in one transaction. The
// event ID insert is the gate: if it already exists, nothing else runs.
// It reports false for a duplicate delivery.
func (s *Service) apply(ctx context.Context, ev Event, status string) (bool, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()

	now := time.Now().Unix()
	res, err := tx.ExecContext(ctx,
		`INSERT INTO processed_events (event_id, processed_at) VALUES (?, ?)
		 ON CONFLICT(event_id) DO NOTHING`, ev.ID, now)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return false, err
	}
	if n == 0 {
		return false, nil
	}

	// The first event that names an order creates it; its amount is kept.
	_, err = tx.ExecContext(ctx,
		`INSERT INTO orders (id, amount, status, updated_at) VALUES (?, ?, ?, ?)
		 ON CONFLICT(id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at`,
		ev.OrderID, ev.Amount, status, now)
	if err != nil {
		return false, err
	}
	return true, tx.Commit()
}

func (s *Service) handleOrder(w http.ResponseWriter, r *http.Request) {
	var o Order
	err := s.db.QueryRowContext(r.Context(),
		`SELECT id, amount, status, updated_at FROM orders WHERE id = ?`, r.PathValue("id")).
		Scan(&o.ID, &o.Amount, &o.Status, &o.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		http.Error(w, "order not found", http.StatusNotFound)
		return
	}
	if err != nil {
		http.Error(w, "lookup failed", http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, o)
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}
