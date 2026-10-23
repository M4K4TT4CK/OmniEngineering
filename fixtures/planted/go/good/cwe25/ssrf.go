package cwe25

import (
	"errors"
	"io"
	"net/http"
	"net/url"
	"time"
)

var allowedHosts = map[string]bool{"github.com": true, "gitlab.com": true, "go.dev": true}

var previewClient = &http.Client{
	Timeout: 5 * time.Second,
	CheckRedirect: func(*http.Request, []*http.Request) error {
		return errors.New("redirects are not followed")
	},
}

// Preview fetches only https URLs on allowlisted hosts.
func Preview(w http.ResponseWriter, r *http.Request) {
	u, err := url.Parse(r.URL.Query().Get("url"))
	if err != nil || u.Scheme != "https" || !allowedHosts[u.Hostname()] {
		http.Error(w, "host not allowed", http.StatusBadRequest)
		return
	}
	resp, err := previewClient.Get(u.String())
	if err != nil {
		http.Error(w, "fetch failed", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()
	_, _ = io.Copy(w, io.LimitReader(resp.Body, 4096))
}

// Ping posts only to an https callback on an allowlisted host.
func Ping(w http.ResponseWriter, r *http.Request) {
	u, err := url.Parse(r.FormValue("callback"))
	if err != nil || u.Scheme != "https" || !allowedHosts[u.Hostname()] {
		http.Error(w, "callback not allowed", http.StatusBadRequest)
		return
	}
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, u.String(), nil)
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
