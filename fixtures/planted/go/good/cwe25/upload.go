package cwe25

import (
	"crypto/rand"
	"encoding/hex"
	"io"
	"net/http"
	"os"
	"path/filepath"
)

const uploadDir = "./var/uploads"

var allowedTypes = map[string]string{"image/png": ".png", "image/jpeg": ".jpg"}

// UploadAvatar caps the size, sniffs the type against an allowlist and picks the name itself.
func UploadAvatar(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 2<<20)
	file, _, err := r.FormFile("avatar")
	if err != nil {
		http.Error(w, "missing or oversized file", http.StatusBadRequest)
		return
	}
	defer file.Close()
	head := make([]byte, 512)
	n, _ := io.ReadFull(file, head)
	ext, ok := allowedTypes[http.DetectContentType(head[:n])]
	if !ok {
		http.Error(w, "unsupported type", http.StatusUnsupportedMediaType)
		return
	}
	id := make([]byte, 16)
	_, _ = rand.Read(id)
	dst, err := os.Create(filepath.Join(uploadDir, hex.EncodeToString(id)+ext))
	if err != nil {
		http.Error(w, "cannot store", http.StatusInternalServerError)
		return
	}
	defer dst.Close()
	_, _ = dst.Write(head[:n])
	_, _ = io.Copy(dst, file)
	w.WriteHeader(http.StatusCreated)
}
