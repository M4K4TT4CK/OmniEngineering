#!/usr/bin/env bash
#
# Can the training cycle save its results? Run this BEFORE the cycle, not
# after.
#
#   ./tools/check_writeback.sh
#
# Run 1 measured everything and committed nothing. `training/` matched a
# gitignore entry, so `git add -A .arbiter training` exited 1, and `bash -e`
# failed the job two seconds after a fifty-five-minute cycle had finished
# successfully. Every number it produced was thrown away, and the job reported
# failure in a way that looked like the measurement had failed.
#
# The measurement is expensive and the check is instant, so the check goes
# first. A write-back that cannot happen is a reason not to start.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# What accumulates across runs and must stay tracked. Per-run logs are stamped
# and deliberately ignored; these are not. Keep this list in step with
# .gitignore's negations -- a test asserts they agree.
ACCUMULATING="
.arbiter/knowledge.json
.arbiter/external-severity.json
training/WORKLIST.md
training/fix-pairs.json
training/disagreements.json
"

fail=0
for path in $ACCUMULATING; do
  if git check-ignore -q "$path"; then
    echo "  IGNORED: $path" >&2
    echo "    .gitignore excludes a file the nightly job has to commit. The" >&2
    echo "    cycle would run for an hour and then refuse to save it." >&2
    fail=1
  fi
done

# The exact command the job runs at the end, as a dry run. This is what
# actually failed: an explicit pathspec that matches only ignored files is an
# error, not a no-op.
if ! git add -An .arbiter training >/dev/null 2>&1; then
  echo "  git add -A .arbiter training would fail:" >&2
  git add -An .arbiter training 2>&1 | sed 's/^/    /' >&2 || true
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo >&2
  echo "  Refusing to start a training cycle whose results cannot be saved." >&2
  exit 1
fi

echo "    write-back checked: 5 accumulating files are committable"
