"""The Go pack beyond the planted corpus: the shapes it must stay silent on.

Every rule's catch is asserted by tests/test_planted_corpus.py. These tests
pin the precision decisions taken after running the pack on cobra,
prometheus/client_golang, metrics-server, traefik, govwa, go-test-bench,
yaegi and the interview Go starter (ARB-061), so a later "improvement" that
re-reports a deliberate idiom fails here with the idiom named.
"""
from __future__ import annotations

import textwrap

import pytest

from arbiter import ast as ts
from arbiter.engine import run_scan
from arbiter.lang_rules import load_packs
from arbiter.policy import load_config

pytestmark = pytest.mark.skipif(not ts.available(), reason="tree-sitter not installed")


def _rules(tmp_path, code: str, name: str = "app.go") -> dict[str, list[int]]:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(code).lstrip(), encoding="utf-8")
    (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.22\n", encoding="utf-8")
    out: dict[str, list[int]] = {}
    for f in run_scan([str(tmp_path)], load_config(None), only=["lang_go"],
                      use_adapters=False, use_cache=False).active():
        out.setdefault(f.rule_id.split(".")[-1], []).append(f.location.start_line)
    return out


def test_pack_declares_a_cwe_on_every_rule():
    pack = load_packs()["go"]
    missing = [r["id"] for r in pack["rules"] if not r.get("cwe")]
    assert not missing, missing
    assert len(pack["rules"]) >= 16


def test_every_query_in_the_pack_compiles(tmp_path):
    # A query that does not compile drops the whole pack silently in a scan:
    # a `#` comment inside a `|` query block once took the planted score from
    # 41/41 to 4/41. Compile each one on its own so the broken rule is named.
    src = tmp_path / "a.go"
    src.write_text("package main\n", encoding="utf-8")
    parsed = ts.parse_file(str(src), "go")
    for rule in load_packs()["go"]["rules"]:
        for key in ("query", "unless_inside", "unless_in_scope", "unless_in_file", "requires_file"):
            if rule.get(key):
                try:
                    ts.run_query("go", rule[key], parsed)
                except ValueError as exc:  # pragma: no cover - the failure message is the point
                    pytest.fail(f"{rule['id']}.{key}: {exc}")


# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------

def test_a_client_with_a_timeout_is_silent(tmp_path):
    # Relies on `#not-match?` filtering; if a tree-sitter upgrade stops
    # honouring it, every http.Client literal reports and this fails.
    code = '''
    package main

    import (
    	"net/http"
    	"time"
    )

    var c = &http.Client{Timeout: 5 * time.Second, Transport: http.DefaultTransport}
    '''
    assert "http-client-no-timeout" not in _rules(tmp_path, code)


def test_a_client_whose_sdk_sets_the_deadline_is_silent(tmp_path):
    # traefik pkg/provider/docker: the client goes to docker's options
    # alongside client.WithTimeout.
    code = '''
    package main

    import (
    	"net/http"
    	"time"
    )

    func opts() []Opt {
    	hc := &http.Client{Transport: &http.Transport{}}
    	return []Opt{WithHTTPClient(hc), client.WithTimeout(30 * time.Second)}
    }
    '''
    assert "http-client-no-timeout" not in _rules(tmp_path, code)


def test_a_bare_client_is_reported(tmp_path):
    # traefik pkg/collector: a tuned Transport but no overall Timeout. Kept.
    code = '''
    package main

    import "net/http"

    func makeClient(t *http.Transport) *http.Client {
    	return &http.Client{Transport: t}
    }
    '''
    assert _rules(tmp_path, code).get("http-client-no-timeout") == [6]


def test_the_default_client_under_a_context_deadline_is_silent(tmp_path):
    code = '''
    package main

    import (
    	"context"
    	"net/http"
    	"time"
    )

    func fetch(url string) (*http.Response, error) {
    	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
    	defer cancel()
    	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
    	return http.DefaultClient.Do(req)
    }
    '''
    assert "http-default-client" not in _rules(tmp_path, code)


DB_HEADER = '''
package main

import (
	"context"
	"database/sql"
	"net/http"
)

type S struct{ db *sql.DB }
'''


def test_db_calls_without_a_context_in_reach_are_silent(tmp_path):
    # govwa's data layer and every startup migration: no ctx to pass.
    code = DB_HEADER + '''
func (s *S) Find(id string) error {
	_, err := s.db.Exec("UPDATE users SET seen = 1 WHERE id = $1", id)
	return err
}

func setup(db *sql.DB) error { return db.Ping() }
'''
    assert "db-call-no-deadline" not in _rules(tmp_path, code)


def test_url_query_in_a_handler_is_not_a_database_call(tmp_path):
    code = DB_HEADER + '''
func (s *S) Get(w http.ResponseWriter, r *http.Request) {
	id := r.URL.Query().Get("id")
	_ = s.db.QueryRowContext(r.Context(), "SELECT 1 WHERE $1", id)
}
'''
    assert "db-call-no-deadline" not in _rules(tmp_path, code)


def test_a_handler_that_drops_its_context_is_reported(tmp_path):
    code = DB_HEADER + '''
func (s *S) Get(w http.ResponseWriter, r *http.Request) {
	_ = s.db.QueryRow("SELECT 1")
	_, _ = s.db.ExecContext(context.TODO(), "SELECT 2")
}
'''
    assert _rules(tmp_path, code).get("db-call-no-deadline") == [12, 13]


def test_db_rule_needs_a_database_import(tmp_path):
    code = '''
    package main

    import "context"

    type Pool struct{ conn Runner }

    func (p *Pool) Run(ctx context.Context) error { return p.conn.Exec("job") }
    '''
    assert "db-call-no-deadline" not in _rules(tmp_path, code)


# ---------------------------------------------------------------------------
# Races
# ---------------------------------------------------------------------------

def test_per_goroutine_slots_and_a_collector_are_not_races(tmp_path):
    code = '''
    package main

    func fanout(items []string) []string {
    	results := make([]string, len(items))
    	done := make(chan struct{})
    	for i, it := range items {
    		go func(i int, it string) {
    			results[i] = it
    			done <- struct{}{}
    		}(i, it)
    	}
    	counts := map[string]int{}
    	ch := make(chan string)
    	go func() {
    		for s := range ch {
    			counts[s] = counts[s] + 1
    		}
    	}()
    	return results
    }
    '''
    found = _rules(tmp_path, code)
    assert "map-write-in-goroutine" not in found
    assert "unsynchronised-counter" not in found


def test_a_counter_under_a_lock_or_atomic_is_silent(tmp_path):
    code = '''
    package main

    import (
    	"sync"
    	"sync/atomic"
    )

    type P struct {
    	mu   sync.Mutex
    	n    int
    	hits atomic.Int64
    }

    func (p *P) run(jobs []int) {
    	for range jobs {
    		go func() {
    			p.mu.Lock()
    			p.n++
    			p.mu.Unlock()
    		}()
    		go func() { p.hits.Add(1) }()
    	}
    }
    '''
    assert "unsynchronised-counter" not in _rules(tmp_path, code)


def test_check_then_act_is_silent_in_a_file_without_goroutines(tmp_path):
    code = '''
    package main

    type C struct{ m map[string]int }

    func (c *C) Put(k string) {
    	if _, ok := c.m[k]; !ok {
    		c.m[k] = 1
    	}
    }
    '''
    assert "check-then-act-map" not in _rules(tmp_path, code)


# ---------------------------------------------------------------------------
# Loops
# ---------------------------------------------------------------------------

def test_a_goroutine_started_from_main_may_run_forever(tmp_path):
    code = '''
    package main

    import "time"

    func main() {
    	go func() {
    		for {
    			time.Sleep(time.Second)
    		}
    	}()
    	select {}
    }
    '''
    assert "worker-loop-no-stop" not in _rules(tmp_path, code)


def test_a_forever_goroutine_started_from_a_helper_is_reported(tmp_path):
    # client_golang examples/customlabels: started from startFireKeeper(),
    # not from main. Kept -- the same shape in a server is a leak.
    code = '''
    package main

    import "time"

    func startTicker() {
    	go func() {
    		for {
    			time.Sleep(time.Second)
    		}
    	}()
    }
    '''
    assert _rules(tmp_path, code).get("worker-loop-no-stop") == [7]


def test_a_retry_that_sleeps_is_silent(tmp_path):
    code = '''
    package main

    import "time"

    func get() error {
    	for {
    		err := call()
    		if err == nil {
    			return nil
    		}
    		time.Sleep(time.Second)
    	}
    }
    '''
    assert "retry-no-backoff" not in _rules(tmp_path, code)


def test_an_ignored_error_with_a_reason_is_a_decision(tmp_path):
    code = '''
    package main

    func f() {
    	if err := g(); err != nil {
    		// best effort: the cache is rebuilt on the next request
    	}
    }
    '''
    assert "swallowed-error" not in _rules(tmp_path, code)


# ---------------------------------------------------------------------------
# Injection, stubs, randomness, tests
# ---------------------------------------------------------------------------

def test_placeholder_lists_built_with_strings_join_are_not_injection(tmp_path):
    code = '''
    package main

    import (
    	"database/sql"
    	"strings"
    )

    func q(db *sql.DB, marks []string, args []any) {
    	db.Query("SELECT id FROM t WHERE id IN ("+strings.Join(marks, ",")+")", args...)
    }
    '''
    assert "sql-injection" not in _rules(tmp_path, code)


def test_exec_without_a_shell_is_not_command_injection(tmp_path):
    code = '''
    package main

    import "os/exec"

    func tar(name string) error { return exec.Command("tar", "czf", name, "/data").Run() }
    func lit() error { return exec.Command("sh", "-c", "echo ok").Run() }
    '''
    assert "command-injection" not in _rules(tmp_path, code)


def test_a_security_stub_is_reported_once(tmp_path):
    code = '''
    package main

    func VerifyToken(t string) bool {
    	panic("not implemented")
    }
    '''
    found = _rules(tmp_path, code)
    assert found.get("stub-on-security-path") == [4]
    assert "stub-not-implemented" not in found


def test_math_rand_for_jitter_or_a_tokenizer_is_not_a_secret(tmp_path):
    code = '''
    package main

    import "math/rand"

    func jitter() int { return rand.Intn(100) }

    func tokenize(s string) []string {
    	tokenizer := rand.Perm(3)
    	_ = tokenizer
    	return nil
    }
    '''
    assert "insecure-random-token" not in _rules(tmp_path, code)


def test_crypto_rand_tokens_are_silent(tmp_path):
    code = '''
    package main

    import (
    	"crypto/rand"
    	"encoding/hex"
    )

    func NewToken() string {
    	b := make([]byte, 32)
    	_, _ = rand.Read(b)
    	return hex.EncodeToString(b)
    }
    '''
    assert "insecure-random-token" not in _rules(tmp_path, code)


def test_a_conditional_skip_is_not_permanent(tmp_path):
    code = '''
    package main

    import "testing"

    func TestSlow(t *testing.T) {
    	if testing.Short() {
    		t.Skip("slow")
    	}
    	t.Log("ran")
    }

    func TestFlaky(t *testing.T) {
    	t.Skip("This test is flaky")
    }
    '''
    # traefik connpool_test.go: the reasoned unconditional skip is reported.
    assert _rules(tmp_path, code, "app_test.go").get("test-skipped-unconditionally") == [13]


# --------------------------------------------------------------- CWE Top 25 (ARB-062)

def test_error_text_is_reported_only_with_a_5xx_in_a_handler(tmp_path):
    code = '''
    package main

    import (
    	"errors"
    	"fmt"
    	"net/http"
    	"os"
    )

    var ErrBusy = errors.New("busy")

    func h(w http.ResponseWriter, r *http.Request) {
    	err := r.ParseForm()
    	if err != nil {
    		http.Error(w, err.Error(), http.StatusBadRequest)
    		return
    	}
    	http.Error(w, ErrBusy.Error(), http.StatusServiceUnavailable)
    	fmt.Fprintf(os.Stderr, "x: %v", err)
    	http.Error(w, err.Error(), http.StatusInternalServerError)
    }

    func cli(w *os.File, err error) { fmt.Fprintf(w, "failed: %v", err) }
    '''
    assert _rules(tmp_path, code).get("error-text-in-response") == [20]


def test_parse_unverified_before_a_verify_picks_the_key(tmp_path):
    code = '''
    package main

    import "github.com/golang-jwt/jwt/v5"

    func issuer(raw string) string {
    	c := jwt.MapClaims{}
    	_, _, _ = jwt.NewParser().ParseUnverified(raw, c)
    	return c["iss"].(string)
    }

    func verified(raw string, key []byte) error {
    	c := jwt.MapClaims{}
    	_, _, _ = jwt.NewParser().ParseUnverified(raw, c)
    	_, err := jwt.ParseWithClaims(raw, c, func(*jwt.Token) (any, error) { return key, nil })
    	return err
    }
    '''
    assert _rules(tmp_path, code).get("jwt-parse-unverified") == [7]


def test_a_byte_literal_needs_a_key_name_and_a_crypto_import(tmp_path):
    code = '''
    package main

    import "github.com/golang-jwt/jwt/v5"

    var bucketKeyLeases = []byte("leases")
    var jwtSecret = []byte("short")
    var signingKey = []byte("a-long-literal-key")

    func tok(t *jwt.Token) (string, error) { return t.SignedString(signingKey) }
    '''
    assert _rules(tmp_path, code).get("hardcoded-signing-key") == [7]
    assert "hardcoded-signing-key" not in _rules(
        tmp_path / "plain", 'package main\n\nvar signingKey = []byte("a-long-literal-key")\n')


def test_a_dsn_with_a_trivial_or_formatted_password_is_silent(tmp_path):
    code = '''
    package main

    import (
    	"database/sql"
    	"fmt"
    )

    func open(pw string) {
    	_, _ = sql.Open("mysql", "root:root@tcp(localhost:3306)/app")
    	_, _ = sql.Open("mysql", fmt.Sprintf("app:%s@tcp(db:3306)/app", pw))
    	_, _ = sql.Open("postgres", "host=db user=app password= dbname=app")
    	_, _ = sql.Open("postgres", "host=db user=app password=Xy9-real-pw dbname=app")
    }
    '''
    assert _rules(tmp_path, code).get("hardcoded-dsn-password") == [12]


# ------------------------------------------------- CWE Top 25 round 2 (ARB-062)

def test_a_request_value_joined_into_a_path_is_reported_unless_checked(tmp_path):
    code = '''
    package main

    import (
    	"net/http"
    	"os"
    	"path/filepath"
    	"strconv"
    )

    func show(w http.ResponseWriter, r *http.Request) {
    	name := r.URL.Query().Get("file")
    	if name == "" {
    		http.Error(w, "missing", http.StatusBadRequest)
    		return
    	}
    	http.ServeFile(w, r, filepath.Join("/srv", name))
    }

    func local(w http.ResponseWriter, r *http.Request) {
    	name := r.FormValue("file")
    	if !filepath.IsLocal(name) {
    		return
    	}
    	http.ServeFile(w, r, filepath.Join("/srv", name))
    }

    func based(w http.ResponseWriter, r *http.Request) {
    	name := filepath.Base(r.FormValue("file"))
    	page, _ := strconv.Atoi(r.FormValue("page"))
    	_, _ = os.ReadFile(filepath.Join("/srv", name, strconv.Itoa(page)))
    }

    func static(w http.ResponseWriter, r *http.Request) {
    	http.ServeFile(w, r, filepath.Join("/srv", r.URL.Path))
    	body := r.FormValue("body")
    	_ = os.WriteFile("/srv/notes.txt", []byte(body), 0o600)
    }
    '''
    assert _rules(tmp_path, code).get("path-traversal-request") == [16]


def test_upload_name_needs_a_multipart_upload_and_no_type_check(tmp_path):
    code = '''
    package main

    import (
    	"net/http"
    	"os"
    	"path/filepath"

    	"github.com/gin-gonic/gin"
    )

    type provider struct{ Filename string }

    func (p *provider) load() { _, _ = os.Open(filepath.Join("/etc", p.Filename)) }

    func save(c *gin.Context) {
    	file, _ := c.FormFile("file")
    	dst := "./uploads/" + file.Filename
    	_ = c.SaveUploadedFile(file, dst)
    }

    func checked(w http.ResponseWriter, r *http.Request) {
    	_, header, _ := r.FormFile("file")
    	if filepath.Ext(header.Filename) != ".png" {
    		return
    	}
    	_, _ = os.Create(filepath.Join("uploads", header.Filename))
    }
    '''
    assert _rules(tmp_path, code).get("upload-client-filename") == [18]


def test_ssrf_needs_a_client_and_a_caller_chosen_host(tmp_path):
    code = '''
    package main

    import (
    	"net/http"
    	"net/url"
    )

    var cache = map[string]string{}

    func proxy(w http.ResponseWriter, r *http.Request, client *http.Client) {
    	_, _ = http.Get("https://api.example.com/items/" + r.FormValue("id"))
    	q := r.URL.Query()
    	_ = q.Get("x")
    	target := r.FormValue("url")
    	_, _ = client.Get(target)
    }

    func allowed(w http.ResponseWriter, r *http.Request) {
    	u, _ := url.Parse(r.FormValue("url"))
    	if u.Hostname() != "example.com" {
    		return
    	}
    	_, _ = http.Get(r.FormValue("url"))
    }
    '''
    assert _rules(tmp_path, code).get("ssrf-request-url") == [15]


def test_argument_injection_needs_a_request_value_and_no_double_dash(tmp_path):
    code = '''
    package main

    import (
    	"net/http"
    	"os/exec"
    	"strings"
    )

    func clone(repo string) error { return exec.Command("git", "clone", repo).Run() }

    func h(w http.ResponseWriter, r *http.Request) {
    	repo := r.FormValue("repo")
    	_ = exec.Command("git", "clone", "--", repo).Run()
    	_ = exec.Command("echo", repo).Run()
    	_ = exec.Command("curl", "-sS", repo).Run()
    }

    func guarded(w http.ResponseWriter, r *http.Request) {
    	host := r.FormValue("host")
    	if strings.HasPrefix(host, "-") {
    		return
    	}
    	_ = exec.Command("ssh", host, "uptime").Run()
    }
    '''
    assert _rules(tmp_path, code).get("argument-injection") == [15]


def test_a_narrowed_parse_needs_no_bounds_check_in_between(tmp_path):
    code = '''
    package main

    import (
    	"math"
    	"strconv"
    )

    func a(s string) int32 {
    	n, _ := strconv.Atoi(s)
    	return int32(n)
    }

    func b(s string) int32 {
    	n, _ := strconv.ParseInt(s, 10, 32)
    	return int32(n)
    }

    func c(s string) int16 {
    	n, _ := strconv.Atoi(s)
    	if n < 0 || n > 100 {
    		return 0
    	}
    	return int16(n)
    }

    func d(s string) uint8 {
    	n, _ := strconv.ParseUint(s, 10, 64)
    	if n > math.MaxUint8 {
    		return 0
    	}
    	return uint8(n)
    }

    func e(s string, m int) int32 {
    	n, _ := strconv.Atoi(s)
    	_ = n
    	return int32(m)
    }
    '''
    assert _rules(tmp_path, code).get("parsed-int-narrowed") == [10]


def test_a_joined_path_is_reported_once_where_it_is_built(tmp_path):
    code = '''
    package main

    import (
    	"net/http"
    	"os"
    	"path/filepath"
    )

    func h(w http.ResponseWriter, r *http.Request) {
    	p := filepath.Join("/srv", r.FormValue("f"))
    	_, _ = os.Stat(p)
    	data, _ := os.ReadFile(p)
    	_, _ = w.Write(data)
    	_, _ = os.ReadFile("/srv/" + r.FormValue("g"))
    }
    '''
    assert _rules(tmp_path, code).get("path-traversal-request") == [10, 14]
