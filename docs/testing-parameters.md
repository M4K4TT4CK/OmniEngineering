# Testing parameters

Every number below decides something about a verdict: whether a rule counts
as proven, what severity a check earns, whether a measurement is reported at
all. They are scattered across these files and were mostly chosen once, on
reasoning that lives in a comment or nowhere.

This page says what each one does and how firmly it is held, so that changing
one is a decision rather than an edit. **Changing any of them changes
published evidence** — the figures in [evidence.md](evidence.md) and the
severities in `.arbiter/knowledge.json` were produced under these values — so
the table records them rather than tuning them.

## Contents

- [How firmly each is held](#how-firmly-each-is-held)
- [Calibration](#calibration)
- [Corpus measurement](#corpus-measurement)
- [Injection](#injection)
- [Comparison](#comparison)
- [Scoring](#scoring)
- [Precision](#precision)
- [Known problems in this table](#known-problems-in-this-table)

---

## How firmly each is held

| Grade | Meaning |
|---|---|
| **derived** | Follows from something external — a statistical definition, a file format. Changing it means being wrong, not being different. |
| **reasoned** | A judgement with a recorded reason, usually a specific incident. Movable, but the reason has to be answered first. |
| **arbitrary** | A round number that has never been tested against an alternative. Movable on evidence; nobody should defend the current value on principle. |

## Calibration

| Parameter | Value | Where | Held | What it decides |
|---|---|---|---|---|
| `MIN_OBSERVATIONS` | `20` | `src/arbiter/learn.py:60` | **arbitrary** | Human verdicts needed before a rule reports as calibrated rather than `unproven`. Twenty is a round number, not a power calculation. It is the single most consequential number here: at 928,000 synthetic observations (27 rules) and **zero** human verdicts (2026-10-23), every rule in the live ledger sits below it, so calibration currently changes nothing. This is not the precision ledger's bar; see [Precision](#precision). |
| Wilson `z` | `1.96` | `src/arbiter/claims.py:319` | **derived** | 95% confidence. Changing it changes what "lower bound" means, not how strict the tool is. |
| `CONFIDENCE_BANDS` | `0.90` high, `0.65` medium, else low | `src/arbiter/learn.py:350` | **arbitrary** | Maps a measured precision lower bound onto the three confidence labels a report prints. |

The lower bound rather than the ratio is the point: ten correct verdicts out
of ten is evidence of a rate above roughly 0.72, not of certainty.
`tests/test_measurement.py` holds that.

## Corpus measurement

| Parameter | Value | Where | Held | What it decides |
|---|---|---|---|---|
| `MIN_OBSERVATIONS` | `5` | `tools/calibrate_external.py:82` | **arbitrary** | Corpus hits an external check needs before any severity is assigned. **Shares a name with the calibration constant above and means something unrelated** — see known problems. |
| `MIN_REPOS_TO_PROMOTE` | `2` | `tools/calibrate_external.py:95` | **reasoned** | A check seen in only one repository is capped at `low`. "Every security-group rule must have a description" fired 33 times in Terragoat and nowhere else; arithmetically a strong ratio, and meaningless. |
| `BANDS` | `≥10×` medium, `≥3×` low | `tools/calibrate_external.py:120` | **arbitrary** | Discrimination ratio to severity. The ceiling is **reasoned** and firm: measurement can never assign `high`, because a ratio measures signal and severity encodes consequence. `tests/test_measurement.py` pins the ceiling. |
| `MIN_FINDINGS_FOR_A_RATIO` | `5` | `tools/discriminate.py:93` | **arbitrary** | Below this a rule's ratio is marked `thin` — reported, not trusted. |
| `JUDGEABLE` | `{security, compliance}` | `tools/discriminate.py:107` | **reasoned** | Only these dimensions get a pass/fail verdict. Nobody publishes a deliberately badly-documented repository, so for quality and drift rules the ratio measures codebase age, not rule quality. |
| `UNSUPPORTED_RATIO` | `1.5` | `tools/worklist.py:80` | **arbitrary** | Below this, a judgeable rule's severity is flagged as unsupported by measurement. |
| `MIN_LANG_LINES` | `5_000` | `tools/worklist.py:79` | **arbitrary** | Clean-population lines a language needs before a missing broken counterpart is worth reporting. |
| `DEPTH` | `300` | `tools/fetch_corpus.sh` | **reasoned** | Clone depth. Sized for fix-pair mining: at depth 1 every repository was skipped as shallow. |

## Injection

| Parameter | Value | Where | Held | What it decides |
|---|---|---|---|---|
| seed | `20260912` | `tools/inject.py:40` | **reasoned** | Fixed so a cycle is reproducible. Not a CLI flag, so two runs cannot be independent samples — worth knowing before treating repeated runs as accumulating evidence. |
| `--trials` | `20000` | `tools/inject.py:861` | **arbitrary** | More trials do not buy more evidence once generator diversity is exhausted; effective sample size is bounded by how many distinct defects the generator can express. |
| `--seeds-per-kind` | `400` | `tools/inject.py:863` | **arbitrary** | Real files mined per language to splice defects into. This is the number that actually bounds diversity. |

## Comparison

| Parameter | Value | Where | Held | What it decides |
|---|---|---|---|---|
| `line_tolerance` | `2` | `src/arbiter/ab.py:258` | **arbitrary** | How far apart two findings can sit and still be the same finding. |
| `wording_threshold` | `0.5` | `src/arbiter/ab.py:258` | **arbitrary** | Jaccard overlap on title tokens for a same-file match. |
| severity gap | `≥2` ranks | `tools/disagree.py` | **arbitrary** | How far two tools must diverge before it is queued as a disagreement. |

Precision is withheld entirely unless a fixture declares `exhaustive: true`.
That is **derived**, not a threshold: against a partial ground-truth list, a
competing tool's legitimate extra findings would score as false positives.

## Scoring

| Parameter | Value | Where | Held |
|---|---|---|---|
| `SEV_WEIGHT` | critical `40`, high `16`, medium `5`, low `1.5`, info `0` | `src/arbiter/core.py:17` | **arbitrary** |
| `CONFIDENCE_FACTOR` | high `1.0`, medium `0.6`, low `0.3` | `src/arbiter/core.py:18` | **arbitrary** |

These two are the arithmetic behind "weighted" everywhere else in this page.
The weighting itself is **reasoned**: counting raw findings made Argo CD look
worse than it is, because 195 of 203 findings for one rule were `testdata/`
manifests the tool had already discounted.

## Precision

The bar a rule must clear before the precision ledger admits it, and the
constants of the harness that measures it. The method is in
[precision.md](precision.md); this table only records the values.

| Parameter | Value | Where | Held | What it decides |
|---|---|---|---|---|
| `rule_min_n` | `30` | `src/arbiter/precision.py:47` (`DEFAULT_BAR`) | **arbitrary** | TP+FP labels a rule needs before it can be admitted. A floor: with no FP, Wilson needs 125 labels to reach 0.97 |
| `rule_lower` | `0.97` | same | **arbitrary** | Wilson 95% lower bound a rule needs to be admitted |
| `kappa_min` | `0.8` | same | **reasoned** | labeller agreement needed where two labellers looked |
| `offline_lower`, `tier_lower` | `0.99` | same | **reasoned** | the lower bound for a verified finding with no model verdict, and for the tier claim |
| `JUMP` | `0.20` | `tools/precision_guard.py:41` | **arbitrary** | how far a labelled rule's snapshot count may rise before the guard asks for new labels |
| `DEFAULT_TOLERANCE` | `2` | `tools/ai_corpus_score.py:76` | **arbitrary** | lines between a finding and a label for them to match |
| perf `tolerance`, `slack_seconds`, `min_factor` | `1.5`, `2.0`, `1.0` | `tools/gold/guards.json` | **arbitrary** | how much slower than its budget a regression-guard scan may run |
| held-out seed, fraction | `20261022`, `0.3` | `tools/gold/heldout.json` | **reasoned** | which gold repositories are held out; frozen so the split cannot drift |
| `context.Limits.total` | `220` lines | `src/arbiter/context.py:60` | **arbitrary** | the most code a labeller or the verify pass is shown |
| `PROMPT_VERSION` | `v3` (labeller), `verify-v3` (verify pass) | `tools/label_models.py:78`, `src/arbiter/verify.py:84` | **derived** | a different version is a different labeller; its labels and verdicts are not pooled |

## Known problems in this table

1. **Two constants share the name `MIN_OBSERVATIONS`.** One means 20 human
   verdicts before a rule is proven; the other means 5 corpus hits before an
   external check is graded. Neither imports the other, so nothing breaks —
   but reading either file alone gives you the wrong idea about the other.
2. **Three unreconciled ratio thresholds** decide closely related questions
   and none of them share a source: `1.5` (`worklist.py`), `3.0`/`10.0`
   (`calibrate_external.py`), and the `>3 / 1–3 / <1` bands printed by
   `discriminate.py`. A rule can be "unsupported" by one and "discriminating"
   by another.
3. **Most of the table is `arbitrary`.** That is the honest grade for a
   number nobody has tested an alternative against, and it is not by itself a
   defect — but it does mean the published figures are conditional on choices
   that were never measured. The threshold worth attacking first is
   `MIN_OBSERVATIONS = 20`, because it is the one currently deciding that no
   rule is calibrated from human verdicts.
