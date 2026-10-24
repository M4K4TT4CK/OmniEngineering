package cwe25

import (
	"errors"
	"net/http"
	"os"
	"strings"

	"github.com/golang-jwt/jwt/v5"
)

// UserFromRequest verifies the signature and pins the algorithm before reading claims.
func UserFromRequest(r *http.Request) (string, bool) {
	raw := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	claims := jwt.MapClaims{}
	_, err := jwt.ParseWithClaims(raw, claims, func(*jwt.Token) (any, error) {
		return []byte(os.Getenv("JWT_SECRET")), nil
	}, jwt.WithValidMethods([]string{"HS256"}), jwt.WithExpirationRequired())
	if err != nil {
		return "", false
	}
	sub, _ := claims["sub"].(string)
	return sub, sub != ""
}

// SessionUser reads the unverified issuer only to choose the key, then verifies
// the token (signature, algorithm and expiry) before trusting any claim.
func SessionUser(raw string, keys map[string][]byte) (string, bool) {
	unverified := jwt.MapClaims{}
	if _, _, err := jwt.NewParser().ParseUnverified(raw, unverified); err != nil {
		return "", false
	}
	iss, _ := unverified["iss"].(string)
	claims := jwt.MapClaims{}
	p := jwt.NewParser(jwt.WithValidMethods([]string{"HS256"}), jwt.WithExpirationRequired())
	if _, err := p.ParseWithClaims(raw, claims, func(*jwt.Token) (any, error) {
		if key, ok := keys[iss]; ok {
			return key, nil
		}
		return nil, errors.New("unknown issuer")
	}); err != nil {
		return "", false
	}
	sub, _ := claims["sub"].(string)
	return sub, sub != ""
}
