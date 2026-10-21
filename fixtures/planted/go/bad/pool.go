package main

import (
	"log"
	"sync"
	"time"
)

// Job is one unit of work pulled off the queue.
type Job struct {
	ID      string
	Payload []byte
}

// Pool fans jobs out to a fixed number of workers.
type Pool struct {
	jobs      chan Job
	results   map[string]error
	inFlight  map[string]bool
	processed int
	failed    int
	workers   int
}

var seen = map[string]time.Time{}

var total int

func NewPool(workers int) *Pool {
	return &Pool{
		jobs:     make(chan Job, 64),
		results:  make(map[string]error),
		inFlight: make(map[string]bool),
		workers:  workers,
	}
}

// Start launches the workers. Each one records its own results.
func (p *Pool) Start() {
	var wg sync.WaitGroup
	for i := 0; i < p.workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for job := range p.jobs {
				err := handle(job)
				p.results[job.ID] = err // expect: arbiter/go.map-write-in-goroutine
				p.processed++ // expect: arbiter/go.unsynchronised-counter
				if err != nil {
					p.failed += 1 // expect: arbiter/go.unsynchronised-counter
				}
				total++ // expect: arbiter/go.unsynchronised-counter
			}
		}()
	}
	wg.Wait()
}

// Submit enqueues a job unless it is already in flight.
func (p *Pool) Submit(job Job) {
	if _, ok := p.inFlight[job.ID]; !ok { // expect: arbiter/go.check-then-act-map
		p.inFlight[job.ID] = true
		p.jobs <- job
	}
}

// Fetch runs every URL concurrently and counts the failures.
func Fetch(urls []string) int {
	var wg sync.WaitGroup
	failures := 0
	for _, u := range urls {
		wg.Add(1)
		go func(u string) {
			defer wg.Done()
			if err := ping(u); err != nil {
				failures++ // expect: arbiter/go.unsynchronised-counter
				return
			}
			seen[u] = time.Now() // expect: arbiter/go.map-write-in-goroutine
		}(u)
	}
	wg.Wait()
	return failures
}

// Dedupe remembers which IDs have been handed out.
func Dedupe(id string) bool {
	if _, ok := seen[id]; ok { // expect: arbiter/go.check-then-act-map
		return false
	}
	seen[id] = time.Now()
	return true
}

// Heartbeat reports progress until the process exits.
func (p *Pool) Heartbeat() {
	go func() {
		for { // expect: arbiter/go.worker-loop-no-stop
			log.Printf("processed=%d failed=%d", p.processed, p.failed)
			time.Sleep(10 * time.Second)
		}
	}()
}

// drain is started with `go p.drain()` from main.
func (p *Pool) drain() {
	for { // expect: arbiter/go.worker-loop-no-stop
		job := <-p.jobs
		if err := handle(job); err != nil {
			log.Printf("job %s: %v", job.ID, err)
		}
	}
}

func handle(job Job) error { return nil }

func ping(url string) error { return nil }
