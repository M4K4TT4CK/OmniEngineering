package main

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// Config is the JSON config file. Unknown fields are rejected so typos fail loudly.
type Config struct {
	Listen        string   `json:"listen"`
	Interval      string   `json:"interval"`
	RetentionDays int      `json:"retention_days"`
	Sources       []string `json:"sources"`
	WorkDir       string   `json:"work_dir"`
	S3            S3Config `json:"s3"`
}

type S3Config struct {
	Endpoint string `json:"endpoint"`
	Region   string `json:"region"`
	Bucket   string `json:"bucket"`
	Prefix   string `json:"prefix"`
	Secure   bool   `json:"secure"`
}

const backupNamePrefix = "backup-"
const backupSuffix = ".tar.gz"

// LoadConfig reads, defaults, and validates the config file at path.
func LoadConfig(path string) (Config, error) {
	f, err := os.Open(path)
	if err != nil {
		return Config{}, err
	}
	defer f.Close()

	dec := json.NewDecoder(f)
	dec.DisallowUnknownFields()
	var cfg Config
	if err := dec.Decode(&cfg); err != nil {
		return Config{}, fmt.Errorf("parse %s: %w", path, err)
	}
	if err := cfg.normalize(); err != nil {
		return Config{}, fmt.Errorf("invalid config %s: %w", path, err)
	}
	return cfg, nil
}

func (c *Config) normalize() error {
	if c.Listen == "" {
		c.Listen = ":8080"
	}
	if c.WorkDir == "" {
		c.WorkDir = os.TempDir()
	}
	if c.S3.Prefix != "" && !strings.HasSuffix(c.S3.Prefix, "/") {
		c.S3.Prefix += "/"
	}
	if d, err := time.ParseDuration(c.Interval); err != nil || d <= 0 {
		return fmt.Errorf("interval must be a positive duration like \"6h\", got %q", c.Interval)
	}
	if c.RetentionDays <= 0 {
		return errors.New("retention_days must be greater than 0")
	}
	if len(c.Sources) == 0 {
		return errors.New("sources must not be empty")
	}
	if c.S3.Endpoint == "" || c.S3.Bucket == "" {
		return errors.New("s3.endpoint and s3.bucket are required")
	}
	return nil
}

// IntervalDuration returns the schedule interval. Valid only after LoadConfig.
func (c Config) IntervalDuration() time.Duration {
	d, _ := time.ParseDuration(c.Interval)
	return d
}

// Archive writes a gzipped tar of every file and directory under sources to dst.
// Entry names are absolute paths with the leading slash removed, so sources
// with the same base name do not collide.
func Archive(dst io.Writer, sources []string) error {
	gz := gzip.NewWriter(dst)
	tw := tar.NewWriter(gz)

	for _, src := range sources {
		root, err := filepath.Abs(src)
		if err != nil {
			return err
		}
		err = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			info, err := d.Info()
			if err != nil {
				return err
			}
			var link string
			if info.Mode()&fs.ModeSymlink != 0 {
				if link, err = os.Readlink(path); err != nil {
					return err
				}
			}
			// Skip sockets, devices, and pipes; they cannot be archived meaningfully.
			if !info.Mode().IsRegular() && !info.IsDir() && link == "" {
				return nil
			}

			hdr, err := tar.FileInfoHeader(info, link)
			if err != nil {
				return err
			}
			hdr.Name = strings.TrimPrefix(filepath.ToSlash(path), "/")
			if info.IsDir() {
				hdr.Name += "/"
			}
			if err := tw.WriteHeader(hdr); err != nil {
				return err
			}
			if !info.Mode().IsRegular() {
				return nil
			}

			f, err := os.Open(path)
			if err != nil {
				return err
			}
			defer f.Close()
			_, err = io.Copy(tw, f)
			return err
		})
		if err != nil {
			return fmt.Errorf("archive %s: %w", src, err)
		}
	}

	if err := tw.Close(); err != nil {
		return err
	}
	return gz.Close()
}

// Status describes the most recent run. It is exposed by the HTTP endpoint.
type Status struct {
	StartedAt  time.Time `json:"started_at"`
	FinishedAt time.Time `json:"finished_at"`
	OK         bool      `json:"ok"`
	Key        string    `json:"key,omitempty"`
	Bytes      int64     `json:"bytes,omitempty"`
	Pruned     int       `json:"pruned"`
	Error      string    `json:"error,omitempty"`
}

// Runner performs backups: archive, upload, then prune old backups.
type Runner struct {
	cfg    Config
	store  Store
	logger *log.Logger
	now    func() time.Time

	mu   sync.Mutex
	last *Status
}

func NewRunner(cfg Config, store Store, logger *log.Logger) *Runner {
	return &Runner{cfg: cfg, store: store, logger: logger, now: time.Now}
}

// Run performs one backup and records the result. It never panics on failure;
// the error is in the returned Status and the log.
func (r *Runner) Run(ctx context.Context) Status {
	start := r.now()
	st := Status{StartedAt: start}

	key, size, err := r.backup(ctx, start)
	st.Key, st.Bytes = key, size
	if err == nil {
		st.Pruned, err = r.prune(ctx, start)
	}

	st.FinishedAt = r.now()
	st.OK = err == nil
	if err != nil {
		st.Error = err.Error()
	}

	r.mu.Lock()
	r.last = &st
	r.mu.Unlock()

	took := st.FinishedAt.Sub(start).Round(time.Millisecond)
	if err != nil {
		r.logger.Printf("backup failed after %s: %v", took, err)
	} else {
		r.logger.Printf("backup ok key=%s bytes=%d pruned=%d took=%s", key, size, st.Pruned, took)
	}
	return st
}

// Last returns a copy of the most recent run, or nil if none has run yet.
func (r *Runner) Last() *Status {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.last == nil {
		return nil
	}
	s := *r.last
	return &s
}

func (r *Runner) backup(ctx context.Context, now time.Time) (string, int64, error) {
	// Archive to a temp file first so the upload has a known size.
	f, err := os.CreateTemp(r.cfg.WorkDir, "backup-*.tar.gz")
	if err != nil {
		return "", 0, err
	}
	defer os.Remove(f.Name())
	defer f.Close()

	if err := Archive(f, r.cfg.Sources); err != nil {
		return "", 0, err
	}
	info, err := f.Stat()
	if err != nil {
		return "", 0, err
	}
	if _, err := f.Seek(0, io.SeekStart); err != nil {
		return "", 0, err
	}

	key := r.cfg.S3.Prefix + backupNamePrefix + now.UTC().Format("20060102T150405Z") + backupSuffix
	if err := r.store.Put(ctx, key, f, info.Size()); err != nil {
		return "", 0, fmt.Errorf("upload %s: %w", key, err)
	}
	return key, info.Size(), nil
}

// prune deletes backups created by this tool that are older than the retention
// period. Objects that do not match the backup naming pattern are never touched.
func (r *Runner) prune(ctx context.Context, now time.Time) (int, error) {
	cutoff := now.AddDate(0, 0, -r.cfg.RetentionDays)
	objs, err := r.store.List(ctx, r.cfg.S3.Prefix+backupNamePrefix)
	if err != nil {
		return 0, fmt.Errorf("list backups: %w", err)
	}

	pruned := 0
	for _, o := range objs {
		if !strings.HasSuffix(o.Key, backupSuffix) || !o.LastModified.Before(cutoff) {
			continue
		}
		if err := r.store.Delete(ctx, o.Key); err != nil {
			return pruned, fmt.Errorf("delete %s: %w", o.Key, err)
		}
		pruned++
	}
	return pruned, nil
}
