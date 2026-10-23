package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"image/draw"
	"image/jpeg"
	"image/png"
	"net/mail"
	"net/smtp"
	"os"
	"path/filepath"
	"strings"

	_ "image/gif" // register GIF decoder
)

const maxDimension = 10000

// ---- resize-image ----

type resizePayload struct {
	Path   string `json:"path"`
	Width  int    `json:"width"`
	Height int    `json:"height"` // 0 keeps the aspect ratio
}

// resizeImageJob resizes images located under root. The output is written next to
// the input as "<name>-resized.<ext>" (GIFs are written as PNG).
func resizeImageJob(root string) JobDef {
	parse := func(raw json.RawMessage) (resizePayload, error) {
		var p resizePayload
		if err := json.Unmarshal(raw, &p); err != nil {
			return p, fmt.Errorf("invalid payload: %w", err)
		}
		switch {
		case p.Path == "":
			return p, errors.New("path is required")
		case p.Width <= 0 || p.Width > maxDimension:
			return p, fmt.Errorf("width must be between 1 and %d", maxDimension)
		case p.Height < 0 || p.Height > maxDimension:
			return p, fmt.Errorf("height must be between 0 and %d", maxDimension)
		}
		return p, nil
	}
	return JobDef{
		Validate: func(raw json.RawMessage) error { _, err := parse(raw); return err },
		Run: func(ctx context.Context, raw json.RawMessage) (any, error) {
			p, err := parse(raw)
			if err != nil {
				return nil, err
			}
			in, err := confine(root, p.Path)
			if err != nil {
				return nil, err
			}
			return resizeFile(in, p.Width, p.Height)
		},
	}
}

// confine resolves path (relative paths are relative to root) and ensures it
// stays inside root, following symlinks.
func confine(root, path string) (string, error) {
	absRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		return "", err
	}
	if !filepath.IsAbs(path) {
		path = filepath.Join(absRoot, path)
	}
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		return "", err
	}
	rel, err := filepath.Rel(absRoot, resolved)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", errors.New("path is outside the allowed image directory")
	}
	return resolved, nil
}

func resizeFile(path string, w, h int) (any, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	src, format, err := image.Decode(f)
	if err != nil {
		return nil, fmt.Errorf("decode image: %w", err)
	}
	sb := src.Bounds()
	if h == 0 {
		h = max(1, sb.Dy()*w/sb.Dx())
	}

	// Nearest-neighbour scaling keeps this dependency-free.
	dst := image.NewRGBA(image.Rect(0, 0, w, h))
	for y := 0; y < h; y++ {
		sy := sb.Min.Y + y*sb.Dy()/h
		for x := 0; x < w; x++ {
			sx := sb.Min.X + x*sb.Dx()/w
			dst.Set(x, y, src.At(sx, sy))
		}
	}

	ext := filepath.Ext(path)
	if format == "gif" {
		ext = ".png"
	}
	out := strings.TrimSuffix(path, filepath.Ext(path)) + "-resized" + ext
	of, err := os.Create(out)
	if err != nil {
		return nil, err
	}
	if format == "jpeg" {
		err = jpeg.Encode(of, draw.Image(dst), &jpeg.Options{Quality: 90})
	} else {
		err = png.Encode(of, dst)
	}
	if cerr := of.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return nil, fmt.Errorf("write output: %w", err)
	}
	return map[string]any{"output": out, "width": w, "height": h}, nil
}

// ---- send-email ----

type emailPayload struct {
	To      string `json:"to"`
	Subject string `json:"subject"`
	Body    string `json:"body"`
}

// sendMail is replaced in tests.
var sendMail = smtp.SendMail

// emailJob reads SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASS and
// SMTP_FROM (defaults to SMTP_USER) from the environment when the job runs.
func emailJob() JobDef {
	parse := func(raw json.RawMessage) (emailPayload, string, error) {
		var p emailPayload
		if err := json.Unmarshal(raw, &p); err != nil {
			return p, "", fmt.Errorf("invalid payload: %w", err)
		}
		addr, err := mail.ParseAddress(p.To)
		if err != nil {
			return p, "", fmt.Errorf("invalid recipient: %w", err)
		}
		if p.Subject == "" || p.Body == "" {
			return p, "", errors.New("subject and body are required")
		}
		if strings.ContainsAny(p.Subject, "\r\n") {
			return p, "", errors.New("subject must not contain line breaks")
		}
		return p, addr.Address, nil
	}
	return JobDef{
		Validate: func(raw json.RawMessage) error { _, _, err := parse(raw); return err },
		Run: func(ctx context.Context, raw json.RawMessage) (any, error) {
			p, to, err := parse(raw)
			if err != nil {
				return nil, err
			}
			host := os.Getenv("SMTP_HOST")
			if host == "" {
				return nil, errors.New("SMTP_HOST is not set")
			}
			port := os.Getenv("SMTP_PORT")
			if port == "" {
				port = "587"
			}
			user, pass := os.Getenv("SMTP_USER"), os.Getenv("SMTP_PASS")
			from := os.Getenv("SMTP_FROM")
			if from == "" {
				from = user
			}
			if from == "" {
				return nil, errors.New("SMTP_FROM (or SMTP_USER) is not set")
			}
			var auth smtp.Auth
			if user != "" {
				auth = smtp.PlainAuth("", user, pass, host)
			}
			msg := "From: " + from + "\r\nTo: " + to + "\r\nSubject: " + p.Subject +
				"\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n" + p.Body
			if err := sendMail(host+":"+port, auth, from, []string{to}, []byte(msg)); err != nil {
				return nil, fmt.Errorf("send mail: %w", err)
			}
			return map[string]any{"sent_to": to}, nil
		},
	}
}
