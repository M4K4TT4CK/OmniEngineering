package aicode

import (
	"compress/gzip"
	"image"
	_ "image/png" // register the decoder
	"io"
	"net/http"
	"os"
)

func thumbnail(path string) (image.Image, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	img, _, err := image.Decode(f) // expect: arbiter/go.image-decode-unbounded
	return img, err
}

func ingest(w http.ResponseWriter, r *http.Request) {
	zr, err := gzip.NewReader(r.Body)
	if err != nil {
		http.Error(w, "bad gzip", http.StatusBadRequest)
		return
	}
	data, err := io.ReadAll(zr) // expect: arbiter/go.decompress-read-unbounded
	if err != nil {
		http.Error(w, "read", http.StatusBadRequest)
		return
	}
	_, _ = w.Write(data[:0])
}

func echo(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body) // expect: arbiter/go.request-body-read-unbounded
	if err != nil {
		http.Error(w, "read", http.StatusBadRequest)
		return
	}
	_, _ = w.Write(body)
}
