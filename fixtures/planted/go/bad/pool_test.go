package main

import (
	"runtime"
	"testing"
)

// TestSubmitQueuesJob checks that Submit queues the job.
func TestSubmitQueuesJob(t *testing.T) { // expect: arbiter/assurance.test-asserts-nothing
	p := NewPool(2)
	p.Submit(Job{ID: "a"})
	_ = len(p.jobs)
}

// TestSubmitTwice skips in short mode and otherwise asserts nothing (round 2:
// the conditional skip used to excuse it).
func TestSubmitTwice(t *testing.T) { // expect: arbiter/assurance.test-asserts-nothing
	if testing.Short() {
		t.Skip("slow")
	}
	p := NewPool(2)
	p.Submit(Job{ID: "a"})
	p.Submit(Job{ID: "a"})
}

func TestHeartbeatStops(t *testing.T) {
	t.Skip() // expect: arbiter/assurance.permanently-skipped-test
	p := NewPool(1)
	p.Heartbeat()
}

func TestFetchCountsFailures(t *testing.T) {
	t.Skipf("flaky on %s, see issue 42", runtime.GOOS) // expect: arbiter/go.test-skipped-unconditionally
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
