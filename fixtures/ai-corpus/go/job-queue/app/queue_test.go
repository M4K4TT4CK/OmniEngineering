package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"image"
	"image/color"
	"image/png"
	"net/http"
	"net/http/httptest"
	"net/smtp"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"
)

func waitDone(t *testing.T, q *Queue, id string) Job {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if j, ok := q.Get(id); ok && (j.Status == StatusCompleted || j.Status == StatusFailed) {
			return j
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("job %s did not finish", id)
	return Job{}
}

func newTestQueue(t *testing.T) *Queue {
	q := NewQueue(2, 10, time.Millisecond)
	t.Cleanup(q.Close)
	return q
}

func TestSuccess(t *testing.T) {
	q := newTestQueue(t)
	q.Register("ok", JobDef{Run: func(context.Context, json.RawMessage) (any, error) { return "done", nil }})
	id, err := q.Submit("ok", json.RawMessage(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	j := waitDone(t, q, id)
	if j.Status != StatusCompleted || j.Result != "done" || j.Attempts != 1 {
		t.Fatalf("unexpected job: %+v", j)
	}
}

func TestRetryThenSucceed(t *testing.T) {
	q := newTestQueue(t)
	var calls atomic.Int32
	q.Register("flaky", JobDef{Run: func(context.Context, json.RawMessage) (any, error) {
		if calls.Add(1) < 3 {
			return nil, errors.New("boom")
		}
		return "ok", nil
	}})
	id, _ := q.Submit("flaky", json.RawMessage(`{}`))
	j := waitDone(t, q, id)
	if j.Status != StatusCompleted || j.Attempts != 3 {
		t.Fatalf("unexpected job: %+v", j)
	}
}

func TestRetriesExhausted(t *testing.T) {
	q := newTestQueue(t)
	q.Register("bad", JobDef{Run: func(context.Context, json.RawMessage) (any, error) { panic("kaboom") }})
	id, _ := q.Submit("bad", json.RawMessage(`{}`))
	j := waitDone(t, q, id)
	if j.Status != StatusFailed || j.Attempts != 1+MaxRetries || j.Error == "" {
		t.Fatalf("unexpected job: %+v", j)
	}
}

func TestQueueFull(t *testing.T) {
	q := NewQueue(1, 1, time.Millisecond)
	release := make(chan struct{})
	started := make(chan struct{}, 1)
	q.Register("block", JobDef{Run: func(context.Context, json.RawMessage) (any, error) {
		started <- struct{}{}
		<-release
		return nil, nil
	}})
	defer func() { close(release); q.Close() }()

	q.Submit("block", json.RawMessage(`{}`))
	<-started // worker busy, buffer empty
	if _, err := q.Submit("block", json.RawMessage(`{}`)); err != nil {
		t.Fatal(err)
	}
	if _, err := q.Submit("block", json.RawMessage(`{}`)); !errors.Is(err, ErrQueueFull) {
		t.Fatalf("want ErrQueueFull, got %v", err)
	}
}

func TestHTTPFlow(t *testing.T) {
	q := newTestQueue(t)
	q.Register("echo", JobDef{Run: func(_ context.Context, p json.RawMessage) (any, error) { return string(p), nil }})
	srv := httptest.NewServer(newServer(q))
	defer srv.Close()

	post := func(body string) *http.Response {
		resp, err := http.Post(srv.URL+"/jobs", "application/json", bytes.NewBufferString(body))
		if err != nil {
			t.Fatal(err)
		}
		return resp
	}

	resp := post(`{"type":"echo","payload":{"a":1}}`)
	if resp.StatusCode != http.StatusAccepted {
		t.Fatalf("status %d", resp.StatusCode)
	}
	var created struct{ ID string }
	json.NewDecoder(resp.Body).Decode(&created)
	resp.Body.Close()
	waitDone(t, q, created.ID)

	resp, _ = http.Get(srv.URL + "/jobs/" + created.ID)
	var job Job
	json.NewDecoder(resp.Body).Decode(&job)
	resp.Body.Close()
	if job.Status != StatusCompleted || job.Result != `{"a":1}` {
		t.Fatalf("unexpected job: %+v", job)
	}

	for _, body := range []string{`{"type":"nope","payload":{}}`, `not json`, `{"type":"echo"}`} {
		if r := post(body); r.StatusCode != http.StatusBadRequest {
			t.Errorf("%s: want 400, got %d", body, r.StatusCode)
		}
	}
	if r, _ := http.Get(srv.URL + "/jobs/missing"); r.StatusCode != http.StatusNotFound {
		t.Errorf("want 404, got %d", r.StatusCode)
	}
}

func TestSendEmail(t *testing.T) {
	t.Setenv("SMTP_HOST", "smtp.test")
	t.Setenv("SMTP_PORT", "2525")
	t.Setenv("SMTP_FROM", "app@example.com")
	t.Setenv("SMTP_USER", "")

	var gotAddr, gotTo, gotMsg string
	orig := sendMail
	sendMail = func(addr string, _ smtp.Auth, from string, to []string, msg []byte) error {
		gotAddr, gotTo, gotMsg = addr, to[0], string(msg)
		return nil
	}
	defer func() { sendMail = orig }()

	q := newTestQueue(t)
	q.Register("send-email", emailJob())
	id, err := q.Submit("send-email", json.RawMessage(`{"to":"bob@example.com","subject":"Hi","body":"Hello"}`))
	if err != nil {
		t.Fatal(err)
	}
	j := waitDone(t, q, id)
	if j.Status != StatusCompleted || gotAddr != "smtp.test:2525" || gotTo != "bob@example.com" {
		t.Fatalf("job %+v addr=%q to=%q", j, gotAddr, gotTo)
	}
	if !bytes.Contains([]byte(gotMsg), []byte("Subject: Hi\r\n")) {
		t.Fatalf("bad message: %q", gotMsg)
	}
}

func TestSendEmailValidation(t *testing.T) {
	q := newTestQueue(t)
	q.Register("send-email", emailJob())
	for _, p := range []string{
		`{"to":"nope","subject":"s","body":"b"}`,
		`{"to":"a@b.com","subject":"s\r\nBcc: x@y.com","body":"b"}`,
		`{"to":"a@b.com","subject":"","body":"b"}`,
	} {
		if _, err := q.Submit("send-email", json.RawMessage(p)); err == nil {
			t.Errorf("expected validation error for %s", p)
		}
	}
}

func TestResizeImage(t *testing.T) {
	dir := t.TempDir()
	img := image.NewRGBA(image.Rect(0, 0, 40, 20))
	for x := 0; x < 40; x++ {
		for y := 0; y < 20; y++ {
			img.Set(x, y, color.RGBA{R: 255, A: 255})
		}
	}
	var buf bytes.Buffer
	png.Encode(&buf, img)
	if err := os.WriteFile(filepath.Join(dir, "in.png"), buf.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}

	q := newTestQueue(t)
	q.Register("resize-image", resizeImageJob(dir))
	id, err := q.Submit("resize-image", json.RawMessage(`{"path":"in.png","width":10}`))
	if err != nil {
		t.Fatal(err)
	}
	j := waitDone(t, q, id)
	if j.Status != StatusCompleted {
		t.Fatalf("unexpected job: %+v", j)
	}
	f, err := os.Open(filepath.Join(dir, "in-resized.png"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	out, _, err := image.Decode(f)
	if err != nil {
		t.Fatal(err)
	}
	if b := out.Bounds(); b.Dx() != 10 || b.Dy() != 5 {
		t.Fatalf("got %v, want 10x5", b)
	}
}

func TestResizeRejectsPathOutsideRoot(t *testing.T) {
	root, other := t.TempDir(), t.TempDir()
	outside := filepath.Join(other, "x.png")
	os.WriteFile(outside, []byte("x"), 0o644)

	if _, err := confine(root, outside); err == nil {
		t.Error("absolute path outside root accepted")
	}
	if _, err := confine(root, "../"+filepath.Base(other)+"/x.png"); err == nil {
		t.Error("relative traversal accepted")
	}

	q := newTestQueue(t)
	q.Register("resize-image", resizeImageJob(root))
	if _, err := q.Submit("resize-image", json.RawMessage(`{"path":"a.png","width":0}`)); err == nil {
		t.Error("expected validation error for width 0")
	}
}
