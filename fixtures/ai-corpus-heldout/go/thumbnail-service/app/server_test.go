package main

import (
	"bytes"
	"encoding/json"
	"image"
	"image/color"
	"image/jpeg"
	"image/png"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

const testBaseURL = "http://thumbs.test"

func newTestServer(t *testing.T, cfg Config) *Server {
	t.Helper()
	cfg.CacheDir = t.TempDir()
	cfg.BaseURL = testBaseURL
	s, err := NewServer(cfg)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func testImage(w, h int) *image.RGBA {
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			img.Set(x, y, color.RGBA{uint8(x), uint8(y), 128, 255})
		}
	}
	return img
}

func pngBytes(t *testing.T, w, h int) []byte {
	t.Helper()
	var buf bytes.Buffer
	if err := png.Encode(&buf, testImage(w, h)); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func jpegBytes(t *testing.T, w, h int) []byte {
	t.Helper()
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, testImage(w, h), nil); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func uploadRequest(t *testing.T, file []byte, widths string) *http.Request {
	t.Helper()
	var body bytes.Buffer
	mw := multipart.NewWriter(&body)
	if widths != "" {
		mw.WriteField("widths", widths)
	}
	if file != nil {
		fw, err := mw.CreateFormFile("file", "image")
		if err != nil {
			t.Fatal(err)
		}
		fw.Write(file)
	}
	mw.Close()
	req := httptest.NewRequest(http.MethodPost, "/thumbnails", &body)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	return req
}

func jsonRequest(t *testing.T, v any) *http.Request {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, "/thumbnails", bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	return req
}

func do(s *Server, req *http.Request) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	s.ServeHTTP(rec, req)
	return rec
}

func decodeThumbs(t *testing.T, rec *httptest.ResponseRecorder) []thumbnail {
	t.Helper()
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rec.Code, rec.Body)
	}
	var resp struct {
		Thumbnails []thumbnail `json:"thumbnails"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	return resp.Thumbnails
}

// fetchThumb GETs a returned URL from the server and decodes it.
func fetchThumb(t *testing.T, s *Server, url string) (image.Image, string) {
	t.Helper()
	path := strings.TrimPrefix(url, testBaseURL)
	rec := do(s, httptest.NewRequest(http.MethodGet, path, nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("GET %s: status %d", path, rec.Code)
	}
	img, format, err := image.Decode(rec.Body)
	if err != nil {
		t.Fatal(err)
	}
	return img, format
}

func TestUploadGeneratesAndServesThumbnails(t *testing.T) {
	s := newTestServer(t, Config{})
	thumbs := decodeThumbs(t, do(s, uploadRequest(t, pngBytes(t, 200, 100), "50, 100,50")))

	if len(thumbs) != 2 || thumbs[0].Width != 50 || thumbs[1].Width != 100 {
		t.Fatalf("unexpected thumbnails: %+v", thumbs)
	}
	for _, th := range thumbs {
		if !strings.HasPrefix(th.URL, testBaseURL+"/thumbnails/") || !strings.HasSuffix(th.URL, ".png") {
			t.Fatalf("unexpected url %q", th.URL)
		}
		img, format := fetchThumb(t, s, th.URL)
		b := img.Bounds()
		if format != "png" || b.Dx() != th.Width || b.Dy() != th.Width/2 {
			t.Errorf("width %d: got %s %dx%d", th.Width, format, b.Dx(), b.Dy())
		}
	}
}

func TestCachedThumbnailIsReused(t *testing.T) {
	s := newTestServer(t, Config{})
	src := pngBytes(t, 120, 80)
	first := decodeThumbs(t, do(s, uploadRequest(t, src, "60")))
	path := filepath.Join(s.cfg.CacheDir, filepath.Base(first[0].URL))

	// Overwrite the cached file; a cache hit must serve it untouched.
	if err := os.WriteFile(path, []byte("sentinel"), 0o644); err != nil {
		t.Fatal(err)
	}
	second := decodeThumbs(t, do(s, uploadRequest(t, src, "60")))
	if second[0].URL != first[0].URL {
		t.Fatalf("url changed: %q vs %q", first[0].URL, second[0].URL)
	}
	got, _ := os.ReadFile(path)
	if string(got) != "sentinel" {
		t.Fatal("cached thumbnail was regenerated")
	}
}

func TestDoesNotUpscale(t *testing.T) {
	s := newTestServer(t, Config{})
	thumbs := decodeThumbs(t, do(s, uploadRequest(t, pngBytes(t, 40, 20), "400")))
	img, _ := fetchThumb(t, s, thumbs[0].URL)
	if b := img.Bounds(); b.Dx() != 40 || b.Dy() != 20 {
		t.Fatalf("got %dx%d, want 40x20", b.Dx(), b.Dy())
	}
}

func TestURLSource(t *testing.T) {
	src := jpegBytes(t, 300, 150)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "image/jpeg")
		w.Write(src)
	}))
	defer upstream.Close()

	s := newTestServer(t, Config{Fetcher: NewFetcher(1<<20, true)})
	thumbs := decodeThumbs(t, do(s, jsonRequest(t, map[string]any{"url": upstream.URL + "/a.jpg", "widths": []int{100}})))
	if !strings.HasSuffix(thumbs[0].URL, ".jpg") {
		t.Fatalf("expected jpeg thumbnail, got %q", thumbs[0].URL)
	}
	img, format := fetchThumb(t, s, thumbs[0].URL)
	if b := img.Bounds(); format != "jpeg" || b.Dx() != 100 || b.Dy() != 50 {
		t.Fatalf("got %s %dx%d", format, b.Dx(), b.Dy())
	}
}

func TestURLSourceTooLarge(t *testing.T) {
	src := pngBytes(t, 100, 100)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(src)
	}))
	defer upstream.Close()

	s := newTestServer(t, Config{Fetcher: NewFetcher(int64(len(src)-1), true)})
	rec := do(s, jsonRequest(t, map[string]any{"url": upstream.URL, "widths": []int{10}}))
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status %d, want 413", rec.Code)
	}
}

func TestURLToPrivateAddressIsBlocked(t *testing.T) {
	var hits atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
	}))
	defer upstream.Close()

	s := newTestServer(t, Config{}) // default fetcher blocks loopback
	for _, u := range []string{upstream.URL, strings.Replace(upstream.URL, "127.0.0.1", "localhost", 1)} {
		rec := do(s, jsonRequest(t, map[string]any{"url": u, "widths": []int{10}}))
		if rec.Code != http.StatusBadRequest {
			t.Errorf("%s: status %d, want 400", u, rec.Code)
		}
	}
	if hits.Load() != 0 {
		t.Fatal("request reached a loopback server")
	}
}

func TestRejectsBadURLs(t *testing.T) {
	s := newTestServer(t, Config{})
	for _, u := range []string{"file:///etc/passwd", "ftp://example.com/a.png", "not a url", "http://"} {
		rec := do(s, jsonRequest(t, map[string]any{"url": u, "widths": []int{10}}))
		if rec.Code != http.StatusBadRequest {
			t.Errorf("%q: status %d, want 400", u, rec.Code)
		}
	}
}

func TestRejectsBadWidths(t *testing.T) {
	s := newTestServer(t, Config{MaxWidth: 1000, MaxWidths: 3})
	src := pngBytes(t, 10, 10)
	for _, widths := range []string{"", "0", "-5", "abc", "1001", "1,2,3,4"} {
		rec := do(s, uploadRequest(t, src, widths))
		if rec.Code != http.StatusBadRequest {
			t.Errorf("widths %q: status %d, want 400", widths, rec.Code)
		}
	}
}

func TestRejectsInvalidInput(t *testing.T) {
	s := newTestServer(t, Config{MaxUploadBytes: 64 << 10, MaxPixels: 400})

	cases := []struct {
		name string
		req  *http.Request
		want int
	}{
		{"not an image", uploadRequest(t, []byte("hello"), "10"), http.StatusUnsupportedMediaType},
		{"too many pixels", uploadRequest(t, pngBytes(t, 30, 30), "10"), http.StatusUnprocessableEntity},
		{"upload too large", uploadRequest(t, bytes.Repeat([]byte("x"), 128<<10), "10"), http.StatusRequestEntityTooLarge},
		{"no source", uploadRequest(t, nil, "10"), http.StatusBadRequest},
		{"wrong content type", httptest.NewRequest(http.MethodPost, "/thumbnails", strings.NewReader("x")), http.StatusUnsupportedMediaType},
	}
	for _, c := range cases {
		if rec := do(s, c.req); rec.Code != c.want {
			t.Errorf("%s: status %d, want %d (%s)", c.name, rec.Code, c.want, rec.Body)
		}
	}
}

func TestServeRejectsUnknownNames(t *testing.T) {
	s := newTestServer(t, Config{})
	os.WriteFile(filepath.Join(s.cfg.CacheDir, "secret.txt"), []byte("x"), 0o644)
	for _, p := range []string{
		"/thumbnails/secret.txt",
		"/thumbnails/" + strings.Repeat("a", 64) + "_10.gif",
		"/thumbnails/" + strings.Repeat("a", 64) + "_10.png", // well-formed but not cached
	} {
		if rec := do(s, httptest.NewRequest(http.MethodGet, p, nil)); rec.Code != http.StatusNotFound {
			t.Errorf("%s: status %d, want 404", p, rec.Code)
		}
	}
}

func TestIsPublicAddr(t *testing.T) {
	cases := map[string]bool{
		"8.8.8.8":                true,
		"2606:4700::1111":        true,
		"127.0.0.1":              false,
		"10.1.2.3":               false,
		"172.16.0.1":             false,
		"192.168.1.1":            false,
		"169.254.169.254":        false,
		"100.64.0.1":             false,
		"0.0.0.0":                false,
		"::1":                    false,
		"fe80::1":                false,
		"fc00::1":                false,
		"::ffff:127.0.0.1":       false,
		"::ffff:169.254.169.254": false,
		"64:ff9b::a9fe:a9fe":     false,
		"224.0.0.1":              false,
	}
	for addr, want := range cases {
		if got := isPublicAddr(netip.MustParseAddr(addr)); got != want {
			t.Errorf("%s: got %v, want %v", addr, got, want)
		}
	}
}
