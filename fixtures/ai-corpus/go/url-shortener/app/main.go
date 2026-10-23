// Command urlshortener is a small URL shortener backed by SQLite.
package main

import (
	"crypto/rand"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"io"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"syscall"
	"time"

	_ "modernc.org/sqlite"
)

const (
	maxURLLen      = 2048
	maxBodyBytes   = 16 << 10
	maxPageBytes   = 512 << 10
	fetchTimeout   = 5 * time.Second
	codeAlphabet   = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
	generatedLen   = 7
	generateTries  = 5
	maxTitleLength = 300
)

var (
	aliasRe = regexp.MustCompile(`^[A-Za-z0-9_-]{1,32}$`)
	titleRe = regexp.MustCompile(`(?is)<title[^>]*>(.*?)</title>`)
	spaceRe = regexp.MustCompile(`\s+`)

	errConflict = errors.New("code already in use")
	errNotFound = errors.New("not found")
)

// reserved aliases that must not be used as short codes.
var reserved = map[string]bool{"api": true}

type link struct {
	Code      string    `json:"code"`
	URL       string    `json:"url"`
	Title     string    `json:"title"`
	Clicks    int64     `json:"clicks"`
	CreatedAt time.Time `json:"created_at"`
}

type server struct {
	db      *sql.DB
	client  *http.Client
	baseURL string // optional; derived from the request when empty
}

func newServer(dsn string, allowPrivate bool) (*server, error) {
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	// A single connection avoids SQLITE_BUSY and keeps ":memory:" databases coherent.
	db.SetMaxOpenConns(1)
	_, err = db.Exec(`CREATE TABLE IF NOT EXISTS links (
		code       TEXT PRIMARY KEY,
		url        TEXT NOT NULL,
		title      TEXT NOT NULL DEFAULT '',
		clicks     INTEGER NOT NULL DEFAULT 0,
		created_at INTEGER NOT NULL
	)`)
	if err != nil {
		db.Close()
		return nil, err
	}
	return &server{db: db, client: newFetchClient(allowPrivate)}, nil
}

// newFetchClient builds the client used to fetch page titles. Unless
// allowPrivate is set, connections to loopback/private/link-local addresses
// are refused (including after redirects) to prevent SSRF.
func newFetchClient(allowPrivate bool) *http.Client {
	dialer := &net.Dialer{Timeout: fetchTimeout}
	if !allowPrivate {
		dialer.Control = func(network, address string, _ syscall.RawConn) error {
			host, _, err := net.SplitHostPort(address)
			if err != nil {
				return err
			}
			if ip := net.ParseIP(host); ip == nil || blockedIP(ip) {
				return fmt.Errorf("refusing to connect to %s", host)
			}
			return nil
		}
	}
	return &http.Client{
		Timeout: fetchTimeout,
		Transport: &http.Transport{
			Proxy:       nil, // a proxy would bypass the dialer check
			DialContext: dialer.DialContext,
		},
		CheckRedirect: func(_ *http.Request, via []*http.Request) error {
			if len(via) >= 5 {
				return errors.New("too many redirects")
			}
			return nil
		},
	}
}

var cgnat = &net.IPNet{IP: net.IPv4(100, 64, 0, 0), Mask: net.CIDRMask(10, 32)}

func blockedIP(ip net.IP) bool {
	return ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() ||
		ip.IsLinkLocalMulticast() || ip.IsMulticast() || ip.IsUnspecified() ||
		cgnat.Contains(ip)
}

// fetchTitle returns the page title of rawURL, or "" if it can't be determined.
func (s *server) fetchTitle(rawURL string) string {
	req, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		return ""
	}
	req.Header.Set("User-Agent", "urlshortener/1.0")
	resp, err := s.client.Do(req)
	if err != nil {
		return ""
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return ""
	}
	body, _ := io.ReadAll(io.LimitReader(resp.Body, maxPageBytes))
	m := titleRe.FindSubmatch(body)
	if m == nil {
		return ""
	}
	title := strings.TrimSpace(spaceRe.ReplaceAllString(html.UnescapeString(string(m[1])), " "))
	if r := []rune(title); len(r) > maxTitleLength {
		title = string(r[:maxTitleLength])
	}
	return title
}

func randomCode() (string, error) {
	b := make([]byte, generatedLen)
	max := big.NewInt(int64(len(codeAlphabet)))
	for i := range b {
		n, err := rand.Int(rand.Reader, max)
		if err != nil {
			return "", err
		}
		b[i] = codeAlphabet[n.Int64()]
	}
	return string(b), nil
}

func (s *server) insert(l *link) error {
	_, err := s.db.Exec(`INSERT INTO links (code, url, title, clicks, created_at) VALUES (?, ?, ?, 0, ?)`,
		l.Code, l.URL, l.Title, l.CreatedAt.Unix())
	if err != nil && strings.Contains(err.Error(), "UNIQUE constraint failed") {
		return errConflict
	}
	return err
}

func (s *server) get(code string) (*link, error) {
	l := &link{}
	var created int64
	err := s.db.QueryRow(`SELECT code, url, title, clicks, created_at FROM links WHERE code = ?`, code).
		Scan(&l.Code, &l.URL, &l.Title, &l.Clicks, &created)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, errNotFound
	}
	if err != nil {
		return nil, err
	}
	l.CreatedAt = time.Unix(created, 0).UTC()
	return l, nil
}

// click atomically increments the click counter and returns the target URL.
func (s *server) click(code string) (string, error) {
	var target string
	err := s.db.QueryRow(`UPDATE links SET clicks = clicks + 1 WHERE code = ? RETURNING url`, code).Scan(&target)
	if errors.Is(err, sql.ErrNoRows) {
		return "", errNotFound
	}
	return target, err
}

func (s *server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/links", s.handleCreate)
	mux.HandleFunc("GET /api/links/{code}/stats", s.handleStats)
	mux.HandleFunc("GET /{code}", s.handleRedirect)
	return mux
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func validateURL(raw string) error {
	if len(raw) == 0 || len(raw) > maxURLLen {
		return fmt.Errorf("url must be between 1 and %d characters", maxURLLen)
	}
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
		return errors.New("url must be an absolute http or https URL")
	}
	return nil
}

func (s *server) handleCreate(w http.ResponseWriter, r *http.Request) {
	var req struct {
		URL   string `json:"url"`
		Alias string `json:"alias"`
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if err := validateURL(req.URL); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if req.Alias != "" && (!aliasRe.MatchString(req.Alias) || reserved[strings.ToLower(req.Alias)]) {
		writeError(w, http.StatusBadRequest, "alias must be 1-32 characters of letters, digits, '-' or '_' and not reserved")
		return
	}

	l := &link{
		URL:       req.URL,
		Title:     s.fetchTitle(req.URL),
		CreatedAt: time.Now().UTC().Truncate(time.Second),
	}
	var err error
	if req.Alias != "" {
		l.Code = req.Alias
		err = s.insert(l)
	} else {
		for i := 0; i < generateTries; i++ {
			if l.Code, err = randomCode(); err != nil {
				break
			}
			if err = s.insert(l); !errors.Is(err, errConflict) {
				break
			}
		}
	}
	switch {
	case errors.Is(err, errConflict):
		writeError(w, http.StatusConflict, "alias already in use")
		return
	case err != nil:
		log.Printf("create link: %v", err)
		writeError(w, http.StatusInternalServerError, "internal error")
		return
	}

	writeJSON(w, http.StatusCreated, map[string]any{
		"code":       l.Code,
		"short_url":  s.shortURL(r, l.Code),
		"url":        l.URL,
		"title":      l.Title,
		"created_at": l.CreatedAt,
	})
}

func (s *server) shortURL(r *http.Request, code string) string {
	base := s.baseURL
	if base == "" {
		base = "http://" + r.Host
	}
	return strings.TrimRight(base, "/") + "/" + code
}

func (s *server) handleRedirect(w http.ResponseWriter, r *http.Request) {
	target, err := s.click(r.PathValue("code"))
	if errors.Is(err, errNotFound) {
		writeError(w, http.StatusNotFound, "unknown code")
		return
	}
	if err != nil {
		log.Printf("redirect: %v", err)
		writeError(w, http.StatusInternalServerError, "internal error")
		return
	}
	http.Redirect(w, r, target, http.StatusFound)
}

func (s *server) handleStats(w http.ResponseWriter, r *http.Request) {
	l, err := s.get(r.PathValue("code"))
	if errors.Is(err, errNotFound) {
		writeError(w, http.StatusNotFound, "unknown code")
		return
	}
	if err != nil {
		log.Printf("stats: %v", err)
		writeError(w, http.StatusInternalServerError, "internal error")
		return
	}
	writeJSON(w, http.StatusOK, l)
}

func main() {
	addr := getenv("ADDR", ":8080")
	dsn := "file:" + getenv("DB_PATH", "links.db") + "?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)"
	s, err := newServer(dsn, false)
	if err != nil {
		log.Fatal(err)
	}
	s.baseURL = os.Getenv("BASE_URL")
	log.Printf("listening on %s", addr)
	log.Fatal((&http.Server{
		Addr:              addr,
		Handler:           s.routes(),
		ReadHeaderTimeout: 5 * time.Second,
	}).ListenAndServe())
}

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
