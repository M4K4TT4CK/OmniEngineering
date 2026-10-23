package cwe25

import (
	"net/http"
	"os"
	"path/filepath"
)

const reportsDir = "/srv/reports"

// DownloadReport is CWE-22: `?file=../../etc/passwd` walks out of reportsDir.
func DownloadReport(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("file")
	http.ServeFile(w, r, filepath.Join(reportsDir, name)) // expect: arbiter/go.path-traversal-request
}

// DeleteExport is CWE-22 with the route value in the call: DELETE /exports/..%2f..%2fapp.db.
func DeleteExport(w http.ResponseWriter, r *http.Request) {
	if err := os.Remove(filepath.Join(reportsDir, "exports", r.PathValue("name"))); err != nil { // expect: arbiter/go.path-traversal-request
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
