# Labeller calibration, rounds 4 and 5: labeller prompt v3 (2026-10-23)

Same question as rounds 1-3: when the model labellers
(`tools/label_models.py`: prosecutor, defender and adjudicator,
`claude-opus-5-5` through `claude -p --model opus`) call a finding `tp`, how
often is that right, measured against truth they never saw?

What changed: the excerpt. Round 3 recommended showing the **same-file
callers** of the enclosing function when the value at the flagged line is a
parameter, because all 8 of its unsure items were Juliet sinks whose `data`
came from a caller the excerpt did not show. `arbiter.context.build` now
does that (below). The excerpt shape changes, so the labeller prompt is
**`v3`** and the verifier prompt **`verify-v3`**. Every round-4 and round-5
label, and every relabelled gold / AI-corpus label
(`2026-10-23-relabel.md`), was made with `v3` and nothing else.

Labels: `tools/gold/labels/calibration-4.jsonl` (200 items, seed 20261025)
and `calibration-5.jsonl` (294 items, seed 20261027). **No person labelled
anything.** Round 4 was the ~200-item round asked for. It could not reach a
0.99 lower bound by itself (that needs 381 clean `tp` calls, and 200 items
hold about 155), so round 5 was drawn the same way to bring `v3` to the
target. Round 5 asked for 300 and stopped at 294 when the budget ran out.

Cost: round 4 $7.64 (400 CLI calls), round 5 $11.46 (about 590 calls).
Round 5 was interrupted once by a session usage limit and resumed from its
cache, so no call was paid for twice.

## Result

Wilson 95% lower bounds on `tp`-call precision, computed exactly (not
rounded up).

| | prompt | n | unsure | `tp` calls right | Wilson 95% lower | `fp` calls right | κ (pros./def.) |
|---|---|---:|---:|---:|---:|---:|---:|
| round 4 alone | v3 | 200 | 0 | 154/154 | 0.9757 | 46/46 | 1.00 |
| round 5 alone | v3 | 294 | 0 | 228/228 | 0.9834 | 66/66 | 0.99 |
| **`v3` pooled (rounds 4-5)** | **v3** | **494** | **0** | **382/382** | **0.99004** | **112/112** | 0.994 |
| round 3 (for comparison) | v2 | 500 | 8 | 286/286 | 0.987 | 206/206 | 0.98 |
| rounds 3-5, across two versions | v2 + v3 | 994 | 8 | 668/668 | 0.994 | 318/318 | 0.986 |
| rounds 1-5, every version | v1-v3 | 1,905 | 47 | 1,162/1,172 | 0.984 | - | 0.957 |

The last row adds the planted-twin rounds, which have their own errors. On
OWASP, Juliet and CVE items alone, rounds 1-2 carry the 6 false `tp` calls
made on the `v1` excerpt.

**The target is met, just: on a single prompt version (`v3`), 382 of 382
`tp` calls were right, and the Wilson 95% lower bound is 0.99004.** It is
no wider than that. One false `tp` call in the next round would put it
back under 0.99 until about 470 clean calls had been made. The `v2` + `v3`
pooled figure (0.994) is shown for context only. It pools two different
labellers, and the claim does not rest on it.

Individual roles on `v3`: the defender was right on all 382 of its `tp`
calls. The prosecutor made one false `tp` call (383 calls, lower 0.985):
OWASP Java `sql-string-built`, where `param` passes through
`thing.doSomething(...)`. The defender said `fp`, the adjudicator agreed,
and the final label matched the truth. It was the only item in rounds 4-5
that went to the adjudicator.

By source, `v3` pooled:

| | n | `tp` calls right | Wilson 95% lower |
|---|---:|---:|---:|
| Juliet Java | 240 | 185/185 | 0.980 |
| OWASP Java | 211 | 154/154 | 0.976 |
| OWASP Python | 43 | 43/43 | 0.918 |

What these rounds cover: benchmark code, rules `java.sql-string-built`
(217 items), `java.command-injection` (106), `java.xss-unescaped-html` (88),
`java.weak-random-for-secret` (40), `python.insecure-random-for-secret`
(22), `python.weak-password-hash` (18), `python.code-injection` (2) and
`python.unsafe-deserialization` (1). The planted categories (tests,
secrets, stubs, resilience, concurrency) were not re-run on `v3`. Their
round-2 figures stand, and none of them reaches 0.99.

## The draw

The sources and stratification are the same as round 3: OWASP Benchmark
Java 1.2 and Python 0.1, and Juliet Java 1.3, stratified by (source, rule,
truth) with square-root allocation. Juliet cases split across files are
left out (`_cross_file`). The blinding is the same: comments are blanked
and case names and paths are replaced, in every file shown. Each round
excluded every earlier round's findings:

```
python tools/label_models.py label \
  --ground-truth tools/gold/labels/owasp-java.jsonl tools/gold/labels/owasp-python.jsonl \
                 tools/gold/labels/juliet-java.jsonl.gz \
  --exclude-labels tools/gold/labels/calibration.jsonl tools/gold/labels/calibration-2.jsonl \
                   tools/gold/labels/calibration-3.jsonl \
  --n 200 --seed 20261025 --budget-usd 8.5 --out tools/gold/labels/calibration-4.jsonl
# round 5: also --exclude-labels calibration-4.jsonl; --n 300 --seed 20261027
```

The truth mix of rounds 4-5 was 382 truth-TP and 112 truth-FP. That is
more TP-heavy than round 3, because rounds 1-3 used up most of the
truth-FP strata (the OWASP Python truth-FP strata are empty). A TP-heavy
mix gives more `tp` calls per item. It does not make a false `tp` call
less likely to show up: each truth-FP item is still a chance for one.

## The excerpt: same-file callers (`arbiter.context.build`, part 7)

Part 7 applies when a **parameter** of the enclosing function feeds the
flagged statement. That is checked with the backward taint slice that
part 5 already uses, intersected with the parameter names from the
function's tree-sitter `parameters` node. Python `self`/`cls` and Go
receivers are left out, and destructured TypeScript patterns are covered.
When that holds, up to **2** same-file call sites of the function are
shown, outside its own body, at most one per calling function, in file
order. Each caller is shown as its whole function when that fits in **25**
lines. Otherwise it shows its first lines and a window that ends 2 lines
after the call, because the argument's history lies above the call. Part 7
comes after cross-file definitions and before other same-file callees, and
it shares the 220-line budget. `Context.callers` lists the call lines
shown. Like the rest of the excerpt, the output depends only on the file's
bytes and is masked. Tests: `tests/test_context.py` (callers shown only for
a parameter, the cap of two, windowing of long callers, parameter names in
five languages, determinism).

Over the 494 round-4/5 items, 217 excerpts showed at least one caller:
OWASP Java 171 (mostly `doGet` calling `doPost(request, response)`) and
Juliet 46. The average excerpt grew to 70 lines, and the longest was 125.
Over round 3's 500 items, 7 of the 8 unsure Juliet items now show the
caller that sets `data` (for example `data = "foo"; sink(data);`). The
eighth is a long `_12` function whose source is cut by the function window,
not a caller case. Round 3's items were not re-labelled on `v3`, so this is
an observation about the excerpt, not a measurement. Rounds 4-5 had **no**
unsure answers at all, against 8 of 500 in round 3.

## Open questions

1. **The margin is one error.** The 0.99 lower bound holds at 382/382. Each
   further round adds evidence. A single false `tp` call would put the
   bound under 0.99 until about 470 calls had been made. Recommendation:
   run a further ~200-item `v3` round each time the excerpt or a model
   changes, and report the bound with its count, never rounded.
2. **Scope of the 0.99.** It covers benchmark injection, crypto and
   deserialisation code. The gold relabel's other categories (resilience,
   tests, supply chain, suppression, iac) have no model-free truth at this
   scale. A model label there carries this calibration only by analogy.
   Recommendation: state the claim as "labeller `v3`, injection / crypto /
   deserialisation, on benchmark code", and extend calibration to the
   mechanically checkable categories (SHA pins, lockfiles, suppressions)
   before leaning on their model labels.
3. **Verify-vs-truth on `verify-v3`** (`tools/verify_eval.py`) has not been
   measured. The verifier now sees the same callers.
