package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"syscall"
	"time"
)

var (
	errInvalidURL     = errors.New("url must be an absolute http or https URL")
	errBlockedAddress = errors.New("destination address is not allowed")
	errTooLarge       = errors.New("image exceeds size limit")
)

// Address ranges that are not covered by netip's IsPrivate/IsLoopback/etc.
// helpers but must never be reachable from a server-side fetch.
var blockedPrefixes = []netip.Prefix{
	netip.MustParsePrefix("0.0.0.0/8"),
	netip.MustParsePrefix("100.64.0.0/10"), // carrier-grade NAT
	netip.MustParsePrefix("192.0.0.0/24"),
	netip.MustParsePrefix("192.0.2.0/24"),
	netip.MustParsePrefix("198.18.0.0/15"),
	netip.MustParsePrefix("198.51.100.0/24"),
	netip.MustParsePrefix("203.0.113.0/24"),
	netip.MustParsePrefix("240.0.0.0/4"),
	netip.MustParsePrefix("100::/64"),
	netip.MustParsePrefix("64:ff9b::/96"),   // NAT64 can map to internal IPv4
	netip.MustParsePrefix("64:ff9b:1::/48"), // local-use NAT64
	netip.MustParsePrefix("2001::/32"),      // Teredo
	netip.MustParsePrefix("2001:db8::/32"),
	netip.MustParsePrefix("2002::/16"), // 6to4
}

// isPublicAddr reports whether a is a publicly routable unicast address.
func isPublicAddr(a netip.Addr) bool {
	a = a.Unmap()
	if !a.IsValid() || a.Zone() != "" {
		return false
	}
	if !a.IsGlobalUnicast() || a.IsPrivate() || a.IsLoopback() ||
		a.IsLinkLocalUnicast() || a.IsUnspecified() || a.IsMulticast() {
		return false
	}
	for _, p := range blockedPrefixes {
		if p.Contains(a) {
			return false
		}
	}
	return true
}

// Fetcher downloads remote images with SSRF protections: only http(s),
// no proxies, a bounded number of redirects, a size cap, and a check of
// every resolved IP at connect time (so DNS rebinding and redirects to
// internal hosts are caught too).
type Fetcher struct {
	client   *http.Client
	maxBytes int64
}

// NewFetcher returns a Fetcher. allowPrivate disables the address check and
// exists only for tests that fetch from a loopback httptest server.
func NewFetcher(maxBytes int64, allowPrivate bool) *Fetcher {
	dialer := &net.Dialer{Timeout: 5 * time.Second}
	if !allowPrivate {
		dialer.Control = func(_, address string, _ syscall.RawConn) error {
			ap, err := netip.ParseAddrPort(address)
			if err != nil || !isPublicAddr(ap.Addr()) {
				return errBlockedAddress
			}
			return nil
		}
	}
	transport := &http.Transport{
		// Never use environment proxies: the address check must apply to
		// the real destination.
		Proxy:                  nil,
		DialContext:            dialer.DialContext,
		ForceAttemptHTTP2:      true,
		TLSHandshakeTimeout:    5 * time.Second,
		ResponseHeaderTimeout:  10 * time.Second,
		MaxResponseHeaderBytes: 64 << 10,
		MaxIdleConns:           16,
		IdleConnTimeout:        30 * time.Second,
	}
	client := &http.Client{
		Transport: transport,
		Timeout:   30 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 3 {
				return errors.New("too many redirects")
			}
			if req.URL.Scheme != "http" && req.URL.Scheme != "https" {
				return errInvalidURL
			}
			return nil
		},
	}
	return &Fetcher{client: client, maxBytes: maxBytes}
}

// Fetch downloads the image at raw and returns its bytes.
func (f *Fetcher) Fetch(ctx context.Context, raw string) ([]byte, error) {
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" {
		return nil, errInvalidURL
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return nil, errInvalidURL
	}
	req.Header.Set("Accept", "image/*")

	resp, err := f.client.Do(req)
	if err != nil {
		if errors.Is(err, errBlockedAddress) {
			return nil, errBlockedAddress
		}
		if errors.Is(err, errInvalidURL) {
			return nil, errInvalidURL
		}
		return nil, fmt.Errorf("fetch: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("fetch: upstream returned status %d", resp.StatusCode)
	}
	if resp.ContentLength > f.maxBytes {
		return nil, errTooLarge
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, f.maxBytes+1))
	if err != nil {
		return nil, fmt.Errorf("fetch: %w", err)
	}
	if int64(len(data)) > f.maxBytes {
		return nil, errTooLarge
	}
	return data, nil
}
