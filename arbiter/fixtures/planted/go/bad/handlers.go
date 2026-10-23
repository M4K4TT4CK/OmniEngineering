package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
)

// Handlers serves the users API on top of the users table.
type Handlers struct {
	db *sql.DB
}

// GetUser drops the request's context: a slow query outlives the client.
func (h *Handlers) GetUser(w http.ResponseWriter, r *http.Request) {
	var email string
	err := h.db.QueryRow("SELECT email FROM users WHERE id = $1", r.URL.Query().Get("id")).Scan(&email) // expect: arbiter/go.db-call-no-deadline
	if err != nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	_ = json.NewEncoder(w).Encode(map[string]string{"email": email})
}

// CountUsers has a ctx and throws it away for one with no deadline.
func (h *Handlers) CountUsers(ctx context.Context) (int, error) {
	var n int
	err := h.db.QueryRowContext(context.Background(), "SELECT count(*) FROM users").Scan(&n) // expect: arbiter/go.db-call-no-deadline
	return n, err
}

// Touch records a login; the update ignores the caller's cancellation.
func (h *Handlers) Touch(ctx context.Context, id string) error {
	_, err := h.db.Exec("UPDATE users SET last_seen = now() WHERE id = $1", id) // expect: arbiter/go.db-call-no-deadline
	return err
}
