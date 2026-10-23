package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"
)

// MaxRetries is the number of times a failed job is retried after its first attempt.
const MaxRetries = 3

type Status string

const (
	StatusQueued    Status = "queued" // waiting (including waiting for a retry)
	StatusRunning   Status = "running"
	StatusCompleted Status = "completed"
	StatusFailed    Status = "failed" // retries exhausted
)

var (
	ErrQueueFull   = errors.New("queue is full")
	ErrUnknownType = errors.New("unknown job type")
)

type Job struct {
	ID        string          `json:"id"`
	Type      string          `json:"type"`
	Payload   json.RawMessage `json:"payload"`
	Status    Status          `json:"status"`
	Attempts  int             `json:"attempts"`
	Result    any             `json:"result,omitempty"`
	Error     string          `json:"error,omitempty"`
	CreatedAt time.Time       `json:"created_at"`
	UpdatedAt time.Time       `json:"updated_at"`
}

// JobDef describes a job type: Validate runs at submission, Run runs in a worker.
type JobDef struct {
	Validate func(payload json.RawMessage) error
	Run      func(ctx context.Context, payload json.RawMessage) (any, error)
}

type Queue struct {
	retryDelay time.Duration
	ch         chan string
	stop       chan struct{}
	ctx        context.Context
	cancel     context.CancelFunc
	wg         sync.WaitGroup

	mu    sync.Mutex
	jobs  map[string]*Job
	defs  map[string]JobDef
	close sync.Once
}

// NewQueue starts `workers` goroutines. At most `size` jobs may be waiting at once.
func NewQueue(workers, size int, retryDelay time.Duration) *Queue {
	ctx, cancel := context.WithCancel(context.Background())
	q := &Queue{
		retryDelay: retryDelay,
		ch:         make(chan string, size),
		stop:       make(chan struct{}),
		ctx:        ctx,
		cancel:     cancel,
		jobs:       map[string]*Job{},
		defs:       map[string]JobDef{},
	}
	for i := 0; i < workers; i++ {
		q.wg.Add(1)
		go q.worker()
	}
	return q
}

// Register must be called before jobs of that type are submitted.
func (q *Queue) Register(name string, def JobDef) {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.defs[name] = def
}

func (q *Queue) Submit(typ string, payload json.RawMessage) (string, error) {
	q.mu.Lock()
	def, ok := q.defs[typ]
	q.mu.Unlock()
	if !ok {
		return "", fmt.Errorf("%w: %q", ErrUnknownType, typ)
	}
	if def.Validate != nil {
		if err := def.Validate(payload); err != nil {
			return "", err
		}
	}

	var b [12]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	now := time.Now()
	job := &Job{
		ID: hex.EncodeToString(b[:]), Type: typ, Payload: payload,
		Status: StatusQueued, CreatedAt: now, UpdatedAt: now,
	}

	// Store before enqueueing so a worker can always find the job.
	q.mu.Lock()
	q.jobs[job.ID] = job
	q.mu.Unlock()

	select {
	case q.ch <- job.ID:
		return job.ID, nil
	default:
		q.mu.Lock()
		delete(q.jobs, job.ID)
		q.mu.Unlock()
		return "", ErrQueueFull
	}
}

// Get returns a snapshot of the job.
func (q *Queue) Get(id string) (Job, bool) {
	q.mu.Lock()
	defer q.mu.Unlock()
	j, ok := q.jobs[id]
	if !ok {
		return Job{}, false
	}
	return *j, true
}

// Close stops accepting work, cancels running jobs' context and waits for goroutines.
func (q *Queue) Close() {
	q.close.Do(func() {
		close(q.stop)
		q.cancel()
	})
	q.wg.Wait()
}

func (q *Queue) worker() {
	defer q.wg.Done()
	for {
		select {
		case <-q.stop:
			return
		case id := <-q.ch:
			q.run(id)
		}
	}
}

func (q *Queue) run(id string) {
	q.mu.Lock()
	job := q.jobs[id]
	def := q.defs[job.Type]
	job.Status = StatusRunning
	job.Attempts++
	job.UpdatedAt = time.Now()
	payload := job.Payload
	q.mu.Unlock()

	result, err := safeRun(q.ctx, def, payload)

	q.mu.Lock()
	job.UpdatedAt = time.Now()
	retry := false
	switch {
	case err == nil:
		job.Status, job.Result, job.Error = StatusCompleted, result, ""
	case job.Attempts > MaxRetries:
		job.Status, job.Error = StatusFailed, err.Error()
	default:
		job.Status, job.Error = StatusQueued, err.Error()
		retry = true
	}
	q.mu.Unlock()

	if retry {
		q.scheduleRetry(id)
	}
}

func (q *Queue) scheduleRetry(id string) {
	q.wg.Add(1)
	go func() {
		defer q.wg.Done()
		select {
		case <-time.After(q.retryDelay):
		case <-q.stop:
			return
		}
		// Block until there is room; retries must not be dropped.
		select {
		case q.ch <- id:
		case <-q.stop:
		}
	}()
}

func safeRun(ctx context.Context, def JobDef, payload json.RawMessage) (res any, err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("job panicked: %v", r)
		}
	}()
	return def.Run(ctx, payload)
}
