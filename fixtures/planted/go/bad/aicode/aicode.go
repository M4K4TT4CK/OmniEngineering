// Package aicode holds shapes reviewers found in model-written Go services
// (ARB-083). Each defect line carries its marker; good/aicode is the fix.
package aicode

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
)

type store struct {
	db *sql.DB
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v) // expect: arbiter/go.ignored-encode-error
}

func (s *store) purgeExpired(ctx context.Context, now int64) {
	s.db.ExecContext(ctx, `DELETE FROM tokens WHERE expires_at < ?`, now) // expect: arbiter/go.ignored-exec-error
}
