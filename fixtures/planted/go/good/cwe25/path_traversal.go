package cwe25

import (
	"net/http"
	"path/filepath"
)

const reportsDir = "/srv/reports"

// DownloadReport serves only a local, relative name inside reportsDir.
func DownloadReport(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("file")
	if !filepath.IsLocal(name) {
		http.Error(w, "bad file name", http.StatusBadRequest)
		return
	}
	http.ServeFile(w, r, filepath.Join(reportsDir, name))
}
