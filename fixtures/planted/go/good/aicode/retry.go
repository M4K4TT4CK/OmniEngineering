package aicode

import (
	"math/rand"
	"time"
)

const MaxRetries = 3

type queue struct {
	ch         chan string
	retryDelay time.Duration
}

func (q *queue) scheduleRetry(id string, attempt int) {
	wait := q.retryDelay << attempt
	wait = wait/2 + time.Duration(rand.Int63n(int64(wait/2)+1))
	go func() {
		<-time.After(wait)
		q.ch <- id
	}()
}
