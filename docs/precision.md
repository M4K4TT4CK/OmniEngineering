# Precision

How Arbiter measures, and works toward, the precision of the findings it
reports at gate-blocking confidence.

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
- **An excerpt of the finding's own file, and no other.** The excerpt is:
  - the enclosing function, found with tree-sitter (`ast.functions`), when it
    fits in `excerpt_lines` (default 120); otherwise a window of that many lines
    around the flagged line;
  - plus at most 30 import lines from the same file;
  - every line numbered;
  - masked with `judgement._mask_secrets` before it leaves the machine.

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

A `line` the excerpt did not contain is dropped.

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
