package cwe25

import (
	"net/http"
	"path/filepath"
)

const reportsDir = "/srv/reports"

// DownloadReport is CWE-22: `?file=../../etc/passwd` walks out of reportsDir.
func DownloadReport(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("file")
	http.ServeFile(w, r, filepath.Join(reportsDir, name)) // gap: CWE-22 - nothing in this build reports it (docs/cwe-top25.md)
}
