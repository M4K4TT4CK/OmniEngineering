package aicode

import (
	"bytes"
	"context"
	"errors"
	"net"
	"net/http"
	"net/netip"
	"syscall"
	"time"
)

type merchant struct {
	CallbackURL string `json:"callback_url"`
}

type forwarder struct {
	client *http.Client
}

// publicOnly refuses to connect to any non-public address, checked on the
// address actually dialled so DNS rebinding cannot slip past it.
func publicOnly(_, address string, _ syscall.RawConn) error {
	ap, err := netip.ParseAddrPort(address)
	if err != nil {
		return err
	}
	ip := ap.Addr()
	if ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsUnspecified() {
		return errors.New("callback address is not public")
	}
	return nil
}

func newForwarder() *forwarder {
	d := &net.Dialer{Timeout: 5 * time.Second, Control: publicOnly}
	return &forwarder{client: &http.Client{Timeout: 10 * time.Second,
		Transport: &http.Transport{DialContext: d.DialContext}}}
}

func (f *forwarder) deliver(ctx context.Context, m merchant, body []byte) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, m.CallbackURL, bytes.NewReader(body))
	if err != nil {
		return err
	}
	resp, err := f.client.Do(req)
	if err != nil {
		return err
	}
	return resp.Body.Close()
}
