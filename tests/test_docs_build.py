"""REQ-060: `omni docs build` -- generated engineering documents and Mermaid diagrams.

Every generator runs against tests/fixtures/docs_project, a five-module project with a committed code graph (so no
test needs tree-sitter or git), a registry, a failure ledger, a suite registry and deployment descriptors. Each
test asserts content only its generator produces, so removing a generator fails its test. The Mermaid validator
here is independent of the generator on purpose. Stdlib only:

    python3 -m unittest tests.test_docs_build -v
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import make_ai as ma  # noqa: E402
import omni_docs  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "docs_project"
EXPECTED_FILES = {
    "index.md", "architecture.md", "design.md", "requirements.md", "requirements-traceability.md",
    "test-plan.md", "modules.md", "class-diagram.md", "CHANGES-SUMMARY.md",
}


# --------------------------------------------------------------------------
# Structural Mermaid validator (independent of omni_docs)
# --------------------------------------------------------------------------

ID = r"[A-Za-z][A-Za-z0-9_]*"
LABEL = r'"(?:[^"<>`]|<br/>)*"'  # <br/> is Mermaid's line break; any other tag is unescaped
RESERVED = {"end", "graph", "subgraph", "flowchart", "class", "classDiagram", "style", "click", "default"}
FLOW_PATTERNS = [
    re.compile(rf"^(?P<id>{ID})\[{LABEL}\]$"),
    re.compile(rf"^(?P<a>{ID}) (-->|-\.->|==>)(\|[A-Za-z0-9 ]+\|)? (?P<b>{ID})$"),
    re.compile(rf"^subgraph (?P<id>{ID}) \[{LABEL}\]$"),
    re.compile(r"^end$"),
]
CLASS_PATTERNS = [
    re.compile(rf"^class (?P<id>{ID})\[{LABEL}\] \{{$"),
    re.compile(rf"^class (?P<id>{ID})\[{LABEL}\]$"),
    re.compile(r"^\+[A-Za-z_][A-Za-z0-9_]*\(([A-Za-z_][A-Za-z0-9_]*(, [A-Za-z_][A-Za-z0-9_]*)*)?\)$"),
    re.compile(r"^\}$"),
    re.compile(rf"^<<external>> (?P<id>{ID})$"),
    re.compile(rf"^(?P<a>{ID}) <\|-- (?P<b>{ID})$"),
    re.compile(rf"^(?P<a>{ID}) \.\.> (?P<b>{ID}) : [a-z ]+$"),
]


def mermaid_blocks(text: str) -> list[list[str]]:
    blocks, current = [], None
    for line in text.splitlines():
        if line.strip() == "```mermaid":
            if current is not None:
                raise AssertionError("nested mermaid fence")
            current = []
        elif line.strip() == "```" and current is not None:
            blocks.append(current)
            current = None
        elif current is not None:
            current.append(line)
    if current is not None:
        raise AssertionError("unterminated mermaid fence")
    return blocks


def validate_mermaid(lines: list[str]) -> list[str]:
    """Problems in one diagram: unknown type, unbalanced subgraph/class blocks, illegal ids, unescaped labels,
    edges to undeclared nodes, statements this validator does not recognise."""
    body = [line.strip() for line in lines if line.strip() and not line.strip().startswith("%%")]
    if not body:
        return ["empty diagram"]
    kind, rest = body[0], body[1:]
    problems: list[str] = []
    declared: set[str] = set()
    referenced: list[tuple[str, str]] = []
    if re.fullmatch(r"flowchart (TD|LR|TB|RL|BT)", kind):
        patterns, depth = FLOW_PATTERNS, 0
        for line in rest:
            match = next((m for m in (p.match(line) for p in patterns) if m), None)
            if not match:
                problems.append(f"unrecognised flowchart line: {line}")
                continue
            if line.startswith("subgraph "):
                depth += 1
            elif line == "end":
                depth -= 1
                if depth < 0:
                    problems.append("`end` without subgraph")
            groups = match.groupdict()
            if groups.get("id"):
                declared.add(groups["id"])
            if groups.get("a"):
                referenced += [(groups["a"], line), (groups["b"], line)]
        if depth != 0:
            problems.append(f"unbalanced subgraph blocks (depth {depth})")
    elif kind == "classDiagram":
        open_class = False
        for line in rest:
            match = next((m for m in (p.match(line) for p in CLASS_PATTERNS) if m), None)
            if not match:
                problems.append(f"unrecognised classDiagram line: {line}")
                continue
            if line.endswith("{"):
                if open_class:
                    problems.append("nested class block")
                open_class = True
            elif line == "}":
                if not open_class:
                    problems.append("`}` without class block")
                open_class = False
            elif line.startswith("+") and not open_class:
                problems.append(f"member outside a class block: {line}")
            groups = match.groupdict()
            if groups.get("id"):
                declared.add(groups["id"])
            if groups.get("a"):
                referenced += [(groups["a"], line), (groups["b"], line)]
        if open_class:
            problems.append("unclosed class block")
    else:
        return [f"unknown diagram type: {kind}"]
    for ident in declared:
        if ident in RESERVED:
            problems.append(f"reserved word as id: {ident}")
    for ident, line in referenced:
        if ident not in declared:
            problems.append(f"edge to undeclared node {ident}: {line}")
    return problems


def render_fixture(out_dir: Path) -> dict[str, str]:
    return omni_docs.render_all(FIXTURE, out_dir, FIXTURE / ".ai/project-graph.json")


class FixtureDocs(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.out = FIXTURE / "docs" / "generated"  # links are resolved relative to here; nothing is written
        cls.docs = render_fixture(cls.out)


class TestDocumentSet(FixtureDocs):
    def test_every_document_is_rendered(self) -> None:
        self.assertEqual(set(self.docs), EXPECTED_FILES)

    def test_index_links_every_document_and_records_the_commit(self) -> None:
        index = self.docs["index.md"]
        for name in EXPECTED_FILES - {"index.md"}:
            self.assertIn(f"]({name})", index)
        self.assertIn(omni_docs.SHA_LINE_PREFIX, index)
        self.assertIn("4 source module(s), 1 test module(s)", index)

    def test_no_timestamps_or_absolute_paths(self) -> None:
        for name, text in self.docs.items():
            self.assertNotRegex(text, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", name)
            self.assertNotIn(str(FIXTURE), text, name)
            self.assertNotIn("\r", text, name)


class TestArchitecture(FixtureDocs):
    def test_sad_sections_and_views(self) -> None:
        sad = self.docs["architecture.md"]
        for heading in ("1. Introduction and goals", "2. Constraints", "3. System context", "4. Building-block view",
                        "5. Runtime view", "6. Deployment view", "7. Cross-cutting concepts", "8. Architecture decisions",
                        "9. Quality requirements", "10. Risks and technical debt", "11. Glossary"):
            self.assertIn(f"## {heading}", sad)
        self.assertIn("A tiny order-taking service", sad)
        self.assertIn('x_dep_requests["requests"]', sad)  # declared dependency in the context view
        self.assertIn("m_shop_cli_py --> m_shop_service_py", sad)  # component import edge
        self.assertIn('subgraph g_shop ["shop"]', sad)
        self.assertIn("f_shop_cli_py_main --> f_shop_service_py_checkout", sad)  # runtime view from the console script
        self.assertIn("service web", sad)  # compose service in the deployment view
        self.assertIn("REQ-003", sad)  # Performance category -> quality requirements
        self.assertIn("REQ-002: CLI output format may change", sad)  # risk note of an open requirement
        self.assertIn("FAIL-001", sad)  # open failure as a risk
        self.assertIn("_Not derived: no ADR directory", sad)

    def test_component_diagram_collapses_past_the_cap(self) -> None:
        model = omni_docs.load_model(FIXTURE, FIXTURE / ".ai/project-graph.json", self.out)
        old = omni_docs.MAX_COMPONENT_NODES
        omni_docs.MAX_COMPONENT_NODES = 3
        try:
            diagram = omni_docs.component_diagram(model)
        finally:
            omni_docs.MAX_COMPONENT_NODES = old
        node_lines = [line for line in diagram.lines if re.match(r'^\s+m_\w+\["', line)]
        self.assertLessEqual(len(node_lines), 3)
        self.assertTrue(any("collapsed" in line for line in node_lines))
        self.assertTrue(any("exceed the 3-node cap" in note for note in diagram.notes))
        self.assertEqual(validate_mermaid(diagram.lines), [])


class TestDesign(FixtureDocs):
    def test_sdd_per_module_design(self) -> None:
        sdd = self.docs["design.md"]
        self.assertIn("### Module shop/service.py", sdd)
        self.assertIn("`checkout(names: list[str]) -> int`: Price a list of item names", sdd)
        self.assertIn("`class Order(Base)`: A customer's basket of items.", sdd)
        self.assertIn("Kept free of I/O so pricing stays testable.", sdd)  # docstring rationale
        self.assertIn("Requirements scoped to this module: REQ-001", sdd)
        self.assertIn("- Imported by: `shop/cli.py`", sdd)
        self.assertIn("classDiagram", sdd)  # per-package class diagram

    def test_modules_reference(self) -> None:
        mods = self.docs["modules.md"]
        self.assertIn("| [shop/models.py](../../shop/models.py) | shop | Domain objects for orders. | 3 |", mods)

    def test_class_diagram(self) -> None:
        text = self.docs["class-diagram.md"]
        self.assertIn("C_shop_models_py_Base <|-- C_shop_models_py_Order", text)
        self.assertIn("C_shop_models_py_Order ..> C_shop_models_py_Item : creates", text)
        self.assertIn("+add(name, price)", text)


class TestRequirementsDocs(FixtureDocs):
    def test_srs(self) -> None:
        srs = self.docs["requirements.md"]
        self.assertIn("#### REQ-001: Price an order", srs)
        self.assertIn("1. total equals the sum of item prices", srs)
        self.assertIn("## 4. Non-functional requirements", srs)
        self.assertLess(srs.index("## 4. Non-functional"), srs.index("#### REQ-003"))  # Performance is non-functional
        self.assertIn('Apply a <percent> "discount" | code.', srs)

    def test_traceability(self) -> None:
        rtm = self.docs["requirements-traceability.md"]
        self.assertIn("| REQ-001: Price an order | completed | shop/models.py, shop/service.py, tests/service_checks.py | tests/service_checks.py | FAIL-001 (open) |", rtm)
        self.assertIn("| FAIL-001 | medium | REQ-001 | Total ignores quantity | shop/models.py |", rtm)
        self.assertIn("Apply a", self.docs["requirements.md"])  # pipes are escaped in tables, not in prose

    def test_test_plan(self) -> None:
        plan = self.docs["test-plan.md"]
        self.assertIn("| shop-checks | unit | plain | `python -m tests.service_checks` | tests | shop |", plan)
        self.assertIn("| REQ-001 | completed | python -m tests.service_checks |", plan)
        self.assertIn("- REQ-002 (pending): Command-line checkout", plan)  # no linked test
        self.assertIn("1 of 1 recorded failure(s) name a regression test.", plan)
        self.assertIn("_Not derived: omni keeps no stored test-run results", plan)

    def test_changes_summary(self) -> None:
        summary = self.docs["CHANGES-SUMMARY.md"]
        self.assertIn("## Recently completed\n\n_The last 10 completed, newest registry entry first._\n\n- REQ-001: Price an order", summary)
        self.assertIn("- REQ-002: Command-line checkout (pending)", summary)
        self.assertIn("## Blocked\n\n- REQ-003", summary)
        self.assertIn("- FAIL-001 (medium, REQ-001): Total ignores quantity", summary)
        # REQ-001 requires README but its commit did not touch it; REQ-002's commit did.
        self.assertIn("| REQ-001 | completed | `README.md` | 1 |", summary)
        self.assertNotIn("| REQ-002 |", summary)


class TestMermaidValidity(FixtureDocs):
    def test_every_diagram_is_structurally_valid(self) -> None:
        count = 0
        for name, text in sorted(self.docs.items()):
            for block in mermaid_blocks(text):
                count += 1
                self.assertEqual(validate_mermaid(block), [], f"{name}: {block[:3]}")
        self.assertGreaterEqual(count, 6)  # context, containers, components, runtime, deployment, classes

    def test_hostile_names_are_escaped(self) -> None:
        ids = omni_docs.Ids("n_")
        self.assertEqual(ids("end"), "n_end")
        self.assertNotEqual(ids("a-b"), ids("a.b"))  # collision gets a suffix
        label = omni_docs.mlabel('say "hi" <b> & `x`')
        self.assertNotRegex(label, r'["<>`]')
        self.assertEqual(validate_mermaid(["flowchart LR", f'    {ids("end")}["{label}"]']), [])

    def test_validator_rejects_broken_diagrams(self) -> None:
        self.assertTrue(validate_mermaid(["flowchart LR", '    subgraph g ["x"]', '    a["y"]']))  # unbalanced
        self.assertTrue(validate_mermaid(["flowchart LR", '    a["say "hi""]']))  # raw quote
        self.assertTrue(validate_mermaid(["flowchart LR", '    a["x"]', "    a --> b"]))  # undeclared node
        self.assertTrue(validate_mermaid(["classDiagram", '    class A["A"] {', "        +f()"]))  # unclosed


class TestDeterminism(unittest.TestCase):
    def test_two_processes_with_different_hash_seeds_write_identical_bytes(self) -> None:
        digests = []
        with tempfile.TemporaryDirectory() as tmp:
            for seed in ("1", "4242"):
                out = Path(tmp) / f"out{seed}"
                env = {**os.environ, "PYTHONHASHSEED": seed}
                result = subprocess.run(
                    [sys.executable, str(ROOT / "omni"), "docs", "build", "--root", str(FIXTURE), "--output", str(out), "--no-graph-rebuild"],
                    capture_output=True, text=True, env=env, cwd=tmp,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                digests.append({p.name: p.read_bytes() for p in sorted(out.iterdir())})
        self.assertEqual(set(digests[0]), EXPECTED_FILES)
        self.assertEqual(digests[0], digests[1])

    def test_second_build_writes_nothing_and_check_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "docs"
            graph = FIXTURE / ".ai/project-graph.json"
            first = omni_docs.build_docs(FIXTURE, out, graph)
            self.assertEqual(sorted(first["written"]), sorted(EXPECTED_FILES))
            second = omni_docs.build_docs(FIXTURE, out, graph)
            self.assertEqual(second["written"], [])
            self.assertEqual(omni_docs.check_docs(FIXTURE, out, graph), [])
            index = out / "index.md"
            index.write_bytes(re.sub(rb"Generated from commit [^\n]*", b"Generated from commit `deadbeef`.", index.read_bytes()))
            self.assertEqual(omni_docs.check_docs(FIXTURE, out, graph), [], "the commit line alone never makes docs stale")
            (out / "design.md").write_bytes(b"edited\n")
            self.assertEqual(omni_docs.check_docs(FIXTURE, out, graph), ["design.md"])


class TestNoGraph(unittest.TestCase):
    def test_missing_graph_marks_code_views_not_derived(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs = omni_docs.render_all(FIXTURE, Path(tmp), Path(tmp) / "missing.json")
        self.assertIn("_Not derived: no code graph._", docs["architecture.md"])
        self.assertIn("No code graph", docs["index.md"])
        self.assertIn("#### REQ-001", docs["requirements.md"])  # registry-only documents still render


class TestRequirementHook(unittest.TestCase):
    """`omni requirement add|update|complete` regenerate docs in a workspace that has built them once."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        shutil.copytree(FIXTURE, self.root)
        previous = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        omni_docs.build_docs(self.root, self.root / "docs/generated", self.root / ".ai/project-graph.json")

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ma.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def srs(self) -> str:
        return (self.root / "docs/generated/requirements.md").read_text(encoding="utf-8")

    def test_complete_regenerates(self) -> None:
        self.assertIn("**Status:** pending", self.srs().split("#### REQ-002")[1].split("####")[0])
        code, output = self.run_cli("requirement", "complete", "REQ-002")
        self.assertEqual(code, 0, output)
        self.assertIn("docs: regenerated", output)
        self.assertIn("**Status:** completed", self.srs().split("#### REQ-002")[1].split("####")[0])

    def test_add_and_status_update_regenerate(self) -> None:
        code, output = self.run_cli("requirement", "add", "--title", "Gift wrap", "--description", "Wrap it")
        self.assertEqual(code, 0, output)
        self.assertIn("Gift wrap", self.srs())
        code, output = self.run_cli("requirement", "update", "REQ-005", "--status", "blocked")
        self.assertEqual(code, 0, output)
        self.assertIn("## Blocked\n\n- REQ-003: Checkout under 10 ms\n- REQ-005: Gift wrap", (self.root / "docs/generated/CHANGES-SUMMARY.md").read_text(encoding="utf-8"))

    def test_note_only_update_and_no_docs_flag_skip(self) -> None:
        before = self.srs()
        code, output = self.run_cli("requirement", "update", "REQ-002", "--note", "just a note")
        self.assertEqual(code, 0, output)
        self.assertNotIn("docs: regenerated", output)
        code, output = self.run_cli("requirement", "complete", "REQ-002", "--no-docs")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.srs(), before)

    def test_config_switch_disables(self) -> None:
        ruleset = self.root / ".ai/rules/universal-engineering-ruleset.json"
        ruleset.parent.mkdir(parents=True, exist_ok=True)
        ruleset.write_text(json.dumps({"configuration": {"docs_auto_regenerate": False}}), encoding="utf-8")
        before = self.srs()
        code, output = self.run_cli("requirement", "complete", "REQ-002")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.srs(), before)

    def test_workspace_without_built_docs_is_left_alone(self) -> None:
        shutil.rmtree(self.root / "docs")
        code, output = self.run_cli("requirement", "complete", "REQ-002")
        self.assertEqual(code, 0, output)
        self.assertFalse((self.root / "docs").exists())

    def test_gate_freshness_is_opt_in(self) -> None:
        (self.root / "docs/generated/design.md").write_bytes(b"stale\n")
        self.assertIsNone(ma.gate_docs_freshness_failure())
        ruleset = self.root / ".ai/rules/universal-engineering-ruleset.json"
        ruleset.parent.mkdir(parents=True, exist_ok=True)
        ruleset.write_text(json.dumps({"configuration": {"docs_gate_freshness": True}}), encoding="utf-8")
        failure = ma.gate_docs_freshness_failure()
        self.assertIsNotNone(failure)
        self.assertIn("design.md", failure)


class TestShipping(unittest.TestCase):
    def test_generator_and_skill_ship_with_adopt_and_update(self) -> None:
        self.assertIn("omni_docs.py", ma.ADOPTION_CLI_FILES)  # so `omni update` merges it like the rest of the CLI
        self.assertIn(".claude/skills/swe-docs", ma.ADOPTION_TOOL_FILES["claude"])
        self.assertTrue((ROOT / ".claude/skills/swe-docs/SKILL.md").is_file())


class TestWorkflowSkills(unittest.TestCase):
    def test_skill_links_appear_only_when_the_skill_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proj"
            shutil.copytree(FIXTURE, root)
            docs = omni_docs.render_all(root, root / "docs/generated", root / ".ai/project-graph.json")
            self.assertNotIn("Related assistant workflow", docs["test-plan.md"])
            skill = root / ".claude/skills/test-debug/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("---\nname: test-debug\n---\n", encoding="utf-8")
            docs = omni_docs.render_all(root, root / "docs/generated", root / ".ai/project-graph.json")
            self.assertIn("Related assistant workflow: [test-debug](../../.claude/skills/test-debug/SKILL.md)", docs["test-plan.md"])
            self.assertNotIn("Related assistant workflow", docs["requirements.md"])  # clarify is absent


if __name__ == "__main__":
    unittest.main()
