"""REQ-041: the adopt loop proven end to end. A fresh directory adopts the workspace with Arbiter wired in
(`omni adopt --with-arbiter`), becomes a git repository, gets a real defect planted (`verify=False`), and
`omni gate` must fail on `completion.arbiter_gate` alone while the required `completion.tests` rule (REQ-047)
runs and passes with no suite registered; the defect is fixed and the same gate must pass.
This mirrors the adopt-loop job in .github/workflows/ci.yml, so a break in the loop shows up here before
it shows up there. The loop needs the real `arbiter` and `git` on PATH and is skipped without them; the
adoption regression test below runs everywhere. Stdlib only. Run with:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import functools
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402

GATE_TIMEOUT = 180  # seconds per `omni gate` call; the rule's own `arbiter gate` command runs verbatim inside it
SEED_ID = "REQ-001"  # REQ-058: the adopted registry is empty, so the first id the adopter adds is REQ-001
PLANTED = "import requests\n\n\ndef fetch(url):\n    return requests.get(url, verify=False)\n"
TESTS_RULE_RAN = r"(?m)^  ran +completion\.tests \d+\.\ds PASS$"  # the gate's per-rule line for a run, passed command rule


class TestAdoptCopiesNestedCliFilesWithTheirDirectory(unittest.TestCase):
    """`--include-cli` lists the graph-viewer files, which live under `.ai` and so are already in place
    once `.ai` is copied whole; reporting them as "skip existing" made every such adoption exit 1."""

    def test_include_cli_into_an_empty_directory_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = ma.build_parser().parse_args(["adopt", "--target", tmp, "--tools", "none", "--include-cli"])
            out = io.StringIO()
            with redirect_stdout(out):
                code = ma.run_adopt(args)
            self.assertEqual(code, 0, out.getvalue())
            self.assertNotIn("skip existing", out.getvalue())
            self.assertIn("copied: .ai/graph-viewer/viewer.html (with .ai)", out.getvalue())
            self.assertTrue((Path(tmp) / ".ai" / "graph-viewer" / "viewer.html").is_file())

    def test_dry_run_reports_the_same_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = ma.build_parser().parse_args(["adopt", "--target", tmp, "--tools", "none", "--include-cli", "--dry-run"])
            out = io.StringIO()
            with redirect_stdout(out):
                code = ma.run_adopt(args)
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("copy: .ai/graph-viewer/viewer.html (with .ai)", out.getvalue())
            self.assertFalse((Path(tmp) / ".ai").exists())


class TestAdoptShipsTheClaudeSkills(unittest.TestCase):
    """REQ-056: `--tools claude` copies the clarify skill (requirements gathering in plan mode) beside CLAUDE.md."""

    def test_claude_tool_copies_the_clarify_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = ma.build_parser().parse_args(["adopt", "--target", tmp, "--tools", "claude"])
            out = io.StringIO()
            with redirect_stdout(out):
                code = ma.run_adopt(args)
            self.assertEqual(code, 0, out.getvalue())
            skill = Path(tmp) / ".claude" / "skills" / "clarify" / "SKILL.md"
            self.assertTrue(skill.is_file(), out.getvalue())
            text = skill.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\nname: clarify\n"), "skill frontmatter must name the skill")
            self.assertIn("Plan mode", text)
            self.assertIn("./omni requirement add", text)


def _adopt_quietly(*argv: str) -> tuple[int, str]:
    args = ma.build_parser().parse_args(["adopt", *argv])
    out = io.StringIO()
    with redirect_stdout(out):
        code = ma.run_adopt(args)
    return code, out.getvalue()


class TestAdoptShipsAnEmptyRegistry(unittest.TestCase):
    """REQ-058: the adopter gets an empty requirements registry, not a copy of this repository's, and none of
    this repository's archive, gate waivers or generated graph output."""

    def test_adopted_registry_is_empty_and_keeps_version_and_prefix(self) -> None:
        source = ma.load_json(ROOT / ma.REQUIREMENTS_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _adopt_quietly("--target", tmp, "--tools", "none")
            self.assertEqual(code, 0, out)
            adopted = ma.load_json(Path(tmp) / ma.REQUIREMENTS_PATH)
            self.assertEqual(adopted, {
                "version": source.get("version", "1.0.0"),
                "requirement_id_prefix": source.get("requirement_id_prefix", "REQ"),
                "requirements": [],
            })

    def test_archive_waivers_and_graph_output_are_not_adopted(self) -> None:
        # Plant each in a copy of the source so the test does not depend on what this checkout happens to hold.
        with tempfile.TemporaryDirectory() as src_tmp, tempfile.TemporaryDirectory() as tmp:
            source = Path(src_tmp) / "src"
            shutil.copytree(ROOT / ".ai", source / ".ai")
            planted = [ma.REQUIREMENTS_ARCHIVE_PATH, ma.GATE_WAIVERS_PATH, Path(ma.GRAPH_DEFAULT_OUTPUT),
                       Path(ma.VIEW_DEFAULT_OUTPUT), Path(ma.RENDER_DEFAULT_OUTPUT)]
            for relative in planted:
                (source / relative).write_text("{}\n", encoding="utf-8")
            result = ma.copy_adoption_path(source, Path(tmp), ".ai", force=False, dry_run=False)
            self.assertEqual(result, "copied: .ai")
            for relative in planted:
                self.assertFalse((Path(tmp) / relative).exists(), f"{relative} was adopted")
            self.assertTrue((Path(tmp) / ma.REQUIREMENTS_PATH).is_file())

    def test_first_requirement_added_in_the_adopter_is_req_001(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _adopt_quietly("--target", tmp, "--tools", "none", "--include-cli")
            self.assertEqual(code, 0, out)
            completed = subprocess.run(
                [sys.executable, "omni", "requirement", "add", "--title", "First", "--description", "first one"],
                cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("Added requirement REQ-001: First", completed.stdout)
            ids = [r["id"] for r in ma.load_json(Path(tmp) / ma.REQUIREMENTS_PATH)["requirements"]]
            self.assertEqual(ids, ["REQ-001"])


def _rule_ids(root: Path) -> list[str]:
    rulepack = ma.load_json(root / ma.COMPLETION_RULEPACK_PATH)
    return [str(rule.get("id")) for rule in rulepack.get("rules", []) if isinstance(rule, dict)]


class TestAdoptWithoutArbiterLeavesTheGateRuleOut(unittest.TestCase):
    """REQ-059: without --with-arbiter the required `completion.arbiter_gate` rule is left out, so `omni gate`
    does not fail for want of Arbiter; with it, or after `omni arbiter install`, the rule is there."""

    def test_plain_adopt_leaves_the_rule_out_and_keeps_the_others(self) -> None:
        source_ids = _rule_ids(ROOT)
        self.assertIn(ma.ARBITER_GATE_RULE_ID, source_ids)  # otherwise this test proves nothing
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _adopt_quietly("--target", tmp, "--tools", "none")
            self.assertEqual(code, 0, out)
            self.assertIn(f"left `{ma.ARBITER_GATE_RULE_ID}` out", out)
            self.assertEqual(_rule_ids(Path(tmp)), [rid for rid in source_ids if rid != ma.ARBITER_GATE_RULE_ID])

    def test_dry_run_says_so_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new"
            code, out = _adopt_quietly("--target", str(target), "--tools", "none", "--dry-run")
            self.assertEqual(code, 0, out)
            self.assertIn(f"would leave `{ma.ARBITER_GATE_RULE_ID}` out", out)
            self.assertFalse(target.exists())

    def test_existing_rulepack_that_was_skipped_is_not_touched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rulepack = Path(tmp) / ma.COMPLETION_RULEPACK_PATH
            rulepack.parent.mkdir(parents=True)
            shutil.copy2(ROOT / ma.COMPLETION_RULEPACK_PATH, rulepack)
            before = rulepack.read_bytes()
            code, out = _adopt_quietly("--target", tmp, "--tools", "none")
            self.assertEqual(code, 1, out)  # .ai exists, so it is skipped
            self.assertNotIn("Arbiter: not requested", out)
            self.assertEqual(rulepack.read_bytes(), before)

    def test_with_arbiter_keeps_the_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ma, "arbiter_write_baseline", lambda *a, **k: 0):
                code, out = _adopt_quietly("--target", tmp, "--tools", "none",
                                           "--with-arbiter", ma.ARBITER_DEFAULT_SOURCE, "--skip-pip")
            self.assertEqual(code, 0, out)
            self.assertIn(ma.ARBITER_GATE_RULE_ID, _rule_ids(Path(tmp)))

    def test_arbiter_install_adds_the_rule_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _adopt_quietly("--target", tmp, "--tools", "none")
            self.assertEqual(code, 0, out)
            with redirect_stdout(io.StringIO()), mock.patch.object(ma, "arbiter_write_baseline", lambda *a, **k: 0):
                self.assertEqual(ma.arbiter_install(Path(tmp), ma.ARBITER_DEFAULT_SOURCE, skip_pip=True), 0)
            self.assertIn(ma.ARBITER_GATE_RULE_ID, _rule_ids(Path(tmp)))


@unittest.skipUnless(shutil.which("arbiter") and shutil.which("git"), "needs the real `arbiter` and `git` on PATH")
class TestAdoptLoop(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        self.transcript: list[str] = []  # every command's output, shown whole when something is not as expected

    # -- helpers -----------------------------------------------------------------------------------

    def diagnosis(self) -> str:
        return "\n\n".join(self.transcript)

    def run_in_repo(self, *argv: str, timeout: float = 60, check: bool = True) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            list(argv), cwd=self.root, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=self.env,
        )
        self.transcript.append(f"$ {' '.join(argv)}\n{completed.stdout}{completed.stderr}(exit {completed.returncode})")
        if check:
            self.assertEqual(completed.returncode, 0, self.diagnosis())
        return completed

    def omni(self, *argv: str, timeout: float = 60, check: bool = True) -> subprocess.CompletedProcess[str]:
        # The adopted copy of the CLI, from the adopted directory: exactly what an adopter runs.
        return self.run_in_repo(sys.executable, "omni", *argv, timeout=timeout, check=check)

    def gate(self, expected_code: int) -> str:
        completed = self.omni("gate", timeout=GATE_TIMEOUT, check=False)
        if completed.returncode != expected_code:
            self.omni("doctor", check=False)  # the gate ran doctor first; its full report makes a red run diagnosable
            self.fail(f"expected `omni gate` to exit {expected_code}, got {completed.returncode}\n\n{self.diagnosis()}")
        return completed.stdout + completed.stderr

    def adopt(self) -> None:
        # The same argv as the CI job, through the same parser. CI installs Arbiter's [mcp] extra only, so no
        # adapter (semgrep, bandit, ...) exists there and the baseline scan is native probes only; a developer
        # machine with those tools on PATH would spend minutes on them for the copied CLI's two large files, so
        # the baseline cut here says so explicitly. The two gate runs below use the rule's command verbatim.
        argv = [
            "adopt", "--target", str(self.root), "--tools", "all", "--include-cli", "--include-legal",
            "--with-arbiter", ma.ARBITER_DEFAULT_SOURCE, "--skip-pip",
        ]
        args = ma.build_parser().parse_args(argv)
        baseline = functools.partial(ma.arbiter_write_baseline, scan_args=("--no-adapters",))
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(ma, "arbiter_write_baseline", baseline):
            code = ma.run_adopt(args)
        self.transcript.append(f"$ omni {' '.join(argv)}\n{out.getvalue()}(exit {code})")
        self.assertEqual(code, 0, self.diagnosis())
        self.assertTrue((self.root / ma.ARBITER_BASELINE_PATH).is_file(), self.diagnosis())

    # -- the loop ----------------------------------------------------------------------------------

    def test_planted_defect_fails_the_gate_on_the_arbiter_rule_alone_and_the_fix_passes(self) -> None:
        self.adopt()
        # Doctor, which every gate runs first, wants a README; CHANGELOG.md is what the co_changed rules watch.
        (self.root / "README.md").write_text("# Adopt loop\n", encoding="utf-8")
        (self.root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
        self.run_in_repo("git", "init", "-q")
        self.run_in_repo("git", "symbolic-ref", "HEAD", "refs/heads/main")
        self.run_in_repo("git", "config", "user.email", "ci@example.invalid")
        self.run_in_repo("git", "config", "user.name", "ci")
        self.run_in_repo("git", "add", "-A")
        self.run_in_repo("git", "commit", "-q", "-m", "Adopt the workspace with Arbiter")

        # The seed requirement and the changelog line ride in the same uncommitted change as the code, so
        # the requirements_registry and changelog_gate co_changed rules are satisfied and cannot mask Arbiter.
        added = self.omni("requirement", "add", "--title", "Adopt loop seed",
                          "--description", "Seed requirement for the adopt loop", "--category", "Feature")
        self.assertIn(f"Added requirement {SEED_ID}:", added.stdout, self.diagnosis())
        app = self.root / "src" / "app.py"
        app.parent.mkdir()
        app.write_text(PLANTED, encoding="utf-8")
        with (self.root / "CHANGELOG.md").open("a", encoding="utf-8") as handle:
            handle.write(f"- {SEED_ID}: src/app.py fetches a url\n")

        output = self.gate(1)
        self.assertIn(ma.ARBITER_GATE_RULE_ID, output, self.diagnosis())
        self.assertNotIn("data.privacy", output, self.diagnosis())
        self.assertNotIn("changelog_gate", output, self.diagnosis())
        failed_rules = [line for line in output.splitlines() if line.startswith("  - ")]
        self.assertEqual(
            [line for line in failed_rules if not line.startswith(f"  - {ma.ARBITER_GATE_RULE_ID}:")], [],
            f"rules other than {ma.ARBITER_GATE_RULE_ID} failed, so the Arbiter verdict is masked\n\n{self.diagnosis()}",
        )
        self.assertTrue((self.root / ma.ARBITER_GATE_OUT_DIR / "report.sarif").is_file(), self.diagnosis())
        # REQ-047: the tests rule is required, and the adopted registry has no suites, so `omni test run
        # --impacted` has nothing to run and passes; it must have run (not been skipped or memoised) here.
        self.assertRegex(output, TESTS_RULE_RAN, f"completion.tests did not run and pass\n\n{self.diagnosis()}")

        app.write_text(PLANTED.replace("verify=False", "verify=True"), encoding="utf-8")
        output = self.gate(0)
        self.assertIn("omni gate: PASS", output, self.diagnosis())
        self.assertRegex(output, TESTS_RULE_RAN, f"the fix changed src/app.py, so completion.tests must run again\n\n{self.diagnosis()}")


if __name__ == "__main__":
    unittest.main()
