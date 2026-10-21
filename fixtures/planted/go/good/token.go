package main

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"fmt"
)

// NewSessionToken returns the opaque token handed to the browser.
func NewSessionToken(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("session token: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

// ResetLink builds the one-time password-reset URL for a user.
func ResetLink(base, userID string) (string, error) {
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	resetToken := hex.EncodeToString(raw)
	return base + "/reset/" + userID + "/" + resetToken, nil
}
