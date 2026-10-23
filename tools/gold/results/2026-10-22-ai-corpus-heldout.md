# Held-out AI corpus: aggregate score (2026-10-22)

Arbiter at `88bcb10` (labels committed in that commit, before this first and only
scan of the tree), native probes, `tools/ai_corpus_score.py --corpus
fixtures/ai-corpus-heldout --aggregate-only`, 2-line tolerance.

**Held out.** Counts and ratios only. No rule id, file, line, finding or missed
defect is named here, and none may be added. Rule authors must not inspect
findings on this tree (`tools/gold/heldout.json`). Because the tree is never
reconciled, `false_alarms` is empty: every finding that detects no label is
*unreviewed* and counts against precision, so held-out precision is a lower
bound (a real defect the labeller missed scores as a false alarm).

Corpus: 40 apps, 8 tasks x 5 languages; 20 written by claude-opus-5-5 and 20
by claude-haiku-5-5 (the development corpus is claude-sonnet-5-5); 236
independent labels (1 high, 68 medium, 167 low). Twelve labels span more than
the 5 lines the labelling brief allowed (up to 25); they were left as
written, which slightly widens their match window.

## Code defects (excluding unpinned-dependency)

| language | apps | defects | recall | recall (independent) | recall high+crit | findings | TP | FP | unreviewed | precision |
|---|---|---|---|---|---|---|---|---|---|---|
| go | 8 | 29 | 10% | 10% | — | 9 | 4 | 0 | 5 | 44% |
| java | 8 | 31 | 3% | 3% | 0% | 20 | 1 | 0 | 19 | 5% |
| python | 8 | 26 | 0% | 0% | — | 3 | 0 | 0 | 3 | 0% |
| ruby | 8 | 34 | 0% | 0% | — | 3 | 0 | 0 | 3 | 0% |
| typescript | 8 | 22 | 9% | 9% | — | 5 | 2 | 0 | 3 | 40% |
| all | 40 | 142 | 4% | 4% | 0% | 40 | 7 | 0 | 33 | 18% |

## All labels

| language | apps | defects | recall | recall (independent) | recall high+crit | findings | TP | FP | unreviewed | precision |
|---|---|---|---|---|---|---|---|---|---|---|
| go | 8 | 29 | 10% | 10% | — | 9 | 4 | 0 | 5 | 44% |
| java | 8 | 32 | 3% | 3% | 0% | 20 | 1 | 0 | 19 | 5% |
| python | 8 | 47 | 45% | 45% | — | 24 | 21 | 0 | 3 | 88% |
| ruby | 8 | 46 | 0% | 0% | — | 3 | 0 | 0 | 3 | 0% |
| typescript | 8 | 82 | 76% | 76% | — | 65 | 62 | 0 | 3 | 95% |
| all | 40 | 236 | 37% | 37% | 0% | 121 | 88 | 0 | 33 | 73% |

## Recall by category

| category | detected / defects | recall |
|---|---|---|
| unpinned-dependency | 81/94 | 86% |
| unbounded-memory | 0/17 | 0% |
| logic-bug | 0/14 | 0% |
| server-no-timeout | 0/11 | 0% |
| missing-authn | 0/10 | 0% |
| undeclared-dependency | 3/10 | 30% |
| missing-timeout | 3/8 | 38% |
| ignored-error | 0/7 | 0% |
| missing-rate-limit | 0/6 | 0% |
| missing-test | 0/6 | 0% |
| unhandled-input-error | 0/6 | 0% |
| unbounded-input | 0/5 | 0% |
| unbounded-disk | 0/4 | 0% |
| csv-injection | 0/3 | 0% |
| ssrf | 0/3 | 0% |
| unbounded-request-body | 0/3 | 0% |
| host-header-injection | 0/2 | 0% |
| race-condition | 0/2 | 0% |
| resource-leak | 0/2 | 0% |
| session-not-invalidated | 0/2 | 0% |
| swallowed-exception | 0/2 | 0% |
| timing-attack | 0/2 | 0% |
| unbounded-concurrency | 0/2 | 0% |
| vulnerable-dependency | 0/2 | 0% |
| blocking-broadcast | 0/1 | 0% |
| doc-drift | 0/1 | 0% |
| info-exposure | 0/1 | 0% |
| insecure-file-permissions | 0/1 | 0% |
| insufficient-session-expiration | 0/1 | 0% |
| missing-encryption | 0/1 | 0% |
| open-redirect | 0/1 | 0% |
| session-no-expiry | 0/1 | 0% |
| slow-consumer-blocking | 0/1 | 0% |
| test-ineffective | 0/1 | 0% |
| token-no-expiry | 0/1 | 0% |
| unbounded-disk-usage | 0/1 | 0% |
| unhandled-send-error | 0/1 | 0% |

## Precision by finding severity

| severity | findings | TP | FP | unreviewed | precision |
|---|---|---|---|---|---|
| high | 11 | 0 | 0 | 11 | 0% |
| medium | 21 | 3 | 0 | 18 | 14% |
| low | 3 | 0 | 0 | 3 | 0% |
| info | 86 | 85 | 0 | 1 | 99% |

## Precision by finding category

| category | findings | TP | FP | unreviewed | precision |
|---|---|---|---|---|---|
| unpinned-dependency | 81 | 81 | 0 | 0 | 100% |
| missing-timeout | 18 | 3 | 0 | 15 | 17% |
| CWE-89 | 10 | 0 | 0 | 10 | 0% |
| undeclared-dependency | 5 | 4 | 0 | 1 | 80% |
| swallowed-exception | 4 | 0 | 0 | 4 | 0% |
| CWE-798 | 2 | 0 | 0 | 2 | 0% |
| CWE-22 | 1 | 0 | 0 | 1 | 0% |

## Labels

236 defects: 1 high, 68 medium, 167 low.

## Against the development corpus (same commit, same scorer)

| | development (25 apps, sonnet) | held out (40 apps, opus + haiku) |
|---|---|---|
| code defects detected | 2/117 (2%) | 6/142 (4%) |
| code findings that detect a label | 2/22 (9%) | 7/40 (18%) |
| code findings reconciled as false alarm | 20 | — (not reviewed; 33 unreviewed) |
| high/critical defects detected | 0/11 | 0/1 |
| high-severity findings that detect a label | 0/4 | 0/11 |
| dependency pinning recall | 52/55 (94%) | 81/94 (86%) |
| all labels: defects detected / findings true | 54/172 (31%) / 67/87 (77%) | 87/236 (37%) / 88/121 (73%) |

The development figures have been reconciled (every finding judged), so
their precision is exact; the held-out precision counts unreviewed findings
as false and is a lower bound. Neither is near the 99% target for any tier
above info: on held-out data the high and medium findings detect a label 0/11
and 3/21 times. Most held-out code-finding volume is in the timeout, SQL
(CWE-89) and swallowed-exception categories, and most of it in Java.
