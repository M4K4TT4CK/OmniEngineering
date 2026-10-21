package cwe25

import (
	"context"
	"errors"
	"net/url"
	"os"
	"os/exec"
	"time"
)

// CloneRepo accepts only https URLs and ends git's options with `--`.
func CloneRepo(ctx context.Context, repoURL string) (string, error) {
	u, err := url.Parse(repoURL)
	if err != nil || u.Scheme != "https" {
		return "", errors.New("repository must be an https URL")
	}
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	dir, err := os.MkdirTemp("", "import-")
	if err != nil {
		return "", err
	}
	return dir, exec.CommandContext(ctx, "git", "clone", "--", u.String(), dir).Run()
}
