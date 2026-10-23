# Changelog

All notable changes to Arbiter are recorded here. Entries are grouped by date
under `[Unreleased]` (there are no release tags yet) and reference the
`REQ-###` they serve. Rationale belongs in `.ai/project-context.md`.

## [Unreleased]
### 2026-10-23 (round 4)
- ARB-065 | `supply.unpinned-action` reported the blank line above a step: `_ACTION_USES` used `^\s*` under `re.M`, which matched from the blank line and swallowed its newline. It now uses `[ \t]*`, so the finding is on the `uses:` line. All 3 `v3` FP labels were this (py-celery, ruby-rack, java-netty). Test: `tests/test_probes_quality.py`. The planted fixtures have no workflows, so there is no planted twin.
- ARB-065 | `ruby.sql-injection`: `lang_rules._rb_splices_safe`, a Ruby counterpart of the Python splice check, skips a call whose interpolated string argument holds only safe interpolations: the rule's existing exclusion (`safe_splice_text`), a literal or a conditional of literals (`#{x ? 'A' : 'B'}`, `#{' IF EXISTS' if f}`), a `quote_*`/`sanitize_sql*`/`escape_*` call (`safe_splice_calls`), or a local assigned only such values or a PRAGMA setting read back with `query_value`/`select_value("PRAGMA name")` (`safe_splice_values`). Values read back from table rows stay reported (second-order injection). It runs after the taint pass so the sink pass cannot re-report the call. The taint sanitizer list now takes any `quote*`. Gold: 20 -> 12 findings, all 8 removed were labelled fp. The AI corpus loses 1 labelled-fp finding (`notes-api` app.rb:252); its snapshot is updated. The planted bad twins are all still caught. Tests: `tests/test_lang_ruby.py`, `tests/test_lang_python.py` (the `safe_splice_*` key validation).
- ARB-067 | `label_models.py fact-check`: `suppression_fact` checks `assurance.blanket-suppression`'s claim, whether the directive on the line names a rule. It uses its own per-tool patterns (noqa, type/pyright ignore, nosec, pylint, rubocop, ts-ignore/expect-error, eslint-disable, nolint, NOSONAR, `@SuppressWarnings`), not Arbiter's. The labellers are never shown it. It agrees with 150/150 model labels.
- ARB-067 | Relabel (`v3`, `--per-rule`, seed 20261026, fresh gold run): `supply.unpinned-action` 200 sampled of 273 and `assurance.blanket-suppression` all 150. That was 116 new items for $3.85, every one `tp`. The SHA-pin fact check agrees on 200/200. The 8 pre-fix `unpinned-action` labels whose marked line moved, and the 9 `ruby.sql-injection` labels of findings no longer reported, moved to `tools/gold/labels/history/`. Ledger: **`supply.unpinned-action` 196/0, lower 0.981, newly admitted**; `blanket-suppression` 150/0, lower 0.975 (was 0.970); `ruby.sql-injection` 0/10. 3 rules admitted, none declared verified.
### 2026-10-23 (relabel at v3, calibration round 4)
- ARB-067 | `context.build` shows up to 2 same-file callers (25 lines each) of the enclosing function when one of its parameters feeds the flagged statement through the taint slice (`context.parameter_names`, `tainted_parameters`, `callers`; `Context.callers`). This was calibration round 3's recommendation: all 8 of its unsure items were Juliet sinks whose argument came from an unseen caller. The labeller prompt is now `v3` and the verifier `verify-v3`, so cached verdicts refresh. Tests: `tests/test_context.py`.
- ARB-067 | Calibration rounds 4 and 5 on `v3` (`calibration-4.jsonl`, 200 items, seed 20261025; `calibration-5.jsonl`, 294 items, seed 20261027; each excludes every earlier round). Round 4 alone: `tp` calls 154/154, Wilson lower 0.976. **`v3` pooled: 382/382, lower 0.99004**, the first single-version labeller bound at 0.99. `fp` calls were 112/112, with 0 unsure and κ 0.994. Shown for context: `v2` (round 3) 0.987, `v2`+`v3` 0.994, rounds 1-5 0.984. Results: `tools/gold/results/2026-10-23-calibration-4.md`.
- ARB-067 | Relabel with `v3` at this commit: the gold sample (601 findings drawn from a fresh `gold_benchmark.py --raw` run by the new `label_models.py --per-rule` allocation), the development AI corpus (115) and the CVE suspects (5); $25.62, κ 0.95. The `v1` files moved to `tools/gold/labels/history/`. `precision_ledger.py build` drops model labels from a superseded prompt version (`--model-prompt any` keeps all), and treats a change of labellers as a re-label. `assurance.blanket-suppression` is newly admitted (125/125, lower 0.970). `ruby.sql-injection` is 0/19 on Rails. `supply.unpinned-action`'s 3 FPs are the rule reporting a blank line. Results: `tools/gold/results/2026-10-23-relabel.md`.
- ARB-067 | FAIL-056: a labeller's reason could quote a credential-shaped string from the corpus (two `postgres://user:pass@` URLs reached a label file, which failed `omni gate`). `label_models.parse_answer` now passes reasons through `mask_reason` (`_mask_secrets` plus inline DB-URL credentials). The reasons in the existing label files, history included, were re-masked; no label changed. The AWS example key in `2026-10-23-calibration.md` is elided for the same check.
- ARB-065 | Ledger rebuilt from the `v3` labels: 120 rules, 2 admitted, none declared verified. `tools/precision_guard.py check` passes again; the 6 rules it failed on planted-count jumps are relabelled. Total labelling spend for the day's work: $44.97.
### 2026-10-23 (labeller context, calibration round 3)
- ARB-067 | Shared excerpt builder `src/arbiter/context.py`, used by the model labellers (`gt_common.excerpt`) and the verification pass (`verify.excerpt_for`). It shows the enclosing function with its decorators and annotations, the class header, imports, same-file route registration, same-file callees on the taint path (a backward textual slice from the flagged statement; the sink's receiver is not followed) and up to 3 definitions (30 lines each) from other files of the repository for calls on that path. Those calls are resolved by receiver type or import, never by name alone. It is bounded to 220 lines, masked with `_mask_secrets`, and deterministic. For ground truth, the other files are blinded too. `verify.PROMPT_VERSION` is `verify-v2`, so cached verdicts refresh. The labeller prompt is `v2`. Tests: `tests/test_context.py`.
- ARB-067 | Calibration round 3, `tools/gold/labels/calibration-3.jsonl`: 500 new blind ground-truth items (OWASP Java/Python, Juliet Java; rounds 1-2 excluded; seed 20261024), $18.34 over 1,005 CLI calls. `tp` calls were 286/286 right (Wilson lower **0.987**) and `fp` calls 206/206, with 8 unsure (all Juliet) and κ 0.98. Pooled over rounds 1-3: 681/687 (lower 0.981). The injection family does not yet reach a 0.99 lower bound. About 80 more clean `tp` calls on `v2` would get there. Round 1-2's 6 false `tp` items, re-run with the new excerpt ($0.24): 5 are now correct `fp` calls (route decorator, `SeparateClassRequest.getTheValue` definition). The sixth is the Juliet `_81` case, now filtered out. Results: `tools/gold/results/2026-10-23-calibration-3.md`.
- ARB-067 | `label_models._cross_file` now also matches Juliet's `_NN_bad`, `_NN_good*` and `_NN_base` files, so the `_81` family stays out of calibration.
- ARB-060, ARB-061 | Decision on the resilience claim wording: the labeller claim for the timeout family no longer says a call "can block indefinitely". It now says "no explicit timeout or deadline set by this code, so how long it waits ... is left to the library's default (which may be long or unlimited)". The old wording was false for Ruby Net::HTTP's 60 s defaults, and the round 2 labellers rejected planted true positives because of it. Rule descriptions checked: `typescript.fetch-no-timeout` no longer says fetch waits "forever" (Node's undici fetch gives up after 300 s). Ruby's `http-no-timeout` already states the 60 s default. The Java, Go, Python `requests`, axios and Node `http.request` texts are accurate.
### 2026-10-23 (labeller calibration, round 2)
- ARB-067 | Labeller calibration round 2, `tools/gold/labels/calibration-2.jsonl`, $21.27 over 1,354 CLI calls. 450 more blind ground-truth items (OWASP Java/Python, Juliet Java; 260 truth-TP / 190 truth-FP; round 1's items excluded): `tp` calls 252/257 right (0.981, Wilson lower 0.955), `fp` calls 176/176, unsure 3.8%, κ prosecutor/defender 0.948. Pooled with round 1: 395/401 (0.985, lower **0.968**), still below 0.99. All 6 false TPs have the deciding fact outside the excerpt: OWASP Java's `scr.getTheValue` "safe source" in another file (3), OWASP Python's fixed-route `request.path.split("/")[1]` (2, including BenchmarkTest01096), and one cross-file Juliet `_81` case. Without them the result is 389/390 (0.986). BenchmarkTest01096: round 1's note was wrong and the benchmark's safe label is right (the route fixes `parts[1]` to `"benchmark"`; 94 of the 112 such cases are marked safe). The result counts against the labellers. Results and open questions: `tools/gold/results/2026-10-23-calibration.md`.
- ARB-067 | Other categories, model-free truth: planted bad/good twins (`label_models.py label --planted CATEGORY... --planted-cap N`; bad-file finding = tp, matching good-twin line = fp; blinded with `gt_common.blind(..., "planted")`, which removes comments, Python docstrings and `cwe`/`planted` names). 211 items, `tp` calls 99/103: tests 24/24 (lower 0.862), secrets 17/17 (0.816), stubs 17/17 (0.816), resilience 25/27 (0.766), concurrency 16/18 (0.672). Several disagreements are fixture or claim faults: Ruby Net::HTTP has 60 s default timeouts, some racy increments are unreachable, and RSpec `pending` runs its body. `label_models.py fact-check` compares model labels with mechanical facts. Secrets under test/example paths: 90/90 called fp. `unpinned-action` vs SHA pin: 18/18. npm ranges with a lockfile: 17/17 decided fp. Docs drift has no decisive fact, so "path exists nowhere" is not used. `tools/mutate_tests.py` only re-applies this repo's own shipped defects, so it gives no truth for test findings elsewhere. No category's model labels reach a 0.99 lower bound; `docs/precision.md` `## Current numbers` has the table.
- ARB-067 | `label_models.py`: `--exclude-labels` for disjoint calibration rounds. A server throttle ("not your usage limit") is retried with growing delays instead of being taken for a usage limit, which had stopped the first run after 196 of 450 items. `tools/precision_ledger.py build` drops `calibration:` records, so `labels/*.jsonl` never double-counts ground truth. Tests in `tests/test_label_models.py` and `tests/test_precision_ledger.py`.
### 2026-10-23 (precision ledger filled)
- ARB-065 | `tools/gold/precision.json` (and its packaged copy) built from the p1b labels: OWASP Java/Python and Juliet Java ground truth, CVE fix pairs, and the model labels on the gold and AI-corpus samples (`calibration.jsonl` left out, because it relabels ground-truth items blind and would count them twice). 98 rules have entries; 1 is admitted (`java.weak-random-for-secret`, 218 TP / 0 FP, lower bound 0.983); none is declared verified, so the tiered gate would block nothing yet and the verified-tier claim is not made. Java injection rules sit far below the bar on Juliet and OWASP (`java.sql-string-built` 0.418, `java.command-injection` 0.406): their false positives are the constant-propagation cases the one-hop data-flow work targets. `tools/precision_ledger.py` reads `.jsonl.gz` label files. Claim scopes in `tools/label_models.py` `CLAIMS` are kept as written (lockfile-backed npm ranges FP; embedded-database calls without a deadline and first-party actions pinned by tag TP).
### 2026-10-23 (rules for AI-written-code defects)
- ARB-083 | New advisory rules for the defect classes a reviewer labelled in the 25-app AI-written development corpus and no rule covered. Unbounded memory: `go`/`java.image-decode-unbounded`, `python.image-bomb-check-disabled`, `typescript.image-pixel-limit-disabled`, `go`/`java.decompress-read-unbounded`, `python`/`ruby.decompress-unbounded`, `typescript.decompress-sync-unbounded`, `go`/`ruby.request-body-read-unbounded`, `python.request-body-unbounded`, `typescript.request-body-accumulated-unbounded`, `typescript.upload-memory-storage-unbounded`. Mail TLS: `python.mail-tls-unverified`, `java.mail-starttls-not-required`, `java.mail-ssl-trust-all`, `typescript.mail-starttls-not-required`, `ruby.mail-tls-verify-none`. Go ignored errors: `go.ignored-encode-error`, `go.ignored-exec-error`. Retries: `<lang>.retry-constant-delay` (all five), `ruby.requeue-without-delay`. SSRF: `<lang>.ssrf-stored-callback` (all five), a server-side POST in a file that stores a callback URL and checks no address. Python's CWE-918 Top 25 cell is now `N+S`.
- ARB-083 | New repository-scoped probe `aiapp` (`src/arbiter/aiapp.py`): `aiapp.unauthenticated-destructive-route` (a DELETE or non-GET admin/purge route with no inline guard, in a repository that authenticates nobody, or in a Go/TS/Python file whose other routes are guarded inline), `aiapp.open-mail-relay` (a write route in a mail-sending repository that authenticates nobody), `aiapp.store-never-evicted` (a map field filled under new items' ids that nothing outside the inserting function removes from, caps or expires). Authentication is recognised by name; auth outside the repository is invisible, so all three are medium confidence. `docs/probes.md` (## AI-written services), `tests/test_aiapp.py`.
- ARB-083 | Dropped after triage: `java.request-body-read-unbounded` (7 of 7 real-code hits were process or classpath streams, test-framework callbacks or Tomcat's admin-only manager servlets, with no Java corpus label to catch), a Go literal-duration variant of `retry-constant-delay` (11 gold hits, all fixed short waits on local resources), and a Pillow `Image.open` rule (Pillow already refuses bombs at twice its default limit).
- ARB-083 | AI corpus (development; held-out not read), code defects: recall 2/117 (2%) -> 33/117 (28%), high+critical 0/11 -> 4/11; precision 2/22 (9%) -> 33/64 (52%), 11 unreviewed. Per language, recall go 0% -> 58%, java 0% -> 16%, python 0% -> 21%, ruby 6% -> 28%, typescript 4% -> 18%. Per category: tls-disabled 3/3, retry-no-backoff 4/5, ignored-error 7/9, missing-authn 7/10, unbounded-memory 5/12, unbounded-input 3/8, ssrf 2/8. `fixtures/ai-corpus/corpus.json` maps `retry-constant-delay` and `requeue-without-delay` to the `retry-no-backoff` category, whose labels carry no CWE. Gold (24 repositories): 10 findings from the new rules, all triaged and within budget (5 `typescript.request-body-accumulated-unbounded` in axios/otel-js example servers, 2 `go.request-body-read-unbounded` in etcd, 1 each `go.retry-constant-delay` containerd devmapper, `java.decompress-read-unbounded` Keycloak persisted config, `ruby.request-body-read-unbounded` ActionMailbox relay ingress); no new breach. Real corpora (corpus-go/java/ruby/typescript/django/clean): 4 findings (nsq `NewReqParams` body read, Zulip `IMAP4_SSL` with no context, healthchecks' fixed 1 s SMTP retry, nodejs-goof's unauthenticated `DELETE /chat`; all TP). Planted 100% with 0 on good twins and 0 unmarked; regression snapshots updated (new-rule findings plus five findings moved down one line in fixtures given a body limit or TLS context).
### 2026-10-23 (judgement provider on 5.x models)
- ARB-065 | The judgement probe's `AnthropicProvider` no longer sends `temperature` to the 5.x models (`claude-{opus,sonnet,haiku,fable}-5*`), which reject it with HTTP 400; older models keep `temperature: 0`. Found by the model verification pass (2026-10-22). `tests/test_judgement_provider.py`.
### 2026-10-22 (precision: tiers and ledger)
- ARB-065 | Precision tiers (`src/arbiter/precision.py`): every finding carries `tier` (`verified` gate-eligible, `advisory` shown and never blocking a tiered gate, `inferred` model-found) and a one-line `tier_reason`. `verified` needs the rule declared verified (rule metadata `tier: verified` or `precision.verified_rules`), admitted by the ledger, and either a model `confirmed` verdict (read generically from `finding.verification`) or, on a profile that cannot call a model (`offline`, `ci`), a rule lower bound of at least 0.99; `rejected`/`unsure` demote to advisory with the model's reason. `gate.tier_mode: legacy` (default, the severity gate unchanged) or `tiered` (only `gate.tiers`, default `[verified]`, can fail); a misspelt mode is an error. Console and markdown print a `tiers: N verified · M advisory · K inferred` line and each finding's tier; SARIF carries tier, reason and requirements, and under a tiered gate an advisory or inferred result is level `note`. Reports from before tiers load as advisory (or inferred), never verified.
- ARB-065 | Per-rule precision ledger `tools/gold/precision.json`, shipped as `src/arbiter/packs/precision.json` (package data; a test holds them identical), rebuilt by `tools/precision_ledger.py build` from JSON-lines labels or `label_sample.py` sheets: TP/FP/unsure, held-out counts, Wilson 95% bounds, Cohen's kappa, labellers, sources, dates, population, `fp_notes` (kept across rebuilds; the verify pass reads them), and `admitted` (lower bound >= 0.97 on >= 30 labels, kappa >= 0.8 where two labellers looked; computed, never trusted from the file). The tier claim is the population-weighted precision of declared-and-admitted rules over held-out labels only, Wilson on Kish's effective n, overall and per category (dimension, each CWE), each claiming 99% only on its own lower bound. With no FP, Wilson needs 125 labels for 0.97 and 381 for 0.99. The ledger ships empty, so every finding is advisory and legacy stays the default. Label format in `docs/precision.md` (`## Label format`) for Phase 1b.
- ARB-065 | CI precision guard `tools/precision_guard.py check` (pr-check): fails when a declared-verified rule has no admitting ledger entry, an entry's `admitted` flag disagrees with its counts, a labelled rule's planted or AI-corpus snapshot count is more than 20% above the count at labelling and the ledger entry is not newer than the snapshot, or the ledger and its packaged copy differ. `regression_guard.py update` now dates snapshots (`recorded`). `tests/test_precision_ledger.py` seeds each failure; `tests/test_precision_tiers.py` covers tier assignment, the legacy and tiered gate (an advisory critical finding passes a tiered gate, a verified one fails it) and the renderers.
- ARB-064 | Rule metadata: every pack rule may carry `requirements: [ARB-xxx]`, `tier` and `precision_ref` (validated at pack load), and rules coded in Python are listed in `src/arbiter/packs/rules/native.yaml`, held to the rule ids the source can emit. `tools/rule_requirements.py` proposed the mapping from CHANGELOG bullets, pack comments, the registry and each rule's introducing commit; reviewed by hand (overrides recorded in the tool). 157 of 228 rules trace to a requirement: all 144 language-pack rules and 13 native ones; the 71 others predate the registry (2026-09-12/13) and are listed with that reason in `tests/test_rule_requirements.py`. Findings carry `requirements`, and the JSON report gains a top-level `rules: [{id, requirements, tier}]` block for the OmniGraph.
### 2026-10-22 (precision: model verification pass)
- ARB-065 | `src/arbiter/verify.py`: a post-probe pass that asks a model (`claude-opus-5-5`, structured output, no `temperature`, which Opus 5.5 rejects) whether each active, non-inferred finding at or above `verify.min_severity` (medium) is real. Each finding is sent on its own, with its rule's description, remediation and known FP notes (`tools/gold/precision.json` `fp_notes`, optional) and a numbered, secret-masked excerpt of its own file only: the enclosing tree-sitter function or a 120-line window, plus that file's imports. The verdict `{confirmed|rejected|unsure, reason, line}` is recorded as `Finding.verification` (new optional field; older reports still load). A finding is never hidden, deleted or re-graded. Unparseable replies, verdicts outside the enum and refusals become `unsure`; a line outside the excerpt is dropped.
- ARB-065 | No provider, an exhausted per-scan budget (`max_findings` 150, `max_tokens` 400k) or a failing provider gives `not_verified` with the reason. `scan_scope.verification` carries the counts, calls, cache hits and tokens, and the report's "Read first" block states how many findings were not model-verified. Verdicts are cached in `.arbiter/verify-cache.json` keyed by (finding id, excerpt sha256, model, `PROMPT_VERSION`, rule-text hash), so an unchanged tree makes 0 calls.
- ARB-065 | New profile `assisted` (`ci` plus verification). `offline` and `ci` never verify, whatever `verify.enabled` or `--model-verify` say; `connected` and `audit` verify on request. `verify:` config defaults are in `policy.DEFAULTS`; `scan`/`gate` accept `--model-verify`/`--no-model-verify`. Console, Markdown and HTML show the verdict and reason under each finding plus a summary line; SARIF carries `verification` in result properties. The default profile's report is unchanged.
- ARB-065 | `tools/verify_eval.py`: runs the pass over the AI-corpus labels (matched as `ai_corpus_score.py` does) or a `label_sample.py` sheet. It reports confirmed-set precision with Wilson 95% intervals, agreement and Cohen's kappa against the labels, the TP-rejection rate, unsure and not-verified counts, and tokens and cost, per rule and overall. It reuses label_sample's Wilson and kappa. `docs/precision.md` (## Model verification) documents the pass. No live measurement yet: `ANTHROPIC_API_KEY` was not set on the build machine.
### 2026-10-22 (precision: ground truth and model labels)
- ARB-067 | Ground-truth labels judged by no model, in `tools/gold/labels/`: OWASP Benchmark Java 1,288 (836 TP / 452 FP) and Python 250 (220 / 30) from the published `expectedresults` for findings of the case's CWE (`tools/ground_truth/benchmarks_gt.py owasp`); Juliet Java 1.3 (NIST SARD, archive pinned by SHA-256 in `tools/ground_truth/fetch_gt.sh`) 6,510 (2,826 / 3,684; `bad*` method TP, `good*` FP; CWE-78/79/89); CVE fix pairs: 70 GitHub advisories, 14 per language, pinned by fix commit and parent in `tools/ground_truth/cve_pairs.json` (`cve_select.py`), scanned on the changed files at both commits (`cve_pairs.py`): 13 TP where a finding of the advisory's CWE on a changed line disappears at the fix, 5 suspects that persist. Only 3 of 70 pairs drew a finding on a line the fix changed.
- ARB-067 | `tools/label_models.py`: two independent model labellers (prosecutor and defender prompts) and an adjudicator on disagreement, through the logged-in Claude CLI (`claude -p --model opus`, no tools, settings, skills or MCP, replaced system prompt). A labeller sees only the excerpt (enclosing function, the same-file functions it calls, imports; at most 180 lines; secrets masked with `judgement._mask_secrets`), the path and a neutral claim written per rule (`CLAIMS`, one for every pack rule, enforced by a test), plus facts the tool computes itself (link target exists, lockfile present, env var read, masked-secret shape). It never sees Arbiter's wording, severity, confidence or a verifier verdict. Stratified sampling by (rule, language, category) via `label_sample.py`, JSON-lines output, resumable, its own cache keyed by finding id, excerpt hash, prompt version, role and model, a `--budget-usd` cap (default 25) checked before each call, `--max`, and a stop at the first CLI usage-limit error. Ground-truth items are labelled blind (comments blanked, Juliet/OWASP case names and paths replaced) for calibration.
- ARB-067 | First batch, $34.82 over 1,950 calls: labeller calibration on 250 blind ground-truth items: `tp` calls 143/144 right (0.993, Wilson lower 0.962, below 0.99), `fp` calls 94/94, κ prosecutor/defender 0.913. Gold sample 600 of 3,160 findings: population-weighted precision 0.349 (95% CI 0.313-0.384), κ 0.89. AI corpus 81 TP / 6 FP. CVE suspects 4 TP / 1 unsure. Per rule and per category with Wilson bounds: `tools/gold/results/2026-10-22-labels.md`. No rule or category reaches a 0.99 lower bound; nearest are `java.weak-random-for-secret` 218/218 (0.983), `python.insecure-random-for-secret` 99/99 and `python.weak-password-hash` 71/71.
- ARB-067 | Held-out split frozen in `tools/gold/heldout.json` (`tools/ground_truth/heldout_split.py`, seed 20261022, `--check`): 7 of 24 gold repositories (30%, stratified by language) plus every ground-truth source. Rule authors must not open held-out findings; `docs/testing-harness.md` section 5. The AI corpus is development data; a second-generator corpus is future work. `tools/gold_benchmark.py` raw findings now carry the finding `id`.
### 2026-10-22 (precision: held-out AI corpus)
- ARB-065 | Held-out AI-written corpus (`fixtures/ai-corpus-heldout/`): eight tasks not in the development corpus (tenant-notes with roles, idempotent payment webhook, thumbnail service, CSV import job, GitHub OAuth login, admin export/report, WebSocket chat relay, scheduled S3 backup uploader) x five languages = 40 apps, from two generator models alternated over the grid (claude-opus-5-5 and claude-haiku-5-5, 20 each; the development corpus is sonnet). `tools/ai_corpus_generate.py` gains `--set heldout`, `--models` (alternation by `model_for`), `--budget-usd`/`--per-run-usd` (a run starts only if spent plus every in-flight run's cap stays under the total) and `--install` (copies the output in and writes `manifest.json` with model and exact prompt per app). Generation cost $17.62 of a $25 cap, including a rerun of 24 apps cut off by a usage limit and one that wrote no files. 236 defects (1 high, 68 medium, 167 low) labelled by one blind subagent per language and committed (`88bcb10`) before Arbiter ran on the tree; no reconciliation, so unreviewed findings count against precision.
- ARB-065 | `tools/gold/heldout.json` (`ai_corpus_heldout`) seals the tree: rule authors do not inspect its findings or labels; only aggregate scores are reported. `tools/ai_corpus_score.py --aggregate-only` writes counts, recall and precision per language, per category and per finding severity, and names no rule, file, line or finding (a test asserts it). `tools/regression_guard.py` aggregate targets (`"aggregate": true`, target `ai-corpus-heldout`) store a count, a SHA-256 digest of the finding list and the scorer's counts, and fail with before/after numbers only.
- ARB-065 | First and only held-out score (`tools/gold/results/2026-10-22-ai-corpus-heldout.md`): code defects 6/142 detected (4%), code findings 7/40 detecting a label (18%, lower bound), high-severity findings 0/11; all labels 87/236 and 88/121. Development corpus at the same commit: 2/117 and 2/22.
### 2026-10-22 (Milestone 3: one-hop data flow)
- ARB-062 | `src/arbiter/dataflow.py`: an intraprocedural abstract interpreter over tree-sitter for Python, TypeScript/JavaScript, Go, Java and Ruby. Each local is CLEAN (built from literals, with its constant), DERIVED (an unseen call on constants), UNKNOWN or TAINT; assignments, destructuring, `+=`, concatenation, format calls, templates, f-strings, interpolation, heredocs, lists and maps with constant keys (`add`/`remove(0)`/`get(1)`, `put`/`get`), Go out-parameters (`Decode(&req)`) and tuple returns carry values. A condition, ternary or `switch` that folds to a constant runs only its live branch; a value reassigned to a literal is CLEAN. One same-file call is followed (arguments to parameters, returns back, sinks in the helper see the bound values). Only immutable file-level bindings (Java `final`, TS `const`, Go `const`, ALL_CAPS) are constants. A step budget bounds each file; a fault or an exhausted budget changes nothing (`ARBITER_DATAFLOW_STRICT=1` raises; the suite sets it).
- ARB-062 | Pack schema: an optional, validated `taint:` block (`sources`, `inputs`, `sinks`, `sanitizers`, `value`, `kill`, `where`, `types`, `element0`, `unless_element`, `built`, `at`, `hops`, `requires`; docs/probes.md). The rule's own `query` stays as the fallback: the block adds sink hits the flow reaches and drops a query hit whose `value` captures are provably constant (or, with `kill: derived`, derived only from constants in a function that reads request input). When a value crossed a function, `unless_in_scope` is checked over the file; one finding is kept per source per function. Migrated: TypeScript html-from-request, sql-string-built, shell-command-built, shell-spawn-built, ssrf-request-url, argument-injection; Go sql-injection, command-injection, error-text-in-response, path-traversal-request, upload-client-filename, ssrf-request-url, argument-injection; Java sql-string-built, command-injection, xss-unescaped-html, ssrf-request-url, argument-injection, upload-client-filename, exception-message-to-client; Python command-injection, argument-injection, upload-client-filename; Ruby sql-injection, command-injection, path-traversal-request, upload-client-filename, ssrf-request-url, argument-injection.
- ARB-065 | Measured before (`9eedb76`) and after. `tools/variants.py`: 485/596 to 608/648 pairs kept overall (81% to 94%); per migrated rule: typescript.html-from-request 1/8 to 13/13, typescript.sql-string-built 3/8 to 11/11, go.sql-injection 9/14 to 18/18, go.command-injection 3/7 to 10/10, go.error-text-in-response 5/13 to 19/19, ruby.sql-injection 5/11 to 13/13, ruby.command-injection 1/3 to 3/3, python.argument-injection 3/7 to 8/8. OWASP Benchmark Java FPR at unchanged recall: sqli 1.0 to 0.254, cmdi 1.0 to 0.688, xss 0.455 to 0.254 (in-scope FPR 0.537 to 0.235, score 0.432 to 0.734); Python cmdi FPR 1.0 to 0.857. Juliet Java (bad* method = TP, good* = FP): CWE-89 FP 3,000 to 1,020 with TP 2,160 unchanged; CWE-78 FP 612 to 204 with TP 432 unchanged. AI corpus: 2 to 3 of 117 code defects (java/file-upload D3), precision 9% to 13%. Planted 361/361 with new hoist/helper bad/good pairs in every language, 0 on good twins, 0 unmarked; snapshots refreshed (16 planted, 1 AI-corpus finding added).
- ARB-065 | Gold, all 24 repositories, against `9eedb76`: 9 findings gone (5 keycloak SQL through `getTableNameForNativeQuery`, 3 jose/otel build-script shell commands on constants, all FPs) and 7 new, each read: 4 Rails SQL built in a one-hop helper from method arguments (postgresql `explain`, `primary_key_column_and_sequence_sql` twice, the `rails query` console), 2 etcd `http.Error(w, msg, 500)` with `msg := fmt.Sprintf("... %v", err)` (the rule's defect, hoisted), 1 nest e2e SQL template through a helper; each is the shape the rule reports inline. ruby.sql-injection 0.029/KLOC (budget 0.03). Scan time: java-keycloak 101.6 s to 108.0 s, ruby-rails 26.6 s to 33.5 s, go-containerd 42.0 s to 42.9 s.
### 2026-10-22 (round 2: scan speed)
- ARB-076 | Per-repository facts are built once per scan, not once per file. `probe_authored` called `facts.setdefault(key, _ecosystem_facts(...))`, which evaluates its argument every time, so each Go, Java or Ruby file rebuilt its repository's declared and local names -- every manifest and, for Java, every file's `package` line: 8,546 rebuilds on keycloak, 1,808 s of 2,486 s under cProfile. `apply_scope_notes` had the same shape per finding (1,026 extractions, 6.7 s). `probe_doc_drift` built each repository's code blob by repeated concatenation, copying the whole blob per file (458 s). `tests/test_scan_once_per_repo.py` counts the calls during a 12-file Java scan.
- ARB-076 | The assigned-credential pattern is tried only near its keywords: every `_ASSIGNED_SECRET` match starts at most 24 characters before a credential word and the pattern has no anchors or lookbehind, so `_assigned_secret_matches` runs `match` only at those starts and yields exactly what `finditer` did (31.0 s -> 6.8 s of regex on keycloak). `tests/test_secrets_scan_equivalence.py` checks the keyword list and bound against the pattern text and the output against `finditer` on hand-written, random and real inputs; change the pattern and update both.
- ARB-076 | Scan seconds per repository, same machine, `tools/gold_benchmark.py --only java-keycloak,ruby-rails,ts-nest,go-grpc,py-celery --jobs 4 --no-fail --raw`, before (`24b2b54`) -> after: java-keycloak 1,819.4 -> 79.0, ruby-rails 133.3 -> 24.0, go-grpc 24.9 -> 14.9, ts-nest 15.0 -> 7.6, py-celery 8.2 -> 6.0; wall clock 30:19 -> 1:19. The gold report is identical apart from its timestamp, commit and seconds: 2,559 findings, every raw finding equal and in the same order, per-rule counts and breaches equal. Full `Finding.to_dict()` output (ids, lines, titles, descriptions, evidence, scope notes) is identical on all five repositories. Keycloak's largest probes now are `lang_java` (35 s), `secrets` (24 s), `authored` (14 s), `ast_metrics` (10 s).
### 2026-10-22 (round 2 precision: secrets and suppression)
- ARB-065 | `assurance.blanket-suppression`: a blanket directive followed by a written reason (two words of prose after it, separators and a second comment marker skipped) is not reported; TypeScript cannot name an error in `@ts-expect-error`/`@ts-ignore`, so a reason is all its author can give (otel-js `// @ts-expect-error access internal property for testing`, axios `// @ts-expect-error -- the encoding opt-in only accepts booleans`, celery `# type: ignore  # whole point here ...`). A `@ts-*`, `eslint-disable` or `nolint` directive matched later inside a comment, after a quote or backtick, or on a `*` block-comment line is prose about the directive and is no longer counted, in the census either (jose's JSDoc `* // @ts-ignore` example, its tools/declaration-comments.js). A bare directive, or one followed by a lone token (`// @ts-expect-error TS2345`), is still blanket. Generated and vendored paths were already skipped.
- ARB-065 | `secrets.private-key`: a data file (no inventory language: `.pem`, `.key`, `.key.insecure`, `.txt`) under a fixture-data directory (`testdata/`, `test-data/`, `fixtures*/`, `__fixtures__/`, `vectors/`, `test-vectors/`) is not reported (grpc-go testdata/x509, etcd tests/fixtures, cryptography vectors). Every other test-path key stays reported at low and tagged `test-fixture`, as since round 1 (tests/certs, examples/, integration/…/tls, keys written into test code). Test material also covers `testsuite/`, `test-suite(s)/`, `dummyserver/`, a `t/` directory, modules named `fixture(s)*` and `*localhost*.key|pem`: keycloak's testsuite PKI, urllib3's dummyserver/certs, celery's t/unit/security, jose's tap/fixtures.ts and caddy's caddytest/caddy.localhost.key were 19 critical, high-confidence reports and are now low. No private-key finding on gold is high any more.
- ARB-065 | `secrets.assigned-credential`, all post-match filters (the `_ASSIGNED_SECRET` patterns are unchanged): the symbol is read back to its first character before the echo checks, because the 24-character prefix cut `LOGOUT_TRIGGERED_BY_ACTION_TOKEN` short; a quoted value that spells its symbol plus at most one naming word (keycloak Messages.java `EXPIRED_ACTION_TOKEN_NO_SESSION = "expiredActionTokenNoSessionMessage"`), or is a letters-only snake_case identifier sharing a word with it (`FORM_PASSWORD = "j_password"`, `_aj_hash_with_indifferent_access`), is a key name; dotted names of three or more segments may carry a digit run per segment (`org.apache.tomcat.manager2.CsrfFilter.token`, grpc's `type.googleapis.com/...v3.AccessTokenCredentials`, which was high); variable references (`$dir/private/ca.key.pem`, `$${project.version}`), interpolated strings (rubygems' `credential_info`, which was high), unquoted values ending in `;` (axios index.d.cts `token: CancelToken;`), Fetch credential modes (`same-origin`, `include`, `omit`), and symbols naming a password hash (`encodedPassword`, `PWD_MD5`, `*_SHA*`; not `DEFAULT_ENCODED_SECRET_KEY`) are skipped, as are fixture-data files as above (cryptography's RFC 8032 `SECRET =` vectors, coredns testdata/*.private). Weak secrets spelled as words (`"super-secret-key"`), snake_case passphrases sharing no word with their symbol, JWTs and test credentials stay reported.
- ARB-065 | Gold benchmark, all 24 repositories, native checks, `--jobs 6`, against `24b2b54` (6,085.9 KLOC): `assurance.blanket-suppression` 280 to 150 (0.046 to 0.0246), `secrets.private-key` 260 to 114 (0.0427 to 0.0187), `secrets.assigned-credential` 210 to 142 (0.0345 to 0.0233); budget 0.03, all three within it; every removed finding was read. 7,385 findings, 6 over budget (3 accepted). Planted corpus unchanged at 100% with 0 on good twins and 0 unmarked. `tests/test_probes_precision_secrets_suppression.py` (7 tests, each failing before the change).
### 2026-10-22 (round 2: controls)
- ARB-062 | Fixed FAIL-055: `controls._probe_of("house/x")` returned `house`, which is not a probe, so a control backed by a house rule always read not assessed. `house/<id>` now maps to `house_rules_ast` for an `ast_query` rule and `house_rules` otherwise, from the scan configuration; `arbiter controls` on a saved report (no configuration) requires both house probes, and an id the configuration does not define reads not assessed. Every rule-id prefix the scanner can emit is enumerated from the source and asserted to map to a registered probe, and a real scan over every planted tree asserts each finding's recorded probe is the one `_probe_of` names (`tests/test_controls_probe_mapping.py`). No shipped pack maps a house rule, so shipped output does not change.
- ARB-062 | Language-pack rules' `controls:` declarations now assess the control they name: a control gains `declared_by` and a `languages` cell per pack (`satisfied`, `violated`, `not_assessed`, `absent`). A declaration does not override a control the pack marks `not_automatable` (NIST 800-53 IA-2; the reason names the disagreeing rules), and `controls.unresolved_declarations()` lists the ones no pack enumerates (800-53 SC-13, SC-23, SI-10, SI-11; OWASP ASVS). Shipped output changes only for SSDF PW.5 and PW.8 and 800-53 IA-5, SA-11 and SC-8: on spring-petclinic (native only) PW.8 goes from satisfied to violated (3 `java.test-without-assertion` findings); on the other four sample repos nothing changes in a native-only run, where PW.5 stays not assessed because semgrep and bandit did not run. With the analyzers, PW.5 can now read violated by a language rule. IaC rules' declarations are still not wired.
- ARB-062 | New control pack `cwe-top25-2024`: one control per 2024 CWE Top 25 weakness, each with one cell per language (`applies`, `not_applicable` with a reason, `out_of_reach` with a reason) and the docs/cwe-top25.md mark. An applying cell is assessed by the language pack's rules tagged with the CWE, so a new tagged rule assesses it with no pack edit. A sixth control state, `not_applicable` (distinct from `not_automatable`), for a weakness no language of the codebase can have; per-language cell states add `out_of_reach` and `absent`. The scan report's controls summary and `arbiter controls` show per language: assessed, violated, not run, gap, out of reach, n/a. `tests/test_controls_cwe_top25.py` holds every cell to the doc matrix (N cell has a native rule, other applying cells none, the Native column matches) and runs each planted bad tree end to end. Framework packs print no `not_applicable` row unless it is non-zero; `counts` gains a `not_applicable` key (0) for every pack.
### 2026-10-22 (round 2: testing harness)
- ARB-065 | AI-written-code corpus (`fixtures/ai-corpus/`): five interview-style tasks in five languages, 25 apps generated by headless `claude -p --model sonnet` (claude-sonnet-5-5) runs with file tools only and a neutral prompt (`tools/ai_corpus_generate.py`; prompt, model and date per app in `manifest.json`), copied in as data and never built or run. 172 defects were labelled by reading the code before Arbiter ran (commit a02f048), then reconciled: every in-scope native finding is a TP or a listed false alarm with its reason. `tools/ai_corpus_score.py` scores recall and precision per language and category (2-line tolerance, CWE or `category_rules` match, unreviewed findings counted as FP). First result: 2 of 117 code defects detected, 2 of 31 code findings true; dependency pinning 52/55. Two Arbiter defects surfaced: `drift.documented-env-var-absent` does not read `config.ru`, and `assurance.test-asserts-nothing` anchors one line early after a blank line (`^\s*` under `re.M`).
- ARB-065 | `tools/label_sample.py`: a stratified labelling sheet from a gold run (`--raw`), with code context and a blank verdict column, and a scorer for precision per rule with 95% Wilson intervals, a stratum-weighted overall estimate, and Cohen's kappa between two labellers with the disagreements listed.
- ARB-065 | `tools/regression_guard.py`: finding snapshots and scan-time budgets for `fixtures/planted`, `fixtures/ai-corpus` and four small pinned gold repositories (`tools/gold/guards.json`, `tools/gold/snapshots/`). The budget is scaled by a pure-Python calibration workload, because a reference that ran Arbiter slowed down with a seeded `sleep` and hid it. In-tree snapshots run in the fast tier; the pinned repositories are fetched at their pins and checked in the slow-tier job. Tests prove each guard fails on a deleted rule and on a seeded sleep.
- ARB-065 | `tools/variants.py`: semantics-preserving rewrites of the planted bad fixtures (rename, hoist, helper, move, string style) in all five languages, with each marker tracked to its new line, giving robustness per rule. First sweep: 404/486 kept (83%). Hoisting the sink argument into a temporary (57/83) or passing it through a helper (60/83) loses the most; `typescript.html-from-request`, the Ruby and Go command-injection rules, and the TypeScript and Ruby sql rules are the least robust.
- ARB-065 | `arbiter.yaml` suppresses `ruff/*` under `fixtures/**`, as it already did for every other analyzer, so the corpus never reaches the self-gate. `docs/testing-harness.md` documents all four instruments and their numbers.
### 2026-10-22 (CWE Top 25 round 2: Go and Ruby)
- ARB-062 | Native rules for ranked gaps 5-8 and 10 in Go and Ruby, each a
same-function source-to-sink rule: a request value (Go `r.URL.Query().Get`,
`r.FormValue`, `r.PathValue`, `mux.Vars`, `chi.URLParam`, gin/echo/fiber
`c.Query`/`c.Param`; Ruby `params[...]`, `params.require/fetch`,
`request.params`) written in the sink call, or assigned to a local that a later
statement of the same block passes to it (tied with `#eq?` up to six levels
below the assignment's sibling; no schema change). `path-traversal-request`
(CWE-22; `filepath.Join`/`os.Open`/`http.ServeFile`/`c.File`, `File.join`/
`Rails.root.join`/`File.read`/`send_file`/`render file:`; silent with
`filepath.IsLocal`/`Base`/`Rel`, `strings.HasPrefix`, `os.OpenRoot`,
`File.basename`, `start_with?`, `include?`, `=~`), `upload-client-filename`
(CWE-434; `header.Filename`/`part.FileName()` in a multipart handler,
`original_filename` or Rack's `params[:f][:filename]`, in a path that is
written; silent with an extension/MIME allowlist or a basename),
`ssrf-request-url` (CWE-918; `http.Get`, `*Client.Get`, `http.NewRequest*`;
Net::HTTP, Faraday, HTTParty, RestClient, http.rb, `URI.open`, a `conn`/`client`
local; silent with a literal scheme and host or a host check) and
`argument-injection` (CWE-77/88; `exec.Command`/`system`/`Open3`/`IO.popen` with
a literal git/curl/ssh/rsync/tar/... and a request value, no `"--"`), all high
severity, medium confidence; Go also gains `parsed-int-narrowed` (CWE-190/681,
medium: `strconv.Atoi` or a 64-bit `ParseInt`/`ParseUint` narrowed to
`int32`/`int16`/`int8`/`uint*` with no comparison in between). The nine `gap:`
markers became `expect:`; the Go argument-injection pair is now an HTTP handler,
and Go SSRF/path and Ruby SSRF gained a variable-form pair. Planted Go 60/60,
Ruby 64/64. Real code: the gold Go and Ruby repositories (1687 KLOC) give none
of the nine rules a finding; corpus-go, corpus-clean, rack and sinatra none;
railsgoat two, both true. Matrix: Go 22, 434, 77, 918, 190 and Ruby 22, 434,
77, 918 are N; Go native 6 to 11 and Ruby 9 to 13, no gaps left in either.
Decisions recorded 2026-10-22: the variable tie stays within one block (no
cross-branch or closure flow); Ruby's broad `include?`/`=~` guards on
path-traversal stay (precision first); an untrusted function parameter is not an
argument-injection source (it needs interprocedural taint or a name heuristic).
### 2026-10-22 (CWE Top 25 round 2: TypeScript and Python)
- ARB-062 | Native rules for ranked gaps 3, 5, 6 and 8 (`docs/cwe-top25.md`)
in TypeScript and Python, same-function form. TypeScript:
`ssrf-request-url` (CWE-918, high severity, medium confidence: `fetch`,
axios, got, ky, needle, superagent, `http(s).get/request`, undici, called
with `req`/`ctx` `.query`/`.body`/`.params` as the URL or its start, directly
or through one local, `const { url } = req.body` included; a fixed origin
before the value, an allowlist or a `.hostname` check is not reported),
`argument-injection` (CWE-77/88: `execFile`/`spawn`/`execa` running git,
curl, ssh, rsync, tar and similar tools with a request value in the argv
array and no `"--"`; a value bound to its option or a `startsWith("-")`
check is not reported), `upload-client-filename` (CWE-434: multer
`diskStorage` named by `originalname`, formidable `originalFilename` or
`originalname` in a write/rename/copy path, express-fileupload `mv`; silent
with a `fileFilter`, MIME check, sanitiser or basename in scope),
`csrf-unsafe-methods-ignored` (CWE-352, high/high: csurf `ignoreMethods`
listing POST/PUT/PATCH/DELETE) and `session-cookie-cross-site` (CWE-352,
medium: a session-library cookie with `sameSite: "none"` in a file that
mentions no CSRF token). Python: `argument-injection` (subprocess argv
lists and `asyncio.create_subprocess_exec`), `upload-client-filename`
(Flask `save(f.filename)`, `open(..., "wb")` with `.filename`/`.name`,
Django `storage.save(f.name, f)`, only in a function that handles an
upload; silent with `secure_filename`, a uuid name, an allowlist or a MIME
check) and `debug-mode-enabled` (CWE-200/489: literal `debug=True` in
`app.run`, `FastAPI`/`Starlette`, `app.debug`, `app.config["DEBUG"]`). A
local is tied to its sink by a regex backreference over the enclosing
function in `requires_in_scope`; no schema change. Python 918 stays
semgrep-only. The planted good twin `server.ts` `/convert` route, which
passed `req.body.file` to ImageMagick, now rejects a leading `-`. `gap:`
markers TS 77, 434, 918 and Python 77, 434 became `expect:`; new pairs for
the TS CSRF weakenings; the TS missing-middleware CSRF line keeps its
`gap:` marker. Planted: Python 57/57, TypeScript 72/72, 0 on good twins, 0
unmarked. Real code: the SSRF lessons in juice-shop
(`profileImageUrlUpload`) and nodegoat (`research.js`) are reported, both
true positives; nothing else in corpus-typescript, the three Django
applications, corpus-clean or the gold TypeScript and Python repositories
(0 gold findings from the new rules). Matrix: TypeScript 352, 434, 77 and
918 and Python 434 and 77 to N; native cells TypeScript 8 to 12, Python 9
to 11; neither language has a gap cell left. `docs/probes.md` not-assessed
rows updated.
### 2026-10-22 (CWE Top 25 round 2: Java)
- ARB-062 | Java native rules for the gaps left after round 1, each in the
same-method form: a value bound from the request (`@RequestParam`,
`@PathVariable`, `@RequestHeader`, JAX-RS `@QueryParam`/`@PathParam`/`@FormParam`,
`String x = request.getParameter(...)`) reaching the sink in the method that
binds it. CWE-79: `java.xss-unescaped-html` (high/medium; the bound string
beside an HTML literal in a `return`, `body(...)` or `ok(...)`, or a servlet
that reads the request and prints a non-constant into a `text/html` page; a
file that applies an HTML encoder is skipped, since OWASP Benchmark escapes in
a helper). CWE-918: `java.ssrf-request-url` (high/medium; the bound name or
`"http://" +` it as the URL of `new URL`, `URI.create`, `new HttpGet`,
`Jsoup.connect`, RestTemplate, WebClient `.uri` or OkHttp `.url`, in a method
that makes a request; a fixed host, a `getHost()` check or an allowlist is
not reported). CWE-77/88: `java.argument-injection` (high/medium; the bound
name as a `ProcessBuilder`/`exec(String[])`/`command` element for git, curl,
ssh, rsync, tar and other option parsers, with no `"--"` before it).
CWE-190: `java.parsed-number-narrowed` (`(byte)`/`(short)` of
`Integer.parseInt`, `(int)` of `Long.parseLong`; radix forms,
`Number.intValue()` bodies and byte-array fills skipped) and
`java.parsed-count-overflow` (a parsed count multiplied into an allocation
with no range check); the planted `int * int` total became these forms.
CWE-434: `java.upload-client-filename` (high/medium; `getOriginalFilename()`
or `getSubmittedFileName()` in a write path, no `FilenameUtils.getName`,
`getFileName()` or extension check). Planted pairs for all five; the gap:
markers for Java 79, 77, 918 and 190 are expect: markers. Java Top 25: native
8 to 13 cells, caught by anything 11 to 15 of 15, no Java gap left. Real
code: WebGoat 3 true positives (the reflected-XSS lesson, the WebWolf upload,
the zip upload); 17 false positives fixed and pinned by tests (a parameter's
`.length()` beside HTML, a directory named after the user, a logged path, a
zip entry name, gson's `intValue`, tomcat's drawboard colour bytes). Gold Java
repositories (httpclient, netty, spring-security, tomcat, and keycloak scanned
with the Java pack alone): 0 findings from the new rules. OWASP Benchmark Java: XSS recall 0 to 0.894 at FPR 0.455
(score 0.44); cmdi, sqli and weak randomness unchanged; in-scope recall 1.0
to 0.97, FPR 0.565 to 0.537, score 0.435 to 0.432.
### 2026-10-22 (round 2 precision: tests, imports, SQL)
- ARB-065 | `assurance.test-asserts-nothing` reads each test's own body: Python through `ast`, JS/TS and Go by matching its braces. The old span ran to the next test, which is the conditional-skip blind spot the user asked about: the NEXT test's `@pytest.mark.skip`, `it.skip(` or `t.Skip()` hid a test that asserts nothing; a skip under a branch (`if testing.Short() { t.Skip() }`) hid the test though `permanently-skipped-test` does not report it either; and an unconditionally skipped test was reported by both rules. Now a test that never runs (an unconditional skip on it or its class, an unconditional `pytest.skip()`/`t.Skip()` at the top of its body, an enclosing `describe.skip`) is left to `permanently-skipped-test`, and one with `skipif`, `xfail(<cond>)` or a skip under a branch is judged on its body. That surfaced 13 aiohttp tests that only call the code, two of them missing an `assert` (`await response.read() == payload`, `resp.text is None`). Also counted as asserting, as round 1 did for the Java and TypeScript rules: node:assert functions called bare (98 of undici's 100), `pytest.warns`/`deprecated_call`/`fail`, `with *raises*(...)` helpers, `self.fail`, `ensure*`, a Promise `reject`, a same-file helper or decorator that asserts (axios `testHeaderValue`, nest `equalTest`, Django `@test_mutation()`), a name or body comment saying not raising is the check, Go's `writer{t: t}`. Not tests: pytest-benchmark tests (cryptography `tests/bench`, 25), nested defs, `test_*` outside a test module (Django `runner.py`), fixtures and functions that return or yield. `_TEST_DEF` anchors on `^[ \t]*`: `^\s*` took the blank lines above a test and reported it on the first (from the harness agent). Python test methods (`def test_x(self)`) were never judged, because the delegated-helper pattern matched their own `def` line; `JUDGE_PYTHON_METHODS = False` keeps that scope by decision (user, 2026-10-22), and turning it on is a separate later step (+357 on gold: Django 226, celery 105, urllib3 16, cryptography 10; of 15 read, 14 only call the code, 1 is an empty inherited-test override). Gold 257 to 74 (0.0422 to 0.0122 per KLOC, budget 0.03).
- ARB-065 | `authored.undeclared-import` reads more of what declares a dependency. Java: a `groupId` given as a Maven property (netty's conscrypt), Gradle build files named for their module (`spring-security-oauth2-client.gradle`), sub-groups of a listed provider (`io.projectreactor.tools`), and provider tables for packages named for neither group nor artifact (Selenium, 44 of keycloak's 158; GraalVM svm, RXTX, uap-java, HtmlUnit 2, Saxon, `ldapsdk`) or guaranteed by a declared dependency (Quarkus/RESTEasy to `jakarta.inject`/`jakarta.annotation`, Infinispan to JGroups and RxJava 3, the Quarkus Operator SDK, Agroal, XNIO, `jakarta.json`, `jakarta.ws.rs`, Jackson 3 from httpcore5-jackson3, R2DBC from spring-r2dbc, Netty from rsocket, JSpecify from Guava, Jakarta Mail from Angus Mail). Python: imports under `if TYPE_CHECKING:` or a `sys.version_info` branch, function-local imports in a module that handles ImportError (the `shell` commands' IPython/bpython), a module tried once and imported again later, a framework's own requirements (FastAPI to pydantic/starlette, Flask to werkzeug/jinja2/click/..., Django to asgiref/sqlparse, requests to urllib3/idna/certifi, gevent/eventlet to greenlet, pymongo to bson, boto3 to botocore, Sphinx to docutils), Pyodide's `js`; test-role files are skipped as for Go, Java and Ruby (celery's `t/`). JS: no check without a package.json (urllib3's lone eslint config); a module a bundler config serves is local (keycloak's "message-bundle"). Ruby: named Bundler gemfiles (`*_gems.rb`), bundler/inline gemfiles, `rescue LoadError` requires, `rails` bringing its components. Gold 237 to 3 (0.0389 to 0.0005): tomcat's jdt and bnd (declared only in its Ant build) and puma's ffi (shipped by JRuby).
- ARB-065 | `python.sql-injection` follows a spliced local one step inside its function. Pack schema: Python-only `safe_splice_calls` and `safe_splice_names` (regexes, validated at load, documented in lang_rules.py and docs/probes.md). A value is safe when it is a literal, a quoting call, a safe name, `str()` of one, a `sep.join(...)` of quoted names or `%s` placeholders, a conditional of safe values, `sql.Identifier`/`Literal`, SQLAlchemy `type.compile(dialect)`, or a local every assignment of which is safe; a parameter, loop variable or container filled later is not. Gold 29 to 21 (0.0389 to 0.0282, budget 0.03): Django's cache backend `table = quote_name(...)` (4), createcachetable, oracle `cache_key`, `str(constraint_sql)`, celery's session.py. The 21 left splice parameters or method results, which needs data flow (Milestone 3).
- ARB-065 | Gold benchmark, all 24 repositories, native checks, `--jobs 6`, against `24b2b54`: 7,729 to 7,304 findings, 9 to 6 rules over budget (3 accepted; the three above leave); no other rule's count moved. Planted 100% in every language, 0 on good twins, 0 unmarked.
### 2026-10-22 (round 1 decisions)
- ARB-065 | Gold budgets gain `accepted_breaches`: a rule over budget by a recorded decision stays in the report and in `rules_over_budget`, and the run fails again only if its rate passes a ceiling set about 10% above the rate at the decision (`failing_breaches`, tested). Recorded on 2026-10-22 by the user: `java.test-without-assertion` is a known breach (0.066/KLOC; its remaining findings are tests that only call the code), and `assurance.permanently-skipped-test` (0.0315) and `typescript.test-without-assertion` (0.034) are accepted overages. The run on the 1047341 report has 9 over budget, 3 of them accepted, and 6 failing.
- ARB-065 | Decisions recorded by the user on 2026-10-22: the test-file exclusions in 1047341 and c4d8bfe stand (TLS off for a test's own local server, `override_settings(DEBUG=True)`, empty skipped tests, Java `@Override` tests). The three Django applications stay a separate corpus, not gold, so Python denominators don't move. Checking `assurance.test-asserts-nothing` for the same conditional-skip blind spot is round 2 work.
### 2026-10-21 (licence)
- ARB-005 | Relicensed under the Apache License, Version 2.0, by decision of
the copyright holder (Nicholas J Pauken); the repository stays public. `LICENSE`
is the standard, unmodified Apache-2.0 text, replacing the interim
all-rights-reserved notice. `NOTICE` opens with "Arbiter / Copyright 2026
Nicholas J Pauken" and reproduces the OmniEngineering workspace's NOTICE
unchanged. `pyproject.toml` keeps `license = { file = "LICENSE" }` (the SPDX
string form needs setuptools 77; the floor is 68) and adds the Apache
classifier; the wheel builds and carries `LICENSE`, `NOTICE` and `NOTICE.md`.
The planned proprietary per-repository licence (the L-3 draft grant) is
dropped; `docs/licensing.md` is rewritten as the current position with a short
history of the superseded draft. Scan output belongs to the user, now stated in
`README.md`, `NOTICE` and `docs/licensing.md`. `NOTICE.md` adds tomli, the
vendored 3d-force-graph viewer and the OmniEngineering workspace, and says the
bundle carries the unmodified PyYAML/tomli wheels. `CONTRIBUTING.md` names
Apache-2.0 §5 as the inbound basis and still takes no outside contributions;
`TRADEMARKS.md` says it covers the OmniEngineering names only. ARB-005 stays
pending: its fourth criterion, the L-6 redistribution review for bundling the
analyzers (semgrep LGPL-2.1), is not done and needs a named reviewer.
- ARB-005 | Pre-publication check (Milestone 1, `docs/gaps-and-next-steps.md`):
a full-history gitleaks scan. Run earlier on 2026-10-21 over 272 commits: 70
hits, all synthetic test values (68 in `tests/`, 2 in
`fixtures/legacy-platform`), none in `src/`, `tools/` or `docs/`. Re-run for
this change with gitleaks 8.21.2, `gitleaks detect --source . --log-opts="--all"
--redact`: 292 commits scanned, 73 hits — 70 in `tests/`, 2 in
`fixtures/legacy-platform/app/config.py` (a planted `DB_PASSWORD`, the same
line in two commits), and 1 in a planted CWE-798 fixture under
`fixtures/planted/python/bad/cwe25/` that exists only on an unpushed local
branch; none in `src/`, `tools/` or `docs/`. Each distinct hit was inspected
and is a synthetic test value. The published history alone
(`--log-opts="--remotes=origin"`, 226 commits) gives 48 hits: 47 in `tests/`,
1 in `fixtures/legacy-platform`. The `training/` redistribution check named
in the same gap is not recorded here.
### 2026-10-21 (round 1 follow-ups)
- ARB-065 | `test-d/` (tsd type-level tests, jose) and `integration-tests/` (opentelemetry-js) are test roles in the inventory, whole directory names only. Gold, ts-jose and ts-otel-js at `d67566e` against `3984b05`: `typescript.jwt-decode-unverified` 2 to 0 (both in jose's `test-d/`), `typescript.error-object-in-response` 1 to 0 (otel `integration-tests/`); no other rule moved; 476 jose and 55 otel TypeScript lines move from production to test.
- ARB-062 | `ruby.csrf-protection-off-in-production` (CWE-352, high): `config.action_controller.allow_forgery_protection = false` in `config/environments/production.rb`, at the root or under any directory; Rails generates the same line in `test.rb`, which is not read. The pack schema's `files:` key (an fnmatch glob, until now undocumented) also takes a list of globs, validated at load; documented in `lang_rules.py` and `docs/probes.md`. Planted bad `production.rb`; good twins `production.rb` with the check on and `test.rb` with it off. No gold file sets it.
- ARB-062 | Three Django applications pinned in `tools/fetch_corpus.sh` (`DJANGO_APPS_ONLY=1`, `get_pinned_app`, so the gold benchmark does not budget them): zulip `4bae1c54`, healthchecks `47415f03`, django-cms `827b4598` (252k production Python lines). Their 49 `@csrf_exempt` views give `python.csrf-protection-disabled` no finding, and none should: the only one reading `request.user` is zulip's `rest_dispatch`, exempt so it can re-apply `csrf_protect()` to its session branch, which is now an explicit exclusion (it was silent only because the word `Authorization` appears in it). No `jwt-*` or `error-*` finding on the three either. Not added to the gold set.
- ARB-065 | `java.test-without-assertion` skips an `@Override` test, a body comment saying not throwing is the check, names saying the call completes (`NoneExceptionIsThrown`, `NoAssertError`, `DoNotCause...`, `...NotRequired`, `CanBeInstanced`, `IsSerializable`, `Compiles`, and `Created`/`Granted`/`GrantsAccess`/`Success`/`Wires`/`Autowires`/`Allow(s|ed)` outcome words, spring-security's authorization tests), `ensure*` helpers and performance-harness classes (`*Performance`, `*Benchmark(s)`), each anchored on the `@Test` method so an anonymous class inside it does not count. Gold 276 to 199 (0.092 to 0.066 per KLOC raw, budget 0.03): still over. Each of the 77 removed was reviewed by name and category; the rest, triaged by category with samples, only call the code (netty boundary tests, tomcat `MessageBytes`, keycloak admin-client flows, empty `// FIXME` tests), so the budget is not reachable without dropping real findings.
- ARB-065 | Per-site counting (ARB-080) kept; precision fixes for the two rules it pushed over budget. `authored.security-check-disabled`: in a test file, a TLS check off for the test's own local server (`localhost`, `127.0.0.1`, `server.address()`, `httptest`) and Django's `override_settings(DEBUG=True)` are the fixture and are not reported; other test-path hits stay reported at low. 328 to 86 (0.054 to 0.014, within budget). `assurance.permanently-skipped-test`: a `t.Skip()`/`it.skip(`/`describe.skip(` under a branch or after code on its line is conditional, `xfail(<expr>, ...)` has a positional condition, and a skip on an empty test (Java `{}`, Python `pass`) switches an inherited test off in a subclass it does not apply to; a skipped test with a body stays reported. 311 to 192 (0.051 to 0.0315, budget 0.03): just over, and the rest are real tests switched off. `typescript.test-without-assertion` unchanged at 22: 16 only call the code, 6 are otel compile-time type checks with no generic signal.
- ARB-065 | Gold benchmark, all 24 repositories, native checks, `--jobs 4`, at `1047341` against `3984b05`: 8,170 to 7,729 findings, 10 to 9 rules over budget (`authored.security-check-disabled` leaves). Scan seconds summed 2,259.8 to 2,229.7; java-keycloak 1,196.3.
### 2026-10-21 (Milestone 1: trustworthy numbers)
- ARB-076 | Each tree-sitter query is compiled once per process: `run_query` used to build `Query(lang, src)` on every call (files × rules compilations). Compiled queries are cached by grammar and query text (tsx and typescript separately); a failed compile is never cached; the on-disk compiled-pack stamp is unchanged. `tests/test_query_cache.py` counts real compilations during a 1- and 8-file Ruby scan. Scan seconds per repository, same machine, `tools/gold_benchmark.py --only java-keycloak,ruby-rails,go-grpc,ts-nest --jobs 4 --no-fail`, before (`de35f15`) → after (`d693074`): ts-nest 252.5 → 26.2, ruby-rails 720.1 → 203.3, go-grpc 59.1 → 45.9, java-keycloak 2,124.1 → 1,499.8; wall clock 35:24 → 25:00; per-rule finding counts identical (2,200 both). Keycloak's remaining time is outside the packs: on `services/` (1,751 Java files) `authored` takes 28.4 s and `doc_drift` 14.9 s against `lang_java` 6.2 s.
- ARB-078 | `import arbiter.lang_rules` works before `arbiter.probes`: lang_rules no longer imports probes at load time (it imported `PACKS` and `ProbeContext`, while probes imports lang_rules to register the packs). Two tests' import-order workaround lines removed. `tests/test_import_order.py` imports every module first in a fresh interpreter. FAIL-052.
- ARB-062 | `controls._probe_of("arbiter/<lang>.x")` returns `lang_<lang>` (it returned `<lang>`, a probe that does not exist, so a control backed by a language-pack rule always read as not assessed). Tested for all five languages, including a real scan per language with a one-control pack. No shipped control pack references a language-pack rule yet, so shipped controls output is unchanged. FAIL-053.
- ARB-080 | Same-evidence findings in one file each count: the distinct sites of one fingerprint get an occurrence ordinal in line order (occurrence 0 keeps its old id, occurrence k gets a derived one). Reports and new baselines say `fingerprint: occurrence-v1` (baseline schema 1.1). A 1.0 baseline still matches every repeat it collapsed, as do suppressions by id and `arbiter diff` from an older report, so upgrading leaves the gate green on unchanged code; `arbiter baseline REPORT --migrate OLD --out OLD` rewrites a 1.0 baseline without accepting anything new and keeps `created`, `commit` and `config_hash`. On this repository's committed 1.0 baseline (native, offline): 86 active (66 existing, 20 new) before, 100 (80 existing, 20 new) after. FAIL-054.
- ARB-065 | `java.test-without-assertion` lowered to confidence low (reviewer-approved, instead of raising its budget). The gate reads severity only, so it is unaffected (the rule is severity low). The gold harness now records confidence and reports a confidence-weighted rate beside the raw one (low counts 0.5, the scorecard's low/medium factor); budgets stay on the raw rate, because weighting them moved three other rules under budget through their existing low-confidence findings while this one stayed over (276 findings, 0.092 raw, 0.046 weighted, budget 0.03).
- ARB-065 / ARB-080 | Gold benchmark, all 24 repositories, native checks, `--jobs 4`: at `bd6bf16` (before ARB-080) 7,457 findings, 7 rules over budget, per-repository counts identical to `2026-10-21-gold-report-after.json` on its 23 repositories; at `7dd990c` 8,162 findings, 10 over budget (`authored.security-check-disabled`, `assurance.permanently-skipped-test` and `typescript.test-without-assertion` join, all from counting each same-evidence site). Scan seconds summed 2,308.9 (6,454.9 in the first run), keycloak 1,239.9. (The `7dd990c` run used the same scan code under an earlier, unpublished budget rule; its over-budget count was re-evaluated from the run's JSON with `evaluate` as committed.)
### 2026-10-21 (CWE Top 25, TypeScript and Go round 1)
- ARB-062 | Native rules for ranked gaps 1, 2 and 4 in TypeScript and Go.
TypeScript: `jwt-decode-unverified` (CWE-287/347, medium: `jwt.decode` or
jose `decodeJwt` in a function that never verifies; `decode(t, {complete:
true})` is the key-lookup form and is not reported), `jwt-verify-insecure-options`
(`algorithms: ["none"]`, `ignoreExpiration: true`; high), `jwt-hardcoded-secret`
(CWE-798, literal secret to `jwt.sign`/`verify`; high), `error-stack-in-response`
(CWE-200/209, `err.stack` in an Express/Koa response unless the function checks
`NODE_ENV`; high) and `error-object-in-response` (`res.json(err)`; medium).
Go: `jwt-parse-unverified` (medium, silent when the function also verifies),
`jwt-claims-validation-disabled` (high), `hardcoded-signing-key` (`[]byte("…")`
to `SignedString`, `hmac.New`, a keyfunc, or a key-named variable; high),
`hardcoded-dsn-password` (mysql `user:pass@` and key=value DSNs; trivial
passwords skipped as in `pg-url`; high), `error-text-in-response` (err text
to `http.Error`/gin/echo with a 5xx, or `fmt.Fprint*`/`Write` to the writer
in a handler; medium) and `pprof-on-default-mux` (medium; silent when the
server listens on loopback). The pack schema gains `requires_in_scope`. The
matrix moves TypeScript 287 and 200 and Go 287, 200 and 798 to N: TypeScript
native 6 to 8 cells, Go 4 (798 part) to 6.
### 2026-10-21 (CWE Top 25 round 1: Python and Ruby)
- ARB-062 | Native rules for the ranked CWE Top 25 gaps in Python and Ruby.
`python.jwt-unverified-decode` / `ruby.jwt-unverified-decode` (CWE-287):
PyJWT and python-jose with `verify_signature: False`, `verify=False`, a
"none" algorithm or `get_unverified_claims`; `JWT.decode(t, k, false)`. A
function that also verifies with a fixed algorithm list (peek, then verify) is
not reported. `python.jwt-literal-signing-key` / `ruby.jwt-literal-signing-key`
(CWE-798): a string literal, or a `*_KEY`/`*_SECRET` constant tied to the call,
as the key of jwt encode/decode; the secrets probe missed both.
`python.csrf-protection-disabled` / `ruby.csrf-protection-disabled` (CWE-352):
`@csrf_exempt` on a session-authenticated Django view, `WTF_CSRF_ENABLED =
False` outside a test config; `skip_before_action :verify_authenticity_token`
and `skip_forgery_protection` in a controller with a state-changing action that
is not an API, webhook or self-authenticating controller. `null_session` is not
reported. `python.error-traceback-to-client` / `ruby.error-backtrace-to-client`
(CWE-209/200, high) and `python.error-message-to-client` /
`ruby.error-message-to-client` (medium): a traceback, backtrace or catch-all
exception message in a response in the same statement. Cells in
`docs/cwe-top25.md`: Python 352, 287 and 200 and Ruby 352, 287 and 200 are
now native; Python native 6 to 9, Ruby native 6 to 9 and caught 8 to 10.
### 2026-10-21 (CWE Top 25, Java round 1)
- ARB-062 | Java native rules for gap-list ranks 1-4 (`docs/cwe-top25.md`).
CWE-287: `java.jwt-unsigned-accepted` (jjwt `parseClaimsJwt`,
`parseUnsecuredClaims`, `.unsecured()`; high confidence) and
`java.jwt-decode-without-verify` (auth0 `JWT.decode`, Nimbus `JWTParser.parse`
with no `verify`/`require`/JWT processor in the file, an issuer or key-id peek
excepted; medium). CWE-798: `java.hardcoded-signing-key` (`Algorithm.HMAC256`,
`Keys.hmacShaKeyFor`, `signWith`/`setSigningKey`, `new SecretKeySpec` given a
literal) and `java.hardcoded-db-password` (`DriverManager.getConnection` with a
literal password or `password=` in the URL, `setPassword` on a data source);
placeholders and empty passwords skipped, test code skipped, the literal never
in the finding. CWE-352: `java.csrf-disabled` (all three Spring Security forms;
high severity, medium confidence) and `java.csrf-disabled-stateless` (low/low
where the file declares `SessionCreationPolicy.STATELESS`). CWE-200/209:
`java.stack-trace-to-client` (high confidence) and
`java.exception-message-to-client` (medium; broad or library exceptions only,
tied to their own catch clause or `@ExceptionHandler`). Java Top 25 coverage:
native 4 to 8 cells, caught by anything 8 to 11 of 15; gaps left 79, 77, 918,
190. Gold Java repositories (httpclient, netty, spring-security, tomcat; keycloak
skipped): 0 findings from the new rules. OWASP Benchmark Java unchanged
(in-scope recall 1.0, FPR 0.565).
### 2026-10-21 (gold re-run)
- ARB-065 | Gold benchmark re-run after the precision fixes (Arbiter 8cf1ee7, 23 repositories without keycloak, native checks): 6,477 findings, 1.32 per KLOC, 7 rules over budget. The first run (d57e479, 24 repositories) had 12,468 findings, 2.05 per KLOC, 16 over budget. Results in `tools/gold/results/2026-10-21-gold-*-after.*`.
### 2026-10-21 (roadmap)
- ARB-066 to ARB-075 | Proposed roadmap requirements: remediation through MCP
with a verify loop, explaining why a rule fired or did not, auto-apply only for
fixes that verify, and seven requirements against hallucination and hidden
assumptions (claims need evidence, assumption ledger, generated briefs,
environment preflight, independent re-verification, verified/inferred/assumed
labels, ask instead of guessing).
### 2026-10-21
- ARB-067 | Gaps and next-steps report (`docs/gaps-and-next-steps.md`):
where Arbiter stands, with measured numbers and their sources (gold benchmark,
OWASP recall, CWE Top 25 matrix, scan times), the gaps in detection,
precision, performance, platform, product and developer experience, fifteen
improvements ranked by value over effort with requirement ids, an evaluation
of fourteen testing options, and three milestones with measurable exit
criteria. Every claim is labelled measured, inferred or assumed (ARB-074).
Records the issues found during the overnight runs: findings with the same
evidence collapsing, per-file query recompilation, the `lang_rules` import
cycle and `controls._probe_of`. Documentation only; no code changed.
- ARB-065 | Java and TypeScript packs fixed against the gold triage and
OWASP Benchmark Java. TypeScript test-without-assertion recognises node:test
plans (`tspl`) and their destructured methods, AVA's `t.*` assertions
(`t.throwsAsync`), node:assert functions called bare, `runClient(t)`-style
helpers, a Promise's `reject`, "should not crash / without error" titles and
Playwright `({ page })` flows: 729 to 18 on the gold set (1.11 to 0.027 per
KLOC). fetch-no-timeout skips browser files (`document`/`window`): 25 to 14.
Java test-without-assertion skips `@Disabled`/`@Ignore` and empty `@Override`
tests, tests whose name or `@DisplayName` says nothing throws (the TypeScript
rule's convention), a conditional `throw`, and a method reference handed to
a runner (keycloak run-on-server): 446 to 276, still over budget; the rest
only call the code. Java sql-string-built needs a receiver (not Ant's
`execute`), SQL words in the string, and a splice that is not
identifier-shaped (`tableName`, `getTableName(...)`; a bare `column` stays),
and tracks `String sql = ...` into a following `try`: gold 45 to 7. Java
command-injection adds `Runtime r = Runtime.getRuntime(); r.exec(...)`,
`exec("echo " + x)`, and a ProcessBuilder list in a method that adds a shell
`-c`; weak-random-for-secret adds `new java.util.Random()`,
`java.lang.Math.random()` and one hop through a local or a `nextBytes`
buffer into a `rememberMe`/reset/auth key. OWASP Java in-scope recall 0.045
to 1.0, false-positive rate 0.043 to 0.565, score 0.003 to 0.435: weak
randomness 218/218 with no false positive; SQL and command injection flag
every safe case too, because the safe cases differ only by constant
propagation.
- ARB-065 | Shared-probe precision on the gold set (23 repositories without
Keycloak). undeclared-import 1,647 to 79: Python requirement files in any
layout (`requirements/*.txt`, `requirements-*.txt`, `requirements.in`, extras
and markers), Go files resolved against their nearest go.mod (and module paths
without a slash), bare Node built-ins, tsconfig path aliases, Java plugin ids,
artifactId-named packages, the Gradle API in plugin builds and a documented
table of guaranteed transitive packages (jspecify, aopalliance,
reactive-streams, jose4j), Ruby gemspecs and renamed requires.
assigned-credential 1,019 to 132: only literals are judged (no unquoted
values in source code), constants holding key names, headers, URLs, hashes or
their own name's words are skipped, as are i18n bundles and docs; under test
paths only generated-looking values report. blanket-suppression 243 to 178:
gosec `#nosec G115` names a rule. stub-on-production-path 309 to 48: abstract,
base-class, hook, explained and "not supported" bodies are declarations.
Fixture private keys stay reported, as low with a `test-fixture` tag. Keycloak:
undeclared-import 678 to 158, assigned-credential 677 to 56. Insecure apps
keep their true positives (planted corpus unchanged in all five languages).
- ARB-065 | Python and Ruby packs, first fixes from the gold triage.
Python `sql-injection` skips a statement whose every spliced value is a
quoting call (`quote_name`, `qn`, `escape_identifier`), an ALL_CAPS constant
or a built SQL fragment, and a two-stage template whose values stay `%%s`
binds (Django 65 to 29). `insecure-random-for-secret` also reports a
`random` value stored in a session or cookie, and knows `normalvariate` and
the other distributions; `weak-password-hash` reports an empty md5/sha1
object fed by `update()` in a function that handles a password (OWASP
Benchmark Python: weak randomness 0/99 to 99/99, weak hash 0/71 to 71/71, no
safe case flagged). `code-injection` skips eval behind a validating guard
that returns or raises (safe cases flagged 33/33 to 11/33; the rest are
constant-propagation decoys). Ruby: `stub-method` treats a class with two or
more bare `raise NotImplementedError` methods, a private hook, an
`Abstract*`/`Base` class and "is not implemented" messages as the abstract
idiom; `vacuous-spec` counts `should_*`/`expect_*`/`mock_*` helpers, a
helper with a heredoc and "does not raise" names; `check-then-act-unlocked`
needs a collection query and a mutating act, and skips a `Concurrent::Map`
constant declared beside its use, ENV and per-thread stores; `sql-injection`
skips `escape_*` and `.to_i` interpolations; `rescue-exception` skips a
hand-off to an error method, a collected or wrapped exception, and a
RuboCop disable; `swallowed-error` skips an empty rescue with a comment
saying why, and `rescue => @ivar`. Each pinned by planted pairs and tests
that keep a still-reported case.
- ARB-062 | CWE Top 25 (2024) measured before any new rule: an
applicability matrix for Python, TypeScript/JavaScript, Go, Java and Ruby
(`docs/cwe-top25.md`), 47 new planted pairs, one per applicable cell not
already in the corpus, and a ranked gap list. Each defect is marked with what
reports it, native or semgrep/bandit. 32 defects nothing reports are tagged
`gap: CWE-N` in the source (TS JWT `decode`, Java `csrf.disable()`, error
detail returned to the client, uploads under the client's filename, SSRF
outside Python...). `docs/probes.md` lists, per language, the CWEs that do not
apply, are out of reach for single-file checks, or are gaps. One marker
comment can now carry both `expect:` and `expect-ext:`.
- ARB-065 | Gold-standard benchmark. A fourth group in
`tools/fetch_corpus.sh` (`GOLD_ONLY=1`) pins 24 exemplary repositories, 4 or 5
per language (urllib3, Django, Celery, aiohttp, cryptography; undici, Nest,
jose, OpenTelemetry JS, axios; Caddy, containerd, CoreDNS, grpc-go, etcd;
Netty, HttpClient 5, Tomcat, Spring Security, Keycloak; Rails, Rack, Puma,
RubyGems). Each is pinned to one commit and chosen on evidence recorded in
`tools/gold/repos.json`: OpenSSF Scorecard checks, the Best Practices badge,
and stewardship. OWASP Benchmark for Java and Python is pinned too, as
labelled ground truth. `tools/gold_benchmark.py` counts findings per rule
per KLOC against per-rule budgets in `tools/gold/budgets.json` and exits 1
on a breach. `--recall` scores a labelled benchmark per CWE: recall,
false-positive rate and TPR-FPR, with the CWEs Arbiter claims kept apart
from those it does not. First measurement (`tools/gold/results/`): 6,086
KLOC, 2.05 findings/KLOC, 16 rules over budget, led by TypeScript
test-without-assertion (1.11/KLOC, from tspl plans and AVA `t.*`),
undeclared-import (0.38) and assigned-credential (0.28). In-scope recall
is 0.045 on OWASP Java (command injection and weak randomness 0%) and 0.22
on OWASP Python. The triage, with a file:line for each verdict and a
prioritised fix list, is in `tools/gold/results/2026-10-21-triage.md`.
No rule changed.
- ARB-061 | Rule packs for Python, TypeScript/JavaScript, Go, Java and Ruby:
races, outbound calls with no timeout (and Go DB calls with no deadline),
retry loops with no backoff or cap, swallowed errors and unstoppable worker
loops, SQL and command injection, stubs, tests that cannot fail, TLS off,
weak randomness for secrets. Planted corpus: every defect caught in all five
languages (41/39/38/45/50), nothing on the good twins. Each pack was checked
for precision on real code (21 deliberately insecure and carefully maintained
repositories, 13 of them added to `tools/fetch_corpus.sh`) and every false
positive found was fixed and pinned by a test. Shared fixes from those runs:
Java undeclared-import reads Gradle version catalogs, maps packages to
groupIds and treats Gradle test source sets as tests (2,295 findings to 117
on seven Java repos); a skip annotation inside a comment is no longer a
skipped test.
- ARB-061 | Precision follow-ups in shared files, from the language runs.
A Go test handing `t` to a helper in any argument position delegates its
assertion (cobra 34 to 2 tests-assert-nothing); `//go:build ignore` generators
may import undeclared modules (client_golang 2 to 0); `InsecureSkipVerify`
beside its own `VerifyPeerCertificate`/`VerifyConnection`/`PeerCertSANs` in the
same literal is custom verification, not off (traefik 25 to 20); a `_test/`
directory holds tests (yaegi 19 source findings under it to 0). Pack queries
are compiled when a pack loads, and one that does not compile raises a
`PackError` naming pack, rule and reason instead of silently emptying a scan.
- ARB-064 | Findings traceable in OmniGraph: rule and CWE nodes, `violates`
and `classified_as` edges, full and gate reports merged, `--if-stale`
rebuild, `omni graph findings --cwe/--rule`, impact by file or CWE.

### 2026-10-20

- ARB-060 | Foundation for every IDE language. Shipped tree-sitter rule packs
  (`packs/rules/ast/{python,typescript,go,java,ruby}.yaml`), one `lang_<pack>`
  probe each, with `unless_inside` / `unless_in_scope` / `unless_in_file` /
  `requires_file` context conditions and a schema that fails loudly. A grammar
  that is not cached is reported as not assessed; `install_tools.sh` caches all
  seven (`.tsx` now parses with the tsx grammar). Findings carry `cwe`, in JSON
  and as SARIF `external/cwe/*` tags, from the packs, a native table, and
  semgrep/bandit/gitleaks output. Undeclared imports are now checked for Go
  (go.mod), Java (pom.xml, Gradle) and Ruby (Gemfile). `*.test.ts`, `*_spec.rb`
  and `src/test/` are tests; java and ruby stacks are detected. A planted
  corpus (`fixtures/planted/`) with correct twins is asserted in CI and scored
  by `tools/planted_score.py`.

- ARB-053, ARB-054 | New `concurrency` probe (Python, stdlib `ast`).
  `concurrency.check-then-act-unlocked` reports an `if` that reads a shared
  collection and then changes it with no lock held;
  `concurrency.unsynchronised-counter` reports `+=` on `self` or a global inside
  a thread target, `Thread.run` or executor job. Each rule is tested against a
  correct twin that must stay silent. Precision run: no findings on Arbiter,
  OmniEngineering, concurrent.futures, urllib3, requests, anyio, uvicorn or
  starlette, after narrowing plain functions in event-loop files to ones started
  as threads (an anyio semaphore had been reported).

- ARB-059 | An analyzer that could not run is now reported as not assessed or as
  an error, never as a clean run. Adapters can declare required files
  (`[requires] files`); semgrep declares its pinned ruleset, so a missing
  `~/.cache/arbiter/semgrep-rules` is skipped with the path and
  `tools/install_tools.sh` named, before any cache replay. An exit outside
  `ok_exit` with no parsed findings is an error with the tool's own message.
  Before this, semgrep with no rules showed `ran, 0 findings`, or replayed the
  last run's findings from the cache.

### 2026-10-09

- FAIL-050 (ARB-048) | The secrets probe no longer reports an environment-variable
  NAME assigned to a credential-named symbol (`SEMANTIC_API_KEY_ENV =
  "OMNI_GRAPH_SEMANTIC_API_KEY"`): a value shaped like an upper-case identifier,
  or a symbol whose suffix says it holds a name (`_ENV`, `_VAR`, `_NAME`), is not
  a credential; a live-looking key beside it still fires. Found by code scanning
  on upstream OmniEngineering pull request #3.
- FAIL-046 (ARB-049) | Every action in `pr-check.yml` and `train.yml` is pinned to
  its tag's commit SHA, the tag kept in a trailing comment, so the workflows run
  what was reviewed rather than what the tag points at on the day.
- ARB-052 | A finding in a `--changed` gate is tagged `req:<ID>` for the requirements
  cited by the commits since the base that touched its own file, marked
  `req-scope:commits`. Before, every finding in the change carried every id the
  round's commits cited, so a five-requirement gate could not say which one
  introduced a finding and OmniEngineering's graph attributed it to all of them.
  A file no citing commit touched falls back to the round's ids marked
  `req-scope:open` (context, not attribution); a full scan carries only that
  marker; old ids resolve through the registry's `id_aliases`; one
  `git log --name-only` runs per repository however many files changed; and the
  By requirement table says under it which scope each count came from. The
  attribution lives in `incremental.attribute_requirements`, called by the
  engine after every probe has run, since a tag on every finding cannot be
  applied by one probe.
- ARB-045 | Tool versions are memoised. Every `arbiter` invocation ran the five
  analyzers' `--version` at registration (25 s measured here; semgrep alone
  reaches its 20 s cutoff), including the partial-scan gate that never runs an
  adapter. `Adapter.tool_version` now reads `~/.cache/arbiter/tool-versions.json`
  (`ARBITER_CACHE_DIR`), keyed on the resolved executable's path, mtime and
  size (and a script argument's, for a tool run through an interpreter); a miss
  probes once and writes the memo atomically, a missing binary is never probed,
  a corrupt or unwritable memo costs one probe. `--no-cache` leaves it alone
  (it is not a result cache); `ARBITER_NO_VERSION_MEMO=1` switches it off. A
  tool replaced in place with the same size and mtime keeps its old string
  until the file is deleted. Registration: 24.5 s cold, 0.3 s warm. The
  mutation tool proves a memo that ignores the binary's stamp is caught.
- ARB-046 | The suite runs in a fast and a slow tier and CI shards them.
  `tests/test_arbiter.py` (5,516 lines, 438 collected ids) is fourteen files by
  area, each under `arbiter.yaml`'s 900-line limit, with the helpers more than
  one file needs in `tests/helpers.py` and the tfplan fixtures in `conftest.py`
  at session scope; `pytest --collect-only` lists the same 557 ids before and
  after, and the failure ledger's regression-test references follow the tests.
  A registered `slow` marker covers the eight tests that run a real analyzer
  through the adapters or scan beyond the fixtures: `-m "not slow"` runs in
  about two minutes here and `-m slow` in about five, against eleven for the
  whole suite before. `pr-check.yml` runs the fast tier on every matrix cell
  and a parallel Linux `slow-tier` job runs the slow tier with the analyzers
  installed; `.ai/test-suites.json` names the fast tier as the gate's suite
  and the slow tier as an integration suite the gate does not run.
- FAIL-044 (ARB-047) | `run_scan(use_adapters=False)` keeps every external analyzer
  out of the run. The flag used to guard only registration, and the registry is
  module-global, so once one scan had registered the analyzers every later scan
  ran all five whatever the flag said; the fast test tier spent 28 minutes in
  semgrep. The scan loop now skips a probe marked external when the flag is off
  and no explicit `only=` names it. Fast tier: 1 minute 53 seconds.
- ARB-047 | Adapters run concurrently and replay when nothing they read
  changed. Probes registered from a manifest carry `Probe.external` and run
  after the native probes in a thread pool of `probes.adapters_parallel`
  workers (default min(4, cpu count); 1 runs them in turn), each on its own
  copy of the probe context, with outcomes and findings recorded in registry
  order so reports stay deterministic. With the result cache on, one entry per
  adapter, keyed on its name, the tool version, the rules hash and a digest
  over every inventory file plus the analyzer configuration at the root
  (`pyproject.toml`, `ruff.toml`, `.ruff.toml`, `setup.cfg`, `.bandit`,
  `bandit.yaml`, `.semgrep.yml`, `.semgrep.yaml`, `.semgrep/`, `.checkov.yaml`,
  `.checkov.yml`, `.gitleaks.toml`), replays the findings with the outcome
  reason `replayed from cache (nothing the tool reads changed)`. All-or-nothing
  by design: no adapter has been measured to be file-local, so one changed
  byte re-runs the whole tool. `--verify-cache` re-runs one memoised adapter a
  scan and reports a divergence as `arbiter/assurance.cache-divergence`;
  `--no-cache` disables the memo (CI's self-gate). Integrity invariant CI-14
  proves a changed inventory file misses the memo, and the mutation tool
  proves a memo keyed without the tree is caught.
- ARB-048 | The doc-drift probe leaves generated output, git-ignored paths and
  cross-repository references alone. Scanning Arbiter with itself, `doc_drift`
  reported every path the root `.gitignore` keeps out of a checkout
  (`.claude/settings.local.json`, `.ai/project-graph.json`, `.arbiter/cache.json`),
  a report file under an `--out` directory, and the OmniEngineering handbook
  named beside its URL. The backticked-path and markdown-link checks now skip a
  candidate the root `.gitignore` matches (comments, `dir/`, `*.ext`, `path/**`,
  a leading `/` and `!` re-inclusion are read, nothing broader), one under the
  output directory (the new optional `out:` key, this run's `--out` when it sits
  inside the tree, or any `arbiter-out` segment) and a link target carrying a
  scheme; a backticked path is also skipped when an `http(s)://` URL sits on its
  line or the adjacent line of the paragraph, since hard wrapping split the
  motivating case. A relative link is never excused that way and a plainly
  missing file still fires. The engine hands `--out` to `ProbeContext.out_dir`
  rather than the config, so cache keys do not change with the output directory.
  `fixtures/doc-drift` plants the four cases and one real one; the self-scan
  drops from 30 doc-drift findings to 20.
- FAIL-042 (ARB-048) | The stub detector counts a marker only at the function body's own
  indentation: a `pass` under `except`, `if` or `with` is a branch, not a body. Found by
  code scanning on OmniEngineering pull request #5, where `store_mcp_probe_memo`, which
  writes a file and swallows a failed write, was reported as a stub.
- ARB-049 | Arbiter's own debt is worked through the review flow and the baseline is
  re-cut. The self-scan went from 482 active findings (57 medium, 324 low, 101
  info; score 71) to 320 (39 medium, 182 low, 99 info; score 81) and the gate's
  baseline from 411 ids at 19b40f7 to 320 at 2a18ce9, one commit per finding
  class with the fast tier, `tools/integrity.py` and `tools/mutate_tests.py` green
  after each. Twenty of thirty-seven broad `except Exception` clauses name the
  exception and the seventeen that must never raise say why on the line; ruff's
  unused imports, dead locals, placeholder-free f-strings and unsorted import
  blocks are gone, and the dead local in `render_html` was the HTML report's
  "Read first" block, built and never rendered since it was added (FAIL-045);
  every suppression comment carries a reason or is removed; `build_parser` and
  `render_html` are split into named helpers with the same inputs and outputs.
  The three tests ARB-046 marked slow only because `use_adapters=False` was a
  no-op are back in the fast tier (0.9 s, 0.2 s, 0.2 s; no analyzer starts).
  `arbiter review --limit 60` drew fifty-nine findings; the marks and reasons are
  committed as a draft in `.arbiter/reviews/2026-10-09-debt-sweep.md` for a
  person to apply, and nothing is recorded in the knowledge ledger. The 55
  over-limit functions left alone are suppressed in `arbiter.yaml` per file,
  functions named, expiring 2027-04-01; `pytest.raises(Exception)` in the
  bad-plan test is `RuntimeError` (FAIL-047); the mutable action tags in the
  workflows are one open entry (FAIL-046); and two of the false positives were
  the probes' own, now fixed with regression tests: the suppression census
  counted `# noqa` inside Python strings and docstrings (FAIL-048) and the
  manifest reader never saw a requirement quoted past the first position of a
  one-line list (FAIL-049). Baseline cut last via `omni arbiter baseline
  --refresh`; README "Project status" records the numbers.
- ARB-050 | `arbiter_review_draft`: an assistant proposes marks with reasons and
  never records one. `docs/mcp.md` rules out any tool that records a verdict,
  and the rule stands. The new MCP tool (`service.review_draft`) draws the queue
  `review_queue` draws (same report, selection and knowledge, read and never
  saved), fills in the marks an assistant proposed with a reason on the line
  beneath each, and writes `review-draft.md` under `output_dir` in the exact
  format `review.parse` reads; its first line says every mark was proposed by an
  assistant and nothing has been recorded. The result carries counts, the ids
  not in the queue (reported, not raised) and, as text, the failure-ledger
  entries the `y` marks would draft (`ledger.preview_entries`, which opens no
  ledger). A `y` or `n` without a reason is refused. No new path argument, so
  HTTPS confinement is unchanged. `arbiter review --apply --reviewer` stays the
  only writer; the no-verdict test is extended to every tool in `TOOLS`, each of
  which must leave the knowledge file byte-identical. The skill describes the
  inline flow: draft with reasons, the person reads, the person applies.
- ARB-051 | The air-gapped bundle, without the analyzers, and the hosted
  limiter shared across processes. `arbiter bundle build --out DIR` writes
  Arbiter and its runtime dependencies as wheels (`pip wheel` of the checkout,
  offline when setuptools can build without isolation, with an isolated retry
  when a distribution-patched setuptools cannot; `pip download` for PyYAML and
  tomli, the one step that needs an index, skipped by `--no-deps-download`), the
  checkout's `.arbiter/knowledge.json` when present, `install.sh` /
  `install.ps1` (`pip install --no-index --find-links wheels arbiter-eval`), a
  README and a `MANIFEST.json` with the version, Python floor, build time and a
  SHA-256 per file; the packs ride inside the wheel and are not copied. The
  five analyzers are not included and the manifest says why (their
  redistribution review, ARB-005 L-6, is pending); operators install them from
  their own mirror. `arbiter bundle verify DIR` recomputes every hash and exits
  1 on a missing, altered or extra file. `api.FileRateLimiter` keeps the same
  caps in `limiter.db` beside the key file, one `BEGIN IMMEDIATE` transaction
  per decision, slots released on exit and expired after the scan ceiling when
  a process dies holding one; `--limiter memory|file` on `arbiter api serve`
  and `arbiter mcp --http` selects it through `api.configure_limiter`, both
  doors reading `api.LIMITER` at call time. Memory stays the single-process
  default. Proven by six spawned processes never exceeding a cap of two, and by
  a real bundle built, verified and installed with `--no-index` into a fresh
  virtualenv. `docs/bundle.md` is new; README, `docs/cli.md`,
  `docs/hosted-api.md`, `docs/mcp.md`, `NOTICE.md` and `docs/licensing.md` no
  longer call the bundle unbuilt or the shared limiter a gap.
- ARB-044 | Requirements use the `ARB` prefix. Two registries on `REQ` collided the
  moment Arbiter was vendored into OmniEngineering (each had reached number 035
  with a different requirement), so `omni requirement renumber --prefix ARB`
  renumbered every requirement keeping its number, wrote `id_aliases` so the
  commits and ledger entries that cite the old ids stay valid, and rewrote the
  governance text. Code, tests and git history are untouched; the gate resolves
  an old id through the aliases.
- ARB-043 | Synced the OmniEngineering workspace tooling as of 2026-10-09:
  `omni doctor` ends with a `Posture:` line and offers `--json`;
  `omni requirement complete` refuses while this branch's Arbiter report is
  missing, stale or red; `omni graph impact`, `omni graph findings` and
  `omni test run` exist here; the `completion.arbiter_gate` rule now runs
  `arbiter gate . --changed {base} --profile offline --baseline
  .arbiter/baseline.json --out arbiter-out/omni-gate --format json,sarif,pr-comment`,
  with `.arbiter/baseline.json` cut from a full scan and committed, so "new"
  means new since this baseline; the installed Arbiter version is recorded in
  `.ai/omni-version.json`; the recommended `completion.tests` example rule is
  present. The handbook for the whole loop lives in the OmniEngineering repository:
  https://github.com/paukennick/OmniEngineering/blob/main/docs/arbiter-integration.md.
- ARB-042 | Run history and the trend dashboard. Every `scan` and `gate`
  appends one line to `<out>/history.jsonl` (time, commit, system, profile,
  mode, grade, score, coverage, counts by severity, new high-or-above, gate
  result, duration, total findings; no evidence, no paths, no titles) unless
  `--no-history`; `arbiter dashboard [--history] [--out]` renders the trend as a
  self-contained HTML page (tiles, a score and coverage line chart, stacked
  severity bars per run, the runs table, an honest empty state). The README's
  "not yet built" list loses the dashboard.
- ARB-041 | A persistent per-file result cache for the file-local probes.
  `.arbiter/cache.json` (git-ignored, never scanned) stores per-file findings
  for `secrets`, `supply_chain`, `ast_metrics`, `house_rules_ast` and
  `authored`, keyed on the file bytes, the path, the configuration, Arbiter's
  own source and a per-probe context digest (the manifests, for `authored`).
  `--no-cache` bypasses it, `--cache-path` relocates it, `--verify-cache`
  re-runs a sample of hits and reports a divergence as
  `arbiter/assurance.cache-divergence` (high). Integrity invariant CI-13 proves
  every cacheable probe is file-local, the mutation tool proves a config change
  invalidates, and CI runs the self-gate with `--no-cache`. On the
  OmniEngineering tree with the adapters skipped (a machine under load from
  three parallel suites): cold 29.3 s wall (scan 4.4 s), warm 25.8 s wall
  (scan 1.1 s), identical findings.
- ARB-040 | Arbiter runs on Python 3.10. `tomllib` was the only 3.11 construct;
  one helper reads TOML with `tomllib` or the `tomli` fallback,
  `requires-python` is `>=3.10` with `tomli` as a conditional dependency, and
  pr-check gains an ubuntu 3.10 cell. OmniEngineering's CI now reads the floor
  from `pyproject.toml`, so its 3.10 cells run doctor and gate with a vendored
  Arbiter.
- ARB-039 | PR-native output. The gate names the findings that failed it
  (`failing_ids`); `--format annotations` prints GitHub workflow commands
  (`::error` for the gating findings, `::warning` for the rest, `::notice`
  for findings outside the change; titles and rule ids only, never evidence);
  `--github` (automatic under `GITHUB_ACTIONS=true`) adds the annotations and
  appends the pull-request comment to `$GITHUB_STEP_SUMMARY`; pr-check runs the
  self-gate in that mode and uploads `report.sarif` to code scanning
  (`continue-on-error`, so the gate's exit code stays the verdict); the
  composite action passes `--github`.
- ARB-038 | The `governance` probe and requirement attribution. A change-scoped
  probe (the new scope runs in partial and full mode) reads the OmniEngineering
  failure ledger: a changed file named as affected by an open or mitigated
  entry, with none of that entry's regression tests in the change, is
  `arbiter/governance.open-failure-untested`; a fixed entry whose regression
  test no longer exists is `arbiter/governance.regression-test-missing`; both
  medium, both n/a without a ledger. Findings inside the change carry
  `req:<ID>` tags from the commits since the base (the registry's own prefix),
  and the Markdown report and the pull-request comment gain a "By requirement" block.
- Closed ARB-024 with the pull-request check deliberately advisory. The
  Windows matrix has now run for real and found two defects (FAIL-035,
  FAIL-039), which was its purpose; the remaining criterion, a required
  status check on `main`, was weighed and declined: it only blocks a merge
  button a sole committer already reads, and the strict form would make
  every pull request stale after each nightly training commit. The
  decision is recorded on the requirement, to revisit when a second
  committer or automated merging arrives. (ARB-024)
- Arbiter now gates its own pull requests. `pr-check` installs the five
  external analyzers and the `api` extra on Linux, then runs `arbiter gate .`
  under this repository's `arbiter.yaml` after the suite, the integrity and
  mutation checks and the workspace gate; with no baseline every finding is
  new, so the policy is "no critical, no high". The first run was red for
  three reasons that were all Arbiter's: the `authored` probe reported the
  TLS-off patterns listed in its own module docstring as three high findings
  (`_blank_comments` blanked `#` comments but not Python docstrings; it now
  blanks both, as the import scanner always did), gitleaks reported fixture
  token shapes it found in a git-ignored graph file and in `__pycache__` as
  four critical secrets (adapter output is now held to the inventory's scope
  on the way in, `in_scope`, instead of teaching each tool its own exclusion
  flags), and semgrep's Python 3.6 compatibility rules fired on a project that
  requires 3.11 (suppressed by name, with the reason). The same job runs the
  sixteen hosted-API tests that had skipped on every pull request, and
  `omni gate` now runs `arbiter gate --changed` through OmniEngineering's new
  `command` validation type whenever `src/`, `tests/`, `tools/` or
  `arbiter.yaml` change. FAIL-036, FAIL-038. (ARB-032, ARB-034)
- Arbiter now installs alongside the OmniEngineering workspace. Upstream's
  `omni adopt --with-arbiter` and `omni arbiter install` (OmniEngineering
  ARB-033) pip-install `arbiter-eval[mcp]` from a checkout or the GitHub URL
  and write the wiring this repository had assembled by hand: the `arbiter`
  MCP server in `.mcp.json`, the `completion.arbiter_gate` command rule, a
  starter `arbiter.yaml`, and `arbiter-out/` in `.gitignore`. Rerunning it
  here reports every piece as already present, which is the check that the
  hand-made wiring and the generated one agree. `SETUP.md` and
  `RUNNING-ON-YOUR-OWN-CODE.md` document the path. (ARB-032)
- Cut the self-scan's noise at its five sources. `drift.doc-references-
  missing-file` asks git whether a path named in prose was ever tracked and
  reports a removed one at `info` as removed, which a changelog is right to
  mention, while a path that never existed stays `low` (24 findings, most in
  append-only history files). `assurance.blanket-suppression` no longer reads
  documentation, so a README explaining `# noqa` is not a blanket suppression.
  The `no-bare-except` house rule matched every `except` clause because its
  query had no predicate (89 findings, `except OSError:` among them); it now
  matches `Exception` and `BaseException` and leaves bare `except:` to ruff
  E722 (FAIL-037). OmniEngineering's `make_ai.py`, `omni_graph.py` and
  `omni_mcp.py` were scored as Arbiter's code (44 of 90 complexity findings);
  their quality and semgrep findings are suppressed with the reason recorded,
  so they stay in the report attributed to the tooling. And a probe that
  declares itself not applicable (the `interface` probe on a single
  repository) now leaves the coverage denominator and prints as `n/a`, while a
  probe that was prevented from running stays in it; claim invariant CI-12
  refuses a report that marks a prevented probe as not applicable, and
  `tools/integrity.py` proves it does. Coverage on this repository went from
  22% (grade withheld) to 99% with the analyzers installed. The Windows job,
  which has no tree-sitter, then showed the rule's one hole: a missing python
  package also read as not applicable, so the two AST probes left the
  denominator instead of lowering coverage. `Probe.prevented()` now reports a
  missing dependency separately and the engine records it as a prevented
  skip, like a missing binary; `applicable()` keeps only the cases where
  there is nothing to assess, and CI-12 names the new wording (FAIL-039).
  (ARB-033)
- `arbiter review --apply --ledger FILE` drafts an open OmniEngineering
  failure-ledger entry for every verdict recorded as a true positive: title,
  severity, rule, location and the finding id, nothing the scanner cannot
  know. Applying the same marks twice adds nothing, and the file passes
  `omni failure check`. The adjudication ledger and the failure ledger were
  parallel records of the same defects; now one feeds the other. (ARB-035)
- Added the Claude Code skill (`.claude/skills/arbiter/SKILL.md`): which
  surface to use for what, how to read a withheld grade and a not-assessed
  probe, how to adjudicate, and what the self-gate requires of a change.
  (ARB-036)
- Added the four commercial control packs README had listed as unbuilt: PCI
  DSS v4.0 (32 of 63 requirement sections), the HIPAA Security Rule (32 of
  59 standards and implementation specifications), SOC 2 TSC 2017 (29 of 61
  criteria) and CIS Controls v8 (41 of 153 safeguards). Each follows the
  government packs' convention of declaring the framework's full size and
  enumerating the scanner-relevant subset, names the human work that remains
  on every control, cites only rule ids that exist, and says that PCI DSS and
  the TSC are licensed documents whose identifiers must be verified against
  the licensed text before an audit package cites them. (ARB-037)
- Made the three setup-script tests run the bash that `PATH` resolves rather
  than the bare name `bash`. The first run of the Windows half of `pr-check`
  (it had never run: this was the workflow's first pull request) failed all
  three, because `CreateProcess` searches `System32` before `PATH` and
  `System32\bash.exe` is the WSL launcher, which prints an install prompt in
  UTF-16 and exits 1 whether or not Git Bash is installed. The one test that
  already went through `shutil.which("bash")` passed on the same runner,
  which is the whole diagnosis; the other three now do the same and skip, as
  it does, only when no bash exists at all. (ARB-024)
- Exempted Arbiter's own test material from the gate's secret check. The
  first change set to touch `tests/` tripped `data.privacy` on a planted PEM
  header: the check is right in general and wrong for a secret scanner's
  corpus, so `tests/**`, `fixtures/**` and `examples/**` are listed in the
  rulepack's ignore set; `src/` and `tools/` hold no literal key shape and
  stay scanned. (ARB-031)
- Re-synced the OmniEngineering workspace to its real upstream and adopted
  the tooling that arrived there since June. Arbiter's `omni` was a fork
  assembled from STEP-Migration copies (ARB-001) and closest to an upstream
  commit from 2026-06-23; `python omni update` now 3-way-merges against the
  ref recorded in `.ai/omni-version.json`, so later template changes are a
  command rather than a re-copy. What changed in the repository: the
  requirements registry and its archive moved to the upstream flat shape
  (`requirements-archive.json`; the categories-keyed form was arbiter-only
  and every new upstream command reads the flat one) and are now queried and
  changed only through `python omni requirement` -- never read or edited
  whole; the five non-Claude assistant shims, `.cursorignore` and the
  OmniEngineering `NOTICE`, `TRADEMARKS.md` and `LICENSES/` are installed,
  which closes the eight `omni doctor` errors this repo had carried as an
  accepted gap since ARB-001 (arbiter's own `NOTICE.md` and `LICENSE` are
  unchanged); `python omni gate` is enforced by `.githooks/pre-commit`, by the
  Claude Code Stop hook in `.claude/settings.json` (now tracked; the
  machine-specific Headroom wiring stays in `settings.local.json`) and by
  `pr-check.yml`, with `training/**` and `.arbiter/**` exempt so the nightly
  job's measurement commits are not blocked; `.ai/test-suites.json` registers
  the pytest suite and `tools/check_writeback.sh`; `.ai/graph-config.json`
  builds the code graph with `fixtures/`, `examples/`, `training/`,
  `.arbiter/` and the scan output directories kept out of the code layer, so
  a planted defect's symbols are never attributed to arbiter; `.mcp.json`
  registers `python omni mcp serve` beside `arbiter mcp`, and doctor now
  starts both servers for real. Three stale playbooks (`planning`, `testing`,
  `release`, `implementation`, `pre-implementation`) still carried
  STEP-Migration text -- `python3 app.py`, `validate_stacks.py`, "`omni map`
  is never run here" -- that contradicted this repo's own configuration;
  they are upstream's versions now. The adapters, `public-release.md`,
  `fallback-llm-rules.json` and `hci-ui-rules.json` authored for arbiter in
  ARB-028 were kept and merged. (ARB-031)
- Backfilled the failure ledger. `.ai/failures/failure-ledger.json` now
  records the 34 shipped defects that `CHANGELOG.md`, `.ai/project-context.md`
  and the requirement records already describe, FAIL-001 (the read cache
  serving stale bytes, ARB-007) through FAIL-034 (CRLF dropped when file
  reading moved to bytes, ARB-030), each with its root cause, the regression
  test that now catches it, and the rule that prevents a repeat. Six have no
  regression test and say so (`no_test_reason`); twelve have no fix commit
  because the clone is shallow and the 2026-09-12 history is not in it.
  `python omni graph why <path>` and `python omni failure search` read this
  ledger, and `python omni failure add` is how the next defect is recorded.
  (ARB-031)
- Ported three arbiter-only improvements upstream instead of keeping them as
  a fork, as OmniEngineering ARB-031: the `withdrawn` terminal status with
  `requirement archive --id` refusing live work; doctor's live check of every
  MCP server `.mcp.json` registers (it replaces the arbiter-specific
  `validate_mcp_server` from ARB-025 and checks both servers here);
  `exclude_code_globs` for the graph; and dated `###` sections under an
  `[Unreleased]` heading -- this file's format -- parsed as separate
  changelog entries. (ARB-031)

### 2026-09-16

- Stopped vendored and generated content from being scanned as first-party
  source, found by running Arbiter as a system over two real repositories
  (STEP-Migration + STEP_App) rather than the fixture corpus. Three
  compounding gaps together reported a third-party library's own source as a
  leaked private key and buried real findings under noise: `_pem_has_key_material`
  judged a PEM block real if any text after a `BEGIN` marker contained a colon
  anywhere in the next ~4000 characters — a check meant to allow encrypted-key
  preambles (`Proc-Type: 4,ENCRYPTED`) but satisfied by ordinary code and
  prose, so `ecdsa`'s own PEM-parsing source and packaging metadata were
  reported as committed keys; it now walks forward line by line and stops at
  the first line that is not base64 or a `Key: value` header, so surrounding
  text cannot manufacture a body. `inventory.classify()` hardcoded the literal
  name `cdk.out`, so a project using `CDK_OUTDIR` to redirect synth output to
  a renamed directory (`cdk.out.chk`, seen on the real system) fell through to
  `role=source`, and a branch-ordering bug meant even the unrenamed case would
  have classified a CDK stack's own synthesized `*.template.json` as
  `generated` rather than the `iac` role that exempts nothing — losing the
  ground-truth scanning `SKIP_DIRS` already says is deliberate; `IAC_HINTS` is
  now checked first, and both hint patterns accept a `cdk.out*` suffix.
  Bandit and checkov each walk `{workdir}` with their own traversal,
  independent of `SKIP_DIRS`, so both walked into a full vendored `.venv` and
  a Lambda asset bundle's dependencies regardless of role — bandit alone
  produced 129,379 raw findings and consumed 669s of its 900s budget on one
  such tree; both are now invoked with exclusions mirroring `SKIP_DIRS`, plus
  a CDK asset-bundle pattern for bandit (which gets no value from a synthesized
  template — it only reads Python) and a narrower one for checkov (which
  still needs the templates). A real system scan went from 3 critical / 6,011
  total findings to 0 critical / 1,121, with bandit's runtime dropping from
  669s to 4s. (ARB-026)
- Fixed two MCP tests reading pydantic alias names instead of the field
  names the installed SDK actually exposes (`.serverInfo`, `.isError`,
  `.inputSchema`) — those models take the camelCase alias on construction
  but expose the field back out as the snake_case Python attribute via an
  alias generator, confirmed directly from the installed SDK's own
  `model_fields`. Pre-existing on `main`, unrelated to ARB-026; found while
  running the full suite during that work and confirmed by reproducing it
  against plain `main` with ARB-026's changes stashed. (ARB-027)
- Registered Arbiter's own MCP server for this repo and made OmniEngineering
  verify it rather than take it on faith. `.mcp.json` now names an "arbiter"
  server that runs `arbiter mcp` (stdio), so an assistant working here can
  call `arbiter_scan`, `arbiter_gate` and `arbiter_review_queue` directly
  instead of shelling out to the CLI — the CLI stays the only way to run
  `arbiter review --apply`, since no MCP tool records a verdict. `omni
  doctor` gained `validate_mcp_server`, which imports `arbiter.mcp` and
  builds the server for real rather than grepping `.mcp.json`'s text for the
  right command, because the `mcp` SDK's own shape has moved under this
  project before (`23bc43e`, same day) and a static check would not have
  caught that. (ARB-025)

### 2026-09-17

- Started testing the scanner against reformulations of its own input, and
  started testing the code that measures the rules. An audit of every testing
  parameter and of the whole bug history found nineteen shipped defects
  collapsing into five recurring causes -- platform and path assumptions,
  encoding defaults, attribution propagation, heuristic proxies with edges
  nobody had hit yet, and bugs in the measurement code itself. Each had been
  fixed with a fixture for the one case that failed, which is why the missing
  `repo_id` had to be found three separate times. The new tests state the
  property instead: no finding anywhere may carry a backslash, an absolute or
  an escaping path; every finding in a multi-repo scan must name a repository
  that exists; every text read and write under `src/` must name its encoding;
  a credential must be found under every encoding and every syntactic carrier
  while placeholders are not reported; and calibrated confidence must not move
  when synthetic volume does. `tests/test_properties.py` generates inputs for
  the hand-rolled HCL reader rather than listing them, since every bug those
  functions have had was an edge of the grammar nobody thought to write down.
  (ARB-030)
- Fixed a fingerprint that was not stable across platforms. `Finding.fingerprint()`
  hashed `location.path` as written, so the same path with backslashes
  and with forward slashes were two identities for one finding.
  Adjudications are permanent and keyed by that
  fingerprint and `record()` refuses to re-adjudicate, so a verdict recorded on
  one platform could never match the same finding on another, and a baseline
  built on one reported every finding as new on the other. Normalization now
  happens at the `Location` boundary that all ~54 construction sites pass
  through, rather than at each of them -- which is the form that was tried and
  missed in ARB-014, ARB-015, ARB-016 and again in ARB-029. (ARB-030)
- Connected the nightly external-severity measurement to the scanner.
  `tools/calibrate_external.py` wrote its measured severities to
  `.arbiter/external-severity.json` while `learn.apply()` read a flat map
  inside `knowledge.json`, and no code path moved a value from one to the
  other -- so every nightly run since the tool was written had measured 818
  external checks and changed nothing, and the 178 severities in the live
  ledger came from a one-off step that no longer exists. Merging the current
  report yields 288 entries, 110 of them checks that had no measured severity
  at all. (ARB-030)
- Stopped reading every UTF-16 file as a binary blob. `inventory._is_binary`
  treated any NUL byte as binary, and UTF-16 puts one between every ASCII
  character, so a file an editor saved as "Unicode" was classified as data and
  never read -- a credential in one was invisible. A declared byte-order mark
  now means text. Headerless UTF-16 is still left as data on purpose: telling
  it from a real binary means guessing, and guessing wrong turns a binary into
  mojibake to scan. (ARB-030)
- Gave seven configuration reads an explicit encoding. `policy.py`, `ab.py`,
  `controls.py`, `probes.py` and `adapters.py` read YAML and TOML under the
  platform default codec, which on Windows is cp1252 and mis-decodes silently
  rather than raising. This is the same defect as ARB-009 and ARB-012, in
  seven call sites those fixes did not touch; the new test checks the property
  across `src/` rather than the two sites that were known. (ARB-030)
- Kept line endings translated when file reading moved to bytes. Reading a
  file as bytes rather than as text is what made the byte-order-mark check
  possible, and it silently dropped Python's newline translation with it: six
  secret formats stopped being detected on any CRLF file, because the rule
  that reads `name = value` ends at the end of the line and was seeing a
  trailing carriage return. The existing suite caught it. The first test
  written to cover it did not -- it used an AWS key, which is recognised by
  its own shape wherever it appears and so was found with or without the
  carriage return; `tools/mutate_tests.py` reported that test as passing with
  the defect present, which is the whole reason that tool exists. (ARB-030)
- Added `tools/mutate_tests.py`, which puts each of those defects back and
  requires the test named after it to fail. A test that has never failed is
  not evidence, and this is the same discipline `tools/integrity.py` already
  applies to the claim invariants. It runs in the pull-request check, so a
  regression test that stops testing anything fails the build. (ARB-030)
- Wrote `docs/testing-parameters.md`: every constant that governs a verdict,
  what it decides, and how firmly it is held -- derived, reasoned or
  arbitrary. Most are arbitrary, which is the honest grade for a number
  nobody has tested an alternative against. It also records two constants
  sharing the name `MIN_OBSERVATIONS` while meaning unrelated things, and
  three unreconciled ratio thresholds deciding closely related questions.
  Nothing was retuned: changing any of them changes published evidence.
  (ARB-030)


- Fixed four related gaps in how adapters read tool output and what they
  scan, found while continuing ARB-026's cross-repo scanning work. gitleaks
  has no write-JSON-to-stdout mode; `--report-path` took the conventional
  `-` literally, creating a file named `-` inside the repo being scanned (the
  adapter never set `cwd`, so `{workdir}` was the default) full of gitleaks'
  own JSON output for the next scan of any repo to read as content ---
  `Adapter.invoke()` now substitutes a private temp path for a
  `{report_file}` placeholder, reads it back once the process exits, and
  deletes it in a `finally` block. checkov and bandit hand back
  backslash-separated relative paths on Windows --- checkov's own output even
  starts with a bare leading backslash --- but every suppress-rule glob and
  `Location.path` in this codebase assumes `/`, so path-based rules silently
  matched zero Windows findings from either tool; adapter output is now
  normalized to forward slashes before that matching. ARB-026's own risk
  notes flagged that its bandit/checkov exclusions, mirroring
  `inventory.SKIP_DIRS`, left semgrep uncovered even though it walks
  `{workdir}` with the same independent traversal; semgrep now carries the
  same exclusion list plus a `cdk.out*` pattern, since none of its configured
  rulesets understand CloudFormation templates either. Finally, `SKIP_DIRS`
  is a fixed name list that can never cover a fresh `arbiter-out/` (or any
  other untracked, `.gitignore`-matched directory) left over from a previous
  run under whatever `--out` name was given that time; `walk_repo` now also
  excludes whatever `git ls-files --others --ignored --exclude-standard
  --directory` reports, the same plumbing `incremental.py` already uses for
  changed-file detection. (ARB-029)

### 2026-09-14

- Ran the test suite on Windows in CI, and made the Windows-only code paths
  testable everywhere. No automation had ever run on Windows: `pr-check.yml`
  was ubuntu-only and `train.yml` is a single ubuntu job. That is how ARB-006
  survived to be found by hand — a crash on *every* adapter timeout on Windows,
  because `os.killpg` does not exist there and the process-group path raised
  `AttributeError` while the analyzer ran on past the timeout meant to stop it.
  The pull-request check is now a matrix over ubuntu-latest and windows-latest
  with `fail-fast: false`, so a Linux failure cannot cancel the Windows job and
  hide the result the matrix was added for, and both jobs run `pytest -rs` so
  each platform's skips appear in the log with reasons rather than silently
  absent. The matrix went on the one-minute pull-request workflow rather than
  the two-hour nightly one, which would have cost four hours to learn the same
  thing. Separately, `_kill_tree` is unreachable on a machine that has process
  groups, so four of the seven new tests take the platform away instead of
  waiting for one: they delete `os.killpg` and assert the fallback kills a real
  process, uses `taskkill /T` rather than killing only the direct child and
  leaving the fanned-out workers alive, and still kills the process when
  `taskkill` is absent or refuses. `tools/integrity.py` was verified on Windows
  before CI was made to depend on it — 354,294 reports enumerated, 0 integrity
  failures. 410 tests to 417. (ARB-024)

- Gave the nightly training job an owned contract and a preflight. The job
  produces the project's only accumulating evidence and had never been covered
  by a requirement; four defects were repaired in it on 2026-09-13 as unplanned
  work, including one where `git add -A .arbiter training` matched a gitignore
  entry, exited 1, and failed the job two seconds after a fifty-five-minute
  cycle had finished — so run 1 measured everything and committed nothing. The
  five accumulating files (`.arbiter/knowledge.json`,
  `.arbiter/external-severity.json`, `training/WORKLIST.md`,
  `training/fix-pairs.json`, `training/disagreements.json`) are now named in one
  place, `tools/check_writeback.sh` asserts none is ignored and dry-runs the
  exact `git add` the job ends with, and both CI and a laptop run it *before*
  the cycle rather than discovering the problem after the work. The push now
  rebases first, because losing a race to another commit is the same lost night
  by a different route. Four tests cover it, including one that reproduces the
  original gitignore arrangement in a temporary repository and asserts the check
  refuses it. (ARB-023)

- Made adjudication require a terminal. `arbiter feedback` and
  `arbiter review --interactive` now check `stdin.isatty()` and refuse when
  nothing is typing, so a piped script, a CI step or an agent shelling out
  cannot write a permanent verdict by accident into a ledger that refuses to
  re-adjudicate it. A deliberate batch import stays possible: `feedback --batch`
  records the entry point `feedback-batch`, and `review --apply` already
  recorded `review-apply`. Both join `import` in a new
  `NON_INTERACTIVE_ENTRY_POINTS`, which is the set worth filtering on later.
  `review --interactive` has no override, because a keypress interface driven
  by something that is not a keyboard is a batch import under another name and
  `--apply` already is one. This is a guard against accident and not proof of
  personhood — anything determined allocates a pseudo-terminal — and
  `docs/calibration.md` says so in those words. (ARB-021)

- Made adapter scope a declaration instead of an inheritance, and said out loud
  what a partial scan does not run. All five external analyzers (ruff, bandit,
  checkov, semgrep, gitleaks) were constructed without a `scope` argument, so
  they took `Probe`'s `repo` default. The value was right; nobody had chosen
  it. Each manifest now carries `scope = "repo"` with the reason, `load_adapter`
  refuses a scope that is neither `file` nor `repo`, and a test fails if a
  manifest omits the key or claims `file` — which would assert a subset-exactness
  nobody has measured. The skip reason was also wrong: every repo-scoped probe
  printed "this check reads relationships between files", true of the native
  probes and false of an external analyzer, which is held back only because it
  is unmeasured. `Probe.scope_reason` now carries the real one. `docs/ci.md` and
  `RUNNING-ON-YOUR-OWN-CODE.md` state plainly that a pull-request gate runs
  Arbiter's own probes and no third-party analyzer at all. Nothing about what
  runs changed. (ARB-022)

- Gave every adjudication verdict a name. `learn.record()` stored a bare string
  — `"true_positive"` or `"false_positive:note"` — with no reviewer, no
  per-verdict timestamp and no record of which command it arrived through; only
  the rule carried `first_seen`/`last_seen`. Because the ledger refuses to
  re-adjudicate a fingerprint, a wrong mark was permanent *and* anonymous, so
  there was no way to work out whose marks to distrust. Verdicts are now a
  `Verdict` record carrying verdict, reviewer, timestamp and entry point.
  `reviewer` is keyword-only with no default, so no caller can record a verdict
  without saying who is answerable for it, and the CLI resolves it from
  `--reviewer` or `git config user.email` and refuses when it has neither
  rather than inventing one. This is attribution, not authentication — it makes
  a bad batch findable and is not evidence that a person produced the mark.
  Schema 1 ledgers migrate with the reviewer `unattributed`; the verdicts and
  counts survive and no plausible name is invented for them. `KNOWLEDGE_VERSION`
  is now 2, and `Knowledge.from_dict` returns a current-schema object rather
  than carrying the file's old version number forward — a migrated ledger was
  otherwise saved still declaring schema 1. The committed
  `.arbiter/knowledge.json` moved from `k:0f258c4ce717` to `k:a37f38560140`
  with no evidence change, so any scan pinning the old hash must be re-pinned.
  Merging alongside training run 2 moved it again, to `k:2e88d7a07351`; that
  second move carries the run's evidence and this one does not. (ARB-020)

### 2026-09-13

- Retired the stale one-session instructions in `HANDOFF.md`. It now routes a
  fresh session to the maintained context and requirements registry, records
  ARB-018 as the dependency root for ARB-019 and ARB-010, and leaves ARB-005
  explicitly with counsel instead of claiming adjudication is the project's
  only blocker. After the rebased implementation passed all 399 Linux tests
  with the `api` and `mcp` extras installed (396 passed, 3 skipped), the three
  requirements were archived in dependency order. (ARB-010, ARB-018, ARB-019)
- Removed the plaintext listener entirely. `--behind-proxy` bound a bare socket
  on loopback and treated `X-Forwarded-Proto: https` as proof the request had
  been secure earlier in its life; that is a header any client can invent and,
  more to the point, a socket carrying API keys and somebody's source in clear.
  Both surfaces now require `--cert` and `--key` — there is no arrangement in
  which a proxy removes that, because the hop from the proxy is a socket too.
  `require_tls` takes only the scheme the socket actually spoke, nothing reads
  the forwarded header, and a test tokenizes `api.py` and `mcp.py` to fail if
  either name comes back. `deploy/` follows: a one-shot `certs` service issues
  the backend a self-signed certificate for `arbiter.internal`, Caddy dials
  `https://127.0.0.1:8443` and verifies against exactly that certificate rather
  than skipping the check, and the container health check does the same instead
  of faking a forwarded header. (ARB-010, ARB-018)
- Fixed the nightly training job, which had never once written its results back.
  `git add -A .arbiter training` matched `.gitignore`'s `training/`, git exited 1,
  and the step's `bash -e` failed the job two seconds after a 55-minute cycle
  finished successfully — so run 1 measured everything and committed nothing.
  `training/` now ignores its contents rather than itself, and the three files
  that accumulate (`WORKLIST.md`, `fix-pairs.json`, `disagreements.json`) are
  tracked while the per-run stamped logs stay out. (No requirement covers the
  nightly training job — the four entries below are unplanned repair, and the
  gap is itself worth a requirement.)
- Fixed the same job silently degrading the handoff it exists to produce. Its
  final step read `/tmp/corpus-out/summary.json` and `/tmp/discriminate.json`,
  which nothing writes, so every night it overwrote the complete `WORKLIST.md`
  that `train_cycle.sh` had just written with one headed "Incomplete: no results
  found for corpus, discrimination". It now reads the newest stamped files under
  `training/`, and still runs on failure so a cycle that dies early leaves a
  handoff from what did finish.
- Clone the practice repositories 300 commits deep instead of 1, and deepen the
  ones already on disk. `fixpairs.py` skips a shallow clone, so fix-pair mining
  had been reporting "0 candidate fix pairs from 41 repositories in 0s" — every
  repository skipped, every night. Those pairs are the only ground truth in the
  system Arbiter did not generate itself, so the cost in clone size is worth
  paying. `train_cycle.sh` now runs `fetch_corpus.sh` every cycle rather than
  only when the corpus is missing, because that is what catches up a cached
  clone; the script is idempotent and records the depth it reached.
- Restored `.arbiter/knowledge.json` from the artifact of the failed run: 27
  rules with measurements where the committed copy had 15, 55,075 planted faults
  against 44,000, and the 12 additional rules all previously unmeasured. Human
  adjudications are 0 in both copies, so nothing a person decided was touched.
  `training/disagreements.json` came back with it — 317 contested findings where
  exactly one of two analyzers is wrong.
- Installed the `api` extra and put the assembled application under test for the
  first time: thirteen tests now go through a real request, twelve of them new —
  missing, unknown and revoked keys, the plaintext refusal and the forwarded
  header that cannot override it, the HSTS header on refusals as well as successes, an oversized upload, a networked
  profile, the hourly cap and its `Retry-After`, an end-to-end scan, and the
  audit line for a served request and a refused one. The client dependency
  (`httpx2`, which starlette's `TestClient` now requires) is recorded in the
  `dev` extra so these run rather than skip. (ARB-018)
- Fixed two defects those tests found, both of which would have met the first
  tester. `from __future__ import annotations` makes every annotation in
  `api.py` a string, and FastAPI resolves them against the module's globals —
  where neither `UploadFile` nor the `ReviewRequest` body model could be found,
  because both are local to `create_app` by design. Every upload request failed
  inside body validation, and `/v1/review-queue` read its body as a query
  parameter and rejected all JSON. Both names are now published to the module
  when the app is built. (ARB-018)
- A report with nothing left to review now comes back as an empty queue instead
  of an error. `arbiter review` exits 0 and writes no file when a report holds
  no findings or every finding has already been adjudicated; `service.review_queue`
  treated the missing file as a failure, so a caller with a clean report — the
  good outcome — got a 400 naming a server temporary directory. (ARB-018)
- Gave the hosted API a client, in `src/arbiter/client.py` and the new `arbiter
  remote` command group. Until now the service had no caller half: every CLI
  command either ran the scanner locally or ran it on the server, and the
  documented way to reach a hosted instance was to assemble multipart uploads by
  hand. `arbiter remote scan .` packs the directory, sends it, and renders the
  answer through the same functions `arbiter scan` uses, so `--out`, `--format`
  and `--limit` mean what they always meant — with none of the five analyzers,
  the grammar pack or the rule engine installed on the caller's machine.
  `remote gate` exits non-zero on a failed gate and deliberately takes no
  `--out`, because `/v1/gate` answers with the verdict and the claim ledger
  rather than a report. `remote health` needs no key, so somebody can see what a
  server offers before asking for access to it. Settings resolve flags first,
  then `ARBITER_SERVER` and `ARBITER_API_KEY`, then `~/.arbiter/client.json`, so
  a one-off `--server` cannot lose to a stale file. The transport is `urllib`
  from the standard library rather than `requests` or `httpx`: a plain install
  still depends on PyYAML alone. (ARB-019)
- Three of the client's refusals happen before anything leaves the machine. An
  `http://` address is rejected outright — the server refuses plaintext too, but
  its refusal arrives after the key has already crossed the network in the
  clear. Certificate verification cannot be switched off: `--cacert` adds a root
  to trust, nothing subtracts one, and a test asserts `CERT_NONE`,
  `check_hostname = False` and `_create_unverified` appear nowhere in the module.
  An oversized target is refused against the cap `/v1/health` publishes, instead
  of uploading for two minutes to earn a 413. The archive also skips
  `inventory.SKIP_DIRS`, so `.git`, `node_modules` and `.venv` never leave the
  caller's disk — source that never left is source nobody has to be trusted
  with. No client call records an adjudication verdict, because no endpoint
  does. 11 tests cover this, and the end-to-end ones route the client's own
  `urllib` through the real application, so the multipart field name, the query
  string and the `X-API-Key` header are proven against the endpoints rather than
  assumed to match. Suite: 381 passed, 3 skipped. (ARB-019)
- Amended ARB-010 and added ARB-019. ARB-010 had justified the MCP server as
  having been "chosen over a hosted API"; that rationale is withdrawn, since
  both now exist, and the requirement gains a multi-user obligation — an HTTP
  transport that authenticates every call against the same key store as the
  hosted API, with the resolved caller reaching `dispatch` so the rate limiter
  and the audit line apply per key, while stdio keeps working unauthenticated
  for the single-user local case. ARB-019 is the client: a CLI that reaches a
  hosted Arbiter, adding no runtime dependency beyond PyYAML. (ARB-010, ARB-019)
- Built the MCP server's second transport, so more than one person can use it:
  `arbiter mcp --http` serves the same three tools over HTTPS, where `arbiter
  mcp` still serves one local agent over stdio. Every HTTP call carries a key
  from the same file `arbiter api key add` writes and `/v1/scan` reads —
  `Authorization: Bearer` or `X-API-Key`, both accepted — so access is granted
  and revoked in one place for both front doors, and a revoked key stops working
  on the next request to either. The resolved caller reaches `dispatch`, which
  takes the same per-key concurrency slot the API takes and writes the same
  audit line (`mcp_scan`, `mcp_gate`, `mcp_review_queue`, with the key id, the
  user, the status and the duration), so a key's budget is one budget rather
  than one per surface. Plaintext is refused with `426` and every response
  carries HSTS, exactly as on the API. Stdio stays unauthenticated, because
  whoever started that subprocess already holds the privileges it runs with.
  (ARB-010)
- Confined every path argument on that transport, which is the thing that made
  it safe to expose at all. These tools take `target` and `output_dir` as paths
  **on the server**: right for a local agent, and for a remote caller an
  arbitrary file read with a scanner attached, since `target: "/etc"` would come
  back as findings quoting what is in there. So `--http` refuses to start
  without `--root`, and rewrites every path to sit beneath `root/<key id>` —
  one directory per key, so callers are separated from each other and not merely
  from the rest of the disk. Relative paths are joined onto that directory,
  absolute and `..` escapes are refused by `service.resolve_within`, which
  resolves symlinks before comparing. The arguments treated this way are listed
  in `mcp.PATH_ARGUMENTS`, and a test fails if a tool grows a path argument that
  is not in the list, because that one would be unconfined and silently so.
  (ARB-010)
- Raised the SDK pin from `mcp>=1.0` to `mcp>=2.2`, and found while doing it
  that `arbiter mcp` had been broken against anything 2.x: the server API moved
  handlers from decorators (`@server.list_tools()`) to constructor arguments, so
  the stdio server raised `AttributeError` at startup under the old pin's own
  range. `_build_server` now builds both transports the one way, so they cannot
  drift in what they expose, and a test constructs every entry of `TOOLS` as an
  SDK `Tool` — the schemas are plain data precisely so tests can read them
  without the SDK, which is also why nothing noticed when the SDK renamed the
  field they map to. A tool that refuses is now returned with `is_error` set
  rather than as ordinary text an agent would read as a result. 14 tests cover
  the transport. Suite: 395 passed, 3 skipped. (ARB-010)
- Wrote `docs/mcp.md`, which ARB-010 has required all along and which did not
  exist: the tool surface, which transport needs a key, the path confinement and
  why, and the one flag the proxy arrangement needs. The transport's
  DNS-rebinding guard allows loopback names only until it is told otherwise,
  while a proxy forwards the public hostname — so every real request comes back
  `421 Invalid Host header` unless `--allowed-host` names it. That cannot be
  detected from inside, so it is documented as the first thing to check when
  every call fails and none of them reaches the audit log. The README's documentation
  table was also missing `hosted-api.md`, `mcp.md`, `pilot-runbook.md`,
  `pilot-terms.md` and `licensing.md`; all five are listed now. (ARB-010)

### 2026-09-12

- Built the hosted API over that service layer, in `src/arbiter/api.py`. Access
  is distributed by hand: `arbiter api key add --user "..."` mints one key for
  one user, prints it once and stores only its SHA-256 hash, so the key file is
  not a credential store; `key list` and `key revoke` complete the set. A key is
  scoped to a user and nothing else, and one user holds at most one live key —
  minting over a live key is refused unless `--replace` is passed, which revokes
  the old one in the same command, so a shared or half-rotated key cannot arise
  quietly. `POST /v1/scan`, `/v1/gate` and `/v1/review-queue` take an uploaded
  archive — never a repository credential — and `GET /v1/health` needs no key.
  Nothing is retained: the workspace is deleted when the request ends and server
  paths are withheld from responses. Uploads are capped at 100 MB and refused
  before extraction. FastAPI and uvicorn are an optional extra imported only
  inside `create_app`, so a plain install still depends on PyYAML alone.
  `arbiter mcp` now runs the MCP server. (ARB-018)
- Raised the per-key request limit from 30 an hour to 120, and published every
  limit on `GET /v1/health` alongside `"free": true`. Nobody is charged and the
  service is for people testing it, so a limit anybody can feel during normal
  use is a restriction dressed up as capacity. The concurrency caps are
  unchanged, because those are what decide whether the machine stays up.
  Reworded the access and terms documents to say plainly that it is free, that
  there is nothing to apply for, and that nobody is asked what they intend to
  scan; issuing keys by hand is how access works in the absence of an identity
  system, not a vetting step. (ARB-018)
- Added the pilot deployment in `deploy/`: a Dockerfile that installs Arbiter
  with the `api` and `tools` extras into a virtualenv and copies it into a clean
  image running as uid 10001, a `compose.yaml` pairing it with Caddy, and a
  `Caddyfile` carrying the hostname, the ACME contact and a 110 MB body limit.
  A one-shot `certs` service gives Arbiter its own certificate so the hop from
  Caddy is HTTPS as well, and Caddy verifies against that certificate rather
  than skipping the check; Arbiter also shares Caddy's network namespace, so
  only Caddy can reach the port at all. The container is read-only, capability-free, `no-new-privileges`, and
  capped at 3 GB, 2 CPUs and 512 processes, because the analyzers parse
  attacker-chosen files even though nothing from an upload is executed. The
  image must stay private: running semgrep conveys no copy, but publishing the
  image would. (ARB-018, ARB-005)
- Capped concurrent scans for the whole server at 4, not just 2 per key, since
  the per-key limit multiplies by the number of testers and five of them at once
  would be ten analyzer runs on one machine. A caller over their own share is
  told that rather than told the service is busy. (ARB-018)
- Added a request log: one JSON line per request to `~/.arbiter/audit.log`
  (`--audit`, `ARBITER_AUDIT`, or `--no-audit` to keep nothing), holding the key
  id, the user, the operation, the status, the bytes uploaded and the elapsed
  time — and nothing about the code, because a log that quoted findings would
  rebuild on disk what the request path deletes. Failures and refused keys are
  logged too, the latter without writing the rejected key down. A log write that
  fails complains on stderr rather than failing the scan. (ARB-018)
- Documented how to run the pilot in `docs/pilot-runbook.md` — container with a
  memory limit, TLS-terminating proxy with a body limit, one key per tester,
  what to read in the log — and what to tell a tester about their code in
  `docs/pilot-terms.md`, which is a draft pending counsel. (ARB-018, ARB-005)
- Made TLS mandatory on the hosted API, with no plaintext mode. Requests carry
  an API key and a copy of somebody's source, so `serve` refuses to start
  without `--cert`/`--key`, and refuses individual plaintext requests with
  `426 Upgrade Required`. Direct TLS
  offers forward-secret AEAD ciphers only, which leaves nothing a TLS 1.0 or 1.1
  client can negotiate — a hard version floor is not assertable from here,
  because uvicorn builds its own SSL context, so it comes from the platform
  policy or from a terminating proxy. Every response carries a two-year
  `Strict-Transport-Security` header, and the default port is now 8443.
  (ARB-018)
- Made an API key a limited grant rather than a permanent one, before any key is
  handed out. Keys now expire after 90 days by default (`--expires-days`, or
  `--no-expiry` for a deliberate permanent one), a single key is capped at 30
  requests an hour and 2 concurrent scans, and exceeding either returns `429`
  with `Retry-After`. Limits are per key, so one recipient cannot exhaust
  another's. Expired, revoked and unknown keys share one `401` message, because
  distinguishing them would confirm that a guessed key had once existed. The
  counts live in one process's memory, which bounds this to a single-machine
  deployment — recorded in `docs/hosted-api.md` rather than implied. (ARB-018)
- Began the move to a hosted API, reversing the decision to expose Arbiter only
  over MCP. MCP relocates the installation rather than removing it, and "no
  local install" was the requirement. Both surfaces now call one
  transport-neutral service layer, `src/arbiter/service.py`, which owns the
  containment: workspaces created outside the server tree with `source/` and
  `output/` as siblings and removed when the scan ends; uploaded archives
  refused whole if any member is absolute, traverses upward, is a link or a
  device node, or breaches the size and count bounds; `connected` and `audit`
  refused unless an operator explicitly allows the network; and no operation
  that records an adjudication verdict, asserted by test rather than convention.
  `docs/hosted-api.md` holds the design; the HTTP surface is not built and the
  framework is deliberately unchosen. The licensing objection recorded against
  hosting was wrong and is corrected in `docs/licensing.md` — LGPL-2.1
  obligations attach to conveying a copy, so L-6 does not gate a hosted service.
  (ARB-018, ARB-010)
- Restructured the documentation. `README.md` is now an overview — what Arbiter
  is, its capabilities, administration, versioning and licensing — and the
  granular material moved into `docs/` split by category: `architecture.md`,
  `cli.md`, `configuration.md`, `probes.md`, `systems.md`, `compliance.md`,
  `evidence.md`, `calibration.md`, `ab-testing.md` and `ci.md`. `SETUP.md` is
  now an installation guide; its training-operations content moved to
  `docs/ci.md`. (ARB-002)
- Settled the corpus composition figures. The docs quoted 42 repositories in one
  place and 39 in another, and the population table mixed a tuned-only count for
  the vulnerable population with full counts for the other two. `tools/corpus.py`
  is authoritative — 41 repositories, 5 held out, 36 tuned — and a new
  `--counts` flag recomputes that without the corpus cloned, so the numbers stop
  drifting. Stack gaps are now reported in both directions. (ARB-003)
- Bounded requirement registry growth. Completed and withdrawn requirements are
  swept into `.ai/requirements/archive.json`, which no context profile loads, by
  `python omni requirement archive`. IDs are allocated across both files and are
  never reused; `omni doctor` errors if they ever collide. The active registry is
  read into every session, so it is a working set, not a history. (ARB-004)
- Wrote up the licensing requirements in `docs/licensing.md`: eight requirements
  covering the operative grant, copyright ownership, ownership of scan output and
  the redistribution review that blocks the air-gapped bundle. `NOTICE.md` now
  records every third-party component and whether it is redistributed. The
  `LICENSE` file itself remains outstanding and needs counsel. (ARB-005)
- Made adapter timeouts stop the analyzer on Windows. `Adapter._kill_group`
  named `signal.SIGKILL`, which does not exist there, so every timeout raised
  `AttributeError` out of the cleanup path. Windows also has no `os.killpg`, so
  signalling the direct child alone left the fanned-out workers running — the
  exact failure the process-group path exists to prevent. Timeouts now fall back
  to `taskkill /T /F`, which walks the child tree from the parent PID. POSIX
  behaviour is unchanged. (ARB-006)
- Fixed the drift rule that reports a file named in prose as missing. It
  normalized the path with `lstrip("./")`, which strips a character set rather
  than a prefix, so `` `.ai/context-brief.md` `` collapsed to
  `ai/context-brief.md` and matched nothing on disk — every dotted path in the
  repository read as missing. It now uses `_normalize_relative()`, the helper
  the sibling link rule already used, and skips paths that escape the
  repository root. Measured by scanning Arbiter with itself before and after,
  the rule goes from 187 findings to 28. (ARB-008)
- Made Arbiter write and read its own artifacts as UTF-8 rather than the
  platform default. Thirteen `write_text()` calls omitted `encoding=`, so on
  Windows the HTML, markdown, PR-comment and review-queue renderings were
  written in the console codepage; a generated `review-queue.md` here was
  undecodable as UTF-8. Reports are meant to travel into accreditation packages
  and pull requests, so they cross machines. Files belonging to the scanned
  repository are untouched — those are decoded with `errors="replace"` by
  design. (ARB-009)
- Put the licensing position in writing as far as it can go without counsel.
  `LICENSE` now exists, asserting copyright to Nicholas J Pauken, reserving all
  rights, assigning ownership of scan output to the user, and disclaiming any
  grant over the adapted third-party analyzers. It says plainly that it is an
  interim notice and not the operative grant. `pyproject.toml` references it by
  file rather than declaring the bare string `Proprietary`, and names the
  author. `CONTRIBUTING.md` states that external contributions are not
  accepted, which L-8 requires be settled before a patch is taken rather than
  after. This closes L-2 and L-8, and L-1 provisionally; ARB-005 stays open
  because L-3 follows from decisions only the owner can make and the L-6
  redistribution review is untouched. (ARB-005)
- Stopped a scan from reading its own output. `--out` defaults to
  `arbiter-out`, a relative path inside the tree being scanned, and the walk
  knew nothing about it, so every run after the first reported on the previous
  run's rendering. Measured here: 28 of 101 unsuppressed findings were located
  inside `arbiter-out` — 27.7% — and 24 of those were
  `assurance.blanket-suppression`, the rule that was about to be adjudicated,
  inflated by a report that is naturally full of the term it searches for.
  `run_scan` now takes `out_dir` and `walk_repo` skips it. Two consecutive
  scans into the default path produce identical findings, none of them inside
  the output directory. (ARB-011)
- Read files belonging to the scanned repository as UTF-8. Nine call sites used
  `read_text(errors="replace")` with no encoding, so the codec was the platform
  default — cp1252 on Windows. `errors=` governs what happens on failure, and
  cp1252 decodes almost every byte without failing, so it never errored, it
  silently produced wrong characters: an em dash in a scanned file reached a
  generated review queue as `â€”`. ARB-009 fixed Arbiter's own artifacts and
  deliberately excluded these sites, reasoning that `errors="replace"` was the
  design intent. That reasoning was wrong — the intent is never crashing on a
  target file, which `encoding="utf-8"` preserves. (ARB-012)
- Checked documented paths against disk rather than only against the walked
  inventory. Directories in `SKIP_DIRS` never enter the inventory, so tracked
  files under `.arbiter` read as missing: 8 of 31 doc-drift findings here.
  Whether a documented file exists is a question about disk, not about what the
  probes were shown. The sibling broken-link rule had the identical defect
  fifteen lines away and got the same fix. `.arbiter/baseline.json` still
  reports, correctly — it is documented but genuinely absent. The other 22
  findings are untouched, and `omni doctor` independently confirms them.
  (ARB-013)
- Gave doc-drift findings a repository in their `Location`. `Location.short()`
  builds its `repo:path` prefix from the Location rather than the Finding, and
  `doc_drift` set the id on the Finding only, so all 330 drift findings in a
  36-repository corpus scan rendered as bare paths. Two of those repositories
  each contain a python/ tree, so a line naming a README under it identified no
  repository a reader could open. (Those paths are deliberately not backticked:
  they belong to another repository, and backticking them here manufactures the
  very drift finding this entry is about.)
  (ARB-014)
- Made the review queue name the repository for every probe, not just for
  doc-drift. The same defect ran wider than one probe: 3,227 of 3,564 findings
  in that scan carried a blank `Location.repo_id` — `supply_chain` 0 of 1,817,
  `secrets` 0 of 377, `resource_policy` 7 of 1,040 — while `assurance` sets it
  at every construction site. The `Finding` carries the id in all 3,564 cases,
  so `review.where()` qualifies the path from there and both the markdown and
  HTML front ends use it, rather than adjudication waiting on some twenty
  construction sites across eight probes, several of them repo-level or
  cross-repo and needing judgement rather than a mechanical pass. Those sites
  are still wrong, so SARIF, HTML and console output remain unqualified.
  (ARB-015)
- Named the repository at every probe construction site, closing the residual
  risk ARB-015 recorded rather than fixed. Eighteen of the twenty sites took the
  id already in scope on the enclosing finding. Two did not, and they are why
  the renderer went first. The `interface` rule for unused IAM grants collected
  service names into a set and discarded where each grant was written, so it
  could cite only a synthetic `iam:` string and attributed the finding to the
  alphabetically first infrastructure repository — the wrong one whenever the
  grant was not in it; it now keeps the grant's location the way the
  neighbouring collections already did. The two `house_rules` path rules matched
  against every path in the scan flattened into one set, which left them the
  only sites in the tree setting no repository on the finding at all, and let
  one repository's LICENSE answer the rule for every repository; both now ask
  per repository. Re-measured on the same 36-repository corpus: 3,564 findings
  before and after, of which 3,227 were unattributed before and none after, and
  no finding changed repository. (ARB-016)
- Made the review sampler spread across repositories, not only across files.
  Within a rule it ordered findings so that distinct files came first, which one
  repository satisfies on its own: of six queues drawn from the 36-repository
  corpus, `resource.k8s-no-security-context` took 19 of 20 from a single
  repository and all 20 from teaching material, which the corpus tooling states
  is useless as a false-positive measure. Twenty findings from one repository
  are largely one author, one generator and one set of conventions, so they
  answer whether the rule is right about that repository while the ledger
  records the answer as though it were about the rule. Selection now
  round-robins across repositories and keeps file spread as the secondary axis
  inside each. Re-measured on the same corpus: average distinct repositories per
  queue 6.0 to 12.2, largest single-repository share 64.2% to 30.0%, and the
  k8s-no-security-context queue from one population to all three. Population is
  deliberately not an input — that label lives in the corpus tooling, and a
  library that ranks findings must not import the test harness — so repository
  spread is the proxy. Spread also cannot exceed the pool:
  `resource.unencrypted-database` still draws 18 of 20 from terragoat, because
  only three repositories in the corpus produce that finding at all. (ARB-017)

Six commits (`8775017`…`54f9a13`) landed from an offline bundle without
changelog entries. Recorded here after the fact, written from their diffs.
(ARB-007)

- Added incremental scanning. `arbiter scan|gate --changed REF` reads only the
  files that differ from a ref plus uncommitted work, and `--only-files` takes
  an explicit list; both run through the new `src/arbiter/incremental.py`.
  Every probe now declares a `scope`. Seven are file-scoped — `secrets`,
  `resource_policy`, `ast_metrics`, `supply_chain`, `house_rules`,
  `house_rules_ast` and `authored` — and everything else keeps the conservative
  `repo` default and is recorded as not-assessed, with the partial scan named
  as the reason, rather than being run against a subset it cannot answer from.
  Selection always retains dependency manifests, lockfiles, CI workflows and
  Terraform, because those are the files a probe reasons about but does not
  report on. (ARB-007)
- Made a partial scan unable to describe the repository. Reports carry
  `scan_scope`; a partial one contributes an abstention naming the unread
  files, which flows into the gate claim, the grade and every dimension. The
  overall grade is withheld outright, "probe ran and found nothing" narrows to
  "found nothing in the files it was given", and finding density is measured
  against lines read rather than repository size. Invariant **CI-11** — a
  partial scan may make no complete-scope claim about the repository — is
  machine-checked alongside the other ten, with the coverage measurement and a
  failing gate as the two reasoned exceptions. A ref that does not resolve
  refuses the scan instead of quietly reading nothing. (ARB-007)
- Added nine provider-issued token rules: Stripe, OpenAI, Anthropic, Google,
  GitLab, npm, SendGrid and PyPI at critical, Slack incoming webhooks at high.
  The issuer assigns these prefixes, so the value is its own evidence and the
  symbol name is irrelevant — which is why `STRIPE_KEY = "sk_live_…"` passed
  the name-based heuristic untouched. Six patterns terminate in an explicit
  lookahead rather than `\b`, because the charset is base64url and a token
  ending in `-` has no word boundary after it. The injection harness gained
  matching positive and control generators, the controls covering test-mode
  keys, wrong lengths, placeholders, interpolations and `sk-` used as a slug.
  (ARB-007)
- Shipped the pull-request gate as a worked example: `examples/pull-request-gate/`
  holds a two-job workflow and its config, `RUNNING-ON-YOUR-OWN-CODE.md` walks
  through a first scan, a baseline, adjudication and then the gate, and
  `docs/ci.md` gained "Scanning only what changed" and "A ready-made workflow".
  Only the nightly full-scan job may refresh the baseline; refreshing it from a
  partial scan would forgive every finding in the files that scan did not read.
  (ARB-007)
- Tagged findings a change did not cause. In a partial scan, findings landing
  in a context file the diff never touched are tagged `outside-this-change` and
  counted on the console. They stay in the report — the baseline, not deletion,
  is what keeps them out of the gate. (ARB-007)
- Added three provider-neutral TLS rules: `database-allows-plaintext-connections`,
  `weak-tls-version` and `no-https-redirect`. Resource rules also gained
  `exclude_native` beside `exclude_providers`, because a control can be
  expressible on one resource of a provider and absent on another — Azure SQL
  enforces TLS with no property saying so, and a provider-wide exclusion is too
  coarse to express that. `aws_elb`/`aws_alb`, Azure app services and GCP HTTP
  proxies and App Engine versions are normalized into the resource graph so the
  new rules have something to match. (ARB-007)
- Fixed a read cache that could serve one file's bytes for another. `_READ_CACHE`
  keyed on path alone and depended on every caller clearing it between scans;
  the injection harness invokes probes directly and writes every generated case
  to the same path, so it was served stale text. The key is now
  `(path, st_mtime_ns, st_size)` with a bounded size, so the cache cannot be
  wrong regardless of who calls it. (ARB-007)
- Taught `tools/corpus.py` to weight what it counts. Per-repository output now
  carries severity-and-confidence weighted totals, weight by language and lines
  by language, and the held-out comparison is made per KLOC of the language the
  rules actually fire on rather than per repository line counted flat. (ARB-007)
- Restored the shebang and executable bits on the four `tools/*.sh` scripts; an
  earlier commit had indented `#!/usr/bin/env bash` by two spaces, which stops
  the kernel recognising the file as a script whatever the mode bit says.
  Added `HANDOFF.md`, a cold-read briefing for a session arriving without the
  history. (ARB-007)

- Adopted the OmniEngineering workspace: `.ai/` source-of-truth scaffold
  (rules, schemas, playbooks, checklists, SWEBOK knowledge pack), the
  repo-local `./omni` CLI (`make_ai.py`), a `CLAUDE.md` routing shim, and a
  requirements registry. (ARB-001)
- Installed Headroom for Claude Code at local scope: `.claude/settings.local.json`
  routes traffic through the Headroom proxy (`127.0.0.1:8787`) and registers
  its session hooks. The file is machine-specific and git-ignored. (ARB-001)
