package main

import (
	"math/rand"
	"time"
)

// Shuffle randomises the worker order so no tenant is always first.
func Shuffle(workers []string) {
	rand.Shuffle(len(workers), func(i, j int) { workers[i], workers[j] = workers[j], workers[i] })
}

// jitter spreads retries out by up to max. Nothing secret depends on it.
func jitter(max time.Duration) time.Duration {
	return time.Duration(rand.Int63n(int64(max)))
}

// pickWorker chooses a worker at random for load spreading.
func pickWorker(workers []string) string {
	return workers[rand.Intn(len(workers))]
}
