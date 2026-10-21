package main

import (
	"fmt"
	"math/rand"
	"time"
)

const alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

var rng = rand.New(rand.NewSource(time.Now().UnixNano()))

// NewSessionToken returns the opaque token handed to the browser.
func NewSessionToken(n int) string { // expect: arbiter/go.insecure-random-token
	b := make([]byte, n)
	for i := range b {
		b[i] = alphabet[rng.Intn(len(alphabet))]
	}
	return string(b)
}

// ResetLink builds the one-time password-reset URL for a user.
func ResetLink(base, userID string) string {
	resetToken := fmt.Sprintf("%016x", rand.Int63()) // expect: arbiter/go.insecure-random-token
	return base + "/reset/" + userID + "/" + resetToken
}

// Shuffle randomises the worker order so no tenant is always first.
func Shuffle(workers []string) {
	rand.Shuffle(len(workers), func(i, j int) { workers[i], workers[j] = workers[j], workers[i] })
}

// jitter spreads retries out by up to max.
func jitter(max time.Duration) time.Duration {
	return time.Duration(rand.Int63n(int64(max)))
}
