# Acme Platform

Architecture is described in [the design doc](design.md) and the
[runbook](../ops/RUNBOOK.md).

The service reads its configuration from `app/settings.py` at startup.

## Environment variables

The following environment variables are required:

- `TASK_QUEUE_URL` — the work queue
- `FEATURE_FLAG_SERVICE` — endpoint for flag evaluation
