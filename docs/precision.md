# Precision

How Arbiter measures, and works toward, the precision of the findings it
reports at gate-blocking confidence. This file has two halves: the tiers and
the per-rule ledger (which findings may block a gate, and on what evidence),
and the model verification pass (`## Model verification`), which supplies
the per-finding verdict the tiers read.

## Definition

**Precision** of a set of findings is the share of them that are true
positives: a real defect of the kind the rule claims, at the place it
claims, that a competent reviewer of that code would want fixed. A finding
that is technically accurate but harmless in context (a "SQL injection" on a
constant table name) is a false positive.

The **claim** Arbiter aims at is: *99% precision on the verified tier*,
measured as the Wilson 95% lower bound of the population-weighted precision
over **held-out** labels only. "99%" is stated only where that lower bound
is at least 0.99 — overall, and for each category (dimension, and each CWE)
on its own: a category's claim is made only where its own lower bound
reaches 0.99, never by borrowing from a better one.

What the labels are, stated plainly: there are no human labellers. Labels
come from two independent model labellers (different prompts; neither sees
Arbiter's reasoning, the verify pass's verdict or the other's answer) with a
third model adjudicating disagreements, and from external ground truth that
no model judges (OWASP Benchmark, CVE fix pairs, Juliet). That is weaker
than human labels. A claim built on it says "by independent model labels and
external ground truth", and a 50-finding human spot check is the step that
would turn it into a human-anchored claim.

The planted corpus (`fixtures/planted`) is a regression check, never part of
the claim: we wrote it.

## Tiers

Every finding in every report carries `tier` and a one-line `tier_reason`
(`src/arbiter/precision.py`):

| Tier | Meaning | Blocks a tiered gate |
|---|---|---|
| `verified` | the rule is declared verified **and** the ledger admits it **and** the model confirmed this finding (or the profile cannot ask a model and the rule's lower bound alone is ≥ 0.99) | yes |
| `advisory` | everything else: a rule on probation, a rule below its bar, a finding the model rejected or was unsure of, one it did not look at | no |
| `inferred` | found by a model (`provenance: inferred`) | only with `gate_on_inferred` and `tiers: [..., inferred]` |

The assignment, in order:

1. `provenance: inferred` → `inferred`.
2. The rule is not declared verified (rule metadata `tier: verified`, or
   `precision.verified_rules` in `arbiter.yaml`) → `advisory`, "rule on
   probation". New rules start here.
3. The ledger does not admit the rule (below) → `advisory`, with the bound
   and the count.
4. `verification.verdict` (from the verify pass, when it ran):
   `confirmed` → `verified`; `rejected` or `unsure` → `advisory`, with the
   model's reason.
5. No verdict, or `not_verified`: on a profile that cannot call a model
   (`precision.deterministic_profiles`, default `offline` and `ci`) the rule
   tier stands only if the rule's own lower bound is ≥ 0.99; otherwise
   `advisory`, "not model-verified". Mostly exact-signature rules
   (a private-key header, a provider-issued token prefix) can reach this.

Nothing is hidden or re-graded: an advisory critical finding is still a
critical finding in every report. The tier only decides whether it may
block.

**Gate.** `gate.tier_mode: legacy` (the default) is the severity gate as it
always was; every tier can fail it. `gate.tier_mode: tiered` reads only the
findings whose tier is in `gate.tiers` (default `[verified]`). With the
ledger shipped today, which has no labels, every finding is advisory, so a
tiered gate blocks nothing. That is why legacy stays the default: switching a
repository to `tiered` is a decision to make after the ledger is populated
and the guard is green, not before.

**Reports.** The console and markdown reports print a `tiers: N verified ·
M advisory · K inferred` line with the gate's tier mode, and each finding's
tier and reason. SARIF carries `tier`, `tier_reason` and `requirements` in
each result's properties and the tier counts in the invocation; under a
tiered gate an advisory or inferred result's level is `note` (an `error`
the gate ignores teaches people to ignore errors), and under legacy the
level stays the severity's, because every finding can block.

**Requirements.** Every finding also carries `requirements: [ARB-xxx]`, the
requirement(s) its rule was built or fixed under, and the JSON report has a
top-level `rules: [{id, requirements, tier}]` block for every rule that
fired; the OmniGraph reads both as EXTRACTED. The ids come from rule
metadata (`requirements:` on each pack rule, `packs/rules/native.yaml` for
rules coded in Python), proposed from the CHANGELOG, pack comments, the
requirement registry and the introducing commit by
`tools/rule_requirements.py` and reviewed by hand. They are distinct from
`req:` tags, which name the requirements the commits touching a file cite.

## Ledger

`tools/gold/precision.json`, shipped in the package as
`src/arbiter/packs/precision.json` so a gate can read it at scan time (a test
holds the two byte-identical). It is rebuilt from label files by
`tools/precision_ledger.py build` and never edited by hand.

Per rule:

| Key | |
|---|---|
| `tp`, `fp`, `unsure`, `n` | final verdicts (below); `n = tp + fp` |
| `held_out` | `{tp, fp, unsure}` over held-out labels only |
| `precision`, `wilson_lower`, `wilson_upper` | over all labels |
| `kappa`, `kappa_pairs`, `observed_agreement` | Cohen's κ between the first two labellers of each finding |
| `labellers`, `sources`, `dates` | who labelled, which corpora, first and last label date |
| `population`, `held_out_population` | findings of the rule in the runs its labels were drawn from, all and held-out (sums of its strata); `null` when any label's population is unknown |
| `population_unknown` | labelled findings whose stratum has no recorded population (0 when every one is known) |
| `strata` | one row per (source, language): `population`, `held_out_population`, and `tp`/`fp`/`unsure` all and held-out |
| `source_population` | the rule's population per source, all and held-out, including strata no label was drawn from |
| `weighted` | the stratified precision over all labels (below), with `pooled` for comparison; `null` when a population is unknown |
| `dimension`, `cwe` | the categories the claim is broken down by |
| `admitted`, `admitted_reason` | computed from the counts (below) |
| `fp_notes` | known false-positive patterns; the verify pass puts them in its prompt |
| `updated`, `snapshot_counts` | when the rule's counts last changed, and its planted / AI-corpus snapshot counts then |

**Admission.** A rule is admitted when, over all of its labels:

* `n ≥ 30` (`bar.rule_min_n`), and
* the Wilson 95% lower bound is ≥ 0.97 (`bar.rule_lower`), and
* where two labellers both labelled findings, κ ≥ 0.8 (`bar.kappa_min`);
  κ is undefined when both always gave the same answer (all TP), which
  does not block.

The 30 is a floor, not the real minimum: with no false positive at all,
Wilson needs 125 labels to reach 0.97 and 381 to reach 0.99 (the
rule-of-three figure of about 300 is a one-sided bound; this is two-sided).
One FP moves those to roughly 200 and 470.

**Tier claim** (`tier` in the ledger): over the rules that are declared
verified *and* admitted, held-out labels only, as a stratified estimate
weighted by population (next section). `claim` is true where the bound
`claim_lower` is ≥ 0.99 (`bar.tier_lower`). The same is computed per
category (the rule's dimension, and each of its CWEs).

### Population weights

A rule's labels are a sample, and not a proportional one: `label_models.py
--per-rule` and `label_sample.py` allocate by the square root of a stratum's
size or by a per-rule budget, so a quiet stratum is labelled far more
densely than a loud one. Pooling the labels would let the quiet stratum
speak for the loud one. The claim is about the findings Arbiter reports, so
each stratum is weighted back to the number of findings it was drawn from.

**Strata.** A stratum is (rule, source, language): the sampler's own
(rule, language) strata within one source run (`gold-model` is one gold
run; each ground-truth benchmark and the AI corpus are their own). Every
label record carries its stratum's population (`## Label format`,
`stratum_size` and the fields beside it), recorded at sampling time from
the population the sampler drew from. For the held-out claim the
population is the stratum's held-out part (`stratum_held_out_size`, the
findings in held-out repositories): the sample is a simple random sample
of the stratum, so its held-out labels are a random sample of that part.

**Estimate.** Over the strata h with at least one decided (tp/fp) held-out
label, with N_h held-out findings, n_h decided labels and p_h = tp_h / n_h:

    W_h = N_h / Σ N_h           p = Σ W_h p_h
    n_Kish = 1 / Σ (W_h² / n_h)
    V = Σ W_h² p_h (1 − p_h) / n_h       n_design = p (1 − p) / V
    n_eff = min(n_Kish, n_design)        (n_Kish when V = 0)
    [lower, upper] = Wilson(p · n_eff, n_eff)

`n_Kish` is the loss from unequal weights and stays defined when every
stratum is all-TP, which is exactly the case a 99% claim is made in. The
design-based `n_design` comes from the stratified variance and is the
smaller of the two when a heavy stratum is both thinly labelled and less
precise than the rest; taking the minimum keeps the bound from borrowing
confidence from the well-labelled strata. One stratum gives its plain
Wilson interval. There is no finite-population correction: the claim is
about the rule on code like the corpus, not about these particular
findings, so a census stratum (every finding labelled) still carries the
sampling error of its n_h.

**Coverage.** Held-out findings of a verified rule in strata with no
decided held-out label (a language the sample missed, or labels that were
all `unsure`) are not estimated. They count against the claim as if every
one were a false positive: `coverage` is the labelled strata's share of
the rules' held-out population (`source_population`), and the claim bound
is `claim_lower = coverage × lower`. Since the true precision is at least
`coverage × p_labelled`, the bound stays valid.

**Unknown populations are not guessed.** A stratum any of whose labels has
no recorded `stratum_size` is unknown as a whole. The rule's `population`
is `null`, `population_unknown` counts its labels, `show` lists it, and a
tier or category containing it has no estimate (`population_unknown` names
the rules) and no claim. The CI guard fails a declared-verified rule with
an unknown population. Admission (`n`, Wilson lower, κ) is per rule on its
own labels and does not need populations.

Two records of one stratum with different sizes are refused at build:
labels drawn from different runs belong under different `source` names.

**Backfill.** Labels written before 2026-10-23 (round 4) had no
population. `tools/label_populations.py backfill` recomputes it with no
model calls: `gold-model.jsonl` from the gold run its `--per-rule` sample
was drawn from (`tools/gold/results/2026-10-23-gold-report-relabel.json.gz`,
matched by repository and finding id, rule, language and split checked),
and the AI corpus, the CVE suspects and the ground-truth files as censuses
(every finding labelled, so a stratum's population is its records). It
writes only the population fields and refuses any other change; a label it
cannot match gets `stratum_size: null` and `sampling: "unknown: <why>"`.
`--check` fails when a file is out of date (a test holds it).

**Final verdict per finding.** Ground truth wins; then the adjudicator's
label; then the labellers' when every TP/FP among them agrees. A
disagreement nobody adjudicated is `unsure` and counts toward neither TP nor
FP. A finding is held out only when every label record for it says so.

**CI precision guard** (`tools/precision_guard.py check`, in pr-check): fails
when

1. a rule declared verified has no ledger entry, or its entry does not reach
   the bar;
2. an entry's `admitted` flag disagrees with its own counts (a hand edit);
3. a labelled rule's count in the `planted` or `ai-corpus` snapshot
   (`tools/gold/snapshots/`) is more than 20% above its `snapshot_counts`
   and the ledger entry is not newer than the snapshot: the rule now fires on
   code its labels never saw;
4. the ledger and its packaged copy differ;
5. a rule declared verified has labels with no recorded population
   (`population_unknown`), so the tier claim cannot weight it.

Each failure is seeded in `tests/test_precision_ledger.py`.

## Label format

What `tools/precision_ledger.py build` reads. Phase 1b's labellers write
this; anything that produces labels (another model, a ground-truth importer,
a person) writes the same.

**JSON lines** (`*.jsonl`), one label record per line; blank lines and lines
starting with `#` are skipped:

```json
{"finding_id": "gold-2026-10-22:f:3f9a0c1d2e4b", "rule_id": "arbiter/python.sql-injection", "label": "fp", "labeller": "claude-opus-5-5/prompt-a@v1", "source": "gold", "held_out": true, "role": "labeller", "date": "2026-10-22", "note": "table name is a module constant", "language": "python", "stratum_size": 41, "stratum_held_out_size": 12, "rule_population": 55, "rule_held_out_population": 15, "sampling": "stratified"}
```

Required:

| Key | |
|---|---|
| `finding_id` | stable id of the labelled finding, shared by every record about it. Use `<batch>:<Finding.id>` (the report's fingerprint), or `<batch>:<sample_id>` for a label_sample sheet |
| `rule_id` | the full rule id, `arbiter/<pack>.<rule>`; every record of one finding must agree |
| `label` | `tp`, `fp` or `unsure` (case-insensitive; `true`/`false` accepted) |
| `labeller` | who labelled: a model and prompt version (`claude-opus-5-5/prompt-a@v1`), a ground-truth source (`owasp-benchmark-1.2`), or a person |
| `source` | the corpus the finding came from: `gold`, `ai-corpus`, `owasp-java`, `cve-fixpairs`, `juliet`, ... |
| `held_out` | `true` only for the frozen held-out split that rule authors never look at; the claim reads only these |

Optional:

| Key | |
|---|---|
| `role` | `labeller` (default), `adjudicator` (decides a disagreement; wins over labellers) or `ground_truth` (published truth; wins over everything) |
| `date` | ISO date of the label |
| `note` | free text; on an `fp` label it is added to the rule's `fp_notes`, which the verify pass reads |
| `language`, `stratum_size` | the sampling stratum (with `rule_id` and `source`) and how many findings it held when the sample was drawn; required for a rule to be verified (`### Population weights`). `label_models.py`, the ground-truth writers and label_sample.py sheets write it |
| `stratum_held_out_size` | how many of the stratum's findings are in the held-out split |
| `rule_population`, `rule_held_out_population` | the rule's findings in the same run, all and held-out, including strata the sample did not reach (for `coverage`) |
| `sampling` | `stratified`, `census` (every finding labelled), or `unknown: <why>` from the backfill |
| `dimension`, `cwe` | categories, when the rule's own metadata does not carry them |

Rules for producers:

* Two labellers per finding, different `labeller` names; κ is computed over
  the first two (by name) that gave TP or FP.
* The verifier never labels: a `labeller` must not be the verify pass's
  model and prompt, and labels used to measure the verify pass must predate
  its verdicts.
* A finding never moves between held-out and not: if any record says
  `held_out: false`, the finding is not held out.

**label_sample.py sheets** (`.csv` or `.json`, the columns `sample_id`,
`rule`, `verdict`, `labeller`, `notes`, `language`, `stratum_size`) are read
too. A sheet has no finding id, source or split, so they are given on the
command line: `--sheet-batch NAME` (the finding is `NAME:sample_id`, so two
labellers' copies of one sheet pair up), `--sheet-source`, and
`--sheet-held-out`. A blank verdict is `unsure`; the labeller is the sheet's
`labeller` column, else the file name.

## Current numbers

As of 2026-10-23 (relabel at labeller `v3`): the ledger holds ground-truth
and `v3` model labels for 120 rules. **2 are admitted**:
`java.weak-random-for-secret` (218/218 ground truth, lower 0.983) and
`assurance.blanket-suppression` (125/125 `v3` model labels, lower 0.970,
exactly at the bar). No rule is declared verified, so every finding is
advisory. There is no verified-tier precision figure and no category
claim. `tools/precision_guard.py check` passes.

**Populations (round 4).** Every label the ledger reads now carries its
stratum's population (backfilled, none unknown). `java.weak-random-for-
secret` is one census stratum (OWASP Java, 218 findings, all held out), so
nothing changes for it. `assurance.blanket-suppression`'s population is
150 (Python 62, TypeScript 88), not the 125 labels it fell back to, and
its held-out population is 20 (18 labelled, all tp; weighted lower 0.824
on n_eff 18). Were both declared verified, the held-out tier estimate would
be 1.0 with a lower bound of 0.984 on n_eff 235.8 over 238 held-out
findings (coverage 1.0): no 99% claim. The previous method gave 0.966 on
n_eff 108, because it weighted each rule's held-out labels by its whole
population (dev and held-out) and the label count stood in for that.

**Labeller calibration** is how far a model label can be trusted. It is
measured blind against truth the labellers never saw, and it bounds every
model-labelled number. The Wilson 95% lower bound applies to the
labellers' `tp` calls (`tools/gold/results/2026-10-23-calibration.md`,
`-calibration-3.md`, `-calibration-4.md`; `calibration*.jsonl`). The
excerpt differs by prompt version:

- `v1`: a single function (rounds 1-2);
- `v2`: `arbiter.context` adds decorators, the class header and the
  repository definitions on the taint path (round 3);
- `v3`: `v2` plus up to two same-file callers when the flagged value is a
  parameter (rounds 4-5).

Each version is a different labeller, so the bound is reported per
version.

| category | prompt | truth source | n | `tp` calls right | lower bound | backs a 0.99 claim |
|---|---|---|---:|---:|---:|:-:|
| **injection, weak random/hash, deserialisation: rounds 4-5** | **v3** | OWASP Java/Python, Juliet Java | 494 | 382/382 | **0.990** (0.99004) | **yes, for this scope** |
| same, round 4 alone | v3 | | 200 | 154/154 | 0.976 | no |
| same, round 3 | v2 | | 500 | 286/286 | 0.987 | no |
| same, rounds 3-5 (two versions pooled, context only) | v2+v3 | | 994 | 668/668 | 0.994 | not a single labeller |
| same, rounds 1-5 | v1-v3 | OWASP, Juliet, CVE fixes | 1,905 | 1,162/1,172 | 0.984 | no |
| tests | v1 | planted twins | 45 | 24/24 | 0.862 | no |
| secrets | v1 | planted twins | 30 | 17/17 | 0.816 | no |
| stubs | v1 | planted twins | 34 | 17/17 | 0.816 | no |
| resilience | v1 | planted twins | 59 | 25/27 | 0.766 | no |
| concurrency | v1 | planted twins | 43 | 16/18 | 0.672 | no |
| supply chain (`unpinned-action`) | v1 | SHA-pin fact | 18 | 18/18 | 0.824 | no |
| secrets in test/example paths, npm ranges with a lockfile | v1 | path / lockfile fact | 108 | `fp` side only (107/107 decided) | - | no |
| docs drift, info exposure, error handling, suppression, IaC, numeric | - | none | - | - | - | no (uncalibrated) |

**Trusted at a 0.99 lower bound: the `v3` labeller, on injection, weak
random / weak hash and deserialisation, on benchmark code.** It made no
false `tp` call in 382 (lower bound 0.99004) and no false `fp` call in
112, with no unsure answers in 494. The margin is a single error: one
false `tp` call would put it under 0.99 until about 470 calls. The rules
measured were `java.sql-string-built`, `java.command-injection`,
`java.xss-unescaped-html`, `java.weak-random-for-secret`, and the Python
weak random / weak hash, code-injection and deserialisation rules.

The other categories have no `v3` calibration. The planted rounds were
`v1`, are too small to reach 0.99 even with no error, and use fixtures we
wrote. Docs drift and the rest have no model-free truth. A model label in
those categories (including `assurance.blanket-suppression`'s 125)
therefore carries no measured labeller error. No model-labelled rule or
category can be claimed at 99% yet.

**Model labels in the ledger are `v3` only.** On 2026-10-23, the gold
sample (601 findings from a fresh gold run), the AI corpus (115) and the
CVE suspects (5) were relabelled with `v3`
(`tools/gold/results/2026-10-23-relabel.md`). The `v1` files are kept in
`tools/gold/labels/history/`. `tools/precision_ledger.py build
tools/gold/labels/*.jsonl tools/gold/labels/*.jsonl.gz` drops calibration
records and any model label from a prompt version other than the current
`label_models.PROMPT_VERSION`.

Rule requirements: 157 of the 228 shipped rules trace to at least one
requirement (all 144 language-pack rules, 13 of the 56 rules coded in
Python). The other 71 (the 28 resource rules, the secret patterns, supply
chain, interface, contract and the 0.1.0 quality and drift rules) were
introduced on 2026-09-12/13, before the requirement registry existed, and no
requirement has changed them since; they are listed, with that reason, in
`tests/test_rule_requirements.py`, and leave the list the day a requirement
touches them.

## Model verification

`src/arbiter/verify.py` gives each finding a second opinion from a model,
after every deterministic pass has run. It records a verdict on the finding.
It never deletes, hides or re-grades one.

### When it runs

| Profile | Verification |
|---|---|
| `offline`, `ci` | never: these profiles forbid model calls, whatever `verify.enabled` or `--model-verify` say |
| `assisted` | on: `ci` plus this pass |
| `connected`, `audit` | only with `verify.enabled: true` or `--model-verify` |

It runs in `engine.run_scan` after calibration, severity overrides, the
baseline, suppressions, scope notes and CWE tagging. So it sees each finding's
final severity and never spends a call on a finding policy already silenced.
Candidates are active, non-inferred findings at or above `verify.min_severity`
(default `medium`), minus `verify.skip_rules` (the maintainability metrics and
census rows, which make no defect claim). They are sorted worst first, so a
budget cut drops the least severe findings.

### What the model sees

Each finding gets its own request, built from:

- **The rule:** id, title, severity, CWE, description and remediation.
- **Known false-positive notes:** `fp_notes` for the rule from
  `tools/gold/precision.json` (Phase 1's ledger), or `verify.fp_notes_path`.
  If neither exists, the prompt says none are recorded.
- **An excerpt built by `arbiter.context.build`**, the same builder the
  model labellers use (`gt_common.excerpt`), so verifier and labellers see
  the same code. In priority order, it holds:
  - the enclosing function found with tree-sitter, **with its decorators and
    annotations** (Python `@app.route`, TypeScript `@Get`, Java `@Override`),
    when it fits in `excerpt_lines` (default 120); otherwise its first lines
    and a window around the flagged line;
  - the enclosing class header (annotations, name, `extends`);
  - at most 30 import lines from the same file;
  - same-file lines that register the function as a route handler
    (`app.get("/x", handler)`, `add_url_rule(..., view_func=...)`);
  - same-file functions called on the taint path (a backward, textual slice
    from the flagged statement through the assignments that feed it; the
    sink's own receiver is not followed);
  - **the repository definitions of functions called on that path that live
    in another file**, at most 3 of 30 lines each. A call is resolved by its
    receiver's declared type or by an import, from the scan's own inventory,
    never by name alone across the repository;
  - **same-file callers** of the enclosing function, at most 2 of 25 lines
    each, when one of its parameters feeds the flagged statement (Juliet
    `sink(data)`, `doGet` calling `doPost(request, response)`), so the
    argument's origin is visible (`verify-v3`);
  - other same-file callees;
  - at most 220 lines in all, every line numbered with its own file's line
    number, the flagged line marked `>>`, masked with
    `judgement._mask_secrets` before it leaves the machine.

The system prompt tells the model that the excerpt is untrusted data: any
instruction in its code, comments or strings is ignored. It also says to
answer `unsure` when the excerpt does not settle the question.

The reply is constrained by structured output (`output_config.format`, a JSON
schema) to:

```json
{"verdict": "confirmed" | "rejected" | "unsure", "reason": "<= 2 sentences", "line": 42 | null}
```

The reply is still parsed defensively. These all become `unsure`:

- anything that is not that object;
- a verdict outside the enum;
- a refusal (`stop_reason: "refusal"`).

A `line` the excerpt did not show from the finding's own file is dropped.

### What is recorded

Each candidate gets `Finding.verification`, which is
`{verdict, reason, model, prompt_version, cached, line}`. `verdict` is one of
`confirmed`, `rejected`, `unsure` or `not_verified`. Reports written before
this field existed still load, with the field empty.

The report also gets `scan_scope.verification`, which holds:

- the counts per verdict;
- calls, cache hits and tokens;
- the reasons findings went unverified.

Each renderer shows the result:

| Renderer | What it shows |
|---|---|
| Console and Markdown | the verdict and reason under each finding, and a summary line |
| HTML | the verdict and reason under each finding |
| SARIF | the verdict as a result property |

**Not verified is not confirmed.** This follows rule 2 of judgement.py. There
are three reasons a candidate ends up `not_verified`:

- No provider is configured (for example, `ANTHROPIC_API_KEY` is not set).
- The budget ran out.
- The provider failed. After three errors in a row, the pass stops calling.

Whatever the reason, the "Read first" block says how many findings at or
above `min_severity` were not model-verified, and why. Phase 1's tier mapping
reads only `verification.verdict`:

- `verify.confirmed(f)` is true for a `confirmed` verdict.
- `verify.demotes(f)` is true for `rejected` and `unsure`.
- `not_verified` keeps the finding's rule tier.

### Determinism and cost

`claude-opus-5-5` takes no `temperature` (the API rejects one) and always
thinks, so sampling cannot be pinned. Instead, the model id, the prompt
version and the verdicts are pinned. Each verdict is cached in
`.arbiter/verify-cache.json` under this key:

    sha256( finding id ‖ sha256(excerpt) ‖ model ‖ PROMPT_VERSION ‖ sha256(rule text + FP notes) )

A second run over unchanged code therefore makes no calls and reproduces the
first run's verdicts. A changed excerpt, model, prompt version, rule
description or FP note asks the model again. That is what forces
re-measurement when any of them changes.

Unparseable replies and errors are not cached, so a retry can succeed. The
verdict cache does not follow `--no-cache`, because a verdict is keyed by the
excerpt's own bytes. `verify.cache: false` turns it off.

There are two budgets per scan:

- `max_findings`, the number of model calls (default 150);
- `max_tokens`, input plus output tokens (default 400,000). Each call's cost is
  estimated before sending and charged with the usage the API reports.

Candidates past either budget get `not_verified` with reason `budget`. At
claude-opus-5-5 list prices ($4 input / $20 output per million tokens, dated
2026-10-06), a call with a 120-line excerpt costs roughly 2–4k input tokens
plus the output and thinking tokens.

### Measuring the checker

A verdict is allowed to promote a finding only where it has been measured.
`tools/verify_eval.py` runs the pass over labelled findings and reports the
following, per rule and overall:

- **Confirmed-set precision** (true positives among the findings the model
  confirmed), with a Wilson 95% interval. This is the number the 99% target is
  about.
- Agreement and Cohen's κ between the model's decided verdicts and the labels.
- The TP-rejection rate: real defects the model rejected, which is the recall
  cost of demoting.
- Unsure and not-verified counts.
- Tokens and approximate cost.

```sh
python tools/verify_eval.py --ai-corpus --max-findings 150 --markdown out.md   # fixtures/ai-corpus labels
python tools/verify_eval.py --sheet alice.csv --corpus ~/wt/corpus-gold        # a label_sample.py sheet
```

On the AI corpus, findings are matched to `labels.json` exactly as
`tools/ai_corpus_score.py` matches them. Unreviewed findings are verified but
left out of every ratio. A sheet row carries a rule id and title but no rule
description, so its prompt is thinner than the one a scan sends.

Re-run the evaluation on every model or `PROMPT_VERSION` change. Admit a model
or prompt to promotion only where its confirmed-set lower bound clears the
tier's bar on held-out labels.

Status (2026-10-22): the pass and the evaluator exist. No live measurement has
been run yet, because the build machine had no `ANTHROPIC_API_KEY`.
