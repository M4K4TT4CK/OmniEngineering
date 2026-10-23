package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	_ "image/gif"
	"image/jpeg"
	"image/png"
	"io"
	"io/fs"
	"log"
	"math"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"golang.org/x/image/draw"
	_ "golang.org/x/image/webp"
)

// Cached thumbnails are named <sha256 of source>_<width>.<ext>. Anything
// else is rejected before touching the filesystem.
var thumbName = regexp.MustCompile(`^[0-9a-f]{64}_[1-9][0-9]{0,4}\.(?:jpg|png)$`)

type Config struct {
	CacheDir       string
	BaseURL        string // public URL prefix used in responses, e.g. https://thumbs.example.com
	MaxUploadBytes int64
	MaxWidths      int   // max distinct widths per request
	MaxWidth       int   // largest width a client may request
	MaxPixels      int64 // largest source image (width*height) we will decode
	MaxConcurrent  int   // concurrent decode/resize jobs
	Fetcher        *Fetcher
}

type Server struct {
	cfg Config
	sem chan struct{}
	mux *http.ServeMux
}

type thumbnail struct {
	Width int    `json:"width"`
	URL   string `json:"url"`
}

type httpError struct {
	status int
	msg    string
}

func (e *httpError) Error() string { return e.msg }

func badRequest(format string, args ...any) error {
	return &httpError{http.StatusBadRequest, fmt.Sprintf(format, args...)}
}

func NewServer(cfg Config) (*Server, error) {
	if cfg.CacheDir == "" {
		return nil, errors.New("cache dir is required")
	}
	if err := os.MkdirAll(cfg.CacheDir, 0o755); err != nil {
		return nil, err
	}
	if cfg.MaxUploadBytes <= 0 {
		cfg.MaxUploadBytes = 20 << 20
	}
	if cfg.MaxWidths <= 0 {
		cfg.MaxWidths = 10
	}
	if cfg.MaxWidth <= 0 {
		cfg.MaxWidth = 4096
	}
	if cfg.MaxPixels <= 0 {
		cfg.MaxPixels = 50_000_000
	}
	if cfg.MaxConcurrent <= 0 {
		cfg.MaxConcurrent = 4
	}
	if cfg.Fetcher == nil {
		cfg.Fetcher = NewFetcher(cfg.MaxUploadBytes, false)
	}
	cfg.BaseURL = strings.TrimRight(cfg.BaseURL, "/")

	s := &Server{cfg: cfg, sem: make(chan struct{}, cfg.MaxConcurrent), mux: http.NewServeMux()}
	s.mux.HandleFunc("POST /thumbnails", s.handleCreate)
	s.mux.HandleFunc("GET /thumbnails/{name}", s.handleServe)
	return s, nil
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mux.ServeHTTP(w, r)
}

func (s *Server) handleCreate(w http.ResponseWriter, r *http.Request) {
	data, widths, err := s.readRequest(w, r)
	if err == nil {
		var thumbs []thumbnail
		thumbs, err = s.generate(r.Context(), data, widths)
		if err == nil {
			writeJSON(w, http.StatusOK, map[string]any{"thumbnails": thumbs})
			return
		}
	}
	var he *httpError
	if !errors.As(err, &he) {
		log.Printf("create thumbnails: %v", err)
		he = &httpError{http.StatusInternalServerError, "internal error"}
	}
	writeJSON(w, he.status, map[string]string{"error": he.msg})
}

// readRequest accepts either multipart/form-data with a "file" or "url"
// field plus "widths" (comma-separated and/or repeated), or a JSON body
// {"url": "...", "widths": [..]}.
func (s *Server) readRequest(w http.ResponseWriter, r *http.Request) ([]byte, []int, error) {
	ct, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
	switch ct {
	case "multipart/form-data":
		r.Body = http.MaxBytesReader(w, r.Body, s.cfg.MaxUploadBytes+1<<20)
		if err := r.ParseMultipartForm(8 << 20); err != nil {
			var mbe *http.MaxBytesError
			if errors.As(err, &mbe) {
				return nil, nil, &httpError{http.StatusRequestEntityTooLarge, "upload too large"}
			}
			return nil, nil, badRequest("invalid multipart body")
		}
		defer r.MultipartForm.RemoveAll()

		widths, err := s.parseWidths(r.MultipartForm.Value["widths"])
		if err != nil {
			return nil, nil, err
		}
		files := r.MultipartForm.File["file"]
		urls := r.MultipartForm.Value["url"]
		if len(files)+len(urls) != 1 {
			return nil, nil, badRequest(`provide exactly one of "file" or "url"`)
		}
		if len(urls) == 1 {
			data, err := s.fetch(r.Context(), urls[0])
			return data, widths, err
		}
		if files[0].Size > s.cfg.MaxUploadBytes {
			return nil, nil, &httpError{http.StatusRequestEntityTooLarge, "upload too large"}
		}
		f, err := files[0].Open()
		if err != nil {
			return nil, nil, err
		}
		defer f.Close()
		data, err := io.ReadAll(io.LimitReader(f, s.cfg.MaxUploadBytes+1))
		if err != nil {
			return nil, nil, err
		}
		if int64(len(data)) > s.cfg.MaxUploadBytes {
			return nil, nil, &httpError{http.StatusRequestEntityTooLarge, "upload too large"}
		}
		return data, widths, nil

	case "application/json":
		r.Body = http.MaxBytesReader(w, r.Body, 64<<10)
		var req struct {
			URL    string `json:"url"`
			Widths []int  `json:"widths"`
		}
		dec := json.NewDecoder(r.Body)
		dec.DisallowUnknownFields()
		if err := dec.Decode(&req); err != nil {
			return nil, nil, badRequest("invalid JSON body")
		}
		if req.URL == "" {
			return nil, nil, badRequest(`"url" is required`)
		}
		widths, err := s.validateWidths(req.Widths)
		if err != nil {
			return nil, nil, err
		}
		data, err := s.fetch(r.Context(), req.URL)
		return data, widths, err

	default:
		return nil, nil, &httpError{http.StatusUnsupportedMediaType, "use multipart/form-data or application/json"}
	}
}

func (s *Server) fetch(ctx context.Context, rawURL string) ([]byte, error) {
	data, err := s.cfg.Fetcher.Fetch(ctx, rawURL)
	switch {
	case err == nil:
		return data, nil
	case errors.Is(err, errInvalidURL), errors.Is(err, errBlockedAddress):
		return nil, badRequest("%v", err)
	case errors.Is(err, errTooLarge):
		return nil, &httpError{http.StatusRequestEntityTooLarge, err.Error()}
	default:
		log.Printf("fetch %q: %v", rawURL, err)
		return nil, &httpError{http.StatusBadGateway, "could not fetch image"}
	}
}

func (s *Server) parseWidths(values []string) ([]int, error) {
	var widths []int
	for _, v := range values {
		for _, part := range strings.Split(v, ",") {
			n, err := strconv.Atoi(strings.TrimSpace(part))
			if err != nil {
				return nil, badRequest("invalid width %q", part)
			}
			widths = append(widths, n)
		}
	}
	return s.validateWidths(widths)
}

// validateWidths checks bounds and removes duplicates, preserving order.
func (s *Server) validateWidths(in []int) ([]int, error) {
	if len(in) == 0 {
		return nil, badRequest("at least one width is required")
	}
	seen := make(map[int]bool)
	var out []int
	for _, w := range in {
		if w < 1 || w > s.cfg.MaxWidth {
			return nil, badRequest("width %d out of range 1-%d", w, s.cfg.MaxWidth)
		}
		if !seen[w] {
			seen[w] = true
			out = append(out, w)
		}
	}
	if len(out) > s.cfg.MaxWidths {
		return nil, badRequest("at most %d widths per request", s.cfg.MaxWidths)
	}
	return out, nil
}

// generate returns a thumbnail for each width, creating missing ones in the
// cache. Images are never upscaled: a width beyond the source width yields
// a copy at the source width.
func (s *Server) generate(ctx context.Context, data []byte, widths []int) ([]thumbnail, error) {
	// DecodeConfig only reads the header, so oversized images are rejected
	// before any pixel memory is allocated.
	cfg, format, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil {
		return nil, &httpError{http.StatusUnsupportedMediaType, "unsupported or invalid image"}
	}
	if cfg.Width < 1 || cfg.Height < 1 || int64(cfg.Width)*int64(cfg.Height) > s.cfg.MaxPixels {
		return nil, &httpError{http.StatusUnprocessableEntity, "image dimensions too large"}
	}
	sum := sha256.Sum256(data)
	key := hex.EncodeToString(sum[:])
	ext := "png"
	if format == "jpeg" {
		ext = "jpg"
	}

	var src image.Image
	out := make([]thumbnail, 0, len(widths))
	for _, w := range widths {
		name := fmt.Sprintf("%s_%d.%s", key, w, ext)
		path := filepath.Join(s.cfg.CacheDir, name)
		_, err := os.Stat(path)
		if errors.Is(err, fs.ErrNotExist) {
			if src == nil {
				select {
				case s.sem <- struct{}{}:
					defer func() { <-s.sem }()
				case <-ctx.Done():
					return nil, ctx.Err()
				}
				if src, _, err = image.Decode(bytes.NewReader(data)); err != nil {
					return nil, &httpError{http.StatusUnsupportedMediaType, "unsupported or invalid image"}
				}
			}
			if err := s.writeThumb(path, resize(src, w), ext); err != nil {
				return nil, err
			}
		} else if err != nil {
			return nil, err
		}
		out = append(out, thumbnail{Width: w, URL: s.cfg.BaseURL + "/thumbnails/" + name})
	}
	return out, nil
}

func resize(src image.Image, width int) image.Image {
	b := src.Bounds()
	if width > b.Dx() {
		width = b.Dx()
	}
	height := int(math.Round(float64(b.Dy()) * float64(width) / float64(b.Dx())))
	if height < 1 {
		height = 1
	}
	dst := image.NewRGBA(image.Rect(0, 0, width, height))
	draw.CatmullRom.Scale(dst, dst.Bounds(), src, b, draw.Src, nil)
	return dst
}

// writeThumb encodes to a temp file and renames it into place, so readers
// never see a partial file and concurrent writers of the same thumbnail
// are harmless.
func (s *Server) writeThumb(path string, img image.Image, ext string) error {
	tmp, err := os.CreateTemp(s.cfg.CacheDir, ".tmp-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if ext == "jpg" {
		err = jpeg.Encode(tmp, img, &jpeg.Options{Quality: 85})
	} else {
		err = png.Encode(tmp, img)
	}
	if cerr := tmp.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}

func (s *Server) handleServe(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	if !thumbName.MatchString(name) {
		http.NotFound(w, r)
		return
	}
	f, err := os.Open(filepath.Join(s.cfg.CacheDir, name))
	if err != nil {
		http.NotFound(w, r)
		return
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		http.NotFound(w, r)
		return
	}
	ctype := "image/png"
	if strings.HasSuffix(name, ".jpg") {
		ctype = "image/jpeg"
	}
	w.Header().Set("Content-Type", ctype)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	// Names are content-addressed, so a given URL never changes.
	w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	http.ServeContent(w, r, name, st.ModTime(), f)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}
