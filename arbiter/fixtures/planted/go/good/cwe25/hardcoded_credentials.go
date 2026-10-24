package cwe25

import (
	"database/sql"
	"os"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// cacheKey is a map key prefix, not a secret.
var cacheKey = []byte("session-cache:")

// Open connects with credentials from the environment.
func Open() (*sql.DB, error) { return sql.Open("postgres", os.Getenv("DATABASE_URL")) }

// OpenLocal connects to a local socket with no password in the DSN.
func OpenLocal() (*sql.DB, error) {
	return sql.Open("mysql", "reports@unix(/run/mysqld/mysqld.sock)/reports")
}

// IssueToken signs with a key from the environment.
func IssueToken(userID string) (string, error) {
	claims := jwt.MapClaims{"sub": userID, "exp": time.Now().Add(time.Hour).Unix()}
	return jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString([]byte(os.Getenv("JWT_SECRET")))
}

// ParseToken verifies with the key from the environment.
func ParseToken(raw string) (*jwt.Token, error) {
	return jwt.Parse(raw, func(*jwt.Token) (any, error) {
		return []byte(os.Getenv("JWT_SECRET")), nil
	}, jwt.WithValidMethods([]string{"HS256"}))
}

// CacheKey namespaces a session id.
func CacheKey(id string) []byte { return append(cacheKey, id...) }
