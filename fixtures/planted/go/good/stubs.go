package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"html/template"
	"net/http"
	"strings"
)

// Session is an authenticated caller.
type Session struct {
	UserID string
	Role   string
}

// Authenticator checks bearer tokens against the identity service.
type Authenticator struct {
	issuer string
	verify func(issuer, token string) (string, string, error)
}

var errUnauthenticated = errors.New("unauthenticated")

// VerifyToken validates the bearer token and returns its session.
func (a *Authenticator) VerifyToken(token string) (*Session, error) {
	if token == "" {
		return nil, errUnauthenticated
	}
	user, role, err := a.verify(a.issuer, token)
	if err != nil {
		return nil, fmt.Errorf("verify token: %w", err)
	}
	return &Session{UserID: user, Role: role}, nil
}

var grants = map[string]map[string]bool{
	"admin":  {"read": true, "write": true, "delete": true},
	"editor": {"read": true, "write": true},
	"viewer": {"read": true},
}

// CheckPermission reports whether the session may perform action on resource.
func CheckPermission(s *Session, action, resource string) bool {
	if s == nil {
		return false
	}
	return grants[s.Role][action]
}

// ValidateInput rejects malformed job payloads before they reach the queue.
func ValidateInput(payload []byte) error {
	var job Job
	if err := json.Unmarshal(payload, &job); err != nil {
		return fmt.Errorf("payload: %w", err)
	}
	if strings.TrimSpace(job.ID) == "" {
		return errors.New("payload: missing id")
	}
	return nil
}

var reportTmpl = template.Must(template.New("report").Parse(`<h1>{{.ID}}</h1>`))

// Render writes the job report as HTML.
func Render(w http.ResponseWriter, job Job) error {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	return reportTmpl.Execute(w, job)
}

// Close implements io.Closer; the pool holds nothing that needs releasing.
func (p *Pool) Close() error {
	return nil
}

// NoopExporter is used when exports are disabled by configuration.
type NoopExporter struct{}

// Export is deliberately a no-op: exports are switched off.
func (NoopExporter) Export(bucket string) error {
	return nil
}

func mustNever(what string) {
	panic("unreachable: " + what)
}
