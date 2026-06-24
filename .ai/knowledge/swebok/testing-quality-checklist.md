# Testing Quality Checklist

Use this checklist before accepting validation as sufficient.

## Traceability

- Tests map to requirement IDs, acceptance criteria, defects, or risks.
- Expected behavior is defined by a clear oracle.
- Changed behavior has corresponding validation.

## Coverage

- Normal path is covered.
- Important edge cases are covered.
- Error behavior is covered when relevant.
- Integration boundaries are covered when changed.
- Regression coverage exists for bug fixes when practical.

## Quality Attributes

- Security-sensitive behavior is tested or reviewed.
- Performance-sensitive behavior has an appropriate check or rationale.
- Reliability, recovery, or retry behavior is tested when relevant.
- Data validation and persistence behavior are tested when relevant.

## Test Design

- Tests are deterministic or controlled.
- Tests avoid unnecessary coupling to implementation detail.
- Test names explain behavior.
- Test setup is understandable.
- Manual verification steps are explicit when automation is unavailable.

## Reporting

- Passing checks are listed.
- Failing checks are listed with relevant error.
- Skipped checks include reason and residual risk.
