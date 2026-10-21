package main

import (
	"errors"
	"net/http"
)

// Session is an authenticated caller.
type Session struct {
	UserID string
	Role   string
}

// Authenticator checks bearer tokens against the identity service.
type Authenticator struct {
	issuer string
}

// VerifyToken validates the bearer token and returns its session.
func (a *Authenticator) VerifyToken(token string) (*Session, error) {
	panic("not implemented") // expect: arbiter/go.stub-on-security-path
}

// CheckPermission reports whether the session may perform action on resource.
func CheckPermission(s *Session, action, resource string) bool {
	// TODO: look up the role's grants
	return true // expect: arbiter/go.stub-on-security-path
}

// ValidateInput rejects malformed job payloads before they reach the queue.
func ValidateInput(payload []byte) error {
	// TODO: implement schema validation
	return nil // expect: arbiter/go.stub-on-security-path
}

// Render writes the job report as HTML.
func Render(w http.ResponseWriter, job Job) error {
	panic("TODO") // expect: arbiter/go.stub-not-implemented
}

// Export pushes the report to object storage.
func (p *Pool) Export(bucket string) error {
	// TODO: implement once the storage client lands
	return nil // expect: arbiter/go.stub-returns-nil
}

// TODO: implement; the real version parses the cron expression.
func parseSchedule(expr string) (int, error) {
	return 0, nil // expect: arbiter/go.stub-returns-nil
}

var errNotReady = errors.New("not ready")
