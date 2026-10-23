# Labeller calibration, round 3: labeller context (2026-10-23)

The question is the same as rounds 1 and 2. When the model labellers
(`tools/label_models.py`: prosecutor, defender and adjudicator,
`claude-opus-5-5` through `claude -p --model opus`) call a finding `tp`, how
often is that right, measured against truth they never saw?

What changed: the excerpt. Round 2 found that all 6 of its false `tp` calls
had the deciding fact outside a single-function excerpt. Round 3 uses prompt
`v2`, whose excerpt comes from the new shared builder `src/arbiter/context.py`
(see "The excerpt" below). The verification pass uses the same builder
(`verify-v2`).

Labels: `tools/gold/labels/calibration-3.jsonl`, 500 ground-truth items.
**No person labelled anything.** Cost **$18.34** over 1,005 CLI calls, plus
$0.24 to re-run round 1-2's 6 false-`tp` items. The total, $18.58, is under the
$20 cap. No throttles and no usage limit were hit.

## Result

| | n | unsure | `tp` calls right | `tp`-call precision | Wilson 95% lower | `fp` calls right |
|---|---:|---:|---:|---:|---:|---:|
| **round 3, final** | 500 | 8 | **286/286** | 1.000 | **0.987** | 206/206 |
| round 3, prosecutor | 500 | 6 | 287/288 | 0.997 | 0.981 | 206/206 |
| round 3, defender | 500 | 8 | 286/288 | 0.993 | 0.975 | 204/204 |
| round 3, unanimous only | 495 | 5 | 286/286 | 1.000 | 0.987 | 204/204 |
| **rounds 1-3 pooled, final** | 1,200 | 37 | **681/687** | 0.991 | **0.981** | 476/476 |
| pooled, Juliet `_81`-family items left out | 1,188 | 29 | 678/683 | 0.993 | 0.983 | 476/476 |

In round 3, κ between prosecutor and defender was 0.98. 5 of the 500 items
went to the adjudicator (3 unsure, 2 `fp`). There were no false `tp` and no
false `fp` calls. The 8 unsure items are all Juliet.

By category and source, round 3 (final):

| | n | `tp` calls right | Wilson 95% lower |
|---|---:|---:|---:|
| injection rules | 428 | 218/218 | 0.983 |
| weak random / weak hash (`auth-crypto`) | 62 | 62/62 | 0.942 |
| deserialisation | 10 | 6/6 | 0.610 |
| Juliet Java | 266 | 128/128 | 0.971 |
| OWASP Java | 181 | 112/112 | 0.967 |
| OWASP Python | 53 | 46/46 | 0.923 |

Pooled over rounds 1-3, the injection rules alone are 515/520 (lower bound
0.978).

**Does the injection family reach a 0.99 lower bound? No, not yet.** On
round 3 alone it is 0.987. A lower bound of 0.99 with no errors needs 363
`tp` calls; round 3 has 286. About 80 more clean `tp` calls would get there,
some 140 more items at this mix (roughly $5). Pooled, it is 0.981, because
rounds 1-2 carry 6 errors made with the old excerpt. With 6 errors, a 0.99
lower bound needs about 1,250 `tp` calls. The honest reading is that the
`v2` labeller is the one to measure, and it needs one more round.

## The draw

The ground-truth sources were OWASP Benchmark Java 1.2 and Python 0.1 and
Juliet Java 1.3. The draw was stratified by (source, rule, truth) with
square-root allocation, as in round 2, seed 20261024. Every round 1 and
round 2 finding was excluded (`--exclude-labels calibration.jsonl
calibration-2.jsonl`). Juliet cases split across files are left out, now
including the `_NN_bad` / `_NN_good*` / `_NN_base` family (see below). The
blinding is unchanged: comments are blanked and case names and paths are
replaced. The blinding now also applies to every other file whose
definitions are shown.

Truth mix: Juliet 266 (131 truth-TP, 135 truth-FP), OWASP Java 181 (112/69),
OWASP Python 53 (46/7). The OWASP Python truth-FP strata are nearly used up
by rounds 1-2.

```
python tools/label_models.py label \
  --ground-truth tools/gold/labels/owasp-java.jsonl tools/gold/labels/owasp-python.jsonl \
                 tools/gold/labels/juliet-java.jsonl.gz \
  --exclude-labels tools/gold/labels/calibration.jsonl tools/gold/labels/calibration-2.jsonl \
  --n 500 --seed 20261024 --budget-usd 19.7 --out tools/gold/labels/calibration-3.jsonl
```

## The excerpt (`arbiter.context.build`)

The excerpt has seven parts, listed in priority order. All of them share
one budget: at most 220 lines for the labellers and `excerpt_lines` + 100
for verify.

1. The enclosing function, with its decorators and annotations. A Python
   `decorated_definition`, TypeScript `decorator` siblings and Java
   modifiers are all part of the span. A long function keeps its first
   lines and a window around the flagged line.
2. The enclosing class header: annotations, name and `extends`, up to 4
   lines.
3. The file's imports, up to 25 lines.
4. Same-file route registration of the handler, up to 3 lines. Examples:
   `app.get('/x', handler)`, `add_url_rule(..., view_func=handler)`.
5. Same-file callees on the taint path. The taint path is a backward,
   textual slice. It starts at the flagged statement and follows each
   assignment whose left side feeds it. The sink call's own receiver is
   not followed: `statement` in `statement.addBatch(sql)` is where the data
   goes, not where it comes from.
6. Definitions from other files of the same repository for calls on that
   path: at most 3 definitions, 30 lines each. A call is resolved only
   through the receiver's declared type or an import, and never by name
   alone. The rules, by language:
   - Java: declared or fully-qualified types, `import` and `import static`,
     the superclass, and the same package.
   - Python: `from m import f`, and `import m` used as `m.f`.
   - TypeScript and JavaScript: relative `import` and `require`.
   - Go: the imported package directory, and the same package.
   - Ruby: `require_relative`.

   Each definition is headed `--- <path>: definition of `f`, called as
   `recv.f(...)` at line N ---`.
7. Other same-file callees.

The output depends only on the files' bytes and is masked with
`_mask_secrets`. The cache keys are therefore stable.

Across the 500 round-3 items:
- 85 showed at least one cross-file definition: OWASP Java 76, Juliet 8,
  OWASP Python 1;
- the most common definitions were `DatabaseHelper.getSqlConnection` and
  `getSqlStatement`, `Utils.getOSCommandString`,
  `SeparateClassRequest.getTheValue`/`getTheParameter`, `ThingFactory` and
  `ThingInterface`;
- the average excerpt was 66 lines and the longest 118.

The two failure patterns from round 2 were all decided correctly:
- 14 items with `scr.getTheValue` (11 safe, 3 vulnerable);
- 5 items with `request.path.split("/")[1]` on a fixed Flask route (1 safe,
  4 vulnerable).

## The 6 round 1-2 false `tp` items, re-run with the `v2` excerpt

Records: `tools/gold/results/2026-10-23-calibration-3-rerun.jsonl` (not
under `labels/`, so no pooled count includes them).

| item | truth | was | now | what decided it |
|---|---|---|---|---|
| OWASP Python 01096 (`unsafe-deserialization`) | safe | tp | **fp** | the `@app.route('/benchmark/deserialization-00/Handler')` decorator: `parts[1]` is always `"benchmark"` |
| OWASP Python 01098 (`command-injection`) | safe | tp | **fp** | the same, for the fixed route `/benchmark/cmdi-00/Handler` |
| OWASP Java 01817 (`sql-string-built`) | safe | tp | **fp** | the definition of `SeparateClassRequest.getTheValue`, which returns `"bar"` |
| OWASP Java 02728 | safe | tp | **fp** | the same |
| OWASP Java 00939 | safe | tp | **fp** | the same |
| Juliet `..._81_goodG2B` | safe | tp | tp | `data` is a parameter set by `_81a` through `_81_base`, which no excerpt shows. This case is now removed from calibration by the widened `_cross_file` filter. |

All 5 reversals were unanimous (prosecutor and defender both `fp`). They are
reported as evidence that the excerpt was the cause. They are **not**
counted as a measurement, because only the known errors were re-run.

## Other changes in this round

- **Juliet cross-file filter.** `_cross_file` now matches
  `_\d+([a-z]|_bad|_good\w*|_base)$`, so it catches the `_81` family and
  not only `_54a`.
- **Resilience claim wording.** The labellers' `CLAIMS` for `*timeout*` and
  `*.db-call-no-deadline` now say the call "is made with no explicit timeout
  or deadline set by this code, so how long it waits when the other side
  stalls is left to the library's default (which may be long or
  unlimited)". The old text said "can block indefinitely". That is false for
  Ruby Net::HTTP (60 s defaults), and round 2's labellers rejected 4 planted
  true positives over it. Go's `http-default-client` keeps "no timeout",
  which is accurate. Among the rule descriptions, Ruby's `http-no-timeout`
  already said "waits 60 seconds by default". TypeScript's `fetch-no-timeout`
  said "no default timeout ... forever", which is wrong for Node's undici
  fetch (300 s header and body defaults), and it is reworded. The Java,
  Go, Python `requests`, axios and Node `http.request` descriptions are
  accurate as written. The planted resilience twins were not re-run in this
  round, so the effect of the new claim on them is not measured.
- **Prompt versions.** The labeller prompt is now `v2`: the system prompt
  describes the definition sections. The verifier prompt is now `verify-v2`,
  so every cached verdict refreshes.

## Open questions

1. **One more round on `v2` to reach 0.99.** About 140 items (about $5)
   with no false `tp` call would put the `v2` labeller's lower bound at 0.99.
   Recommendation: run it, and report the claim on `v2` labels only, with
   the pooled figure beside it.
2. **Same-file callers.** All 8 unsure items are Juliet sinks whose `data`
   is a parameter, set by a caller in the same file that the excerpt does
   not show. Showing the same-file callers of the enclosing function when the
   tainted value is a parameter would probably decide them. This is cheap
   to add, and it changes the excerpt hashes again. Recommendation: add it
   before the next round, not during one.
3. **Pooling across prompt versions.** Rounds 1-2 (`v1`) and round 3 (`v2`)
   measure different labellers. Recommendation: the ledger's model labels
   from `v1` (gold-model, ai-corpus-model) should be treated as the weaker
   labeller, or relabelled with `v2` before any claim rests on them.
4. **Verify-vs-truth on `verify-v2`.** The verifier now sees the same
   context. Its agreement with ground truth (`tools/verify_eval.py`) has
   not been re-measured on `verify-v2`.
