#!/usr/bin/env bash
# Fetch the ground-truth corpora the precision labels are built from (ARB-067).
#
#   tools/ground_truth/fetch_gt.sh [DEST]        # default ~/wt/corpus-gt
#
# * Juliet Test Suite for Java 1.3 (NIST SARD, public domain), pinned by the
#   archive's SHA-256; only the CWE directories the labels use are extracted.
# * CVE fix pairs: the files each pinned fix commit changed, at the fix and at
#   its parent (tools/ground_truth/cve_pairs.json, chosen by cve_select.py).
# * OWASP Benchmark Java and Python come from tools/fetch_corpus.sh (pinned).
#
# Everything fetched is data: it is scanned and read, never built or run, and
# stays out of git (DEST is outside the repository).
set -euo pipefail
DEST="${1:-$HOME/wt/corpus-gt}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
JULIET_URL="https://samate.nist.gov/SARD/downloads/test-suites/2017-10-01-juliet-test-suite-for-java-v1-3.zip"
JULIET_SHA256="d985f4177c2bcd7b03455a05c1c8f2e755f55c9eb250accd052f05f877347e60"
JULIET_DIRS=(CWE80_XSS CWE81_XSS_Error_Message CWE83_XSS_Attribute CWE23_Relative_Path_Traversal
             CWE36_Absolute_Path_Traversal CWE78_OS_Command_Injection CWE89_SQL_Injection
             CWE190_Integer_Overflow)

mkdir -p "$DEST/juliet-java-dl" "$DEST/juliet-java"
zip="$DEST/juliet-java-dl/juliet-java-1.3.zip"
if [ ! -f "$zip" ]; then
  curl -sfL -m 900 -o "$zip.part" "$JULIET_URL"
  mv "$zip.part" "$zip"
fi
echo "$JULIET_SHA256  $zip" | sha256sum -c -
pats=()
for d in "${JULIET_DIRS[@]}"; do pats+=("Java/src/testcases/$d/*"); done
(cd "$DEST/juliet-java" && unzip -q -o "$zip" "${pats[@]}" 'Java/src/testcasesupport/*')

PYTHONPATH="$ROOT/src" python "$ROOT/tools/ground_truth/cve_pairs.py" fetch \
  --pairs "$ROOT/tools/ground_truth/cve_pairs.json" --dest "$DEST/cve"
echo "ground truth fetched under $DEST"
