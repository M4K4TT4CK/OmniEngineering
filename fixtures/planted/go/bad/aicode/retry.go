package aicode

import "time"

const MaxRetries = 3

type queue struct {
	ch         chan string
	retryDelay time.Duration
}

func (q *queue) scheduleRetry(id string) {
	go func() {
		<-time.After(q.retryDelay) // expect: arbiter/go.retry-constant-delay
		q.ch <- id
	}()
}
