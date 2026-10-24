# Detection Reference

Every check Arbiter can run, what it requires, and what it catches. Probes that
cannot run are recorded as *not assessed* with a reason; they are never silently
treated as passes.

## Contents

- [Native probes](#native-probes)
- [External adapters](#external-adapters)
- [Infrastructure as code](#infrastructure-as-code)
- [The resource graph](#the-resource-graph)
- [Concurrency](#concurrency)
- [Language packs](#language-packs)
- [Machine-authored code](#machine-authored-code)
- [AI-written services](#ai-written-services)
- [Contracts between artifacts](#contracts-between-artifacts)
- [Assurance: is the checking switched on?](#assurance-is-the-checking-switched-on)
- [The judgement pass](#the-judgement-pass)
- [Two findings that read history](#two-findings-that-read-history)
- [What doc_drift leaves alone](#what-doc_drift-leaves-alone)

---

## Native probes

| Probe | Dimension | Requires | Catches |
|---|---|---|---|
| `secrets` | security | nothing | AWS keys, private keys, GitHub/Slack tokens, credentialed URLs, high-entropy literals assigned to credential-named symbols |
| `resource_policy` | security, compliance | nothing | 28 provider-neutral rules (`src/arbiter/packs/rules/resource.yaml`) over the normalized resource graph — unencrypted storage, public buckets, open ingress, plaintext listeners, missing retention |
| `quality` | quality | nothing | Oversized files, TODO clusters, repositories with no tests |
| `ast_metrics` | quality | tree-sitter | Function length, cyclomatic complexity and nesting depth measured from a real parse tree, in 11 languages |
| `house_rules_ast` | quality, security | tree-sitter | Your own tree-sitter queries from `arbiter.yaml` |
| `supply_chain` | supply chain, security | nothing | Unpinned Python/npm dependencies, GitHub Actions on mutable refs, `pull_request_target` |
| `doc_drift` | drift | nothing | Broken documentation links, files described in prose that do not exist, documented environment variables nothing reads |
| `interface` | interface | 2+ repos | Six cross-repo seam checks — see [systems.md](systems.md). On a single repository it is recorded as not applicable (`n/a` in the NOT ASSESSED block) and leaves the coverage denominator; a probe that was *prevented* from running stays in it, and claim invariant CI-12 refuses a report that marks a prevented probe as not applicable. |
| `house_rules` | quality, drift | nothing | Your own rules from `arbiter.yaml` |
| `assurance` | assurance | nothing | Whether the *checking* is switched on — see below |
| `authored` | supply chain, security | nothing | Defects characteristic of machine-drafted code — see below |
| `concurrency` | quality | nothing | Python: shared state changed by concurrent workers with no lock held — see below |
| `aiapp` | security, quality | tree-sitter (for function boundaries) | App-level defects found in AI-written services: destructive routes or mail-sending endpoints in an app that authenticates nobody, and per-request stores that are never evicted — see [AI-written services](#ai-written-services) |
| `lang_<pack>` | quality, security | tree-sitter + that language's grammar | Shipped structural rules for each IDE language: `lang_python`, `lang_typescript` (TS and JS), `lang_go`, `lang_java`, `lang_ruby` — see below |
| `contract` | drift | nothing | Contracts declared in one artifact and implemented in another — see below |
| `governance` | drift | a failure ledger | The ledger held against the change — see [Probe scopes](#probe-scopes). `governance.open-failure-untested`: an open or mitigated entry's affected file changed without any of its regression tests (`--changed` only). `governance.regression-test-missing`: a fixed entry whose regression test file is gone. Not applicable (`n/a`) without `.ai/failures/failure-ledger.json` |
| `judgement` | drift | a model | Claims in prose the code contradicts. Inferred, never gates |

List what can run in a given checkout, and why anything cannot:

```bash
arbiter probes ./repo
```

The `CHECKS` column is how many rules each probe holds. On this repository,
2026-10-23, the language packs hold 176 rules (Python 26, TypeScript/JavaScript
42, Go 34, Java 39, Ruby 35) and Arbiter ships 263 rules in all. Every rule
starts in the `advisory` precision tier ([precision.md](precision.md#tiers)).

### Probe scopes

Every probe declares how much of the tree it needs to answer honestly, which
is what decides its fate under `--changed` (see [ci.md](ci.md#scanning-only-what-changed)):

| Scope | Meaning | In a partial scan |
|---|---|---|
| `file` | every finding depends only on the file it is in | runs over the changed files; the answer is complete for those files |
| `repo` | the answer depends on relationships between files | recorded as not assessed, because a subset gives a wrong answer rather than a smaller one |
| `change` | reads a small, fixed set of files it names itself (the failure ledger, the requirement registry) plus the set of changed paths | runs; a full scan is the same probe answered with the empty change |

`governance` is the one change-scoped probe. In a `--changed` scan the run
also reads the commits since the base, once per repository, and tags every
finding in the change with `req:<ID>` for the requirement ids cited by the
commits that touched the finding's own file (the registry's
`requirement_id_prefix`, old ids resolved through its `id_aliases`), marked
`req-scope:commits`: that is attribution. A file no such commit cited falls
back to every id the range cites, marked `req-scope:open`, which is context
rather than a claim about which requirement introduced the finding; a full
scan carries only the `req-scope:open` marker. The Markdown report and the
pull-request comment add a **By requirement** table (counts by severity and
the first three titles per id, with an `unattributed` row for in-change
findings no commit attributed) and a note under it saying which scope each
count came from. Titles only, never evidence.

## External adapters

Adapters for `ruff`, `bandit`, `checkov`, `semgrep` and `gitleaks` normalize
third-party output into the same `Finding` shape. Adapters are declarative
manifests in `src/arbiter/packs/adapters/`, not code.

Arbiter does not vendor or redistribute these analyzers; install them
separately (`./tools/install_tools.sh` pins known-good versions). Any that are
absent report as skipped with the binary named, and coverage drops accordingly.

External findings often arrive without a severity. How Arbiter assigns one from
measurement rather than invention is described in
[calibration.md](calibration.md#calibrating-someone-elses-tool).

---

## Infrastructure as code

### Terraform: supply a plan

Reading `.tf` source is a fallback. A plan has resolved variables, expanded
`for_each` and `count`, and flattened modules — source has none of that.

```bash
terraform plan -out=tfplan.bin
terraform show -json tfplan.bin > tfplan.json
arbiter scan . --tfplan tfplan.json          # or drop tfplan.json in the repo
```

Arbiter never generates the plan itself: `terraform init` downloads and executes
provider code, and the tool does not execute what it scans.

On the bundled `fixtures/tfplan`, where one of two `for_each` bucket instances
gets a public ACL from a conditional expression:

| | Source only | With the plan |
|---|---|---|
| Public bucket | **missed** — the conditional is unparseable literally | found, cited at `modules/storage/main.tf:5` |
| Unencrypted volume (`encrypted = var.encrypt_volumes`) | **false high** — reads the variable name as a value | *not assessed*, unknown until apply |
| Destroyed security group with open ingress | flagged | ignored; the plan destroys it |
| Bucket instances seen | 1 | 2 |

Three details make this work:

- **Findings still cite source.** Plan JSON has no file or line, so each plan
  resource is joined back to the HCL block that declared it by address, and the
  finding points at code you can open.
- **Unknown is not absent.** A value in `after_unknown` cannot be judged.
  Treating it as missing is exactly how a scanner reports an encrypted bucket as
  unencrypted, so the check returns *unknown* and is reported as not assessed —
  never as a pass, never as a finding.
- **Siblings link through `configuration`.** By the time
  `bucket = aws_s3_bucket.data.id` reaches `after`, it is a resolved string or
  an unknown; the reference only survives in the plan's configuration section.

A repository read from source alone gets an `info` finding saying so.

## The resource graph

Terraform, CloudFormation, CDK-synthesized templates and Kubernetes manifests
normalize into one `Resource` shape with a provider-neutral `kind`, so a rule is
written once:

```yaml
- id: unencrypted-database
  match_kinds: [database]
  assert: property_truthy
  any_of: [storage_encrypted, StorageEncrypted, encrypted, KmsKeyId]
  severity: high
  controls: [NIST-800-53r5:SC-28]
```

That fires on an RDS instance, an Azure PostgreSQL server and a Cloud SQL
instance without a line of provider-specific code.

---

## Concurrency

The race model-drafted worker code most often ships with: it reads correctly,
the single-threaded test passes, and two workers eventually take the same task.
None of the wrapped analyzers look for it. Python only, from the standard
library's own parser.

| Check | What it catches |
|---|---|
| `concurrency.check-then-act-unlocked` | An `if` that reads a shared collection and then changes it with no lock held: `if self.tasks: return self.tasks.pop(0)`, `if key not in CACHE: CACHE[key] = v`, `job = JOBS[id]` then `if job["state"] == "queued": job["state"] = "running"`. |
| `concurrency.unsynchronised-counter` | `self.processed += 1` (or a `global`) inside a function the same file starts as a thread, a `Thread` subclass's `run`, or an executor job. |

*Shared* means an attribute of `self` or a collection bound at module level.
*Lock held* means inside a `with` on a lock (a name bound to `Lock()`, `RLock()`,
`Condition()`, `Semaphore()`, or containing lock/mutex/cond/sem), or a function
that calls `.acquire()`. Synchronous code is checked only in files that use
threads or executors; an `async def` only when an `await` falls between the
check and the change, since asyncio cannot interleave anywhere else. In a file
built on an event loop, a plain function counts only if it is started as a
thread.

Limits: a lock taken by the caller is invisible, and nothing follows calls into
other functions or files. Both rules report at medium confidence.

## Language packs

Each IDE language has a shipped pack of structural rules in
`src/arbiter/packs/rules/ast/<pack>.yaml`, and each pack is its own probe, so
a grammar this machine lacks shows that one language as **not assessed**
rather than as a clean pass. `tools/install_tools.sh` caches every grammar the
packs need; scans never download one.

Rules are tree-sitter queries, with five optional context conditions for the
things a bare pattern cannot say ("no lock held", "no timeout set"):
`unless_inside`, `unless_in_scope` and `requires_in_scope` (the enclosing
function, or the file), `unless_in_file` and `requires_file`. `files:`
restricts a rule to paths matching an fnmatch glob, or any glob of a list
(`*` also crosses `/`): the JSX rules read `*.[jt]sx`, the RSpec rules
`*_spec.rb`, and `ruby.csrf-protection-off-in-production` reads
`config/environments/production.rb` at the root or under any directory,
because the same line is Rails' generated default in `test.rb`. A Python rule
may also give `safe_splice_calls` and `safe_splice_names` (regexes): a hit that
builds a string is skipped when every value spliced into it is a literal, a
call or name those match, or a local whose every assignment in the enclosing
function is (`python.sql-injection`; one function, no data flow). A Ruby
rule may give the same two keys plus `safe_splice_text` (searched in each
`#{...}`) and `safe_splice_values` (full-matched against a value or a local's
assigned value): a call whose first argument is an interpolated string is
skipped when every interpolation is safe, which includes a literal chosen by
a condition (`ruby.sql-injection`: `#{x ? 'A' : 'B'}`, `#{' IF EXISTS' if
f}`, a PRAGMA setting read back with `query_value("PRAGMA foreign_keys")`).
The schema is documented at the top of
`src/arbiter/lang_rules.py`, and a pack that breaks it fails at import.
Rule ids are `arbiter/<pack>.<id>`; each carries its CWE.

#### One-hop data flow (`taint:`)

A rule may add a `taint:` block (Milestone 3, `src/arbiter/dataflow.py`). An
abstract interpreter walks each function holding a sink, statement by
statement, and gives every local one of four values: CLEAN (built from
literals, with its constant when known), DERIVED (returned by an unseen call
whose inputs are all CLEAN), UNKNOWN (a parameter, a field, an unseen call)
and TAINT (reached from a source). Assignments, destructuring, `+=`,
concatenation, format calls, templates, f-strings, interpolation, lists and
maps with constant keys carry values; a condition, ternary or `switch` that
folds to a constant runs only its live branch; a same-file call is followed
one hop (arguments to parameters, returns back, sinks inside the helper see
the bound values). The rule's own `query` stays as the fallback, so recall
never drops: the block only adds findings and drops provably constant ones.

```yaml
taint:
  mode: source            # source (default): report a TAINT sink; nonconst: any non-CLEAN sink
  sources: |              # @source: a value from outside, unless it evaluates CLEAN
  inputs: |               # @input: always TAINT (request accessors, @RequestParam parameters)
                          # (with `sources` too, a sink must be reached from a source)
  sinks: |                # @sink: the value checked; @hit: where the finding is reported
  sanitizers: |           # @sanitizer: an expression whose value is CLEAN
  value: [sql]            # captures of `query`: a hit whose values are all CLEAN is dropped
  kill: derived           # also drop a hit whose values are DERIVED, in a function that reads an input
  where: {sh: "regex"}    # a capture's text, or a hoisted literal's text, must match
  types: {w: "regex"}     # a captured identifier's declared type must match
  element0: "regex"       # the sink is a list whose first element matches (an argv's tool)
  unless_element: "^--$"  # skip a list sink holding a matching element
  built: true             # only a string spliced together from parts is a sink value
  at: source              # report at the source when it is in the sink's function
  hops: 1                 # same-file calls followed (0-2)
  requires: false         # apply requires_file / requires_in_scope to the new findings too
```

`unless_inside`, `unless_in_scope` and `unless_in_file` apply to the new
findings as well; when a value crossed a function on its way, `unless_in_scope`
is checked over the whole file, since the check may sit in either function.
One finding is kept per source per function. An analysis that outgrows its
step budget, or fails, changes nothing (`ARBITER_DATAFLOW_STRICT=1` makes a
failure raise; the test suite sets it). Nothing crosses a file; fields,
aliasing and exceptions are not followed.

Every finding, native or external, carries `cwe` in the JSON report and as
`external/cwe/cwe-N` tags in SARIF. Native rules declare it in their pack or
in `src/arbiter/cwe.py`; semgrep, bandit and gitleaks report their own, read
through each adapter's `cwe` map key.

### The planted corpus

`fixtures/planted/<lang>/bad/` holds known defects, each line marked with the
rule that must report it (`// expect: arbiter/go.http-default-client`).
`good/` holds the same code done correctly. `tests/test_planted_corpus.py`
asserts that every marked defect is reported, nothing fires on a good twin,
and no owned rule fires on an unmarked line. `python tools/planted_score.py
[--adapters]` prints the catch rate per language, with or without the external
analyzers.

A comment may carry both kinds of marker, `# expect: <native> expect-ext:
<external>`, when both report the same defect. A planted defect that nothing
reports yet carries `gap: CWE-N` instead of a marker, so it shows in the source
without failing the score.

### CWE Top 25: not assessed

The 2024 CWE Top 25 is planted per language and measured in
[`docs/cwe-top25.md`](cwe-top25.md), with the matrix, the per-language counts
and the ranked gap list. The CWEs below are **not assessed** by Arbiter in the
named language. A scan that finds none of them says nothing about them.

| Language | Not assessed: does not apply | Not assessed: out of reach for single-file checks | Applies, but no check yet (gap) |
|---|---|---|---|
| Python | 787, 125, 416, 119 (memory-safe); 476 (`None` access raises); 190 (arbitrary-precision ints) | 862, 863, 306, 269, 20; 918 beyond one function; 434, 77 beyond one function | none |
| TypeScript / JavaScript | 787, 125, 416, 119; 476 (`undefined` access raises); 190 (`number` is a double) | 862, 863, 306, 269, 20; 918, 434 and 77 beyond one function; 352 as a missing CSRF middleware | none |
| Go | 787, 125, 416, 119 (outside `unsafe`); 94 (no runtime eval); 502 (stdlib decoders fill declared types) | 862, 863, 306, 269, 20, 476 (nil-flow analysis), 352 (absent middleware); 22, 434, 77 and 918 beyond one function | none |
| Java | 787, 125, 416, 119 (outside JNI) | 862, 863, 306, 269, 20, 476 (nullness analysis); 79, 918, 77, 190 and 434 beyond one function | none |
| Ruby | 787, 125, 416, 119 (outside C extensions); 476 (`nil` access raises); 190 (arbitrary-precision ints) | 862, 863, 306, 269, 20; 22, 434, 77 and 918 beyond one function | none |

Gap cells that semgrep or bandit cover only when installed are listed as
covered in `docs/cwe-top25.md`. Without the analyzers they fall back to the
native column there. In a scan, an analyzer that did not run is already
reported as not assessed (see [Native probes](#native-probes)). The same
cells are the `cwe-top25-2024` control pack, so a scan's controls report says
per language which weaknesses were assessed, which are gaps, which are out of
reach and which do not apply (`arbiter controls --framework CWE-Top25-2024`;
see [compliance.md](compliance.md#cwe-top-25-per-language)).

## Machine-authored code

A growing share of new code is drafted by a model, and model-drafted code fails
in ways human-drafted code mostly does not: confident, fluent, plausible-looking
code that refers to things which do not exist. A model is the worst available
reviewer for this — asked to check its own hallucinated import, it reads the
import, finds it plausible (it generated it precisely because it was plausible)
and passes.

These failures are decidable, so they are a deterministic probe rather than part
of the judgement pass:

| Check | What it catches |
|---|---|
| `undeclared-import` / `import-of-nonexistent-package` | A module nothing declares. See the two modes below. |
| `stub-on-production-path` | `return True  # TODO`, `raise NotImplementedError`, a `pass` body. Severity keyed to whether the function name says it decides something: a stub in `verify_signature` is not an unfinished feature, it is an always-yes. |
| `security-check-disabled` | `verify=False`, `InsecureSkipVerify: true`, `StrictHostKeyChecking=no`. Written to make an example work, then never removed. Under a test or local-development path it is reported low; in a test file, a TLS check off for the test's own local server (`localhost`, `server.address()`, `httptest`) and Django's `override_settings(DEBUG=True)` are the fixture and are not reported. |
| `docstring-promises-absent-behaviour` | A docstring saying the function validates or retries, in a body that does neither. |

The import check ships in **two modes**, and the split is the honest part:

- **Offline** it knows only that a manifest does not declare something. That is
  mostly a stale manifest, so it reports at zero weight and says the distinction
  needs a registry.
- **Connected** it asks the registry whether the package exists at all. A name
  that resolves to nothing is **critical** — the import cannot ever have worked,
  so the name was invented, and an unclaimed package name sitting in a shipped
  import is an invitation to whoever registers it first.

```console
$ arbiter scan ./app --profile connected
  critical  import-of-nonexistent-package  fastapi_auth_middleware_helper
  critical  import-of-nonexistent-package  secure_token_validator
```

---

## AI-written services

Rules added from the AI-written development corpus (`fixtures/ai-corpus`,
ARB-083): the defect classes a reviewer labelled in model-written services
that no rule covered. All start **advisory** (docs/precision.md). Most are
pack rules, listed with their packs; the cross-file ones are the `aiapp`
probe (`src/arbiter/aiapp.py`).

| Class | Rules |
|---|---|
| Unbounded memory | `<lang>.image-decode-unbounded` (Go `image.Decode` with no `DecodeConfig`, Java `ImageIO.read` with no `ImageReader` size check), `python.image-bomb-check-disabled`, `typescript.image-pixel-limit-disabled`; `go`/`java.decompress-read-unbounded`, `python`/`ruby.decompress-unbounded`, `typescript.decompress-sync-unbounded`; `go`/`ruby.request-body-read-unbounded`, `python.request-body-unbounded` (Flask with no `MAX_CONTENT_LENGTH`, Starlette `await request.body()`), `typescript.request-body-accumulated-unbounded`, `typescript.upload-memory-storage-unbounded`; `aiapp.store-never-evicted` |
| Missing authentication | `aiapp.unauthenticated-destructive-route`, `aiapp.open-mail-relay` |
| Mail TLS | `python.mail-tls-unverified`, `java.mail-starttls-not-required`, `java.mail-ssl-trust-all`, `typescript.mail-starttls-not-required`, `ruby.mail-tls-verify-none` |
| Ignored errors (Go) | `go.ignored-encode-error`, `go.ignored-exec-error` |
| Constant-delay retries | `<lang>.retry-constant-delay` (all five), `ruby.requeue-without-delay` |
| SSRF to stored callbacks | `<lang>.ssrf-stored-callback` (all five) |

The `aiapp` probe, per repository:

- **`aiapp.unauthenticated-destructive-route`** (CWE-306, CWE-862): a DELETE
  route, or a non-GET route under `/admin`, `/purge`, `/wipe` or `/flush`,
  with no inline guard, when either nothing in the repository authenticates
  a caller (no `login_required`, `requireAuth`, Bearer/JWT, passport, API key,
  `current_user`, `@PreAuthorize`, HMAC signature check...), or other routes
  in the same Go, TypeScript/JavaScript or Python file pass an auth
  middleware, wrapper or `Depends(...)` inline and this one does not. Java and
  Ruby are only checked at repository level, because they usually guard
  routes globally (a Spring filter chain, a `before` filter).
- **`aiapp.open-mail-relay`** (CWE-306): a write route in a repository that
  sends mail (smtplib, net/smtp, nodemailer, Jakarta/JavaMail, Net::SMTP,
  ActionMailer) and authenticates nobody.
- **`aiapp.store-never-evicted`** (CWE-770, CWE-401): a map held by an object
  (`self._jobs`, `q.jobs`, `@jobs`, `this.jobs`, a Java `Map` field) filled
  under the id of a freshly created item (`job.id`, in a file that calls
  uuid4/randomUUID/SecureRandom/crypto/rand...), that nothing outside the
  inserting function or constructor removes from, clears, bounds by size or
  expires. Reported at the insert.

Authentication is recognised by name, so auth applied outside the repository
(a gateway, a mesh, a proxy) is invisible: the route findings are medium
confidence and say so. The store check reads one file. On the 24 gold
repositories and the real corpora the probe reported one finding
(nodejs-goof's unauthenticated `DELETE /chat`, a true positive). The CWE-306
Top 25 cell stays out of reach: these are application-level heuristics, not a
per-function check.

## Contracts between artifacts

A repository usually holds two descriptions of the same thing, in different
languages, maintained by different habits, and checked against each other by
nobody: an OpenAPI document and the routes the server registers; a set of
migrations and the model the application queries. Each half is valid on its own
terms — the spec parses, the routes compile, the migrations apply — and no
linter compares them, because each tool sees one side. The failure shows up at
runtime as a 404 against a documented endpoint.

This is the cross-repo seam idea moved inside a single repository, and it is
deterministic rather than a question for a model: a path is either in the route
table or it is not.

Every check is biased hard toward silence. Route parameters are normalized
across five spellings (`{id}`, `:id`, `<int:id>`, `(?P<id>…)`), paths assembled
from variables are skipped rather than guessed at, and a spec whose code side
yields fewer than three recognisable routes reports **`spec-not-compared`** at
`info` instead of declaring every path missing — because "the code implements
none of the spec" is nearly always a parser limitation wearing the costume of a
finding.

---

## Assurance: is the checking switched on?

A clean report has two possible causes that look identical from the outside: the
analyzers ran over everything and found nothing, or large parts of the tree were
excluded, hundreds of findings were silenced by inline comments, and the tests
that would have caught a regression assert nothing.

Every scanner hands you the same green tick for both. They have to — they honour
the suppression comments that hide findings from them, and a test that asserts
nothing still passes. The silencing is invisible to the thing being silenced.

Measured across ten corpus repositories: **445 suppression comments**, 169 in one
project, and not one of them appears in any tool's output.

The `assurance` dimension reports three things:

1. inline suppressions across fifteen tools' ignore syntaxes, with the blanket
   ones — an ignore naming no rule — separated out
2. configuration that excludes parts of the tree from analysis
3. tests that cannot fail

It carries **weight zero**. A repository with four hundred `# noqa` comments is
not insecure, it is *unmeasured*, and scoring those the same way is the
conflation the dimension exists to expose.

---

## The judgement pass

A model is worth adding only for questions with no definite shape: does the
README describe behaviour the code no longer has, does a comment contradict the
function under it. It is not here to re-find secrets — those have decidable
answers, and swapping a decidable check for a probabilistic one is a downgrade
dressed as an upgrade.

```yaml
profile: connected          # offline, ci and assisted do not run it
judgement:
  provider: anthropic       # reads ANTHROPIC_API_KEY (or api_key_env) from the environment
  model: claude-sonnet-4-5  # the default
```

This is a different pass from model *verification* (the `assisted` profile),
which judges Arbiter's own findings rather than producing new ones; see
[precision.md](precision.md#model-verification).

Three rules it obeys:

- **Inferred findings never blend with deterministic ones.** Everything from
  this pass is `provenance: inferred`, and the gate ignores inferred findings
  unless `gate.gate_on_inferred` is set. A model's opinion should not turn a build
  red on its own.
- **No provider configured is *not assessed*, never a pass.** The easy
  implementation returns an empty list when there is no key — and an empty list
  is indistinguishable from "looked, found nothing". So the probe reports skipped
  with the reason, and coverage drops by exactly what was not checked.
- **A finding citing a file the model was not shown is discarded**, not reported
  with a caveat. Secrets are masked before anything leaves the machine, and
  model-stated confidence never reaches `high` — confidence asserted by a model
  is a different quantity from confidence measured from adjudicated outcomes,
  and sharing a scale would be a category error.

## Two findings that read history

`drift.doc-references-missing-file` asks git whether a path named in prose was
ever committed. A path that was tracked and is gone is reported at `info` as
*removed*: a changelog or decision record is right to name it. A path that was
never there keeps the `low` finding, because that is a typo or a rename the
document missed. `assurance.blanket-suppression` does not read documentation
at all: a README explaining what `# noqa` means is not a suppression.

## What doc_drift leaves alone

A path named in prose is not drift when it is something a checkout never
carries. `drift.doc-references-missing-file` and `drift.broken-doc-link` skip a
path the root `.gitignore` matches (comments, `dir/`, `*.ext`, `path/**`, a
leading `/` and `!` re-inclusion are read; nested ignore files are not), a path
under the output directory — the `out:` key of `arbiter.yaml`, this run's `--out`
when it sits inside the scanned tree, or any `arbiter-out` segment — and a link
whose target carries a scheme (`git://`, `ssh://`). A backticked path is also
skipped when an `http(s)://` URL sits on its line or on the adjacent line of the
same paragraph, because `docs/handbook.md` named beside a link to another
repository describes that repository; a relative *link* is a claim about this
tree whatever else the line says, so it is not excused that way. A plainly
missing file still fires. The `.gitignore` shapes honoured are deliberately the
exact-path and directory ones: a pattern read too broadly hides real drift
(ARB-048).

