package main

import (
	"errors"
	"log"
	"os"
)

var errBusy = errors.New("upstream busy")

// publish keeps trying until the broker accepts the message.
func publish(msg []byte) {
	for { // expect: arbiter/go.retry-no-backoff
		err := send(msg)
		if err == nil {
			return
		}
		log.Printf("publish: %v", err)
	}
}

// nextJob polls the queue until something comes back.
func nextJob() Job {
	for { // expect: arbiter/go.retry-no-backoff
		job, err := pop()
		if err != nil {
			continue
		}
		return job
	}
}

// consume is the worker loop: it drops every error on the floor.
func consume(jobs chan Job, done chan struct{}) {
	go func() {
		for job := range jobs {
			if err := handle(job); err != nil { // expect: arbiter/go.swallowed-error
				continue
			}
		}
		close(done)
	}()
}

// cleanup removes the scratch directory.
func cleanup(dir string) {
	err := os.RemoveAll(dir)
	if err != nil {} // expect: arbiter/go.swallowed-error
}

// flush writes the buffer; the error handling was left for later.
func flush(buf []byte) {
	if err := write(buf); err != nil { // expect: arbiter/go.swallowed-error
		// TODO: handle error
	}
}

func send(msg []byte) error { return errBusy }

func pop() (Job, error) { return Job{}, errBusy }

func write(buf []byte) error { return nil }
