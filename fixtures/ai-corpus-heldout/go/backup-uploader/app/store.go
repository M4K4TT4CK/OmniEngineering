package main

import (
	"context"
	"io"
	"os"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

// Object is the subset of object metadata the uploader needs.
type Object struct {
	Key          string
	LastModified time.Time
}

// Store is the object-store operations the runner depends on. It exists so
// tests can substitute an in-memory fake.
type Store interface {
	Put(ctx context.Context, key string, r io.Reader, size int64) error
	List(ctx context.Context, prefix string) ([]Object, error)
	Delete(ctx context.Context, key string) error
}

type s3Store struct {
	client *minio.Client
	bucket string
}

// NewS3Store works with any S3-compatible service. Credentials come from the
// AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables.
func NewS3Store(cfg S3Config) (Store, error) {
	client, err := minio.New(cfg.Endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(os.Getenv("AWS_ACCESS_KEY_ID"), os.Getenv("AWS_SECRET_ACCESS_KEY"), ""),
		Secure: cfg.Secure,
		Region: cfg.Region,
	})
	if err != nil {
		return nil, err
	}
	return &s3Store{client: client, bucket: cfg.Bucket}, nil
}

func (s *s3Store) Put(ctx context.Context, key string, r io.Reader, size int64) error {
	_, err := s.client.PutObject(ctx, s.bucket, key, r, size, minio.PutObjectOptions{
		ContentType: "application/gzip",
	})
	return err
}

func (s *s3Store) List(ctx context.Context, prefix string) ([]Object, error) {
	// Cancel the listing goroutine if we return early on error.
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	var out []Object
	for obj := range s.client.ListObjects(ctx, s.bucket, minio.ListObjectsOptions{
		Prefix:    prefix,
		Recursive: true,
	}) {
		if obj.Err != nil {
			return nil, obj.Err
		}
		out = append(out, Object{Key: obj.Key, LastModified: obj.LastModified})
	}
	return out, nil
}

func (s *s3Store) Delete(ctx context.Context, key string) error {
	return s.client.RemoveObject(ctx, s.bucket, key, minio.RemoveObjectOptions{})
}
