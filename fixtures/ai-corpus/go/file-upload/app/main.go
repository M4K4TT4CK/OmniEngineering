// Command fileupload is a small HTTP file upload service backed by local disk.
package main

import (
	"encoding/json"
	"errors"
	"image"
	"image/color"
	"image/gif"
	"image/jpeg"
	"image/png"
	"io"
	"log"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	thumbDirName = ".thumbs"
	thumbMaxDim  = 128
)

// Server serves the upload API and stores files under Dir.
type Server struct {
	Dir            string
	MaxUploadBytes int64
}

// NewServer creates the storage directories and returns a Server.
func NewServer(dir string, maxUpload int64) (*Server, error) {
	if err := os.MkdirAll(filepath.Join(dir, thumbDirName), 0o755); err != nil {
		return nil, err
	}
	return &Server{Dir: dir, MaxUploadBytes: maxUpload}, nil
}

// Handler returns the HTTP routes.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /files", s.upload)
	mux.HandleFunc("GET /files", s.list)
	mux.HandleFunc("GET /files/{name}", s.download)
	mux.HandleFunc("DELETE /files/{name}", s.remove)
	mux.HandleFunc("POST /files/{name}/thumbnail", s.makeThumbnail)
	mux.HandleFunc("GET /files/{name}/thumbnail", s.getThumbnail)
	return mux
}

type fileInfo struct {
	Name       string    `json:"name"`
	Size       int64     `json:"size"`
	UploadedAt time.Time `json:"uploaded_at"`
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

// validName rejects anything that could escape the storage directory or
// collide with the internal thumbnail directory.
func validName(name string) bool {
	if name == "" || len(name) > 255 || strings.HasPrefix(name, ".") {
		return false
	}
	return !strings.ContainsAny(name, "/\\\x00") && filepath.Base(name) == name
}

func (s *Server) path(name string) string      { return filepath.Join(s.Dir, name) }
func (s *Server) thumbPath(name string) string { return filepath.Join(s.Dir, thumbDirName, name+".png") }

// nameParam returns the validated {name} path value, writing an error if invalid.
func nameParam(w http.ResponseWriter, r *http.Request) (string, bool) {
	name := r.PathValue("name")
	if !validName(name) {
		writeErr(w, http.StatusBadRequest, "invalid file name")
		return "", false
	}
	return name, true
}

// upload accepts multipart/form-data with a "file" part. The body is streamed
// to disk; existing files are never overwritten.
func (s *Server) upload(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, s.MaxUploadBytes)
	mr, err := r.MultipartReader()
	if err != nil {
		writeErr(w, http.StatusBadRequest, "expected multipart/form-data")
		return
	}
	for {
		part, err := mr.NextPart()
		if err == io.EOF {
			writeErr(w, http.StatusBadRequest, `missing "file" part`)
			return
		}
		if err != nil {
			s.bodyErr(w, err)
			return
		}
		if part.FormName() != "file" || part.FileName() == "" {
			continue
		}
		name := filepath.Base(strings.ReplaceAll(part.FileName(), "\\", "/"))
		if !validName(name) {
			writeErr(w, http.StatusBadRequest, "invalid file name")
			return
		}
		f, err := os.OpenFile(s.path(name), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
		if errors.Is(err, os.ErrExist) {
			writeErr(w, http.StatusConflict, "file already exists")
			return
		}
		if err != nil {
			writeErr(w, http.StatusInternalServerError, "cannot store file")
			return
		}
		_, err = io.Copy(f, part)
		if cerr := f.Close(); err == nil {
			err = cerr
		}
		if err != nil {
			os.Remove(s.path(name))
			s.bodyErr(w, err)
			return
		}
		fi, err := os.Stat(s.path(name))
		if err != nil {
			writeErr(w, http.StatusInternalServerError, "cannot stat file")
			return
		}
		writeJSON(w, http.StatusCreated, fileInfo{name, fi.Size(), fi.ModTime().UTC()})
		return
	}
}

func (s *Server) bodyErr(w http.ResponseWriter, err error) {
	var tooBig *http.MaxBytesError
	if errors.As(err, &tooBig) {
		writeErr(w, http.StatusRequestEntityTooLarge, "file too large")
		return
	}
	writeErr(w, http.StatusBadRequest, "invalid upload")
}

func (s *Server) list(w http.ResponseWriter, r *http.Request) {
	entries, err := os.ReadDir(s.Dir)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "cannot read storage")
		return
	}
	files := []fileInfo{}
	for _, e := range entries {
		if !e.Type().IsRegular() || !validName(e.Name()) {
			continue
		}
		fi, err := e.Info()
		if err != nil {
			continue
		}
		files = append(files, fileInfo{e.Name(), fi.Size(), fi.ModTime().UTC()})
	}
	sort.Slice(files, func(i, j int) bool { return files[i].Name < files[j].Name })
	writeJSON(w, http.StatusOK, files)
}

// open returns the regular file for name or writes a 404.
func (s *Server) open(w http.ResponseWriter, p string) (*os.File, os.FileInfo, bool) {
	f, err := os.Open(p)
	if err == nil {
		fi, serr := f.Stat()
		if serr == nil && fi.Mode().IsRegular() {
			return f, fi, true
		}
		f.Close()
	}
	writeErr(w, http.StatusNotFound, "not found")
	return nil, nil, false
}

func (s *Server) download(w http.ResponseWriter, r *http.Request) {
	name, ok := nameParam(w, r)
	if !ok {
		return
	}
	f, fi, ok := s.open(w, s.path(name))
	if !ok {
		return
	}
	defer f.Close()
	// Always force a download and disable sniffing so uploaded HTML/SVG
	// cannot execute in the service's origin.
	w.Header().Set("Content-Disposition", mime.FormatMediaType("attachment", map[string]string{"filename": name}))
	w.Header().Set("X-Content-Type-Options", "nosniff")
	http.ServeContent(w, r, name, fi.ModTime(), f)
}

func (s *Server) remove(w http.ResponseWriter, r *http.Request) {
	name, ok := nameParam(w, r)
	if !ok {
		return
	}
	if err := os.Remove(s.path(name)); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			writeErr(w, http.StatusNotFound, "not found")
		} else {
			writeErr(w, http.StatusInternalServerError, "cannot delete file")
		}
		return
	}
	os.Remove(s.thumbPath(name))
	w.WriteHeader(http.StatusNoContent)
}

// makeThumbnail decodes the stored image (PNG, JPEG or GIF) and writes a PNG
// thumbnail no larger than thumbMaxDim on either side.
func (s *Server) makeThumbnail(w http.ResponseWriter, r *http.Request) {
	name, ok := nameParam(w, r)
	if !ok {
		return
	}
	f, _, ok := s.open(w, s.path(name))
	if !ok {
		return
	}
	defer f.Close()
	img, _, err := image.Decode(f)
	if err != nil {
		writeErr(w, http.StatusUnsupportedMediaType, "not a supported image (png, jpeg, gif)")
		return
	}
	out, err := os.Create(s.thumbPath(name))
	if err != nil {
		writeErr(w, http.StatusInternalServerError, "cannot store thumbnail")
		return
	}
	err = png.Encode(out, resize(img, thumbMaxDim))
	if cerr := out.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(s.thumbPath(name))
		writeErr(w, http.StatusInternalServerError, "cannot store thumbnail")
		return
	}
	w.Header().Set("Location", "/files/"+name+"/thumbnail")
	writeJSON(w, http.StatusCreated, map[string]string{"thumbnail": "/files/" + name + "/thumbnail"})
}

func (s *Server) getThumbnail(w http.ResponseWriter, r *http.Request) {
	name, ok := nameParam(w, r)
	if !ok {
		return
	}
	f, fi, ok := s.open(w, s.thumbPath(name))
	if !ok {
		return
	}
	defer f.Close()
	w.Header().Set("Content-Type", "image/png")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	http.ServeContent(w, r, name+".png", fi.ModTime(), f)
}

// resize scales img down (preserving aspect ratio) so neither side exceeds
// maxDim, averaging the source pixels covered by each destination pixel.
// Images already small enough are returned at their original size.
func resize(src image.Image, maxDim int) image.Image {
	b := src.Bounds()
	sw, sh := b.Dx(), b.Dy()
	if sw <= maxDim && sh <= maxDim {
		maxDim = max(sw, sh)
	}
	dw, dh := maxDim, maxDim
	if sw >= sh {
		dh = max(1, sh*maxDim/sw)
	} else {
		dw = max(1, sw*maxDim/sh)
	}
	if sw <= maxDim && sh <= maxDim {
		dw, dh = sw, sh
	}
	dst := image.NewNRGBA(image.Rect(0, 0, dw, dh))
	for y := 0; y < dh; y++ {
		y0, y1 := b.Min.Y+y*sh/dh, b.Min.Y+(y+1)*sh/dh
		if y1 <= y0 {
			y1 = y0 + 1
		}
		for x := 0; x < dw; x++ {
			x0, x1 := b.Min.X+x*sw/dw, b.Min.X+(x+1)*sw/dw
			if x1 <= x0 {
				x1 = x0 + 1
			}
			var r, g, bl, a, n uint64
			for yy := y0; yy < y1; yy++ {
				for xx := x0; xx < x1; xx++ {
					pr, pg, pb, pa := src.At(xx, yy).RGBA()
					r, g, bl, a, n = r+uint64(pr), g+uint64(pg), bl+uint64(pb), a+uint64(pa), n+1
				}
			}
			dst.Set(x, y, color.RGBA64{uint16(r / n), uint16(g / n), uint16(bl / n), uint16(a / n)})
		}
	}
	return dst
}

// Ensure decoders are registered for image.Decode.
var (
	_ = gif.Decode
	_ = jpeg.Decode
)

func main() {
	dir := envOr("UPLOAD_DIR", "./uploads")
	addr := envOr("ADDR", ":8080")
	maxMB, err := strconv.ParseInt(envOr("MAX_UPLOAD_MB", "100"), 10, 64)
	if err != nil || maxMB <= 0 {
		log.Fatal("MAX_UPLOAD_MB must be a positive integer")
	}
	s, err := NewServer(dir, maxMB<<20)
	if err != nil {
		log.Fatal(err)
	}
	srv := &http.Server{
		Addr:              addr,
		Handler:           s.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
	}
	log.Printf("serving on %s, storing files in %s", addr, dir)
	log.Fatal(srv.ListenAndServe())
}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
