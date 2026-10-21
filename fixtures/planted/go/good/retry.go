package main

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"time"
)

var errBusy = errors.New("upstream busy")

const maxAttempts = 5

// publish retries with exponential backoff and gives up after maxAttempts.
func publish(ctx context.Context, msg []byte) error {
	delay := 100 * time.Millisecond
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		err := send(msg)
		if err == nil {
			return nil
		}
		log.Printf("publish attempt %d: %v", attempt, err)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(delay):
		}
		delay *= 2
	}
	return fmt.Errorf("publish: gave up after %d attempts", maxAttempts)
}

// nextJob polls the queue, sleeping between misses.
func nextJob(ctx context.Context) (Job, error) {
	for {
		job, err := pop()
		if err != nil {
			if !errors.Is(err, errBusy) {
				return Job{}, err
			}
			select {
			case <-ctx.Done():
				return Job{}, ctx.Err()
			case <-time.After(250 * time.Millisecond):
			}
			continue
		}
		return job, nil
	}
}

// waitReady blocks until the dependency answers; each iteration waits on the channel.
func waitReady(ticks <-chan time.Time) {
	for {
		<-ticks
		if err := probe(); err != nil {
			continue
		}
		return
	}
}

// consume is the worker loop: every error is logged with its job id.
func consume(jobs chan Job, done chan struct{}) {
	go func() {
		for job := range jobs {
			if err := handle(job); err != nil {
				log.Printf("job %s: %v", job.ID, err)
				continue
			}
		}
		close(done)
	}()
}

// cleanup removes the scratch directory.
func cleanup(dir string) {
	err := os.RemoveAll(dir)
	if err != nil {
		log.Printf("cleanup %s: %v", dir, err)
	}
}

// flush writes the buffer. A failed flush is best effort by design.
func flush(buf []byte) {
	if err := write(buf); err != nil {
		// best effort: the buffer is rebuilt on the next tick, so a lost flush is harmless
	}
}

func send(msg []byte) error { return errBusy }

func pop() (Job, error) { return Job{}, errBusy }

func probe() error { return nil }

func write(buf []byte) error { return nil }

// serve is a protocol read loop (nsqlookupd's shape): a read error ends the
// loop, a bad command is answered and skipped. Nothing here is a retry.
func serve(r *bufio.Reader, handle func(string) error) {
	for {
		line, err := r.ReadString('\n')
		if err != nil {
			return
		}
		if err := handle(line); err != nil {
			log.Printf("bad command %q: %v", line, err)
			continue
		}
	}
}
