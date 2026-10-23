package main

import (
	"bytes"
	"encoding/json"
	"image"
	"image/png"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func newTestServer(t *testing.T, maxUpload int64) (*httptest.Server, string) {
	t.Helper()
	dir := t.TempDir()
	s, err := NewServer(dir, maxUpload)
	if err != nil {
		t.Fatal(err)
	}
	ts := httptest.NewServer(s.Handler())
	t.Cleanup(ts.Close)
	return ts, dir
}

func upload(t *testing.T, ts *httptest.Server, filename string, data []byte) *http.Response {
	t.Helper()
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, err := mw.CreateFormFile("file", filename)
	if err != nil {
		t.Fatal(err)
	}
	fw.Write(data)
	mw.Close()
	resp, err := http.Post(ts.URL+"/files", mw.FormDataContentType(), &buf)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	return resp
}

func do(t *testing.T, method, url string) *http.Response {
	t.Helper()
	req, _ := http.NewRequest(method, url, nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	return resp
}

func pngBytes(t *testing.T, w, h int) []byte {
	t.Helper()
	var buf bytes.Buffer
	if err := png.Encode(&buf, image.NewRGBA(image.Rect(0, 0, w, h))); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func TestUploadListDownloadDelete(t *testing.T) {
	ts, dir := newTestServer(t, 1<<20)

	resp := upload(t, ts, "hello.txt", []byte("hello world"))
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("upload status = %d", resp.StatusCode)
	}
	if b, _ := os.ReadFile(filepath.Join(dir, "hello.txt")); string(b) != "hello world" {
		t.Fatalf("stored content = %q", b)
	}

	resp, err := http.Get(ts.URL + "/files")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var files []fileInfo
	if err := json.NewDecoder(resp.Body).Decode(&files); err != nil {
		t.Fatal(err)
	}
	if len(files) != 1 || files[0].Name != "hello.txt" || files[0].Size != 11 || files[0].UploadedAt.IsZero() {
		t.Fatalf("unexpected list: %+v", files)
	}

	resp = do(t, "GET", ts.URL+"/files/hello.txt")
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 || string(body) != "hello world" {
		t.Fatalf("download = %d %q", resp.StatusCode, body)
	}
	if cd := resp.Header.Get("Content-Disposition"); cd == "" {
		t.Fatal("missing Content-Disposition")
	}

	if resp = do(t, "DELETE", ts.URL+"/files/hello.txt"); resp.StatusCode != http.StatusNoContent {
		t.Fatalf("delete status = %d", resp.StatusCode)
	}
	if resp = do(t, "GET", ts.URL+"/files/hello.txt"); resp.StatusCode != http.StatusNotFound {
		t.Fatalf("download after delete = %d", resp.StatusCode)
	}
	if resp = do(t, "DELETE", ts.URL+"/files/hello.txt"); resp.StatusCode != http.StatusNotFound {
		t.Fatalf("second delete = %d", resp.StatusCode)
	}
}

func TestUploadDuplicateConflict(t *testing.T) {
	ts, _ := newTestServer(t, 1<<20)
	upload(t, ts, "a.txt", []byte("one"))
	if resp := upload(t, ts, "a.txt", []byte("two")); resp.StatusCode != http.StatusConflict {
		t.Fatalf("status = %d, want 409", resp.StatusCode)
	}
}

func TestUploadTooLarge(t *testing.T) {
	ts, dir := newTestServer(t, 100)
	resp := upload(t, ts, "big.bin", make([]byte, 1000))
	if resp.StatusCode != http.StatusRequestEntityTooLarge {
		t.Fatalf("status = %d, want 413", resp.StatusCode)
	}
	if _, err := os.Stat(filepath.Join(dir, "big.bin")); !os.IsNotExist(err) {
		t.Fatal("partial file was not removed")
	}
}

func TestUploadPathTraversalIsContained(t *testing.T) {
	ts, dir := newTestServer(t, 1<<20)
	resp := upload(t, ts, "../../evil.txt", []byte("x"))
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	if _, err := os.Stat(filepath.Join(dir, "evil.txt")); err != nil {
		t.Fatalf("expected sanitized file inside dir: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "..", "evil.txt")); err == nil {
		t.Fatal("file escaped storage directory")
	}
}

func TestInvalidNames(t *testing.T) {
	ts, _ := newTestServer(t, 1<<20)
	if resp := upload(t, ts, ".hidden", []byte("x")); resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("dotfile upload = %d, want 400", resp.StatusCode)
	}
	if resp := do(t, "GET", ts.URL+"/files/.thumbs"); resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("get .thumbs = %d, want 400", resp.StatusCode)
	}
	if resp := do(t, "DELETE", ts.URL+"/files/..%2Fx"); resp.StatusCode == http.StatusNoContent {
		t.Fatal("traversal delete succeeded")
	}
}

func TestMissingFilePart(t *testing.T) {
	ts, _ := newTestServer(t, 1<<20)
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	mw.WriteField("other", "x")
	mw.Close()
	resp, err := http.Post(ts.URL+"/files", mw.FormDataContentType(), &buf)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", resp.StatusCode)
	}
}

func TestThumbnail(t *testing.T) {
	ts, dir := newTestServer(t, 1<<20)
	upload(t, ts, "pic.png", pngBytes(t, 400, 200))

	if resp := do(t, "GET", ts.URL+"/files/pic.png/thumbnail"); resp.StatusCode != http.StatusNotFound {
		t.Fatalf("thumbnail before generation = %d", resp.StatusCode)
	}
	if resp := do(t, "POST", ts.URL+"/files/pic.png/thumbnail"); resp.StatusCode != http.StatusCreated {
		t.Fatalf("generate = %d", resp.StatusCode)
	}
	resp := do(t, "GET", ts.URL+"/files/pic.png/thumbnail")
	if resp.StatusCode != 200 || resp.Header.Get("Content-Type") != "image/png" {
		t.Fatalf("get thumbnail = %d %s", resp.StatusCode, resp.Header.Get("Content-Type"))
	}
	img, err := png.Decode(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	if b := img.Bounds(); b.Dx() != 128 || b.Dy() != 64 {
		t.Fatalf("thumbnail size = %dx%d, want 128x64", b.Dx(), b.Dy())
	}

	// Thumbnails must not show up in the listing, and are removed with the file.
	resp, _ = http.Get(ts.URL + "/files")
	var files []fileInfo
	json.NewDecoder(resp.Body).Decode(&files)
	resp.Body.Close()
	if len(files) != 1 {
		t.Fatalf("list = %+v", files)
	}
	do(t, "DELETE", ts.URL+"/files/pic.png")
	if _, err := os.Stat(filepath.Join(dir, thumbDirName, "pic.png.png")); !os.IsNotExist(err) {
		t.Fatal("thumbnail not removed on delete")
	}
}

func TestThumbnailErrors(t *testing.T) {
	ts, _ := newTestServer(t, 1<<20)
	upload(t, ts, "note.txt", []byte("not an image"))
	if resp := do(t, "POST", ts.URL+"/files/note.txt/thumbnail"); resp.StatusCode != http.StatusUnsupportedMediaType {
		t.Fatalf("non-image = %d, want 415", resp.StatusCode)
	}
	if resp := do(t, "POST", ts.URL+"/files/missing.png/thumbnail"); resp.StatusCode != http.StatusNotFound {
		t.Fatalf("missing = %d, want 404", resp.StatusCode)
	}
}

func TestResizeSmallImageKeepsSize(t *testing.T) {
	out := resize(image.NewRGBA(image.Rect(0, 0, 50, 20)), 128)
	if b := out.Bounds(); b.Dx() != 50 || b.Dy() != 20 {
		t.Fatalf("size = %dx%d, want 50x20", b.Dx(), b.Dy())
	}
}
