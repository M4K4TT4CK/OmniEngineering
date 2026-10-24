package cwe25

import (
	"context"
	"net/http"
	"os"
	"os/exec"
	"time"
)

// ImportRepo is CWE-77 (argument injection, CWE-88): a repo_url of
// `--upload-pack=touch /tmp/pwned` is an option to git, not a URL.
func ImportRepo(w http.ResponseWriter, r *http.Request) {
	repoURL := r.FormValue("repo_url")
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Minute)
	defer cancel()
	dir, err := os.MkdirTemp("", "import-")
	if err != nil {
		http.Error(w, "no space", http.StatusInternalServerError)
		return
	}
	if err := exec.CommandContext(ctx, "git", "clone", repoURL, dir).Run(); err != nil { // expect: arbiter/go.argument-injection
		http.Error(w, "clone failed", http.StatusBadGateway)
		return
	}
	w.WriteHeader(http.StatusCreated)
}
