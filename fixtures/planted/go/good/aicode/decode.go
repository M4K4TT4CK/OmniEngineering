package aicode

import (
	"compress/gzip"
	"errors"
	"image"
	_ "image/png" // register the decoder
	"io"
	"net/http"
	"os"
)

const maxPixels = 40_000_000
const maxBytes = 1 << 20

func thumbnail(path string) (image.Image, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	cfg, _, err := image.DecodeConfig(f)
	if err != nil {
		return nil, err
	}
	if cfg.Width*cfg.Height > maxPixels {
		return nil, errors.New("image too large")
	}
	if _, err := f.Seek(0, io.SeekStart); err != nil {
		return nil, err
	}
	img, _, err := image.Decode(f)
	return img, err
}

func ingest(w http.ResponseWriter, r *http.Request) {
	zr, err := gzip.NewReader(http.MaxBytesReader(w, r.Body, maxBytes))
	if err != nil {
		http.Error(w, "bad gzip", http.StatusBadRequest)
		return
	}
	data, err := io.ReadAll(io.LimitReader(zr, 16*maxBytes+1))
	if err != nil || len(data) > 16*maxBytes {
		http.Error(w, "too large", http.StatusRequestEntityTooLarge)
		return
	}
	_, _ = w.Write(data[:0])
}

func echo(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxBytes)
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "too large", http.StatusRequestEntityTooLarge)
		return
	}
	_, _ = w.Write(body)
}
