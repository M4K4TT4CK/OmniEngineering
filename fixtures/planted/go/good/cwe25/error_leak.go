package cwe25

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"strconv"
)

// Orders logs the database error and gives the client a generic message.
func Orders(db *sql.DB) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		limit, err := strconv.Atoi(r.URL.Query().Get("limit"))
		if err != nil {
			// A parse error describes the caller's own input: 400 with it is fine.
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		rows, err := db.QueryContext(r.Context(), "SELECT id, total FROM orders WHERE owner = $1 LIMIT $2", r.URL.Query().Get("owner"), limit)
		if err != nil {
			slog.ErrorContext(r.Context(), "list orders", "err", err)
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}
		defer rows.Close()
		_ = json.NewEncoder(w).Encode(map[string]bool{"ok": true})
	}
}

// Invoice logs the failure and writes only a fixed message.
func Invoice(db *sql.DB, w http.ResponseWriter, r *http.Request) {
	var total int
	if err := db.QueryRowContext(r.Context(), "SELECT total FROM invoices WHERE id = $1", r.PathValue("id")).Scan(&total); err != nil {
		fmt.Fprintf(os.Stderr, "invoice lookup: %v\n", err)
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte("internal error"))
		return
	}
	_, _ = w.Write([]byte(strconv.Itoa(total)))
}

// Serve mounts Orders on its own mux, with no debug endpoints.
func Serve(db *sql.DB) error {
	mux := http.NewServeMux()
	mux.Handle("/orders", Orders(db))
	srv := &http.Server{Addr: ":8080", Handler: mux, ReadHeaderTimeout: 5e9}
	return srv.ListenAndServe()
}

// Report formats an error for a CLI, not a response.
func Report(err error) string { return fmt.Sprintf("failed: %v", err) }
