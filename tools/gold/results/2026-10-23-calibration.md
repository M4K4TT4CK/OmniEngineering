# Labeller calibration, round 2 (2026-10-23)

The question: when the model labellers (`tools/label_models.py`, prosecutor +
defender + adjudicator, `claude-opus-5-5` through `claude -p --model opus`,
prompt `v1`) call a finding `tp`, how often is that right, measured against
truth they never saw? Round 1 (`calibration.jsonl`, 250 items) gave 143/144,
Wilson 95% lower bound 0.962. A 0.99 lower bound needs about 381 `tp` calls
with no error.

Labels: `tools/gold/labels/calibration-2.jsonl` (661 records: 450 ground
truth, 211 planted twins). **No person labelled anything.** Cost **$21.27**
over 1,354 CLI calls (ground truth $15.50, planted $5.77), under the $25 cap.
The run hit a server throttle once and the session usage limit once; both
times it stopped and resumed from the cache. A throttle is now retried
instead of ending the run (see below).

## Verdict by category

| category | truth | n | `tp` calls right | `tp`-call precision | Wilson 95% lower | can back a 0.99 claim? |
|---|---|---:|---:|---:|---:|:-:|
| injection, weak random/hash, deserialisation: rounds 1+2 pooled | OWASP Java/Python, Juliet Java, CVE fixes | 700 | 395/401 | 0.985 | **0.968** | no |
| same, BenchmarkTest01096 left out | | 699 | 395/400 | 0.988 | 0.971 | no |
| same, items whose taint source is outside the excerpt left out | | 675 | 389/390 | 0.997 | 0.986 | no (close) |
| round 2 alone | OWASP Java/Python, Juliet Java | 450 | 252/257 | 0.981 | 0.955 | no |
| tests | planted twins | 45 | 24/24 | 1.000 | 0.862 | no |
| secrets | planted twins | 30 | 17/17 | 1.000 | 0.816 | no |
| stubs | planted twins | 34 | 17/17 | 1.000 | 0.816 | no |
| resilience | planted twins | 59 | 25/27 | 0.926 | 0.766 | no |
| concurrency | planted twins | 43 | 16/18 | 0.889 | 0.672 | no |
| secrets in test/example paths | path fact | 90 | 0 `tp` calls; 90/90 `fp` | - | - | no (`fp` side only) |
| `supply.unpinned-action` | SHA-pin fact | 18 | 18/18 | 1.000 | 0.824 | no |
| `supply.unpinned-npm-dep` with a lockfile | lockfile fact | 18 | 0 `tp` calls; 17/17 decided `fp`, 1 unsure | - | - | no (`fp` side only) |
| docs drift | - | 1 decidable | - | - | - | no (no usable fact) |

**Plainly: no category's model labels reach a lower bound of 0.99.** The
injection family is the best calibrated, at 0.968 on 401 `tp` calls. Its
remaining errors are not random: every one of its 6 false TPs is a case
where the fact that decides the claim is not in the excerpt (below). With
those cases left out, it is 389/390 (0.986). The planted categories have
too few items to reach 0.99 even with no errors (about 381 needed), and they
are fixtures we wrote. Model labels may back a precision figure only with
the labeller error carried into it. Treat a model-labelled rule as known
to about ±3% at best, and as uncalibrated outside injection.

## 1. Injection family: 450 more blind ground-truth items

The draw: OWASP Benchmark Java 1.2 and Python 0.1, and Juliet Java 1.3
(single-file cases only). It is stratified by (source, rule, truth) with
square-root allocation and seed 20261023, with every round-1 finding
excluded (`--exclude-labels tools/gold/labels/calibration.jsonl`). Truth mix:
260 truth-TP, 190 truth-FP (Juliet 228, OWASP Java 157, OWASP Python 65). Blinding is unchanged: comments blanked, case
names and paths replaced.

| round 2 | n | unsure | accuracy (decided) | `tp` calls right | `tp`-call precision | Wilson 95% lower | false-TP rate | recall of true TPs |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **final** | 450 | 17 | 0.988 | 252/257 | 0.981 | 0.955 | 0.026 | 0.969 |
| prosecutor | 450 | 18 | 0.986 | 251/257 | 0.977 | 0.950 | 0.032 | 0.965 |
| defender | 450 | 10 | 0.982 | 257/265 | 0.970 | 0.942 | 0.042 | 0.988 |
| unanimous only | 438 | 9 | 0.988 | 251/256 | 0.980 | 0.955 | 0.027 | 0.988 |

κ prosecutor/defender 0.948; final vs truth 0.976; 12 of 450 adjudicated.
Unsure rate 3.8% (all 17 on Juliet). Every `fp` call was right (176/176;
round 1 94/94).

| pooled rounds 1+2 | n | unsure | accuracy (decided) | `tp` calls right | `tp`-call precision | Wilson 95% lower |
|---|---:|---:|---:|---:|---:|---:|
| **final** | 700 | 29 | 0.991 | 395/401 | 0.985 | 0.968 |
| prosecutor | 700 | 31 | 0.988 | 393/401 | 0.980 | 0.961 |
| defender | 700 | 13 | 0.981 | 405/418 | 0.969 | 0.948 |

κ prosecutor/defender 0.936; final vs truth 0.981.

By source, round 2 (final): Juliet 104/105 `tp` calls right (lower 0.948);
OWASP Java 97/100 (0.915); OWASP Python 51/52 (0.899). By CWE and truth:
CWE-89 truth-TP 88/88 called right, CWE-89 truth-FP 4 of 106 called `tp`.
CWE-78 truth-FP 1 of 58 called `tp`. CWE-79, 502, 95, 330/338 and 327/916
had no false TP. Per rule, the lower bounds are 0.89 to 0.92 for the Java
injection rules and 0.38 to 0.81 for the small Python strata. Full tables:
`label_models.py summarize --labels tools/gold/labels/calibration*.jsonl`.

### The false TPs: all six have their deciding fact outside the excerpt

| case | truth | why the labellers were wrong |
|---|---|---|
| OWASP Java 01817, 02728, 00939 (`sql-string-built`) | safe | `param = scr.getTheValue(...)`. `SeparateClassRequest.getTheValue` is the benchmark's deliberate "safe source": it `return "bar";`. It is defined in `helpers/SeparateClassRequest.java`, which the excerpt does not show. The labellers assumed it wraps the request. The benchmark is right. |
| OWASP Python 01098 (`command-injection`), round 1's 01096 (`unsafe-deserialization`) | safe | `param = request.path.split("/")[1]` on a fixed Flask route `/benchmark/<cat>-00/BenchmarkTestNNNNN`, so `param` is always the literal `"benchmark"`. The route decorator is outside the excerpt (it shows the function from its `def`). The benchmark is right. |
| Juliet `CWE89_..._executeUpdate_81_goodG2B` | safe | A cross-file `_81` case. The safe constant is set in `_81a`, which calls this file through a base class. The `_cross_file` filter only catches `_NNa`-style names, so this one slipped into calibration. |

Across both rounds, 25 items have one of the first two patterns. Their `tp`
calls were right 6 times in 11 (`scr.getTheValue`: 4/7; `request.path`
index 1: 2/4). Items without them: 675, with **389/390** `tp` calls right (lower
0.986). The one remaining error is the Juliet `_81` case.

### BenchmarkTest01096 (task item 3): the benchmark's label is right

Round 1 said the labellers looked right and the benchmark wrong. That was
mistaken. Evidence:

- The route is `@app.route('/benchmark/deserialization-00/BenchmarkTest01096')`.
  Werkzeug matches it literally, so `request.path.split("/")` is
  `['', 'benchmark', 'deserialization-00', 'BenchmarkTest01096']` and
  `parts[1] == "benchmark"`. The `'should' in bar` trick does pass `param`
  through, but `param` is a constant. `pickle.loads(base64.urlsafe_b64decode("benchmark"))`
  never sees attacker bytes.
- The benchmark is consistent about it. Of the 112 test cases that take
  `param` from `request.path.split("/")[1]`, 94 are marked safe. The 18
  marked vulnerable are all weakrand, hash or securecookie, categories that
  need no tainted input.

So 01096 stays a labeller error, and the numbers are reported both ways
above: 395/401 (lower 0.968) with it, 395/400 (0.971) without it. The
correct reading is "with it".

What the labellers missed is shown neither in the round-1 excerpt nor in
round 2's: `gt_common._function_span` starts at `def`, not at the
decorator. Showing Python decorators would expose the route. That changes
every Python excerpt hash, so every cached Python label would be relabelled.
It is left as an open question, not done mid-calibration.

## 2. Other categories

### Planted twins (tests, secrets, stubs, concurrency, resilience)

`label_models.py label --planted ... --planted-cap 6` takes each Arbiter
finding on `fixtures/planted/<lang>/bad` (truth `tp`, the planted `expect:`
line), at most 6 per category and language. It pairs each with the
matching line of the `good` twin (truth `fp`): the same test title or
assigned name, else the closest line of the same-named function. Both
sides are blinded: comments (so the `expect:` markers), Python docstrings,
and `cwe`/`planted` names in paths and packages are removed.

| | n | unsure | accuracy (decided) | `tp` calls right | `tp`-call precision | Wilson 95% lower | `fp`-call precision |
|---|---:|---:|---:|---:|---:|---:|---:|
| final | 211 | 10 | 0.935 | 99/103 | 0.961 | 0.904 | 0.908 |
| prosecutor | 211 | 13 | 0.960 | 101/104 | 0.971 | 0.919 | 0.947 |
| defender | 211 | 7 | 0.956 | 106/110 | 0.964 | 0.910 | 0.947 |

κ prosecutor/defender 0.904; 11 adjudicated. Per category, see the table at
the top. Tests, secrets and stubs had no false TP. Their `fp` misses (a true
TP called `fp`) were 1 each: a Ruby `pending` spec, and a secret the shape
facts called placeholder-like.

The 14 disagreements with construction truth are worth reading before
trusting the fixtures more than the labellers:

- **Ruby `Net::HTTP` "no timeout" (4 false FPs).** The claim says the call
  "can block indefinitely". The labellers point out that Net::HTTP defaults
  to 60 s open/read (and write since Ruby 2.6) timeouts. That is right: the
  planted defect is a long default, not an unbounded one. The rule's claim
  wording, or the fixture, is the thing to fix.
- **Go `unsynchronised-counter` in a branch that cannot run (2), Ruby
  `@@started += 1` on one thread (1).** The planted helper always returns
  nil, so the racy increment is unreachable as written. The labellers are
  literally right, and the fixture is too simple.
- **Ruby `pending` (1).** RSpec runs a `pending` body and fails if it
  passes, so it is not "skipped unconditionally". The labellers are right
  about RSpec semantics.
- **Real labeller errors on good twins (4 false TPs):** a Python retry loop
  with a 60 s deadline and constant sleep (claim: "no growing delay *or* no
  limit" — the labellers read "or" as either suffices, so arguably not an
  error); an axios call in the good twin with `axios.defaults.timeout` set
  outside the excerpt; Ruby `NAMES[name] ||= klass` on a `Concurrent::Map`;
  and a Go results map written by a collector goroutine.

So the planted numbers are a lower bound on labeller quality as much as a
measure of it. They cannot back a claim in any case: with no errors, 45
items give at most 0.92.

### Tests: mutation testing is not usable as truth here

`tools/mutate_tests.py` is not a general mutation tester. It reapplies a
fixed list of defects this project shipped to `src/arbiter/` and runs the
single pytest that names each one. It says nothing about a
test-asserts-nothing finding on another file. Using mutation results as
truth for gold-corpus tests would mean executing corpus tests, which the
rules forbid. Running the planted test files would need their toolchains
(vitest, RSpec, JUnit, go test) and gives no more truth than the
construction already does. The planted twins above are the model-free truth
used for tests.

### Secrets: path facts and known-public values

- **Known-public example values** (AWS's `AKIA...EXAMPLE` access key id /
  `wJalrXUtnFEMI/K7MDENG...EXAMPLEKEY`, the jwt.io sample token, Stripe's
  documented `sk_test_` key): none of the 102 gold secret findings carries
  one, so this gives no items. GitHub secret-scanning fixtures were not
  fetched. The gold set already has 90 path-decidable items, and fetching
  more is only worth it with planted true positives to pair them with.
- **Path fact** (`label_models.py fact-check`): 90 of the 102 gold secret
  findings sit under a test, example, fixture, docs or sample path. The claim
  itself excludes those, and the labellers called all 90 `fp`. That tests
  the `fp` side only. Of the other 12 (production paths), the model called
  1 `tp` (a Codecov upload token in `codecov.yml`), and no fact decides
  those 12.
- Planted: 17/17 `tp` calls right.

### Supply chain and docs drift: model labels against facts

`label_models.py fact-check --labels gold-model.jsonl ai-corpus-model.jsonl cve-suspects-model.jsonl`:

| rule | items a fact decides | agree | fact | note |
|---|---:|---:|---|---|
| `supply.unpinned-action` | 18 | 18 | 18 use a tag/branch → tp | the labellers saw the line; this checks they apply the claim |
| `supply.unpinned-npm-dep` | 18 | 17 + 1 unsure | lockfile present → fp | the lockfile fact was in their prompt |
| `drift.documented-env-var-absent` | 1 | 1 | all names read in code → fp | |
| `drift.broken-doc-link`, `drift.doc-references-missing-file` | 0 | - | - | see below |

The labellers were shown the lockfile and link facts, so agreement here
measures whether they follow facts, not an independent truth. Docs drift
has no usable fact. "The path exists nowhere in the repository" looked
decisive, but 21 of 28 such items are model `fp` for sound reasons:

- Rails guides link to the `.html` page that the `.md` builds into;
- prose names generic files (`structure.sql`, `config/recurring.yml` inside
  a user's app) or other projects (`express.js`).

The rule's claim and the excerpt cannot settle these. Only "every target
exists → fp" is kept as a fact.

## 3. Tool changes made for this round

- `label_models.py label --exclude-labels FILE...` keeps a ground-truth draw
  away from earlier rounds' items.
- `--planted CATEGORY... [--planted-cap N]` builds the planted twin items,
  and `gt_common.blind(..., "planted")` / `blind_path` do the blinding.
- `label_models.py fact-check` compares labels with mechanical facts.
- A server throttle ("temporarily limiting requests (not your usage
  limit)") is retried with growing delays (2/5/15/40 s). The old pattern
  matched "Rate limited" as a usage limit and stopped the first run after
  196 of 450 items. Usage, session and credit limits still stop the run.
- `precision_ledger.py build` drops `calibration:` records, so
  `labels/*.jsonl` can be passed whole without double-counting ground truth
  or inventing planted good-twin findings.

## Open questions

1. **Show what the excerpt hides.** All 6 injection false TPs come from
   two things. First, a source defined in another file
   (`scr.getTheValue`, Juliet `_81` base classes). Second, a decorator
   outside the function span (Flask routes). Options, cheapest first:
   - include Python decorators in the span;
   - add to the excerpt the same-repository definition of any method called
     on the taint path, not only same-file helpers;
   - widen `_cross_file` to Juliet `_NN_bad`/`_NN_good*` names.
   Each changes excerpt hashes, so cached labels are redone. Recommendation:
   do all three, then run round 3 (about 500 items, about $18). If it holds
   at round 2's rate on visible-source items, the pooled lower bound
   reaches 0.99 at roughly 470 clean `tp` calls.
2. **The Phase 2 verifier has the same blind spot.** It sees the same kind
   of single-file excerpt. Its precision on injection cannot be better than
   the excerpt allows, so fix (1) in `verify.py` too.
3. **Claim wording for resilience.** "Can block indefinitely" is false for
   Ruby Net::HTTP's 60 s defaults, and the labellers say so. Either reword the
   claim to "no explicit timeout, so it waits as long as the library default
   (60 s or more)", or treat these as FP. The rule owner decides; it changes
   the claim hash.
4. **Planted fixtures are too easy and sometimes wrong.** These include
   unreachable racy branches and `pending` treated as skipped. They
   regression-test Arbiter fine, but as labeller truth they need review, and
   they cannot reach 0.99 at their size. Truth for tests, concurrency and
   resilience on real code still needs a source that no model judges. For
   tests, mutation runs on this repo's own suite are the only option the
   rules allow.
