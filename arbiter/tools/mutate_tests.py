#!/usr/bin/env python3
"""Put each defect back and check the test that should catch it does.

A test suite that has never failed is not evidence. Every test in
`tests/test_invariants.py`, `tests/test_metamorphic.py` and
`tests/test_measurement.py` was written against a defect this project
actually shipped, and the claim each one makes is "this cannot come back".
That claim is only worth something if the test still fails when the defect
returns.

So this reintroduces each one -- a small, surgical edit to a source file --
runs the single test that names it, and requires a failure. A mutation that
survives means the test is decoration: it passes whether the code is right or
not, and it would have passed on the day the bug shipped.

`tools/integrity.py` does the same thing for the claim invariants. This is
that idea applied to the rest.

    python tools/mutate_tests.py            # every mutation
    python tools/mutate_tests.py --list     # what it would run

Source files are restored on the way out, including after a crash or a
Ctrl-C. Nothing here writes to the knowledge ledger.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Mutation:
    name: str
    defect: str          # the bug being put back, in one line
    path: str            # file to edit, relative to the repo root
    old: str             # text to replace (must appear exactly once)
    new: str             # what to replace it with
    test: str            # the test that must fail as a result


# The newline-normalizing line, written without escape sequences so that the
# anchor is the literal source text rather than the characters it denotes.
_CR, _LF = chr(92) + "r", chr(92) + "n"
_NEWLINE_LINE = (f'    return text.replace("{_CR}{_LF}", "{_LF}")'
                 f'.replace("{_CR}", "{_LF}")')


MUTATIONS = [
    Mutation(
        name="path-separator",
        defect="Location stops normalizing separators, so one finding has two "
               "fingerprints and a verdict recorded on Linux never matches Windows.",
        path="src/arbiter/core.py",
        old='        if self.path:\n            self.path = self.path.replace("\\\\", "/")',
        new="        pass",
        test="tests/test_invariants.py::test_the_same_finding_has_one_fingerprint_on_either_platform",
    ),
    Mutation(
        name="implicit-encoding",
        defect="A config file is read under the platform default codec again, "
               "which on Windows mis-decodes silently rather than raising.",
        path="src/arbiter/policy.py",
        old='yaml.safe_load(Path(path).read_text(encoding="utf-8"))',
        new="yaml.safe_load(Path(path).read_text())",
        test="tests/test_invariants.py::test_every_text_read_and_write_names_its_encoding",
    ),
    Mutation(
        name="utf16-is-binary",
        defect="A NUL byte alone means binary again, so every UTF-16 file is "
               "classified as data and never read.",
        path="src/arbiter/inventory.py",
        old="    if sample.startswith(_TEXT_BOMS):\n        return False\n    return b\"\\x00\" in sample",
        new='    return b"\\x00" in sample',
        test="tests/test_metamorphic.py::test_headerless_utf16_is_left_as_unread_rather_than_called_clean",
    ),
    Mutation(
        name="crlf-not-normalized",
        defect="Reading bytes stops translating line endings, so a pattern that "
               "ends at the end of a line sees a trailing carriage return and "
               "the unquoted secret formats go silent on Windows.",
        path="src/arbiter/probes.py",
        old=_NEWLINE_LINE,
        new="    return text",
        test="tests/test_metamorphic.py::test_every_carrier_survives_windows_line_endings",
    ),
    Mutation(
        name="synthetic-counts-as-evidence",
        defect="Generated trials count toward the observations that decide a rule "
               "is proven, so a night of injection manufactures confidence.",
        path="src/arbiter/learn.py",
        old="        return self.true_positives + self.false_positives",
        new="        return (self.true_positives + self.false_positives\n"
            "                + self.synthetic_detected + self.synthetic_clean_pass)",
        test="tests/test_measurement.py::test_synthetic_volume_does_not_shift_a_calibrated_confidence",
    ),
    Mutation(
        name="ungraded-check-stored",
        defect="A check the corpus declined to grade is stored anyway, so an "
               "unmeasured severity looks measured.",
        path="src/arbiter/learn.py",
        old="            severity = record.get(\"severity\")\n            if severity:",
        new="            severity = record.get(\"severity\")\n            if True:",
        test="tests/test_measurement.py::test_a_measured_severity_reaches_a_scan",
    ),
    Mutation(
        name="config-not-in-cache-key",
        defect="The result cache stops keying on the configuration, so a changed "
               "threshold or house rule is answered from the previous scan's findings.",
        path="src/arbiter/cache.py",
        old='    return _sha(cfg, getattr(probe, "version", ""), ARBITER_VERSION, code_digest(), context)',
        new='    return _sha("", getattr(probe, "version", ""), ARBITER_VERSION, code_digest(), context)',
        test="tests/test_cache.py::test_a_config_change_invalidates_the_cache",
    ),
    Mutation(
        name="governance-ignores-status",
        defect="The open-failure rule stops reading the ledger entry's status, so "
               "a fixed failure whose affected file changed is reported as if it "
               "were still open and every change to a once-broken file is noise.",
        path="src/arbiter/probes.py",
        old='        if str(e.get("status") or "").lower() not in _OPEN_STATUSES:\n'
            '            continue\n        fid = str(e.get("id") or "?")\n'
            '        tests = [_test_file(t)',
        new='        fid = str(e.get("id") or "?")\n'
            '        tests = [_test_file(t)',
        test="tests/test_governance.py::test_a_changed_affected_file_without_its_regression_test_is_reported",
    ),
    Mutation(
        name="adapter-memo-ignores-the-tree",
        defect="The adapter memo stops keying on the inventory digest, so an "
               "analyzer's findings from the previous tree are replayed after a "
               "file changed.",
        path="src/arbiter/cache.py",
        old='    return _sha("adapter", name, version, rules, inputs)',
        new='    return _sha("adapter", name, version, rules, "")',
        test="tests/test_adapter_cache.py::test_one_changed_file_reruns_every_adapter",
    ),
    Mutation(
        name="version-memo-ignores-the-binary",
        defect="The tool-version memo stops comparing the executable's stamp, so a "
               "reinstalled analyzer keeps reporting the old version string.",
        path="src/arbiter/adapters.py",
        old='            if isinstance(entry, dict) and entry.get("stamp") == stamp \\\n'
            '                    and isinstance(entry.get("version"), str):',
        new='            if isinstance(entry, dict) and isinstance(entry.get("version"), str):',
        test="tests/test_tool_version_memo.py::test_touching_the_executable_invalidates_its_entry",
    ),
]


def run_test(test: str) -> bool:
    """True when the test passes."""
    env_src = str(ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", test, "-q", "--no-header", "-x"],
        cwd=ROOT, capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": env_src},
    )
    return proc.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show the mutations and stop")
    args = ap.parse_args()

    if args.list:
        for m in MUTATIONS:
            print(f"  {m.name:32} {m.test}")
        return 0

    print("Mutation check")
    print("==============")
    print("Each line puts one defect back and requires its test to fail.\n")

    survived: list[str] = []
    unarmed: list[str] = []

    for m in MUTATIONS:
        target = ROOT / m.path
        original = target.read_text(encoding="utf-8")
        if original.count(m.old) != 1:
            unarmed.append(f"{m.name}: anchor text not found exactly once in {m.path}")
            print(f"  ?? {m.name:30} anchor missing -- mutation could not be applied")
            continue

        try:
            target.write_text(original.replace(m.old, m.new), encoding="utf-8", newline="")
            passed = run_test(m.test)
        finally:
            target.write_text(original, encoding="utf-8", newline="")

        if passed:
            survived.append(m.name)
            print(f"  FAIL {m.name:30} test still passed with the defect present")
            print(f"       {m.defect}")
        else:
            print(f"  ok   {m.name:30} caught")

    print()
    if unarmed:
        print("Mutations that could not be applied (the code moved under them):")
        for line in unarmed:
            print(f"  - {line}")
    if survived:
        print("Tests that do not test anything:")
        for name in survived:
            print(f"  - {name}")
    if survived or unarmed:
        return 1
    print(f"All {len(MUTATIONS)} mutations were caught.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
