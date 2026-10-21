package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"time"
)

// Handlers serves the users API on top of the users table.
type Handlers struct {
	db *sql.DB
}

// GetUser bounds the query by the request's context plus a timeout.
func (h *Handlers) GetUser(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	var email string
	err := h.db.QueryRowContext(ctx, "SELECT email FROM users WHERE id = $1", r.URL.Query().Get("id")).Scan(&email)
	if err != nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	_ = json.NewEncoder(w).Encode(map[string]string{"email": email})
}

// CountUsers passes the caller's ctx through.
func (h *Handlers) CountUsers(ctx context.Context) (int, error) {
	var n int
	err := h.db.QueryRowContext(ctx, "SELECT count(*) FROM users").Scan(&n)
	return n, err
}

// Touch records a login under the caller's deadline.
func (h *Handlers) Touch(ctx context.Context, id string) error {
	_, err := h.db.ExecContext(ctx, "UPDATE users SET last_seen = now() WHERE id = $1", id)
	return err
}

// Migrate runs at startup, where there is no caller context to inherit;
// it is bounded by its own timeout.
func Migrate(db *sql.DB) error {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	_, err := db.ExecContext(ctx, "CREATE TABLE IF NOT EXISTS users (id text PRIMARY KEY)")
	return err
}

// ping is a startup probe with no context in reach: left alone.
func ping(db *sql.DB) error {
	return db.Ping()
}
