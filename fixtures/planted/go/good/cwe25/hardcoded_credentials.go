package cwe25

import (
	"database/sql"
	"os"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// Open connects with credentials from the environment.
func Open() (*sql.DB, error) { return sql.Open("postgres", os.Getenv("DATABASE_URL")) }

// IssueToken signs with a key from the environment.
func IssueToken(userID string) (string, error) {
	claims := jwt.MapClaims{"sub": userID, "exp": time.Now().Add(time.Hour).Unix()}
	return jwt.NewWithClaims(jwt.SigningMethodHS256, claims).SignedString([]byte(os.Getenv("JWT_SECRET")))
}
