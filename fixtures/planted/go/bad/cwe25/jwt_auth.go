package cwe25

import (
	"net/http"
	"strings"

	"github.com/golang-jwt/jwt/v5"
)

// UserFromRequest is CWE-287: the token's claims are trusted without checking its signature.
func UserFromRequest(r *http.Request) (string, bool) {
	raw := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	claims := jwt.MapClaims{}
	if _, _, err := jwt.NewParser().ParseUnverified(raw, claims); err != nil { // gap: CWE-287 - nothing in this build reports it (docs/cwe-top25.md)
		return "", false
	}
	sub, _ := claims["sub"].(string)
	return sub, sub != ""
}
