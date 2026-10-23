package cwe25

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	_ "net/http/pprof" // expect: arbiter/go.pprof-on-default-mux
)

// Orders is CWE-200: the database error text, with table and constraint names, goes to the client,
// and importing net/http/pprof publishes /debug/pprof on the default mux the server uses.
func Orders(db *sql.DB) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rows, err := db.QueryContext(r.Context(), "SELECT id, total FROM orders WHERE owner = $1", r.URL.Query().Get("owner"))
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError) // expect: arbiter/go.error-text-in-response
			return
		}
		defer rows.Close()
		_ = json.NewEncoder(w).Encode(map[string]bool{"ok": true})
	}
}

// Invoice writes the raw error into the body twice over.
func Invoice(db *sql.DB, w http.ResponseWriter, r *http.Request) {
	var total int
	if err := db.QueryRowContext(r.Context(), "SELECT total FROM invoices WHERE id = $1", r.PathValue("id")).Scan(&total); err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		fmt.Fprintf(w, "lookup failed: %v", err) // expect: arbiter/go.error-text-in-response expect-ext: semgrep/semgrep-rules.go.lang.security.audit.xss.no-fprintf-to-responsewriter
		return
	}
	body, err := json.Marshal(map[string]int{"total": total})
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(err.Error())) // expect: arbiter/go.error-text-in-response
		return
	}
	_, _ = w.Write(body)
}

// Serve mounts Orders on the default mux.
func Serve(db *sql.DB) error {
	http.Handle("/orders", Orders(db))
	srv := &http.Server{Addr: ":8080", ReadHeaderTimeout: 5e9}
	return srv.ListenAndServe()
}
