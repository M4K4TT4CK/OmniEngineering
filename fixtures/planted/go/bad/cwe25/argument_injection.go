package cwe25

import (
	"context"
	"os"
	"os/exec"
	"time"
)

// CloneRepo is CWE-77 (argument injection, CWE-88): a repoURL of
// `--upload-pack=touch /tmp/pwned` is an option to git, not a URL.
func CloneRepo(ctx context.Context, repoURL string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	dir, err := os.MkdirTemp("", "import-")
	if err != nil {
		return "", err
	}
	return dir, exec.CommandContext(ctx, "git", "clone", repoURL, dir).Run() // gap: CWE-77 - nothing in this build reports it (docs/cwe-top25.md)
}
