package cwe25

import (
	"io"
	"net/http"
	"os"
	"path/filepath"
)

const uploadDir = "./public/uploads"

// UploadAvatar is CWE-434: any type, any size, stored under the client's filename.
func UploadAvatar(w http.ResponseWriter, r *http.Request) {
	file, header, err := r.FormFile("avatar")
	if err != nil {
		http.Error(w, "missing file", http.StatusBadRequest)
		return
	}
	defer file.Close()
	dst, err := os.Create(filepath.Join(uploadDir, header.Filename)) // expect: arbiter/go.upload-client-filename
	if err != nil {
		http.Error(w, "cannot store", http.StatusInternalServerError)
		return
	}
	defer dst.Close()
	_, _ = io.Copy(dst, file)
	w.WriteHeader(http.StatusCreated)
}
