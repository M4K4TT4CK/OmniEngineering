package cwe25

import (
	"io"
	"net/http"
	"time"
)

var previewClient = &http.Client{Timeout: 5 * time.Second}

// Preview is CWE-918: the server fetches any URL the caller names, including 169.254.169.254.
func Preview(w http.ResponseWriter, r *http.Request) {
	resp, err := previewClient.Get(r.URL.Query().Get("url")) // gap: CWE-918 - nothing in this build reports it (docs/cwe-top25.md)
	if err != nil {
		http.Error(w, "fetch failed", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()
	_, _ = io.Copy(w, io.LimitReader(resp.Body, 4096))
}
