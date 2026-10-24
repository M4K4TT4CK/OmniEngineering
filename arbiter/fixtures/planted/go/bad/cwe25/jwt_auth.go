package cwe25

import (
	"net/http"
	"os"
	"strings"

	"github.com/golang-jwt/jwt/v5"
)

// UserFromRequest is CWE-287: the token's claims are trusted without checking its signature.
func UserFromRequest(r *http.Request) (string, bool) {
	raw := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	claims := jwt.MapClaims{}
	if _, _, err := jwt.NewParser().ParseUnverified(raw, claims); err != nil { // expect: arbiter/go.jwt-parse-unverified
		return "", false
	}
	sub, _ := claims["sub"].(string)
	return sub, sub != ""
}

// SessionUser is CWE-287: the signature is checked, but expiry and not-before are not,
// so a stolen token works forever.
func SessionUser(raw string) (string, bool) {
	claims := jwt.MapClaims{}
	p := jwt.NewParser(jwt.WithValidMethods([]string{"HS256"}), jwt.WithoutClaimsValidation()) // expect: arbiter/go.jwt-claims-validation-disabled
	if _, err := p.ParseWithClaims(raw, claims, func(*jwt.Token) (any, error) {
		return []byte(os.Getenv("JWT_SECRET")), nil
	}); err != nil {
		return "", false
	}
	sub, _ := claims["sub"].(string)
	return sub, sub != ""
}
