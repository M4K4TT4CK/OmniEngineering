package cwe25

import (
	"io"
	"net/http"
	"time"
)

var previewClient = &http.Client{Timeout: 5 * time.Second}

// Preview is CWE-918: the server fetches any URL the caller names, including 169.254.169.254.
func Preview(w http.ResponseWriter, r *http.Request) {
	resp, err := previewClient.Get(r.URL.Query().Get("url")) // expect: arbiter/go.ssrf-request-url
	if err != nil {
		http.Error(w, "fetch failed", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()
	_, _ = io.Copy(w, io.LimitReader(resp.Body, 4096))
}

// Ping is CWE-918 too: the callback URL is the caller's, so the request goes wherever they say.
func Ping(w http.ResponseWriter, r *http.Request) {
	callback := r.FormValue("callback")
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, callback, nil) // expect: arbiter/go.ssrf-request-url
	if err != nil {
		http.Error(w, "bad callback", http.StatusBadRequest)
		return
	}
	resp, err := previewClient.Do(req)
	if err != nil {
		http.Error(w, "callback failed", http.StatusBadGateway)
		return
	}
	_ = resp.Body.Close()
	w.WriteHeader(http.StatusNoContent)
}
