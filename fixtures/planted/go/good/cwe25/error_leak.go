package cwe25

import (
	"database/sql"
	"encoding/json"
	"log/slog"
	"net/http"
)

// Orders logs the database error and gives the client a generic message.
func Orders(db *sql.DB) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rows, err := db.QueryContext(r.Context(), "SELECT id, total FROM orders WHERE owner = $1", r.URL.Query().Get("owner"))
		if err != nil {
			slog.ErrorContext(r.Context(), "list orders", "err", err)
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}
		defer rows.Close()
		_ = json.NewEncoder(w).Encode(map[string]bool{"ok": true})
	}
}

// Serve mounts Orders on its own mux, with no debug endpoints.
func Serve(db *sql.DB) error {
	mux := http.NewServeMux()
	mux.Handle("/orders", Orders(db))
	srv := &http.Server{Addr: ":8080", Handler: mux, ReadHeaderTimeout: 5e9}
	return srv.ListenAndServe()
}
