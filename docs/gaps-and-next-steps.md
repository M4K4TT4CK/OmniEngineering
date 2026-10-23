# Gaps and next steps

Where Arbiter and its workspace tooling (OmniEngineering, "omni") stand on
2026-10-21, what is missing, what to improve first, which testing to add,
and a three-milestone sequence. Written for the product's future, not for
one demo. (ARB-067)

## How claims are labelled

ARB-074 asks every report to say how it knows what it says. This one does.

| Label | Meaning |
|---|---|
| **[M]** | measured: the source file or command is named next to it |
| **[I]** | inferred: reasoned from measured facts; the reasoning is given |
| **[A]** | assumed: a judgement or estimate nobody has measured yet |

Effort is given as S (hours to a day), M (days) or L (weeks). Every effort
figure is **[A]**.

Two caveats apply to every gold and OWASP number below:

- They were measured at Arbiter `d57e479`, **native probes only**
  (`"adapters": false` in `tools/gold/results/2026-10-21-*.json`) **[M]**.
  semgrep, bandit and gitleaks were not part of those runs, so OWASP recall
  is a native-only floor.
- Four shared precision fixes merged after `d57e479`: Go `t` helpers,
  `//go:build ignore`, `InsecureSkipVerify` with a custom verifier, and
  `_test/` directories (`git log overnight-integration`) **[M]**. They touch
  rules that are not on the over-budget list, so the 16 breaches most likely
  still stand **[I]**.

---

## 1. Where Arbiter stands today

### Coverage

| What | Number | Label and source |
|---|---|---|
| Native pack rules | python 12, typescript 25, go 16, java 19, ruby 19 | [M] `- id:` count in `src/arbiter/packs/rules/ast/*.yaml` |
| Planted corpus, native-only | go 42/42, java 39/39, python 40/40, ruby 46/46, typescript 51/51; 0 on good twins and 0 unmarked in every language | [M] `python tools/planted_score.py`, run for this report |
| Planted defects nothing reports | 32, each tagged `gap: CWE-N` in the fixture source | [M] `grep -r "gap: CWE-" fixtures/planted` |
| CWE Top 25: applicable cells caught by anything (native or analyzers) | Python 12/14, TS 10/14, Go 7/12, Java 8/15, Ruby 8/14 | [M] `docs/cwe-top25.md`, "Measured coverage" |
| CWE Top 25: native-only | Python 6, TS 8, Go 6, Java 4, Ruby 6 | [M] same table |
| Out of reach for single-file checks | 862, 863, 306, 269 and 20 in every language; 476 in Go and Java; 352 in Go; 918 beyond one function | [M] `docs/probes.md`, "CWE Top 25: not assessed" |

The planted corpus is a regression floor, not a recall estimate. Every
planted defect was written by the same people who wrote the rules **[I]**.

### Precision on exemplary code (gold benchmark, ARB-065)

| Figure | Value | Source |
|---|---|---|
| Corpus | 24 pinned repositories, 6,085.9 KLOC | [M] `tools/gold/results/2026-10-21-gold-summary.md` |
| Findings | 12,468 (2.049 per KLOC), 97 rules fired | [M] same |
| Rules over budget | 16 | [M] same |
| Share of all findings from the 16 over-budget rules | 6,514 of 12,468 (52%) | [M] `rules[].count` in `2026-10-21-gold-report.json` |
| Share from the three P1 rules alone | 4,750 (38%) | [M] same |
| `typescript.test-without-assertion` | 729 findings, about 98% false positives | [M] count / [I] FP share from bucketing, `2026-10-21-triage.md` |
| `authored.undeclared-import` | 2,325 findings, about 99% false positives | [M] / [I] same |
| `secrets.assigned-credential` | 1,696 findings, about 100% false positives in the sample | [M] / [I] same |

The triage reached its "≈" FP shares by bucketing each rule's findings with
a pattern and spot-checking every bucket. It did not read every finding
(`2026-10-21-triage.md`, "Method") **[M]**, so the FP shares are **[I]**. No
human-labelled sample with a confidence interval exists yet.

### Recall on labelled ground truth (OWASP Benchmark, native-only)

| Benchmark | In-scope recall | In-scope FPR | Worst cells | Source |
|---|---|---|---|---|
| Java v1.2, 2,740 cases | 0.045 | 0.043 | command injection 0/126, weak randomness 0/218, SQL injection 28/272 with 27 FP | [M] `2026-10-21-recall-owasp-benchmark-java.md` |
| Python v0.1, 1,230 cases | 0.221 | 0.132 | weak randomness 0/99, weak hash 0/71; code injection 20/20 caught **and** 33/33 safe cases flagged (FPR 1.0); command injection FPR 1.0 | [M] `2026-10-21-recall-owasp-benchmark-python.md` |

The Java misses are syntactic, not conceptual (`2026-10-21-triage.md`)
**[M]**:

- `r.exec(...)` through a local `Runtime r` is missed;
- a `List` passed to `pb.command(...)` is missed;
- `new java.util.Random()` is a `scoped_type_identifier`, which the query
  does not match;
- a sink inside `try { }` is not a sibling of its `String sql` declaration.

The Python false-positive rates come from constant-propagation decoys, which
no syntactic rule can tell apart from real defects (same file) **[M]**.

### Scan time

| Repository | KLOC | Seconds | KLOC/s | Source |
|---|---:|---:|---:|---|
| java-keycloak | 1,187.3 | 2,044.6 (34 min) | 0.58 | [M] `repos[].seconds`, `2026-10-21-gold-report.json` |
| ruby-rails | 499.8 | 1,066.3 | 0.47 | [M] same |
| ts-nest | 167.6 | 363.5 | 0.46 | [M] same |
| py-aiohttp | 85.0 | 14.0 | 6.07 | [M] same |
| All 24 | 6,085.9 | 6,454.9 (1 h 48 min) | 0.94 | [M] same |
| yaegi (training corpus) | | about 240 (4 min) | | [M] observed during the ARB-061 precision run; not in a results file |

By language, throughput ranges are **[M]** (same file):

- Ruby: 0.47–0.53 KLOC/s on all four repos.
- Java: 0.58–1.81.
- TypeScript: 0.46–3.98.
- Python: 0.71–6.07.
- Go: 2.24–5.26.

The Ruby, Java and TypeScript packs hold the most rules (19–25). The slowest
repos are in those languages, which fits the per-file query recompilation
described in section 2 **[I]**. A profile has not been taken, so the share
of time spent compiling queries is unknown.

---

## 2. Gaps

### Detection

| Gap | Evidence | Label |
|---|---|---|
| 32 planted CWE defects with no detector | ranked list in `docs/cwe-top25.md`: JWT decoded without verification, literal signing keys, CSRF switched off, error detail returned to the client, uploads saved under the client's filename, SSRF, path joins, argument injection, Java XSS, Go narrowing | [M] |
| No value tracking (constant propagation or taint), even within one function | OWASP Python FPR 1.0 on code and command injection; the Java SQL sink inside `try` is missed | [M] |
| No cross-file or interprocedural analysis | 862, 863, 306 and 918 beyond one function are declared out of reach | [M] `docs/probes.md` |
| Rules are brittle to small rewrites | aliasing (`Runtime r`), fully qualified names (`java.util.Random`), nesting (`try`) drop whole OWASP categories to 0 or near it (0/126, 0/218, 28/272) | [M] triage; [I] that the planted corpus never exercised these variants |
| gitleaks finds none of the planted credentials | they are generic passwords and keys, not provider-shaped tokens | [M] `docs/cwe-top25.md` |
| Findings with the same evidence in one file and symbol collapse into one: six `catch {}` report once | `Finding.fingerprint()` hashes rule, repo, path, logical symbol and snippet but no line or ordinal (`src/arbiter/core.py`), and `engine._dedupe` keeps one per id | [M] code; [I] that the collapse is a side effect of making the fingerprint survive line shifts, not an intended count. **Fixed 2026-10-21 (ARB-080):** each distinct site gets an occurrence ordinal; old baselines keep matching (`tests/test_occurrence_fingerprint.py`) **[M]** |

### Precision

The 16 rules over budget, and the fix for each, are in
`tools/gold/results/2026-10-21-triage.md` **[M]**. In order of false
positives removed per unit of work:

1. **`typescript.test-without-assertion`**: recognise `tspl` plan
   assertions and AVA's `t.*`. `throwsAsync` fails the `\bthrows?\b` match.
2. **`authored.undeclared-import`**: three cheap manifest-lookup bugs, and
   one hard case:
   - Python `requirements/*.txt`;
   - Go multi-module `go.mod`;
   - Node built-ins imported without `node:`;
   - the hard case is Gradle convention plugins and Maven parent POMs and
     BOMs.
3. **`secrets.assigned-credential`**: report only a literal on the
   right-hand side that looks like a secret, and skip `.properties` message
   bundles.
4. **SQL injection rules (Python, Ruby, Java)**: accept splices that pass
   through `quote_name` or `escape_identifier`, and statements that use `?`
   placeholders.

Corpus thinness was a precision gap of its own: early precision corpora had
two clean TypeScript repositories **[M]**, observed in the ARB-061 run. The
gold set now holds 4 or 5 per language (`tools/gold/repos.json`) **[M]**.

### Performance

- **`run_query` recompiles every query for every file.**
  `src/arbiter/ast.py` builds `Query(lang, query_src)` inside `run_query` on
  each call **[M]**. With N files and R rules, that is N × R compilations
  where R would do.
  **Fixed 2026-10-21 (ARB-076):** compiled queries are cached per process by
  grammar and query text; `tests/test_query_cache.py` counts compilations
  **[M]**. Same four repositories, same machine,
  `tools/gold_benchmark.py --corpus ~/wt/corpus-gold --only
  java-keycloak,ruby-rails,go-grpc,ts-nest --jobs 4 --no-fail`, at
  `de35f15` and then `d693074`, identical findings (2,200) **[M]**:

  | Repository | Before (s) | After (s) |
  |---|---:|---:|
  | ts-nest | 252.5 | 26.2 |
  | ruby-rails | 720.1 | 203.3 |
  | go-grpc | 59.1 | 45.9 |
  | java-keycloak | 2,124.1 | 1,499.8 |

  Other agents were scanning on the same machine during both runs (load
  average 4 to 17), so single figures carry noise **[M]** load; **[I]** effect.
  Keycloak is now dominated by shared probes, not packs: on
  `java-keycloak/services` (1,751 Java files) `authored` took 28.4 s,
  `doc_drift` 14.9 s and `lang_java` 6.2 s (`ProbeOutcome.duration_s`)
  **[M]**. The full 24-repository gold run at `7dd990c` (`--jobs 4`) summed
  2,308.9 s, 36% of 6,454.9 s, with keycloak at 1,239.9 s **[M]**.
- **No performance budget exists.** The gold harness records seconds but
  fails only on precision budgets (`tools/gold_benchmark.py`, ARB-065)
  **[M]**. A twofold regression would pass CI today **[I]**.

### Platform

| Gap | Evidence | Label |
|---|---|---|
| No macOS CI | the `pr-check.yml` matrix is `[ubuntu-latest, windows-latest]` on 3.11, plus ubuntu on 3.10 | [M] |
| Windows runs no analyzers and no self-gate | `if: runner.os == 'Linux'` on the install and self-gate steps | [M] `pr-check.yml` |
| No `tools/install_tools.ps1` | file absent | [M] |
| No OS or Python classifiers in `pyproject.toml` | `grep classifiers` finds none | [M] |
| Path-separator bugs reach code that Linux tests pass | Windows CI caught a backslash in a graph rebuild message (commit `da26819`) | [M] |
| A test passes in CI and fails locally | OmniEngineering `tests/test_gate_hook` (post-commit) fails on WSL and passes in CI | [M] observed tonight; cause not established |
| Python 3.12 and 3.13 untested | matrix covers 3.10 and 3.11 only | [M] |

### Product

| Gap | Evidence | Label |
|---|---|---|
| **The repository is already public, but the Phase 3 pre-publication gate is not recorded as run** | `gh repo view paukennick/arbiter` returns `PUBLIC` (2026-10-20); the plan made publication conditional on a full-history gitleaks scan, a fixture-key review, a `training/` redistribution check and an ARB-005 decision; no CHANGELOG entry records them. **Update 2026-10-21:** the ARB-005 decision (Apache-2.0, stays public) and a full-history gitleaks scan with its hits reviewed are now in `CHANGELOG.md`; the `training/` redistribution check is still not recorded | [M] visibility; [I] that the gate was skipped (absence of a record is not proof) |
| ~~Licence and visibility disagree~~ — resolved 2026-10-21 | was: `LICENSE` an interim notice that "reserves all rights and grants none". The owner chose Apache-2.0 and kept the repository public; `LICENSE`, `NOTICE` and `.ai/checklists/public-release.md` now say so (ARB-005) | [M] |
| ARB-005's last criterion is open | the licence is decided (Apache-2.0); the semgrep LGPL-2.1 redistribution review (L-6) still blocks bundling the analyzers in the air-gapped bundle, and needs a named reviewer | [M] registry |
| Install is from source only | `SETUP.md` documents `pip install -e .` (or `omni arbiter install` from a checkout, URL or pip spec), and the analyzers come from a bash script | [M] SETUP.md; [A] that no package is published to an index |
| Two import-time fragilities | `import arbiter.lang_rules` before `arbiter.probes` raises a circular `ImportError`; `controls._probe_of("arbiter/python.x")` returns `"python"`, not `"lang_python"`, so a language-pack control always reads as not assessed | [M] both reproduced for this report. **Both fixed 2026-10-21:** ARB-078 (`tests/test_import_order.py`) and ARB-062 criterion 5 (`tests/test_controls_lang_packs.py`) **[M]** |
| The controls report cannot express per-language CWE applicability | one automatable value per control and no language dimension (ARB-062 acceptance criteria) | [M] |
| Test discovery in OmniEngineering is easy to fool | Version B's auto-detection ran planted defect fixtures as test suites until `.ai/graph-config.json` excluded them | [M] observed tonight |
| The self-gate needs config to pass on omni's own code | a `shell=True` in omni's test runner is suppressed in `arbiter.yaml` with a by-design reason instead of an inline, rule-specific annotation | [M] `arbiter.yaml:25` |

### Developer experience

| Gap | Evidence | Label |
|---|---|---|
| No "why did this fire" or "why didn't it" | ARB-067 is proposed, not built | [M] registry |
| MCP can scan, gate, queue and draft reviews, but cannot propose a fix or verify one | four tools in `docs/mcp.md`; ARB-066 proposed | [M] |
| Feedback verdicts do not move rule confidence | ARB-067 acceptance criterion 2, not built | [M] |
| Agent numbers can come from the wrong install | a precision run imported the shared copy instead of the worktree (ARB-072 description) | [M] |

---

## 3. Improvements ranked by value ÷ effort

Value and effort are **[A]** judgements, anchored to the measured figures
cited in each row. "New" IDs are proposals and are not yet in the registry.

| # | Improvement | Value | Effort | Why | Requirement |
|---|---|---|---|---|---|
| 1 | Compile each pack query once per process (cache keyed by grammar and query text) | High | S | Keycloak takes 34 min and every Ruby repo runs ≤ 0.53 KLOC/s. Removing the N × R compiles should speed those packs up; how much is unknown until a profile is taken **[I]** | new ARB-076 |
| 2 | Fix the three P1 precision rules | High | S–M | Removes up to 4,750 findings, 38% of all gold findings, which are about 98–100% FP **[I]** | ARB-065 follow-up; new ARB-077 "gold budgets green" |
| 3 | Fix the `lang_rules` import cycle; map `_probe_of` to `lang_<pack>` | Med | S | The import cycle crashes any tool that imports packs first. The `_probe_of` bug makes every language control read "not assessed" | ARB-062 (criterion 5); new ARB-078 for the cycle |
| 4 | `arbiter preflight` (import path, HEAD, tool and grammar versions, packs compile) | High | S | Stops confident wrong numbers. It has already happened once | ARB-072 |
| 5 | Ranked CWE gaps 1–3: JWT `decode`, literal signing keys, CSRF disabled | High | S each | Exact call patterns, near-zero noise, typical AI output (`docs/cwe-top25.md`) | ARB-062 |
| 6 | OWASP Java syntactic fixes: aliased `exec`, `ProcessBuilder` with a list, `scoped_type_identifier`, sinks nested in `try` | High | S–M | Moves 0/126, 0/218 and 28/272 with rule edits alone **[I]** | ARB-062; new ARB-079 "OWASP in-scope recall target" |
| 7 | An occurrence ordinal in the fingerprint for same-evidence findings | Med | M | Six defects must count as six. This changes fingerprints, so it needs a baseline migration step | new ARB-080 |
| 8 | Per-language CWE applicability in the controls report | Med | M | Makes "not assessed" visible where it is claimed | ARB-062 |
| 9 | `arbiter why` / `why-not` | High | M | Makes rule tuning and user trust cheaper. Prerequisite for 12 | ARB-067 |
| 10 | macOS CI, `install_tools.ps1`, analyzers and self-gate on all three OSes | Med | M | Windows has already caught a bug Linux missed (`da26819`) | ARB-063 |
| 11 | Same-function constant propagation and taint in the pack engine | High | L | The only route below FPR 1.0 on OWASP Python code and command injection | new ARB-081 |
| 12 | MCP fix + verify loop | High | M–L | Differentiates the product for AI agents; needs 9 and fix pairs | ARB-066 → ARB-068 |
| 13 | Decide the public status and licence; run the pre-publication checklist retroactively | High (risk) | S to run, owner decision | Done 2026-10-21 for the licence (Apache-2.0, stays public) and the full-history gitleaks scan (`CHANGELOG.md`); the `training/` redistribution check is not recorded | ARB-005, ARB-075 |
| 14 | Packaged install: a wheel and a pinned-analyzer bootstrap per OS | Med | M | Removes the "clone and bash" barrier | ARB-063; new ARB-082 |
| 15 | Cross-file analysis (routes, auth middleware) | High | L+ | Unlocks 862, 863 and 306, but is a different engine. Prefer an adapter (semgrep Pro / CodeQL) first; their licence terms are **[A]** unchecked | new, defer |

---

## 4. Testing options

### What already exists

| Asset | What it does | Label |
|---|---|---|
| `tools/mutate_tests.py` | Mutates Arbiter's **own source** and checks that the named regression test fails. Runs in `pr-check` | [M] |
| `tools/integrity.py` | Breaks claim invariants and checks that they are caught. Runs in `pr-check` | [M] |
| `tools/inject.py` | Plants defects into real corpus files and runs controls. Nightly via `train_cycle.sh` | [M] |
| `tests/test_metamorphic.py` (7 tests) | Same meaning, different spelling. Secrets and encodings only | [M] |
| `tests/test_properties.py` (13 tests, hypothesis) | HCL and snippet parsers only, not tree-sitter packs | [M] |
| `tools/disagree.py` | Finds disagreement between tools. Run on IaC corpora (terragoat, kubernetes-goat) | [M] `train_cycle.sh` |
| `tools/fixpairs.py` → `training/fix-pairs.json` | 163 pairs, 148 of them `supply.unpinned-npm-dep`, 0 confirmed. Generated 2026-10-20, before the language packs | [M] |
| SARIF | `test_sarif_is_wellformed`. Upload to code scanning runs with `continue-on-error: true` | [M] |
| MCP | `tests/test_mcp_api.py` (15 tests). Schemas are plain data in `mcp.TOOLS` | [M] |

### Evaluation

Cost and catch-value are **[A]** unless a source is cited.

| Option | What it would catch | Cost | Trade-off | Verdict |
|---|---|---|---|---|
| **Mutation / metamorphic variants of planted defects** (alias the receiver, fully qualify the type, wrap in `try`, rename, split across statements, change quote style) | The brittleness OWASP Java exposed: three categories at or near 0 **[M]** | S–M: an operator set per language over `fixtures/planted/*/bad`; reuse `planted_score` | Only explores rewrites someone thought of; each operator must keep the meaning | **Do first.** Cheapest direct attack on the measured recall failure |
| **Performance benchmarks with budgets** | Regressions like the per-file recompile | S: `seconds` already in the gold report; add `tools/gold/perf-budgets.json` and a KLOC/s floor per pack on a 3-repo subset | CI runners are noisy, so budgets need headroom (about 2×) and a fixed subset | **Do first**, together with improvement 1 |
| **Regression snapshots on pinned repos** | Silent rule drift: a precision fix that also kills true positives | S: store `{rule: count}` and fingerprint sets for 3–5 small gold repos; diff in CI | Snapshots get re-blessed by reflex; require a reason per changed rule | **Do first.** Uses existing pins |
| **Human-labelled precision samples with inter-rater agreement** | Turns the triage's "≈98%" into a figure with an interval; checks whether the labels are reliable | M, ongoing: 50 findings per noisy rule, two raters, Cohen's κ; queue via `arbiter review` | Costs people's time, so a small sample per rule is enough; a low κ means the rule definition is vague | Second wave; needed for ARB-067 "precision published" |
| **Real AI-assistant output corpus** (several models, typical tasks, labelled) | The product thesis: what AI-written code actually gets wrong | M–L: about 50 task prompts × 5 languages × 3 models; labelling is the cost; pin model versions | Outputs drift with model releases; labelling needs expertise; model terms of use need a check **[A]** | Second wave. **Highest strategic value**; start small |
| **LLM-generated adversarial variants of planted defects** | Realistic rewrites that hand-written operators miss | S–M per batch | The generator also changes meaning; every variant needs an oracle (does the good twin still differ?) or human review | After mutation operators, as their extension |
| **Differential testing vs semgrep / CodeQL** | Where Arbiter is silent and others fire, and the reverse; feeds `disagree.py` | M: extend `disagree.py` from IaC to the language corpora; CodeQL needs builds for Java and Go | Disagreement is not truth: it ranks what to label; check CodeQL licence terms for commercial use **[A]** | Second wave; cheap with semgrep, which is already pinned |
| **Fix-pair mining as ground truth** | Real fixes the rule saw before and after | S to rerun on the gold set; M to confirm | Today's 163 pairs are 91% one supply rule and 0 are confirmed **[M]**; gold repos rarely ship the defects the packs target **[I]** | Rerun now on the language corpora; it feeds ARB-066/068 verify rates |
| **Property-based / fuzz testing of parsers and queries** | Crashes, hangs and "silent empty" on odd input (encodings, huge files, deep nesting) | S: extend hypothesis to `parse_file` / `run_query` with "never raises, terminates within the time budget" | Finds robustness bugs, not detection bugs | Second wave, cheap |
| **Cross-OS matrix tests** | Path separators, encodings, line endings (`da26819`) | M (ARB-063); macOS minutes cost more on private repos, free on public ones **[A]** | Slower PR feedback; keep the slow tier on Linux | Do with milestone 2 |
| **End-to-end OmniGraph traceability** | A broken link from finding to rule, CWE, requirement or commit | S: a scripted planted app → `make scan` → `omni graph why` with assertions (the plan's verification steps) | Spans two repos; pin both | Second wave |
| **MCP contract tests** | Schema drift that breaks agents | S: snapshot `mcp.TOOLS` and the response shapes; add when ARB-066 lands | Snapshots must be versioned, not re-blessed | With ARB-066 |
| **SARIF consumer tests** | GitHub silently rejecting or mis-rendering uploads | S: validate against the SARIF 2.1.0 schema; drop `continue-on-error`, or assert the upload's processing status | The GitHub API check needs a token in CI | Cheap; do in milestone 1 |
| **Dogfooding on Arbiter and on OmniEngineering history** | Noise on code the team knows; regressions across real commits | S: `fixpairs.py` over both histories, and a self-gate on the omni repo | Small corpus; the team's own idioms can hide bias | Continuous; already partly done (self-gate) |

**Also worth adding:**

- **Grammar-version canary.** tree-sitter APIs moved between 0.21 and 0.26
  (`ast.py`) **[M]**. Pin the grammars and test packs against the next
  release.
- **"Silent empty" tests.** Assert that every pack rule fires at least once
  on its corpus, so a query that compiles but never matches is caught
  **[I]**.
- **Test-discovery hygiene in OmniEngineering.** Exclude
  `fixtures/planted/**` by default and test that it stays excluded (issue
  6).

### Order

1. Mutation variants of planted defects.
2. Performance budgets.
3. Regression snapshots.

All three build on assets that exist and target measured failures. Then
human-labelled samples and the first real AI-output corpus, because they are
the only checks whose ground truth Arbiter did not author.

---

## 5. Proposed sequence

### Milestone 1: trustworthy numbers

Fast, honest and quiet on good code.

- Improvements 1–4 and 13.
- Perf budgets, regression snapshots and SARIF schema validation.

Exit criteria, all measurable:

- [ ] Gold benchmark: the three P1 rules are within budget; total
  over-budget rules ≤ 8 (from 16). *2026-10-22, round 2: 6 over, 3 of them
  accepted breaches **[M]**; `authored.undeclared-import` (0.0005),
  `assurance.test-asserts-nothing` (0.0122) and `python.sql-injection`
  (0.0282) are within budget (CHANGELOG, round 2 precision).* *2026-10-22: 9 over at `1047341`, 3 of them
  accepted breaches recorded in `tools/gold/budgets.json` (java and typescript
  test-without-assertion, permanently-skipped-test), so 6 fail the run **[M]**.
  The criterion counts all 9, so it stays open.* *2026-10-21, all 24 repositories: 7
  over at `bd6bf16`, 10 at `7dd990c` **[M]**. The rise is ARB-080 counting
  each same-evidence site (7,457 to 8,162 findings; the only scan change
  between the two) **[I]**: it adds `authored.security-check-disabled`
  (145 to 328), `assurance.permanently-skipped-test` (157 to 311) and
  `typescript.test-without-assertion` (18 to 22). P1 status:
  `typescript.test-without-assertion` 0.034 and `secrets.assigned-credential`
  0.035 (budget 0.03) are just over, `authored.undeclared-import` 0.039.
  `java.test-without-assertion` is now low confidence and still over (276,
  0.092; 0.046 confidence-weighted, which is reported and not budgeted).
  Later the same day, at `1047341`: 9 over **[M]**, still above 8.
  `authored.security-check-disabled` is back within budget (86, 0.014) and
  `assurance.permanently-skipped-test` is just over (192, 0.0315), both
  with per-site counting kept; `java.test-without-assertion` 199 (0.066),
  `typescript.test-without-assertion` 22 (0.034). The remaining findings of
  those three were triaged (all 22 TypeScript read; Java and skipped tests
  by category with samples) and are real by each rule's letter **[I]**
  (see CHANGELOG, round 1 follow-ups).*
- [ ] The full gold run takes ≤ 50% of 6,454.9 s on the same machine, and
  java-keycloak ≤ 10 min. *2026-10-21: the first half is met (2,308.9 s
  summed, 36%, at `7dd990c`, `--jobs 4`; the jobs setting of the 6,454.9 s run is not recorded) **[M]**, the second is not (keycloak 1,239.9 s,
  20.7 min; the remaining time is in `authored` and `doc_drift`) **[M]**.
  At `1047341`: 2,229.7 s summed, keycloak 1,196.3 s (19.9 min) **[M]**.*
- [x] A test proves each pack query is compiled once per process.
  *`tests/test_query_cache.py` (ARB-076); it fails with the cache disabled
  **[M]**.*
- [ ] `python -c "import arbiter.lang_rules"` succeeds in CI, and
  `_probe_of("arbiter/python.x") == "lang_python"`. *Both hold locally:
  `tests/test_import_order.py` (ARB-078) and
  `tests/test_controls_lang_packs.py` (ARB-062) pass in the fast tier
  **[M]**; not yet seen in CI, because the branch has not been pushed.*
- [ ] `arbiter preflight` output is embedded in gold and planted reports.
- [ ] Perf budgets and snapshots fail CI on a seeded regression (proven by
  a mutation in `mutate_tests.py`).
- [x] The public/licence decision is recorded (ARB-005/075), and a
  full-history gitleaks scan result is in the CHANGELOG. (2026-10-21:
  Apache-2.0; gitleaks findings all synthetic test values.)

### Milestone 2: close what single-file analysis can reach

- Improvements 5–8 and 10.
- Mutation variants in CI.
- Fix pairs rerun on the language corpora.

Exit criteria:

- [ ] Ranked CWE gaps 1–6 are native; the `gap: CWE-N` count falls from 32
  to ≤ 18 with 0 on good twins.
- [ ] OWASP Java in-scope recall ≥ 0.30 with FPR ≤ 0.10, native-only (from
  0.045 / 0.043). The target is **[A]**.
- [ ] The mutation-variant suite: ≥ 90% of variants caught per language.
- [ ] `pr-check` is green on ubuntu, windows and macos, 3.10 and 3.13, with
  analyzers and the self-gate on all three (ARB-063).
- [ ] Six identical `catch {}` in one file report six findings, and the
  baseline migration is tested.

### Milestone 3: the agent loop, measured on AI-written code

- Improvements 9, 11 (same-function data flow) and 12.
- Human-labelled precision with κ.
- AI-output corpus v1.

Exit criteria:

- [ ] `why` and `why-not` work for every native pack rule (ARB-067).
- [ ] An MCP fix+verify tool exists. Applying each planted good twin's
  change clears its finding for 100% of planted defects (ARB-066).
- [ ] Per-rule precision with 95% intervals is published in `docs/` for
  every rule over 0.03/KLOC, from ≥ 2 raters with κ ≥ 0.6 **[A]**.
- [ ] AI-output corpus v1: ≥ 250 labelled generated files across 5
  languages and ≥ 2 models, with recall and precision reported per rule.
- [ ] OWASP Python in-scope FPR on code and command injection ≤ 0.5 (from
  1.0).

---

## Appendix: tonight's issues, mapped

| # | Issue | Where it lands |
|---|---|---|
| 1 | Same-evidence findings in one file collapse | §2 Detection; improvement 7 (new ARB-080) |
| 2 | `run_query` recompiles per file (yaegi about 4 min, keycloak 34 min) | §2 Performance; improvement 1 (new ARB-076) |
| 3 | `arbiter.lang_rules` import cycle | §2 Product; improvement 3 (new ARB-078) |
| 4 | Controls cannot express per-language CWE; `_probe_of` returns `python` | improvements 3 and 8 (ARB-062) |
| 5 | `shell=True` in omni's test runner suppressed in config | §2 Product. Prefer an inline, rule-named annotation with a reason at the call site, so the exception travels with the code **[A]** |
| 6 | Planted fixtures auto-detected as test suites | §4 "Also worth adding" |
| 7 | `tests/test_gate_hook` fails on WSL, passes in CI | §2 Platform. Needs a reproduce → isolate pass; the cause is unknown |
| 8 | Thin clean-code corpora (2 TS repos at first) | §2 Precision; now 4–5 per language |
| 9 | Windows caught a path-separator bug Linux missed | §2 Platform; ARB-063 |
