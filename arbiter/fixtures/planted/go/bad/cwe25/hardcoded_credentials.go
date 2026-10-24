package cwe25

import (
	"database/sql"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

var signingKey = []byte("s3cr3t-signing-key-do-not-share") // expect: arbiter/go.hardcoded-signing-key

const dsn = "postgres://app_user:Tr1cky-Pr0d-Pw@db.internal:5432/app?sslmode=require" // expect: arbiter/secrets.pg-url

// Open connects with the compiled-in credentials.
func Open() (*sql.DB, error) { return sql.Open("postgres", dsn) }

// OpenReports connects to the reporting replica with a password in the DSN.
func OpenReports() (*sql.DB, error) {
	return sql.Open("mysql", "reports:Rep0rts-Pr0d-Pw@tcp(db-replica.internal:3306)/reports") // expect: arbiter/go.hardcoded-dsn-password
}

// IssueToken signs with the compiled-in key.
func IssueToken(userID string) (string, error) {
	claims := jwt.MapClaims{"sub": userID, "exp": time.Now().Add(time.Hour).Unix()}
	return jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(signingKey)
}

// IssueAdminToken signs with a key literal written at the call.
func IssueAdminToken(userID string) (string, error) {
	claims := jwt.MapClaims{"sub": userID, "adm": true, "exp": time.Now().Add(time.Hour).Unix()}
	return jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString([]byte("adm1n-signing-key")) // expect: arbiter/go.hardcoded-signing-key
}

// ParseToken verifies with the same literal key.
func ParseToken(raw string) (*jwt.Token, error) {
	return jwt.Parse(raw, func(*jwt.Token) (any, error) {
		return []byte("s3cr3t-signing-key-do-not-share"), nil // expect: arbiter/go.hardcoded-signing-key
	}, jwt.WithValidMethods([]string{"HS256"}))
}
