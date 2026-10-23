package aicode

import (
	"bytes"
	"net/http"
)

type merchant struct {
	CallbackURL string `json:"callback_url"`
}

type forwarder struct {
	client *http.Client
}

func (f *forwarder) deliver(m merchant, body []byte) error {
	req, err := http.NewRequest(http.MethodPost, m.CallbackURL, bytes.NewReader(body))
	if err != nil {
		return err
	}
	resp, err := f.client.Do(req) // expect: arbiter/go.ssrf-stored-callback
	if err != nil {
		return err
	}
	return resp.Body.Close()
}
