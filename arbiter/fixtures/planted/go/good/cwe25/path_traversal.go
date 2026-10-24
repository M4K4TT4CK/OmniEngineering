package cwe25

import (
	"net/http"
	"os"
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

// DeleteExport removes only a local name inside the exports directory.
func DeleteExport(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	if !filepath.IsLocal(name) {
		http.Error(w, "bad name", http.StatusBadRequest)
		return
	}
	if err := os.Remove(filepath.Join(reportsDir, "exports", name)); err != nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
