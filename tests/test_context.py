"""The shared excerpt builder (arbiter.context, ARB-067): no network, nothing run."""
from __future__ import annotations

from arbiter import context
from arbiter.inventory import FileInfo, Inventory

FLASK = '''from flask import request
import subprocess


def init(app):

    @app.route("/benchmark/cmdi-00/Handler", methods=["POST"])
    def handler_post():
        parts = request.path.split("/")
        param = parts[1]
        return subprocess.run("echo " + param, shell=True)
'''

JAVA_MAIN = '''package org.example.web;

import javax.servlet.http.HttpServletRequest;
import org.example.helpers.SafeRequest;

@WebServlet(value = "/sqli/Handler")
public class Handler extends HttpServlet {
    @Override
    public void doPost(HttpServletRequest request) throws Exception {
        SafeRequest scr = new SafeRequest(request);
        String param = scr.getTheValue("Handler");
        String sql = "SELECT * FROM users WHERE name='" + param + "'";
        statement.execute(sql);
    }
}
'''

JAVA_HELPER = '''package org.example.helpers;

public class SafeRequest {
    private final HttpServletRequest request;

    public SafeRequest(HttpServletRequest request) { this.request = request; }

    public String getTheParameter(String p) {
        return request.getParameter(p);
    }

    public String getTheValue(String p) {
        return "bar";
    }
}
'''

# An unrelated class elsewhere with the same method name: never shown, since
# nothing in the handler's file ties `scr` to it.
JAVA_DECOY = '''package org.other;

public class Decoy {
    public String getTheValue(String p) { return System.getenv(p); }
}
'''


def _repo(files: dict[str, str]) -> context.RepoView:
    return context.RepoView(paths=list(files), read_raw=lambda rel: files.get(rel, ""))


def test_python_decorator_and_route_are_in_the_excerpt():
    ctx = context.build(FLASK, "app/handler.py", 11)
    assert '@app.route("/benchmark/cmdi-00/Handler", methods=["POST"])' in ctx.text
    assert ">>   11" in ctx.text and "import subprocess" in ctx.text
    assert 7 in ctx.lines and ctx.start == 7


def test_typescript_decorators_are_part_of_the_function():
    src = ("import { Get } from '@nestjs/common';\n\nclass C {\n  @Get('/x')\n  @UseGuards(A)\n"
           "  handle(q: string) {\n    return run(q);\n  }\n}\n")
    ctx = context.build(src, "c.ts", 7)
    assert "@Get('/x')" in ctx.text and "@UseGuards(A)" in ctx.text and "class C {" in ctx.text


def test_express_route_registration_is_shown():
    src = ("const express = require('express');\nconst app = express();\n\n"
           "function show(req, res) {\n  res.send(req.query.q);\n}\n\n"
           + "\n".join(f"const pad{i} = {i};" for i in range(60))
           + "\napp.get('/show', show);\n")
    ctx = context.build(src, "srv.js", 5)
    assert "app.get('/show', show);" in ctx.text


def test_cross_file_definition_on_the_taint_path_is_shown_and_resolved_by_type():
    repo = _repo({"src/org/example/web/Handler.java": JAVA_MAIN,
                  "src/org/example/helpers/SafeRequest.java": JAVA_HELPER,
                  "src/org/other/Decoy.java": JAVA_DECOY})
    ctx = context.build(JAVA_MAIN, "src/org/example/web/Handler.java", 13, repo=repo)
    assert ctx.definitions == [("src/org/example/helpers/SafeRequest.java", "getTheValue")]
    assert "definition of `getTheValue`, called as `scr.getTheValue(...)` at line 11" in ctx.text
    assert 'return "bar";' in ctx.text
    assert "System.getenv" not in ctx.text        # the decoy is never a candidate
    assert "getTheParameter" not in ctx.text      # only the method called
    assert '@WebServlet(value = "/sqli/Handler")' in ctx.text   # class header
    # own-file lines only: a verdict line from the other file is not "shown"
    assert max(ctx.lines) <= len(JAVA_MAIN.splitlines())


def test_a_name_alone_never_pulls_in_another_file():
    main = "def run(x):\n    y = clean(x)\n    return os.system(y)\n"
    repo = _repo({"app/main.py": main, "lib/util.py": "def clean(v):\n    return 'safe'\n"})
    ctx = context.build(main, "app/main.py", 3, repo=repo)
    assert ctx.definitions == [] and "safe" not in ctx.text


def test_python_import_resolution():
    main = ("from helpers.util import clean\n\n\ndef run(x):\n    y = clean(x)\n"
            "    return os.system(y)\n")
    repo = _repo({"app/main.py": main, "helpers/util.py": "def clean(v):\n    return 'safe'\n"})
    ctx = context.build(main, "app/main.py", 6, repo=repo)
    assert ctx.definitions == [("helpers/util.py", "clean")] and "return 'safe'" in ctx.text


def test_excerpt_is_bounded_masked_and_deterministic():
    body = "\n".join(f"    v{i} = h{i % 5}(v{i - 1})" for i in range(1, 400))
    helpers = "\n".join(f"def h{k}(x):\n" + "\n".join(f"    x = x + {j}" for j in range(80)) + "\n    return x\n"
                        for k in range(5))
    main = ('from lib.secret import h0, h1, h2, h3, h4\nKEY = "sk_live_abcdefghijklmnop1234"\n\n\n'
            f"def run(v0):\n{body}\n    return v399\n")
    other = 'API_TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"\n\n' + helpers
    repo = _repo({"app/main.py": main, "lib/secret.py": other})
    limits = context.Limits(function_lines=80, total=150)
    a = context.build(main, "app/main.py", 300, repo=repo, limits=limits)
    b = context.build(main, "app/main.py", 300, repo=repo, limits=limits)
    assert a.text == b.text
    shown = [ln for ln in a.text.splitlines() if ln[:7].strip().lstrip(">").strip().isdigit()]
    assert len(shown) <= 150
    assert len(a.definitions) <= context.LIMITS.defs and a.definitions
    assert "sk_live_abcdefghijklmnop1234" not in a.text
    for path, _name in a.definitions:
        assert path == "lib/secret.py"
    # each definition is cut to def_lines
    assert "x = x + 40" not in a.text


def test_blinding_transform_and_shown_path_apply_to_other_files():
    repo = context.RepoView(
        paths=["src/org/example/web/Handler.java", "src/org/example/helpers/SafeRequest.java"],
        read_raw=lambda rel: JAVA_HELPER.replace("return \"bar\";", "return \"bar\"; // safe source")
        if rel.endswith("SafeRequest.java") else JAVA_MAIN,
        transform=lambda text, rel: text.replace("// safe source", ""),
        shown=lambda rel: "hidden/" + rel.rsplit("/", 1)[-1])
    ctx = context.build(JAVA_MAIN, "src/org/example/web/Handler.java", 13, repo=repo)
    assert "safe source" not in ctx.text and "--- hidden/SafeRequest.java:" in ctx.text


def test_verify_excerpt_uses_the_inventory_for_cross_file_definitions(tmp_path):
    from arbiter import verify
    from arbiter.core import Finding, Location
    files = {"src/org/example/web/Handler.java": JAVA_MAIN,
             "src/org/example/helpers/SafeRequest.java": JAVA_HELPER}
    inv = Inventory()
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        fi = FileInfo(path=rel, abspath=str(p), repo_id="r", language="java")
        inv.files.append(fi)
        inv.by_repo.setdefault("r", []).append(fi)
    f = Finding(rule_id="arbiter/java.sql-string-built", title="t", severity="high",
                location=Location(path="src/org/example/web/Handler.java", start_line=13), repo_id="r")
    ex = verify.excerpt_for(f, inv, 120)
    assert ex.definitions == [("src/org/example/helpers/SafeRequest.java", "getTheValue")]
    assert 'return "bar";' in ex.text and "@Override" in ex.text
    assert 4 not in ex.lines or "SafeRequest" in JAVA_MAIN.splitlines()[3]
