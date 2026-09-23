# Debugging Playbook

Use this when investigating failures, confusing behavior, or validation errors.

## Sequence

1. Reproduce or identify the failure signal.
2. Check history first: `omni graph why <file or symbol>` and
   `omni failure search <error text>`. If this failure, or one like it, is
   already in the ledger, read its root cause and prevention before forming a
   hypothesis, and record the new one with `--recurrence-of`.
3. Read the smallest relevant code path.
4. Form one or two testable hypotheses.
5. Inspect logs or traces only when permitted and relevant.
6. Make one targeted change.
7. Re-run the failing check.
8. Write the regression test first when practical, and confirm it fails without
   the fix and passes with it.
9. Record the failure (below) before reporting completion.

## Record The Failure

Every confirmed defect, failed validation or build, reverted change, or
user-reported regression goes in the failure ledger. Record it as soon as the
signal is understood and finish the entry when the fix lands:

```bash
./omni failure add --requirement REQ-### --title "..." --symptom "<exact error or wrong behaviour>"
./omni failure update FAIL-### --status fixed --root-cause "<why it happened>" \
    --fix "<what changed>" --tests <test path or path::name> \
    --affected <files or symbols> --prevention-rules <rule id or playbook path>
```

- The root cause is the reasoning: why the code allowed this, not a restatement
  of the symptom. "Used float for money" is a root cause; "total was wrong" is
  not.
- Name the regression test. If none is practical, give an honest
  `--no-test-reason`; do not leave it blank.
- Name what prevents a repeat. If a rule, playbook or checklist should have
  caught this, or no instruction covered it, change that file in the same task
  and cite its rule id or path in `--prevention-rules`. That is how the
  workspace stops incorporating the same error again.
- `./omni failure check` verifies the entry is complete and every file, test and
  rule it names exists. `./omni graph build` then links it to the code,
  requirement, tests and rules, so `omni graph why` shows it next time.

## Guardrails

- Do not broad-scan unrelated files unless the failure path is unknown.
- Do not mask failures by weakening validation.
- Do not delete data, caches, or lock files without explicit approval.
- Preserve the original error in the final report and in the ledger's
  `symptom`.
- Do not close a failure as fixed on the strength of "it works now"; the
  regression test or a stated reason there is none is the evidence.
