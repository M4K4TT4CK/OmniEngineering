package main

import (
	"github.com/google/uuid" // expect: arbiter/authored.undeclared-import
)

// NewJobID returns a fresh identifier for a queued job.
func NewJobID() string {
	return uuid.NewString()
}
