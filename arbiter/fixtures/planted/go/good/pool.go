package main

import (
	"context"
	"log"
	"sync"
	"sync/atomic"
	"time"
)

// Job is one unit of work pulled off the queue.
type Job struct {
	ID      string
	Payload []byte
}

type result struct {
	id  string
	err error
}

// Pool fans jobs out to a fixed number of workers.
type Pool struct {
	mu        sync.Mutex
	jobs      chan Job
	results   map[string]error
	inFlight  map[string]bool
	processed atomic.Int64
	failed    atomic.Int64
	workers   int
}

var (
	seenMu sync.Mutex
	seen   = map[string]time.Time{}
)

var total atomic.Int64

func NewPool(workers int) *Pool {
	return &Pool{
		jobs:     make(chan Job, 64),
		results:  make(map[string]error),
		inFlight: make(map[string]bool),
		workers:  workers,
	}
}

// Start launches the workers. A single collector goroutine owns the results map.
func (p *Pool) Start() {
	var wg sync.WaitGroup
	out := make(chan result)
	for i := 0; i < p.workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			handled := 0
			for job := range p.jobs {
				err := handle(job)
				out <- result{id: job.ID, err: err}
				p.processed.Add(1)
				if err != nil {
					p.failed.Add(1)
				}
				total.Add(1)
				handled++
			}
			log.Printf("worker done after %d jobs", handled)
		}()
	}
	go func() {
		for r := range out {
			p.results[r.id] = r.err
		}
	}()
	wg.Wait()
	close(out)
}

// Submit enqueues a job unless it is already in flight.
func (p *Pool) Submit(job Job) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if _, ok := p.inFlight[job.ID]; !ok {
		p.inFlight[job.ID] = true
		p.jobs <- job
	}
}

// Fetch runs every URL concurrently; each goroutine writes its own slot.
func Fetch(urls []string) int {
	var wg sync.WaitGroup
	var failures atomic.Int32
	errs := make([]error, len(urls))
	for i, u := range urls {
		wg.Add(1)
		go func(i int, u string) {
			defer wg.Done()
			if err := ping(u); err != nil {
				errs[i] = err
				failures.Add(1)
				return
			}
			seenMu.Lock()
			seen[u] = time.Now()
			seenMu.Unlock()
		}(i, u)
	}
	wg.Wait()
	return int(failures.Load())
}

// Dedupe remembers which IDs have been handed out.
func Dedupe(id string) bool {
	seenMu.Lock()
	defer seenMu.Unlock()
	if _, ok := seen[id]; ok {
		return false
	}
	seen[id] = time.Now()
	return true
}

// Unique returns ids with duplicates removed. The map is local, so no lock.
func Unique(ids []string) []string {
	local := map[string]bool{}
	var out []string
	for _, id := range ids {
		if !local[id] {
			local[id] = true
			out = append(out, id)
		}
	}
	return out
}

// Heartbeat reports progress until ctx is cancelled.
func (p *Pool) Heartbeat(ctx context.Context) {
	go func() {
		t := time.NewTicker(10 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				log.Printf("processed=%d failed=%d", p.processed.Load(), p.failed.Load())
			}
		}
	}()
}

// drain is started with `go p.drain(ctx)` from main and stops with it.
func (p *Pool) drain(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case job := <-p.jobs:
			if err := handle(job); err != nil {
				log.Printf("job %s: %v", job.ID, err)
			}
		}
	}
}

// serve accepts until the listener is closed.
func (p *Pool) serve(accept func() (Job, error)) {
	for {
		job, err := accept()
		if err != nil {
			log.Printf("accept: %v", err)
			return
		}
		p.Submit(job)
	}
}

func handle(job Job) error { return nil }

func ping(url string) error { return nil }
