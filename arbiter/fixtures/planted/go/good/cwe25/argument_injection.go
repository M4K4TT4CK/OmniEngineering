package cwe25

import (
	"context"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"time"
)

// ImportRepo accepts only https URLs and ends git's options with `--`.
func ImportRepo(w http.ResponseWriter, r *http.Request) {
	u, err := url.Parse(r.FormValue("repo_url"))
	if err != nil || u.Scheme != "https" {
		http.Error(w, "repository must be an https URL", http.StatusBadRequest)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Minute)
	defer cancel()
	dir, err := os.MkdirTemp("", "import-")
	if err != nil {
		http.Error(w, "no space", http.StatusInternalServerError)
		return
	}
	if err := exec.CommandContext(ctx, "git", "clone", "--", u.String(), dir).Run(); err != nil {
		http.Error(w, "clone failed", http.StatusBadGateway)
		return
	}
	w.WriteHeader(http.StatusCreated)
}
