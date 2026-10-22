package cwe25

import (
	"database/sql"
	"encoding/json"
	"net/http"
	_ "net/http/pprof" // gap: CWE-200 - nothing in this build reports it (docs/cwe-top25.md)
)

// Orders is CWE-200: the database error text, with table and constraint names, goes to the client,
// and importing net/http/pprof publishes /debug/pprof on the default mux the server uses.
func Orders(db *sql.DB) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rows, err := db.QueryContext(r.Context(), "SELECT id, total FROM orders WHERE owner = $1", r.URL.Query().Get("owner"))
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError) // gap: CWE-200 - nothing in this build reports it (docs/cwe-top25.md)
			return
		}
		defer rows.Close()
		_ = json.NewEncoder(w).Encode(map[string]bool{"ok": true})
	}
}

// Serve mounts Orders on the default mux.
func Serve(db *sql.DB) error {
	http.Handle("/orders", Orders(db))
	srv := &http.Server{Addr: ":8080", ReadHeaderTimeout: 5e9}
	return srv.ListenAndServe()
}
