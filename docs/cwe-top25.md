# CWE Top 25 (2024): what Arbiter assesses, per language

Phase 2 (ARB-062) measures, before any new rule is written, what Arbiter
already catches for the [2024 CWE Top 25](https://cwe.mitre.org/top25/archive/2024/2024_top25_list.html)
in the five IDE languages. "Already" means:

- the native language packs (`src/arbiter/packs/rules/ast/<lang>.yaml`) and the
  other native probes (`secrets`, `authored`, ...);
- the pinned external analyzers: semgrep with the rules fetched by
  `tools/install_tools.sh` (semgrep-rules at the pinned ref, run offline),
  bandit (Python only) and gitleaks.

Every number here comes from a planted bad/good pair in
`fixtures/planted/<lang>/` and from `python tools/planted_score.py --adapters`.
Nothing is credited to an analyzer unless that run shows it reporting the bad
line and staying silent on the good twin.

## How the cells are marked

| Mark | Meaning |
|---|---|
| **N** | a native rule reports it (asserted by `tests/test_planted_corpus.py`) |
| **S** / **B** | semgrep / bandit reports it, and not the fixed twin |
| **N+S** etc. | more than one reports it |
| **gap** | the CWE applies and a pair is planted, but nothing reports it |
| *part* | some of the cell's planted defects are reported and some are not |
| n/a | does not apply in this language |
| OOR | applies, but is out of reach for single-file AST checks |

A planted defect line carries `expect:` for native rules and `expect-ext:` for
external ones (one comment may carry both). A defect nothing reports carries
`gap: CWE-N`, so the gap is visible in the source and the score stays green.
When a native rule for it lands, the `gap:` comment becomes an `expect:` marker.

New pairs live in `cwe25/` (Python, Go), `src/cwe25/` (TypeScript),
`src/main/java/planted/cwe25/` (Java) and `app/controllers/` (Ruby), one file
per CWE. Cells marked † are measured on pairs that were already in the corpus.

## The matrix

| # | CWE | Python | TypeScript | Go | Java | Ruby |
|---|---|---|---|---|---|---|
| 1 | 79 XSS | S | N+S † | S | N | S |
| 2 | 787 OOB write | n/a | n/a | n/a | n/a | n/a |
| 3 | 89 SQL injection | N+S+B † | N+S † | N+S † | N+S † | N+S † |
| 4 | 352 CSRF | N+S | **gap** | OOR | N+S | N |
| 5 | 22 Path traversal | S | S | **gap** | S | **gap** |
| 6 | 125 OOB read | n/a | n/a | n/a | n/a | n/a |
| 7 | 78 OS command injection | N+S+B † | N+S † | N+S † | N+S † | N+S † |
| 8 | 416 Use after free | n/a | n/a | n/a | n/a | n/a |
| 9 | 862 Missing authorization | OOR | OOR | OOR | OOR | OOR |
| 10 | 434 Dangerous upload | **gap** | **gap** | **gap** | N+S | **gap** |
| 11 | 94 Code injection | N+S+B † | N+S † | n/a | S | N+S † |
| 12 | 20 Input validation | OOR | OOR | OOR | OOR | OOR |
| 13 | 77 Command injection (argument) | **gap** | **gap** | **gap** | N | **gap** |
| 14 | 287 Improper authentication | N+S | N | N | N+S | N+S |
| 15 | 269 Privilege management | OOR | OOR | OOR | OOR | OOR |
| 16 | 502 Deserialization | N+S+B † | S | n/a | N+S † | N † |
| 17 | 200 Information exposure | N+S+B | N | N | N | N |
| 18 | 863 Incorrect authorization | OOR | OOR | OOR | OOR | OOR |
| 19 | 918 SSRF | S | **gap** | **gap** | N | **gap** |
| 20 | 119 Buffer bounds | n/a | n/a | n/a | n/a | n/a |
| 21 | 476 NULL dereference | n/a | n/a | OOR | OOR | n/a |
| 22 | 798 Hard-coded credentials | N+S+B | N+S | N | N | N+S |
| 23 | 190 Integer overflow | n/a | n/a | **gap** | N | n/a |
| 24 | 400 Resource consumption | N+S+B † | N † | N † | N † | N † |
| 25 | 306 Missing auth for critical function | OOR | OOR | OOR | OOR | OOR |

### Measured coverage per language

Over the cells that apply and are planted (OOR and n/a excluded). A cell is
counted under every source that catches it, so the columns do not add up to
the total. "Native only" means no external analyzer is needed.

| Language | Applicable cells | Native | Semgrep | Bandit | Caught by anything | Gaps |
|---|---|---|---|---|---|---|
| Python | 14 | 9 | 12 | 7 | 12 | 2 (434, 77) |
| TypeScript | 14 | 8 | 7 | n/a | 10 | 4 (352, 434, 77, 918) |
| Go | 12 | 6 | 3 | n/a | 7 | 5 (22, 434, 77, 918, 190) |
| Java | 15 | 13 | 8 | n/a | 15 | 0 |
| Ruby | 14 | 9 | 6 | n/a | 10 | 4 (22, 434, 77, 918) |

gitleaks ran on every language and reported none of the planted credentials:
they are generic passwords and signing keys, not provider-shaped tokens.
semgrep's bundled `generic.secrets.gitleaks.*` rules did report two of them.

### Notes on individual cells

- **Python 77**: bandit's B603/B607 fire on the bad *and* the good twin
  (`subprocess` with a list), so they do not separate the defect from the fix.
- **Python 200**: `app.run(debug=True)` is caught (semgrep `debug-enabled`,
  bandit B201). Returning `traceback.format_exc()` to the client is caught by
  `python.error-traceback-to-client`, and `str(e)` from a catch-all handler by
  `python.error-message-to-client` (medium confidence).
- **Python 352 and 287**: native since round 1 (`python.csrf-protection-disabled`,
  `python.jwt-unverified-decode`). The CSRF rule reports `@csrf_exempt` only on
  a view that acts as the session user (`request.user`, `@login_required`) and
  does not authenticate the request itself (HMAC, bearer token); semgrep's
  `no-csrf-exempt` reports every use.
  Real-code check, 2026-10-21: the three pinned Django applications in
  `tools/fetch_corpus.sh` (`DJANGO_APPS_ONLY=1`: zulip, healthchecks,
  django-cms; 252k production lines) have 49 `@csrf_exempt` views and
  `python.csrf-protection-disabled` reports none. Only one reads
  `request.user`: zulip's `rest_dispatch`, which re-applies `csrf_protect` to
  its session branch (now an explicit exclusion). The rest are API-key or
  remote-server APIs, webhook, bot and ping endpoints, signed-link
  unsubscribes, development-only logins and a test view. No `jwt-*` or
  `error-*` finding either (the one `jwt.decode` verifies).
- **Python 918**: semgrep's Flask SSRF rule also fires on the fixed twin, which
  validates the host against an allowlist. The Django-labelled taint rule
  (`ssrf-injection-requests`) is the one that discriminates, and it is the one
  marked.
- **Java 434**: caught by semgrep's `tainted-file-path`, because the defect
  passes `getOriginalFilename()` into a path, and natively since round 2 by
  `java.upload-client-filename` (see below), which also accepts an extension
  check as the fix.
- **Java 94**: semgrep `spel-injection` reports the handler method (its
  `@PostMapping` line), not the `parseExpression` call, so the marker sits on
  the line semgrep reports.
- **Ruby 77**: semgrep `dangerous-exec` reports the bad `system("git", "clone",
  params…)`, but it also fires on three correct twins already in the corpus.
  Those are array-form `system` calls whose interpolations are validated or
  `.to_i`. It does not separate defect from fix, so the cell is a gap.
- **Ruby 352** is native since round 1 (`ruby.csrf-protection-disabled`):
  `skip_before_action :verify_authenticity_token` or `skip_forgery_protection`
  in a controller with a state-changing action, not an `ActionController::API`
  one, not a webhook, not one that authenticates the request itself.
  `protect_from_forgery with: :null_session` is not reported (it is a
  protection strategy). `ruby.csrf-protection-off-in-production` reports
  `config.action_controller.allow_forgery_protection = false` in
  `config/environments/production.rb` only (Rails generates that line in
  `test.rb`). Ruby 287 and 200 are native too
  (`ruby.jwt-unverified-decode`, `ruby.error-backtrace-to-client`,
  `ruby.error-message-to-client`).
- **Ruby 352 and Ruby 22**: semgrep fires, but on both twins:
  `missing-csrf-protection` on any `ActionController::Base` subclass without
  `protect_from_forgery` (the default since Rails 5.2), and `check-send-file`
  on the guarded `send_file` too. They are counted as gaps because they cannot
  tell the defect from the fix.
- **Ruby 287**: semgrep's `ruby-jwt-hardcoded-secret` also reports
  `JWT.decode(token, nil, false)`, where the "secret" is `nil`. Its marker
  lists both rules because both report the line.
- **Java 287, 798, 352, 200 (round 1)**: native rules `java.jwt-unsigned-accepted`,
  `java.jwt-decode-without-verify`, `java.hardcoded-signing-key`,
  `java.hardcoded-db-password`, `java.csrf-disabled`, `java.stack-trace-to-client`
  and `java.exception-message-to-client`. semgrep's `spring-csrf-disabled`
  reports only the `http.csrf().disable()` form, not the lambda or
  `AbstractHttpConfigurer::disable` forms, so 352 counts for semgrep on that
  one line. A file that declares `SessionCreationPolicy.STATELESS` gets
  `java.csrf-disabled-stateless` (low severity, low confidence) instead: a
  bearer-header API is not CSRF-able, but one that keeps the token in a
  cookie is. `exception-message-to-client` is medium confidence and counts
  only a broad or library exception (`Exception`, `SQLException`, ...): the
  message of a validation or domain exception is usually meant for the caller.
- **Java 79, 918, 77, 190, 434 (round 2)**: native rules in the same-method
  form, a value bound from the request (`@RequestParam`, `@PathVariable`,
  `@RequestHeader`, JAX-RS `@QueryParam`/`@PathParam`/`@FormParam`, or
  `String x = request.getParameter(...)`) reaching the sink in the method
  that binds it. `java.xss-unescaped-html` (high/medium): the bound string
  spliced beside an HTML literal in a `return`, `ResponseEntity...body(...)`
  or `ok(...)`, or a servlet that reads the request and prints a
  non-constant into a `text/html` page through `getWriter()`. A file that
  applies any HTML encoder (`HtmlUtils.htmlEscape`, `Encode.forHtml`,
  `StringEscapeUtils.escapeHtml4`, ESAPI `encodeForHTML`, tomcat's
  `HTMLFilter.filter`) is not reported: OWASP Benchmark escapes inside a
  helper the value passes through, which a method-scoped check cannot follow.
  `java.ssrf-request-url` (high/medium): the bound name, or `"http://" +` it,
  as the URL of `new URL`/`URI.create`/`new HttpGet`/`Jsoup.connect`, a
  RestTemplate method, or a WebClient/OkHttp `.uri`/`.url`, in a method that
  makes a request; a fixed host with the value in the path, a host read back
  (`getHost()`) or an allowlist is not reported. `java.argument-injection`
  (high/medium, CWE-88/77): the bound name as an element of
  `new ProcessBuilder`, `List.of`, `exec(new String[]{...})` or `command(...)`
  for git, curl, ssh, rsync, tar and other option-parsing programs, with no
  `"--"` before it. CWE-190 is `java.parsed-number-narrowed` (`(byte)`/`(short)`
  of `Integer.parseInt`, `(int)` of `Long.parseLong`; a radix argument, a
  `Number.intValue()` body and a byte-array fill are skipped) and
  `java.parsed-count-overflow` (a count parsed in the method multiplied into
  `new T[...]` or `ByteBuffer.allocate` with no comparison, `Math.min` or
  `multiplyExact` of it); the planted `int * int` money total was replaced by
  these forms, because a rule for every multiplication would be noise.
  CWE-434 adds `java.upload-client-filename` (high/medium):
  `getOriginalFilename()`/`getSubmittedFileName()`, or a local holding it, in
  `transferTo`, `Files.copy/write`, `new File`, `Paths.get` or `resolve` in a
  method that writes, with no `FilenameUtils.getName`, `getFileName()` or
  extension check; semgrep's `tainted-file-path` still reports it too.
- **TypeScript 352**: Express has no CSRF protection to switch off, so the
  defect is an *absent* middleware. semgrep's `express-check-csurf-middleware-usage`
  did not fire on the planted app.
- **TypeScript 287, 200 and Go 287, 200, 798** (native rules, ARB-062 round
  1): `typescript.jwt-decode-unverified` and `go.jwt-parse-unverified` are
  medium confidence, because reading `kid` or `iss` before verifying is
  legitimate; a function that also verifies is not reported.
  `jwt-verify-insecure-options` (`algorithms: ["none"]`, `ignoreExpiration:
  true`) and `go.jwt-claims-validation-disabled` are high. CWE-200 is
  `error-stack-in-response` (high), `error-object-in-response` and
  `go.error-text-in-response` (medium: proxies and cluster-internal APIs
  return error text on purpose; Go and gin/echo are reported only with a 5xx
  status) and `go.pprof-on-default-mux`. Go 798 is `go.hardcoded-signing-key`
  (`[]byte("…")` keys) and `go.hardcoded-dsn-password`; TypeScript also gains
  a native `jwt-hardcoded-secret`. A golang-jwt keyfunc that does not check
  `t.Method` is not a rule: the library already refuses `none` and a key of
  the wrong type, so it would be noise.

## Why a cell does not apply, or is out of reach

| CWE | Cells | Reason |
|---|---|---|
| 787, 125, 416, 119 | all five | Memory-safe runtimes: no pointer arithmetic or manual frees. These CWEs would only occur through `unsafe`, JNI, FFI or a C extension, which the packs do not parse. |
| 476 | Python, TS, Ruby | `None`/`undefined`/`nil` access raises a catchable exception, not a pointer dereference. The crash case is covered as error handling (CWE-703). |
| 476 | Go, Java | OOR. A nil or null dereference is a real panic or NPE, but proving a value is nil at its use needs nullness flow analysis across calls. |
| 190 | Python, Ruby | Integers are arbitrary-precision and cannot wrap. |
| 190 | TypeScript | `number` is a double, so overflow means lost precision (CWE-1339), not wraparound. `bigint` is arbitrary-precision. |
| 94 | Go | No runtime code evaluation. `text/template` injection is CWE-1336 and reaches only template data, not code. |
| 502 | Go | The stdlib decoders (`encoding/json`, `gob`, `xml`) fill declared types. There is no gadget chain or code execution path. |
| 352 | Go | OOR. `net/http` has no CSRF protection to switch off. Protection is middleware (`gorilla/csrf`, Go 1.25's `http.CrossOriginProtection`), and its absence only shows in the whole router set-up. |
| 862, 863 | all five | OOR. Telling a missing or wrong permission check from a correct one needs the application's authorization model: which routes need which roles, and who owns which object. `go.stub-on-security-path` (tagged CWE-863) only catches a stubbed-out check. |
| 306 | all five | OOR. Needs the route and middleware topology across files, plus knowing which functions are "critical". |
| 269 | all five | OOR. Privilege management is a deployment and role-design property. Its single-file signals (running as root, broad IAM) sit in Dockerfiles, Kubernetes and IaC, where checkov covers part of it, not in application code. |
| 20 | all five | OOR as a class. CWE-20 is a pillar: every concrete instance is a more specific CWE (22, 79, 89, 434, 1287 ...) that has its own row. A "missing validation" with no specific sink has no single-file signature. |
| 918 | all five | Assessed **only in the same-function form** (request value to HTTP client in one handler). A URL that passes through helper functions or other files needs interprocedural taint, which is OOR. The plan listed 918 as out of reach "without taint". The same-function form is common enough in AI-written code, and semgrep catches it in Python, so it is planted. |

## Gap list for the next step

These cells are ranked by two things: how often AI-written code contains the
defect, and how practical a single-file native rule is. Practicality means a
recognisable API call plus at most a same-function condition, at an
acceptable false-positive rate. Writing these rules is the next step and is
not part of this change.

| Rank | Gap | Languages | Why it ranks here |
|---|---|---|---|
| 1 | CWE-287 JWT decoded without verification (`jwt.decode`, `ParseUnverified`) | native in all five languages since round 1 | Assistants reach for `decode` to "read the user id". The call names are unambiguous, so a rule would be near-zero noise. |
| 2 | CWE-798 literal signing key or DB password passed to a known API (`Algorithm.HMAC256("…")`, `[]byte("…")` signing key, `DriverManager.getConnection(…, "pw")`) | native in Java and Go, and the Python and Ruby JWT-key form, since round 1 | Placeholder secrets are the classic AI output. The secrets probe misses them because the literal sits in an argument, not a credential-named assignment. |
| 3 | CWE-352 CSRF switched off (`csrf.disable()`, `csrf(c -> c.disable())`, `skip_before_action :verify_authenticity_token`) | native in Java, Python and Ruby since round 1; TypeScript still a gap | The standard AI "fix" for a 403 on POST. It is an exact call pattern, and semgrep's Ruby rule cannot tell it from a correct controller. |
| 4 | CWE-200 error detail returned to the client (`err.stack`, `traceback.format_exc()`, `e.backtrace`, `printStackTrace` into the body, `http.Error(w, err.Error(), 500)`) | native in all five languages since round 1 | Very common in generated handlers. A response sink containing the error in the same statement is practical. The Go `err.Error()` form needs a medium-confidence level. |
| 5 | CWE-434 upload stored under the client's filename (`f.filename`, `file.originalname`, `header.Filename`, `getOriginalFilename()`, `original_filename` in a save or write path) | Python, TS, Go, Ruby; native in Java since round 2 | The names are framework-specific and stable. A same-function absence of an extension or MIME allowlist could raise the confidence. |
| 6 | CWE-918 request value passed straight to an HTTP client (`fetch(req.query…)`, `client.Get(r.URL.Query()…)`, `URI.create(param)`, `Faraday#get(params…)`) | TS, Go, Ruby; native in Java since round 2 | Link previews, webhooks and image proxies are common asks. The same-statement or same-function source-to-sink form is the existing `sql-injection` rules' shape. |
| 7 | CWE-22 request value joined into a path (`filepath.Join(dir, r.URL.Query()…)`, `File.join(…, params[…])`) with no `IsLocal`/`basename`/prefix check in scope | Go, Ruby | Common. The "unless_in_scope" guard condition fits the pack schema. |
| 8 | CWE-77 argument injection: `git`/`curl`/`ssh` invoked with a non-literal argument and no `--` | Python, TS, Go, Ruby; native in Java since round 2 | Less common, but none of the analyzers sees it. The tool names and the missing `--` are easy to check, though the defect is narrower. |
| 9 | CWE-79 HTML string built from a `@RequestParam` in a `produces = TEXT_HTML` handler | native in Java since round 2 | Less common, since Java assistants usually use templates. Practical, using the TS `html-from-request` shape. |
| 10 | CWE-190 narrowing conversion of a parsed integer (`int32(n)` after `strconv.Atoi`) | Go; native in Java since round 2 (`(byte) Integer.parseInt`, a parsed count in an allocation) | A gosec-style rule (G109/G115) is practical. The Java `int * int` overflow is not: every multiplication would be a candidate. |

Not ranked: TypeScript 352 (absent middleware, low practicality per file),
Python 77 (noise). The out-of-reach rows above are not ranked
either: no single-file rule can assess them.
