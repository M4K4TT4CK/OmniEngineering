package main

import (
	"context"
	"testing"
	"time"
)

func TestSubmitQueuesJob(t *testing.T) {
	p := NewPool(2)
	p.Submit(Job{ID: "a"})
	if got := len(p.jobs); got != 1 {
		t.Fatalf("queued %d jobs, want 1", got)
	}
}

func TestHeartbeatStops(t *testing.T) {
	if testing.Short() {
		t.Skip("heartbeat test waits on a real ticker")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	p := NewPool(1)
	p.Heartbeat(ctx)
	<-ctx.Done()
	assertDrained(t, p)
}

func TestFetchCountsFailures(t *testing.T) {
	if got := Fetch([]string{"a", "b"}); got != 0 {
		t.Fatalf("Fetch() = %d, want 0", got)
	}
}

func TestDedupe(t *testing.T) {
	if !Dedupe("x") {
		t.Fatal("first Dedupe(x) should be true")
	}
	if Dedupe("x") {
		t.Fatal("second Dedupe(x) should be false")
	}
}

func assertDrained(t *testing.T, p *Pool) {
	t.Helper()
	if n := len(p.jobs); n != 0 {
		t.Errorf("%d jobs still queued", n)
	}
}
