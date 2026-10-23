# Testing Harness

Four instruments that measure Arbiter beyond the planted corpus, the gold
benchmark and OWASP Benchmark:

| # | Instrument | Question | Tool | Runs in CI |
|---|---|---|---|---|
| 1 | [AI-written corpus](#1-the-ai-written-corpus) | On code a model wrote, how much of what is wrong does Arbiter report, and how much of what it reports is right? | `tools/ai_corpus_score.py` | labels validated (fast tier); snapshot (fast tier) |
| 2 | [Human-label sampling](#2-human-label-sampling) | What is each rule's *measured* precision, and do two people agree on it? | `tools/label_sample.py` | no (people label) |
| 3 | [Regression guards](#3-regression-guards) | Did a change remove findings, add findings, or slow the scan? | `tools/regression_guard.py` | in-tree snapshots (fast tier); pinned repos + timings (slow tier) |
| 4 | [Mutation variants](#4-mutation-variants) | Does a rule still fire when its defect is written another way? | `tools/variants.py` | proven by a test; the sweep is run by hand |

Every command below runs from the repository root with the source tree on the
path, e.g. `PYTHONPATH=$PWD/src python tools/ai_corpus_score.py`.
Numbers are from 2026-10-22, at Arbiter `da90898`. Other work changes rule
precision, probe speed and controls all the time, so treat them as a baseline
to re-measure, not as a property of Arbiter. The harness is what has to stay
reliable.

## Contents

- [1. The AI-written corpus](#1-the-ai-written-corpus)
- [2. Human-label sampling](#2-human-label-sampling)
- [3. Regression guards](#3-regression-guards)
- [4. Mutation variants](#4-mutation-variants)
- [What the numbers say](#what-the-numbers-say)

---

## 1. The AI-written corpus

### Layout

```
fixtures/ai-corpus/
  manifest.json            prompt, model, CLI version, date and file list per app
  corpus.json              scoring settings: metric rules, category→rule map, hygiene categories
  <language>/<task>/app/   what the model wrote, unchanged
  <language>/<task>/labels.json   the defects a reviewer found by reading app/
```

There are five tasks (`url-shortener`, `notes-api`, `file-upload`,
`job-queue`, `webhook-receiver`) in five languages (python, typescript, go,
java, ruby), so 25 apps and about 12,500 lines.

**Why `fixtures/`.** The repository already treats `fixtures/**` as a
corpus of defects, not as its own code. `arbiter.yaml` suppresses every
Arbiter, semgrep, bandit, checkov, gitleaks and (from this change) ruff
finding there, and the self-gate counts none of them. pytest collects only
`tests/` (`testpaths`), so the apps' own test files are never imported or
run. The scorer scans each `app/` directory as a repository root of its
own, so path-based test heuristics see the app's own relative paths
rather than a path under `fixtures/`. That is the same way `planted.score` scans
`fixtures/planted/<lang>/bad`.

### How the apps were made

Each app is one headless Claude Code run, made by
`tools/ai_corpus_generate.py`:

- `claude -p "<prompt>" --model sonnet`, which resolved to `claude-sonnet-5-5`
  (Claude Code 2.1.309), run on 2026-10-23 UTC (2026-10-22 local).
- The run starts in an empty directory. It gets only the file tools
  (`--tools Read Write Edit Glob`), so it cannot run, build or install
  anything. It loads no settings, skills or MCP servers
  (`--setting-sources "" --strict-mcp-config`), so nothing in the
  environment hints at what is being measured.
- The prompt is neutral. It says what the service does and then: *"Write it
  in {language}, using whatever libraries or framework you think are
  appropriate. Include automated tests and a short README. Keep it to a small
  number of files. Put all files in the current directory. Only write the
  files; do not run, build or install anything."* It never mentions
  security, error handling or review.
- The output was copied in unchanged. The apps are data. Nothing builds,
  installs or runs them, and nothing should.

Regenerate with
`python tools/ai_corpus_generate.py --out <scratch>` (`--only lang/task` for
one app). A regeneration is a new corpus, so labels have to be redone.

### How the labels were made

The labels are in two stages, and the stages are separate commits so that
history shows the order:

1. **Independent** (`a02f048`): one reviewer per language read every file of
   its five apps and listed what a senior reviewer would block or require a
   fix for. That covers security (CWE Top 25 and OWASP), error handling,
   concurrency, test quality, dependencies, documentation drift and clear
   logic bugs. Style, size metrics and features nobody asked for are left
   out. The reviewers were model subagents with no access to Arbiter, its
   rules, its docs or its output; the prompt gave only the scope. Each defect
   records `file`, `line` (and an optional `end_line`, ≤ 5 lines), `cwe`,
   `category`, `severity` and a `justification`, with
   `source: "independent"`. The result was 172 defects.
2. **Reconciliation** (`e965e1a`): Arbiter was run on the apps and every
   in-scope finding was then decided by hand. It is either a true positive
   against a label, or it goes in the app's `false_alarms` with a reason.
   A finding that showed a real defect the first pass missed would become a
   new label with `source: "reconcile"`. None did. Recall is reported on both
   sources, and the independent figure is the honest one.

> **Caveat.** The labels were written by a model, not by people. They are
> careful and every justification can be checked, but they are one
> reviewer's judgement per language and nobody has audited them. The tooling
> in [section 2](#2-human-label-sampling) can be pointed at this corpus
> (export findings as a sheet) to get a second labeller and a κ.

Labelling granularity was not fully uniform. The TypeScript and Ruby
reviewers labelled each loose version range as its own defect (one per
`package.json` or `Gemfile` line). The Python reviewer labelled each
`requirements.txt` as one span. That is why the headline leaves dependency
hygiene out; see below.

### Scoring

```bash
python tools/ai_corpus_score.py                       # all apps, native probes
python tools/ai_corpus_score.py --only go,python/notes-api
python tools/ai_corpus_score.py --adapters            # with semgrep, bandit, ruff, ...
python tools/ai_corpus_score.py --json out.json --markdown out.md
python tools/ai_corpus_score.py --validate            # labels only: every file/line exists
```

A finding **detects** a defect when all of these hold:

- it is in the same file;
- its line range is within `--tolerance` (2) lines of the defect's
  `[line, end_line]`;
- it names the same weakness: a shared CWE, or a rule glob in the defect's
  `match_rules`, or a rule glob in `corpus.json` → `category_rules` for the
  defect's category.

`category_rules` is the one place where a rule with no CWE (for example
`supply.unpinned-*` or `authored.undeclared-import`), or with a neighbouring
CWE (a swallowed error reported as CWE-391 against a CWE-390 label), is tied
to a category. A file-level finding (line 0, as `supply.unpinned-npm-dep`
reports) matches on weakness alone.

**Precision** counts every in-scope finding. One that detects a label is a
TP. One listed in `false_alarms` is an FP. One that is neither is
*unreviewed*: it counts as an FP in the headline and is listed by name, so
a rule change that starts firing somewhere new shows up as labelling work
instead of quietly lowering the number. Maintainability metrics and census
rows (`metric_rule_prefixes`) are out of scope. Info-severity findings stay
in scope, because the dependency rules report at info.

The **code-defect** view drops `hygiene_categories` (today
`unpinned-dependency`) and the findings that detected them. One app's
`package.json` can hold more labels than all of its code, and a headline
driven by version ranges says nothing about the code.

### Results (native probes, 2026-10-22)

Code defects, with dependency pinning left out:

| language | defects | detected | recall | findings | TP | FP | precision |
|---|---|---|---|---|---|---|---|
| go | 24 | 0 | 0% | 15 | 0 | 15 | 0% |
| java | 25 | 0 | 0% | 6 | 0 | 6 | 0% |
| python | 28 | 0 | 0% | 4 | 0 | 4 | 0% |
| ruby | 18 | 1 | 6% | 3 | 1 | 2 | 33% |
| typescript | 22 | 1 | 4% | 3 | 1 | 2 | 33% |
| **all** | **117** | **2** | **2%** | **31** | **2** | **29** | **6%** |

All labels, with dependency pinning included: 67/172 detected (31%), and
67/96 findings were true (70%). Dependency pinning alone is 52/55. The
three misses are two open-ended `Gemfile` ranges (`>= 1.7`), which no rule
covers, and a Maven plugin missing from the `pom.xml`. None of the 11 high-severity defects is detected.

With `--adapters` (semgrep, bandit, ruff and gitleaks installed), recall is
unchanged at 2/117. The analyzers add 225 findings, none of which detects a
labelled defect. 155 of them are bandit B101 (`assert` in pytest files) and
22 are semgrep `useless-inner-function` on FastAPI route handlers. Adapter
findings are not reconciled into `false_alarms`; they show as unreviewed.

The full tables, misses and false alarms are in
`tools/gold/results/2026-10-22-ai-corpus-score.md`.

#### Where the misses are

| category | defects | detected | notes |
|---|---|---|---|
| unbounded-memory | 12 | 0 | decompression bombs (`image.Decode`/`ImageIO.read`/Pillow on untrusted uploads), job maps never evicted |
| missing-authn | 10 | 0 | destructive or relay-capable endpoints with no auth (open SMTP relay through `send-email` jobs in 4 languages) |
| swallowed-exception | 10 | 2 | catch → 500 without logging, `rescue StandardError` → `false`, Java `printStackTrace` only |
| ignored-error | 9 | 0 | Go `json.NewEncoder(w).Encode(...)` and `db.Exec` results discarded (no `_ =`, so `go.swallowed-error` does not apply) |
| ssrf | 8 | 0 | DNS-rebinding gaps in hand-written guards, IPv4-mapped IPv6 bypass, merchant callback URLs to internal addresses. No SSRF rule fires on any of them. |
| unbounded-input / unhandled-input-error | 16 | 0 | body read before signature check, Jackson/Javalin bad JSON → 500 |
| path-traversal | 6 | 0 | the same shape in four languages: `resize-image` accepts any path when `IMAGE_ROOT` is unset (the default) |
| retry-no-backoff | 5 | 0 | fixed delays and immediate requeue. Arbiter's backoff rules match loops with *no* delay, not a constant one. |
| missing-timeout / server-no-timeout | 9 | 0 | `smtp.SendMail`, nodemailer and MiniMagick defaults; `http.ListenAndServe` with no `ReadHeaderTimeout` |
| tls-disabled | 3 | 0 | `smtplib.starttls()` / `SMTP_SSL` without a context (no certificate check); STARTTLS not required in Jakarta Mail |
| test-ineffective | 5 | 0 | tests that pass with the guard deleted (port 9 is refused anyway; `/files/..%2F` never routes) |

The model wrote defensive code by default: parameterised SQL, constant-time
compares, `secure_filename`, SSRF guards, bcrypt or scrypt, `crypto/rand`
and body limits. The injection and crypto rules that the planted corpus
scores at 100% had almost nothing to find. What remains is mostly
design-level: authorization, resource bounds, guards that are incomplete
rather than missing, and error paths. Pattern rules are weakest exactly
there.

#### False alarms, and two Arbiter defects they exposed

- `go.db-call-no-deadline` ×12: calls on a local SQLite file. This one is
  debatable, but neither pass would block the PR on it.
- `go.sql-injection` ×3 and `ruby.sql-injection` ×1: only package constants
  or literal fragments are concatenated; every value is a `?` placeholder.
- `authored.undeclared-import` ×9: `pydantic` with FastAPI, `werkzeug` with
  Flask, and `jakarta.mail` with `angus-mail`. Each is the declared
  framework's own dependency and documented API.
- `typescript.promise-catch-noop` ×2: `.catch(() => null)` used as a
  not-found signal and checked on the next line; a best-effort
  `reader.cancel()`.
- **Bug:** `drift.documented-env-var-absent` reports `WORKERS` as unread,
  but `config.ru` reads it. The probe does not read `.ru` files.
- **Bug:** `assurance.test-asserts-nothing` anchors one line early (on the
  blank line) whenever a blank line precedes the test. In `_TEST_DEF`, `^\s*`
  under `re.M` consumes the preceding newline. The mutation sweep found it
  too (section 4).

## 2. Human-label sampling

The gold budgets assume every finding on exemplary code is a false positive.
These two commands replace that assumption with a measurement.

```bash
# 1. A gold run that keeps every finding.
python tools/gold_benchmark.py --corpus ~/wt/corpus-gold --raw --no-fail --out arbiter-out/gold

# 2. A stratified sheet: N findings, at least one per (rule, language),
#    the rest by square root of stratum size; metric rules left out.
python tools/label_sample.py sample --report arbiter-out/gold/gold-report.json \
    --corpus ~/wt/corpus-gold --n 200 --out arbiter-out/labels/sheet.csv

# 3. Two people each label their own copy (see below).

# 4. Score.
python tools/label_sample.py score alice.csv bob.csv --json arbiter-out/labels/result.json
```

**The sheet** (CSV, or JSON if `--out` ends in `.json`) has one row per
finding. The columns are `sample_id`, `rule`, `language`, `repo`, `path`,
`line`, `severity`, `title`, `stratum_size` (how many findings its rule and
language had in the run, used to weight the estimate back), `context` (±4
lines with `>` on the finding line), and the three columns the labeller
fills in: `verdict`, `labeller` and `notes`. Rows are shuffled, so one rule's
rows do not come in a block. `--rule-prefix arbiter/go.` restricts the
sheet; `--exclude-prefix` replaces the default exclusions; `--seed` makes a
draw reproducible (default 20261022).

**Labelling instructions.**

1. Copy the sheet once per labeller. Do not share verdicts until both copies
   are done.
2. For each row, read the context, opening the file at the pinned commit
   when it is not enough. Write `TP` if the finding is correct about this
   code: the rule's claim holds and a maintainer should act on it. Write `FP`
   otherwise.
3. Leave it blank or write `unsure` to abstain. An abstention is not counted,
   and it is better than a guess.
4. Write the reason for every `FP` in `notes`. The reasons become rule fixes.

**The score** has three parts:

- Precision per rule, with a 95% Wilson interval and the rule's population.
- An overall precision weighted back to the population by stratum, with a
  stratified confidence interval and finite-population correction. The
  unweighted share would let a rare rule count as much as a loud one.
- With two sheets: observed agreement, Cohen's κ overall and per rule, and
  the `sample_id`s to adjudicate. Precision is then computed on the rows
  both labellers agree on; settle each disagreement by writing the agreed
  verdict into both sheets and scoring again.

**Reading κ.** Under 0.6 means the verdict cannot be decided from the sheet.
Write down the rule's intent, add context, and relabel before believing any
precision number built on it. κ is undefined (`null`) when both labellers
used a single category: chance agreement is then 1, and agreement says
nothing.

Exercised on 2026-10-22 against a five-repository gold run (py-urllib3,
ts-jose, ruby-puma, go-coredns, java-httpclient): 401 in-scope findings gave
a 60-row sheet from 41 strata. No labelling has been done yet; that is work
for people.

## 3. Regression guards

```bash
python tools/regression_guard.py check --targets planted,ai-corpus --no-perf   # what the fast tier asserts
python tools/regression_guard.py fetch --corpus ~/corpus-guard                 # the pinned repos, at their pins
python tools/regression_guard.py check --corpus ~/corpus-guard                 # everything, with timings
python tools/regression_guard.py update --corpus ~/wt/corpus-gold              # accept an intended change
```

Targets are in `tools/gold/guards.json`. Two are in-tree (`fixtures/planted`
and `fixtures/ai-corpus`). Four are small gold repositories pinned in
`tools/fetch_corpus.sh`: `py-urllib3`, `ts-jose`, `ruby-puma` and
`go-coredns`. They have 47, 134, 74 and 308 findings and scan in 1.6 to 7 s.
The large gold repositories stay with `gold_benchmark.py`, because a
snapshot diff of 5,000 findings is one nobody reads.

**Snapshots** (`tools/gold/snapshots/<target>.json`) list every active
`rule⇥path⇥line` with native probes and no cache. `check` fails on any
finding gone or new, prints the diff, and names every rule that no longer
fires anywhere in the target. If the change is intended, run `update` and
commit the snapshot with the rule change, so the reviewer sees what moved.
Parallel work on rule precision will move these often; that is the guard
doing its job.

**Time budgets** (`tools/gold/snapshots/perf-baseline.json`) record each
target's best-of-three scan time and a calibration time. A target fails when
its scan takes longer than `baseline × machine_factor × 1.5 + 2 s`. The
machine factor is this machine's time on a fixed pure-Python workload
divided by the baseline machine's, and it is never below 1. A CI runner is
therefore held to its own speed, and a faster machine never tightens the
budget. The calibration runs no Arbiter code. The first version timed a
scan of the planted corpus instead, and a seeded `sleep` in the rule runner
slowed that reference as much as every target. The budget grew with the
regression and the guard passed, so it was replaced.

**Where it runs.** `tests/test_regression_guard.py` runs in the fast tier
(about 15 s). It asserts that the in-tree snapshots match, and it proves
each guard both ways:

| seeded regression | expected | test |
|---|---|---|
| none | passes | `test_snapshot_guard_passes_on_the_unchanged_tree` |
| `python.sql-injection` deleted from its pack | snapshot fails, names the rule | `test_snapshot_guard_fails_when_a_rule_is_deleted` |
| `sleep` in `lang_rules.run_pack` | budget fails | `test_perf_guard_fails_when_a_probe_gets_slower` |
| the same, with the machine factor measured live | budget still fails | `test_perf_guard_is_not_fooled_by_a_slowdown_that_also_hits_the_reference` |

It was also proven by hand from the CLI. Removing `db-call-no-deadline` from
`go.yaml` made `check` fail planted with "rules that no longer fire at all:
arbiter/go.db-call-no-deadline". A 3 s sleep in `run_pack` failed py-urllib3
(8.2 s against a 4.6 s budget) and ts-jose (5.0 s against 4.7 s).
`pr-check.yml`'s slow-tier job fetches the four pinned repositories and runs
the full `check` (about a minute).

## 4. Mutation variants

```bash
python tools/variants.py                                  # all languages, all transforms (~15 s)
python tools/variants.py python go --kinds hoist,helper
python tools/variants.py --json v.json --markdown v.md --keep /tmp/variants   # keep the rewritten trees
```

`tools/variants.py` rewrites each planted bad file with one transform at a
time, using tree-sitter, in all five languages:

| transform | what changes |
|---|---|
| `rename` | locals and parameters become `v1, v2, …`. Attributes, keyword arguments, struct keys, shorthand properties and Ruby keyword parameters are left alone. |
| `hoist` | the first non-trivial argument of the marked statement's outermost call moves into a temporary on the line before |
| `helper` | the same argument passes through an identity function `_same(x)` defined in the file |
| `move` | the function holding the marker moves to the end of its module or class |
| `strings` | f-string ↔ `%` ↔ `.format` ↔ `+` (Python); template literal ↔ `+` (TS/JS); `fmt.Sprintf("%s")` ↔ `+` (Go); `String.format("%s")` ↔ `+` (Java); `#{}` ↔ `+ .to_s` (Ruby) |

Each `expect:` marker is followed to its new line. Markers are comments on
the defect line, so they travel with it. A rewritten file that no longer
parses is discarded, never scored. Some shapes are refused because moving
them would change meaning: a heredoc opener, an `await`, a lambda or a
multi-line argument. The rewrites are checked for syntax, not types. For
example, Go `Sprintf("%s", x)` → `"" + x` assumes `x` is a string. That is
enough for a static checker and not a compiler guarantee. For `hoist`, a
finding on the temporary's line counts as kept, because that is where the
defect's expression now is.

**Robustness** per rule is `kept / applicable` over every (marker,
transform) pair the transform actually changed. Markers that the unmodified
scan already misses are not scored. A lost finding reported within three
lines of the defect is flagged as a location regression.

### Results (2026-10-22)

404 of 486 pairs kept (83%).

| transform | kept |
|---|---|
| rename | 200/219 |
| hoist | 57/83 |
| helper | 60/83 |
| move | 56/57 |
| strings | 31/44 |

The weakest rules, with at least three applicable pairs:

| rule | kept | what breaks it |
|---|---|---|
| `typescript.html-from-request` | 1/8 | any rename, hoist or helper |
| `go.stub-not-implemented` | 1/3 | hoist, helper |
| `go.test-skipped-unconditionally` | 1/3 | rename, hoist |
| `ruby.command-injection` | 1/3 | `#{}` → `+` concatenation |
| `typescript.sql-string-built` | 3/8 | hoist, helper, template → `+` |
| `go.error-text-in-response` | 5/13 | renaming `w`/`c` (the rule keys on the parameter name) |
| `go.command-injection` | 3/7 | hoist, helper |
| `ruby.sql-injection` | 5/11 | hoist, helper, interpolation → `+` |
| `go.sql-injection` | 9/14 | helper (0/3) |

The pattern is consistent: most injection and response-leak rules match the
dangerous value only while it is written inline in the sink call. Put it in
a temporary or pass it through a function, as generated code routinely
does, and the finding is gone. Name-keyed rules (`*.weak-random-for-secret`,
`go.error-text-in-response`) lose findings under `rename` by design. That is
worth knowing, not necessarily a defect. The full table is
`tools/gold/results/2026-10-22-variants.md`.

`tests/test_variants.py` checks that every transform parses and keeps its
marker in all five languages, refuses unsafe shapes and discards unparsable
output. It also seeds a regression: `python.sql-injection` is made blind to
`" + str(` concatenation, and the sweep must report the loss and a
robustness below 1.

## What the numbers say

1. **On code a current model writes for interview-style tasks, Arbiter's
   native probes catch 2% of the code defects a reviewer finds, and 6% of
   their findings in code are right** (dependency pinning aside, where it is
   94% and 100%). The planted corpus measures something real, the rules
   firing on the shapes they were written for, but it says little about this
   population.
2. Biggest recall gains, by count and severity: resource bounds on untrusted
   input (image decoding, request bodies, job retention); unauthenticated
   destructive or relay endpoints; SSRF on any server-side fetch of a
   user-supplied URL, including callback URLs; ignored errors in Go beyond
   `_ =`; path checks that only run when an env var is set; TLS without
   verification in SMTP clients; constant-delay retries.
3. Biggest precision fixes: `go.db-call-no-deadline` on embedded databases,
   `go/ruby.sql-injection` on constant concatenation, and
   `authored.undeclared-import` for a framework's own dependencies.
4. Robustness: make the injection rules follow a value through one local
   assignment and one function call. Without that, the AI corpus will keep
   showing misses that the planted corpus scores as caught.
