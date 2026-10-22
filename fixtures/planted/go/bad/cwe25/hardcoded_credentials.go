package cwe25

import (
	"database/sql"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

var signingKey = []byte("s3cr3t-signing-key-do-not-share") // gap: CWE-798 - nothing in this build reports it (docs/cwe-top25.md)

const dsn = "postgres://app_user:Tr1cky-Pr0d-Pw@db.internal:5432/app?sslmode=require" // expect: arbiter/secrets.pg-url

// Open connects with the compiled-in credentials.
func Open() (*sql.DB, error) { return sql.Open("postgres", dsn) }

// IssueToken signs with the compiled-in key.
func IssueToken(userID string) (string, error) {
	claims := jwt.MapClaims{"sub": userID, "exp": time.Now().Add(time.Hour).Unix()}
	return jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString(signingKey)
}
