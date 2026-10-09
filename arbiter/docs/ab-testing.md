# A/B Harness

An **arm** is any named way of producing findings for the same target. One
abstraction covers all three comparisons worth making: configuration against
configuration, Arbiter against another analyzer, and one build of Arbiter
against another.

## A spec

```yaml
# examples/ab-native-vs-checkov.yaml
ab: native suite vs checkov
targets: [../fixtures/legacy-platform]
arms:
  - name: native-all
    skip: [checkov, semgrep, bandit, ruff, gitleaks]
  - name: checkov
    kind: tool
    tool: checkov
```

```bash
arbiter ab --spec examples/ab-native-vs-checkov.yaml --out ab-out
```

Three spec examples ship in `examples/`: native vs. checkov, offline vs.
connected, and version regression.

## Arm kinds

| Arm kind | Meaning |
|---|---|
| `arbiter` | the pipeline with a given profile / probe selection / config |
| `tool` | one external analyzer on its own, normalized through its adapter |
| `command` | any command that writes a `report.json` — use it to compare two builds of Arbiter |

## How findings are matched

Matching runs in three passes, and every match records which pass found it,
because a comparison that hides how it matched is not evidence:

1. identical fingerprint
2. same repository, file and line (±2)
3. same file, overlapping title wording (Jaccard ≥ 0.5)

## Scoring against ground truth

Where a target carries `.arbiter-expected.yaml`, each arm is also scored against
that ground truth.

Recall is always reported. **Precision is reported only when the fixture
declares `exhaustive: true`** — against a partial list of planted defects, a real
analyzer's extra findings are not false positives, and reporting them as such
would be a lie in Arbiter's favor.

## A sample result, read honestly

```text
A  native-all   23 findings in 0.02s · recall 100% (14/14 planted)
B  checkov      32 findings in 5.50s · recall  36% (5/14 planted)

agreement 17% — 8 matched, 15 only in A, 24 only in B
8 severity disagreements: Arbiter rates a public bucket critical, Checkov CE rates it medium
```

Checkov goes far deeper on Terraform than Arbiter's 13 native rules, and the 24
findings only it produced are real. What the comparison shows is that the two are
complementary, that severity calibration differs sharply, and that nothing
Arbiter found was invented.

This is also the mechanism behind external severity calibration — see
[calibration.md](calibration.md#calibrating-someone-elses-tool).
