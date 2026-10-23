package main

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"errors"
	"io"
	"log"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
)

var testNow = time.Date(2026, 10, 23, 3, 0, 0, 0, time.UTC)

// fakeStore is an in-memory Store that records calls.
type fakeStore struct {
	objects map[string]time.Time
	puts    map[string]int64
	deleted []string
	putErr  error
}

func newFakeStore() *fakeStore {
	return &fakeStore{objects: map[string]time.Time{}, puts: map[string]int64{}}
}

func (f *fakeStore) Put(_ context.Context, key string, r io.Reader, size int64) error {
	if f.putErr != nil {
		return f.putErr
	}
	n, err := io.Copy(io.Discard, r)
	if err != nil {
		return err
	}
	if n != size {
		return errors.New("size mismatch")
	}
	f.puts[key] = n
	f.objects[key] = testNow
	return nil
}

func (f *fakeStore) List(_ context.Context, prefix string) ([]Object, error) {
	var out []Object
	for key, mod := range f.objects {
		if strings.HasPrefix(key, prefix) {
			out = append(out, Object{Key: key, LastModified: mod})
		}
	}
	return out, nil
}

func (f *fakeStore) Delete(_ context.Context, key string) error {
	delete(f.objects, key)
	f.deleted = append(f.deleted, key)
	return nil
}

func newTestRunner(cfg Config, store Store) *Runner {
	r := NewRunner(cfg, store, log.New(io.Discard, "", 0))
	r.now = func() time.Time { return testNow }
	return r
}

func testConfig(t *testing.T, src string) Config {
	t.Helper()
	return Config{
		Interval:      "6h",
		RetentionDays: 7,
		Sources:       []string{src},
		WorkDir:       t.TempDir(),
		S3:            S3Config{Endpoint: "s3.example.com", Bucket: "b", Prefix: "backups/"},
	}
}

func writeSourceTree(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "a"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "a", "b.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "c.txt"), []byte("world"), 0o644); err != nil {
		t.Fatal(err)
	}
	return dir
}

func TestArchiveContainsFilesAndContents(t *testing.T) {
	src := writeSourceTree(t)

	var buf bytes.Buffer
	if err := Archive(&buf, []string{src}); err != nil {
		t.Fatalf("Archive: %v", err)
	}

	gz, err := gzip.NewReader(&buf)
	if err != nil {
		t.Fatal(err)
	}
	tr := tar.NewReader(gz)
	got := map[string]string{}
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		if hdr.Typeflag != tar.TypeReg {
			continue
		}
		data, err := io.ReadAll(tr)
		if err != nil {
			t.Fatal(err)
		}
		got[hdr.Name] = string(data)
	}

	want := map[string]string{"/a/b.txt": "hello", "/c.txt": "world"}
	for suffix, content := range want {
		found := false
		for name, data := range got {
			if strings.HasSuffix(name, suffix) {
				found = true
				if data != content {
					t.Errorf("%s: content %q, want %q", name, data, content)
				}
			}
		}
		if !found {
			t.Errorf("archive has no entry ending in %s; entries: %v", suffix, got)
		}
	}
}

func TestArchiveMissingSourceFails(t *testing.T) {
	var buf bytes.Buffer
	err := Archive(&buf, []string{filepath.Join(t.TempDir(), "missing")})
	if err == nil {
		t.Fatal("expected error for missing source")
	}
}

func TestPruneRemovesOnlyOldBackups(t *testing.T) {
	src := writeSourceTree(t)
	store := newFakeStore()
	store.objects["backups/backup-20261001T000000Z.tar.gz"] = testNow.AddDate(0, 0, -22) // old: delete
	store.objects["backups/backup-20261020T030000Z.tar.gz"] = testNow.AddDate(0, 0, -3)  // recent: keep
	store.objects["backups/notes.txt"] = testNow.AddDate(0, 0, -60)                      // not ours: keep
	store.objects["other/backup-20260101T000000Z.tar.gz"] = testNow.AddDate(0, 0, -300)  // outside prefix: keep

	r := newTestRunner(testConfig(t, src), store)
	pruned, err := r.prune(context.Background(), testNow)
	if err != nil {
		t.Fatalf("prune: %v", err)
	}
	if pruned != 1 {
		t.Errorf("pruned = %d, want 1", pruned)
	}
	if want := []string{"backups/backup-20261001T000000Z.tar.gz"}; !equalStrings(store.deleted, want) {
		t.Errorf("deleted = %v, want %v", store.deleted, want)
	}
}

func TestRunSuccessUploadsAndRecordsStatus(t *testing.T) {
	src := writeSourceTree(t)
	store := newFakeStore()
	r := newTestRunner(testConfig(t, src), store)

	st := r.Run(context.Background())
	if !st.OK {
		t.Fatalf("Run failed: %s", st.Error)
	}
	wantKey := "backups/backup-20261023T030000Z.tar.gz"
	if st.Key != wantKey {
		t.Errorf("key = %q, want %q", st.Key, wantKey)
	}
	if store.puts[wantKey] == 0 || st.Bytes != store.puts[wantKey] {
		t.Errorf("uploaded %d bytes, status reports %d", store.puts[wantKey], st.Bytes)
	}

	last := r.Last()
	if last == nil || !last.OK || last.Key != wantKey {
		t.Errorf("Last() = %+v, want successful run with key %s", last, wantKey)
	}
}

func TestRunFailureIsRecorded(t *testing.T) {
	src := writeSourceTree(t)
	store := newFakeStore()
	store.putErr = errors.New("bucket unavailable")
	r := newTestRunner(testConfig(t, src), store)

	st := r.Run(context.Background())
	if st.OK {
		t.Fatal("Run succeeded, want failure")
	}
	if !strings.Contains(st.Error, "bucket unavailable") {
		t.Errorf("error = %q, want it to mention the upload failure", st.Error)
	}
	if last := r.Last(); last == nil || last.OK {
		t.Errorf("Last() = %+v, want a failed run", last)
	}
}

func TestStatusHandler(t *testing.T) {
	src := writeSourceTree(t)
	r := newTestRunner(testConfig(t, src), newFakeStore())
	h := statusHandler(r)

	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/status", nil))
	if !strings.Contains(rec.Body.String(), `"last_run":null`) {
		t.Errorf("before any run, body = %s", rec.Body.String())
	}

	r.Run(context.Background())

	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/status", nil))
	body := rec.Body.String()
	if !strings.Contains(body, `"ok":true`) || !strings.Contains(body, `"started_at"`) {
		t.Errorf("after run, body = %s", body)
	}
}

const validConfig = `{
  "interval": "6h",
  "retention_days": 30,
  "sources": ["/data/a"],
  "s3": {"endpoint": "s3.example.com", "bucket": "backups", "prefix": "nightly", "secure": true}
}`

func TestLoadConfig(t *testing.T) {
	path := filepath.Join(t.TempDir(), "backup.json")
	if err := os.WriteFile(path, []byte(validConfig), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := LoadConfig(path)
	if err != nil {
		t.Fatalf("LoadConfig: %v", err)
	}
	if cfg.Listen != ":8080" {
		t.Errorf("Listen = %q, want default :8080", cfg.Listen)
	}
	if cfg.S3.Prefix != "nightly/" {
		t.Errorf("S3.Prefix = %q, want trailing slash added", cfg.S3.Prefix)
	}
	if cfg.IntervalDuration() != 6*time.Hour {
		t.Errorf("IntervalDuration = %s, want 6h", cfg.IntervalDuration())
	}
}

func TestLoadConfigRejectsInvalid(t *testing.T) {
	cases := map[string]string{
		"unknown field":    strings.Replace(validConfig, `"interval"`, `"intervall"`, 1),
		"bad interval":     strings.Replace(validConfig, `"6h"`, `"soon"`, 1),
		"zero retention":   strings.Replace(validConfig, `30`, `0`, 1),
		"no sources":       strings.Replace(validConfig, `["/data/a"]`, `[]`, 1),
		"missing bucket":   strings.Replace(validConfig, `"bucket": "backups"`, `"bucket": ""`, 1),
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "backup.json")
			if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
				t.Fatal(err)
			}
			if _, err := LoadConfig(path); err == nil {
				t.Error("LoadConfig succeeded, want error")
			}
		})
	}
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	a = append([]string(nil), a...)
	b = append([]string(nil), b...)
	sort.Strings(a)
	sort.Strings(b)
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
