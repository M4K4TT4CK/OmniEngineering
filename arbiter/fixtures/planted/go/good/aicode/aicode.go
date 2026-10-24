// Package aicode: the fixed twins of bad/aicode (ARB-083).
package aicode

import (
	"context"
	"database/sql"
	"encoding/json"
	"log"
	"net/http"
)

type store struct {
	db *sql.DB
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		log.Printf("write response: %v", err)
	}
}

// A deliberate discard is spelled out and stays silent.
func writeBestEffort(w http.ResponseWriter, v any) {
	_ = json.NewEncoder(w).Encode(v)
}

func (s *store) purgeExpired(ctx context.Context, now int64) error {
	if _, err := s.db.ExecContext(ctx, `DELETE FROM tokens WHERE expires_at < ?`, now); err != nil {
		return err
	}
	return nil
}
