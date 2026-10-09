#!/usr/bin/env python3
"""Defect injection: measured recall and specificity at scale.

Two populations, both generated, both labelled by construction:

  POSITIVES  a known defect planted into a real file from the corpus. The rule
             that should fire is known, so a miss is measured recall loss.
  CONTROLS   a case that looks like the defect but is not one. The rule must
             stay silent, so a fire is a measured false alarm.

Seeds come from the public corpus rather than from templates, because
effective sample size is bounded by generator diversity, not by trial count.
Ten million instances of one hand-written template is one observation with
noise on it; ten thousand defects planted into ten thousand real files from
twenty-seven projects is closer to ten thousand.

Controls matter more than positives. A rule that fires on everything has
perfect recall.
"""
from __future__ import annotations

import argparse
import random
import shutil
import string
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from arbiter.core import RepoInfo  # noqa: E402
from arbiter.graph import build_graph  # noqa: E402
from arbiter.inventory import build_inventory  # noqa: E402
from arbiter.learn import Knowledge, record_synthetic  # noqa: E402
from arbiter.probes import ProbeContext, probe_by_name  # noqa: E402

rng = random.Random(20260912)

ALNUM_UPPER = string.ascii_uppercase + string.digits
ALNUM = string.ascii_letters + string.digits


# ---------------------------------------------------------------------------
# Seed mining — real files from the corpus, grouped by what they can carry
# ---------------------------------------------------------------------------

SEED_KINDS = {
    "python":   (".py",),
    "js":       (".js", ".ts"),
    "go":       (".go",),
    "terraform": (".tf",),
    "k8s":      (".yaml", ".yml"),
    "workflow": (".yml", ".yaml"),
    "markdown": (".md",),
    "requirements": ("requirements.txt",),
    "packagejson": ("package.json",),
}


@dataclass
class Seeds:
    by_kind: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    def pick(self, kind: str) -> tuple[str, str] | None:
        pool = self.by_kind.get(kind) or []
        return rng.choice(pool) if pool else None

    def count(self) -> dict[str, int]:
        return {k: len(v) for k, v in sorted(self.by_kind.items())}


def mine_seeds(root: Path, per_kind: int = 400, max_bytes: int = 60_000) -> Seeds:
    seeds = Seeds()
    for kind in SEED_KINDS:
        seeds.by_kind[kind] = []

    for repo in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in repo.rglob("*"):
            if not path.is_file() or ".git/" in str(path):
                continue
            try:
                if path.stat().st_size > max_bytes or path.stat().st_size < 40:
                    continue
                text = path.read_text(errors="strict")
            except (OSError, UnicodeDecodeError):
                continue
            name = path.name
            suffix = path.suffix

            def add(kind: str) -> None:
                bucket = seeds.by_kind[kind]
                if len(bucket) < per_kind:
                    bucket.append((name, text))

            if suffix == ".py":
                add("python")
            elif suffix in (".js", ".ts"):
                add("js")
            elif suffix == ".go":
                add("go")
            elif suffix == ".tf":
                add("terraform")
            elif suffix == ".md":
                add("markdown")
            elif name in ("requirements.txt", "requirements-dev.txt"):
                add("requirements")
            elif name == "package.json":
                add("packagejson")
            elif suffix in (".yaml", ".yml"):
                if "workflows/" in str(path):
                    add("workflow")
                elif "apiversion:" in text.lower() and "kind:" in text.lower():
                    add("k8s")
    return seeds


# ---------------------------------------------------------------------------
# Value generators — variation inside each defect class
# ---------------------------------------------------------------------------

def aws_key() -> str:
    return rng.choice(("AKIA", "ASIA")) + "".join(rng.choices(ALNUM_UPPER, k=16))


def strong_secret(n: int = 28) -> str:
    body = "".join(rng.choices(ALNUM + "-_", k=n - 2))
    return rng.choice(string.ascii_uppercase) + body + rng.choice(string.digits)


def pem_block() -> str:
    kind = rng.choice(("RSA PRIVATE KEY", "EC PRIVATE KEY", "PRIVATE KEY", "OPENSSH PRIVATE KEY"))
    body = "\n".join("".join(rng.choices(ALNUM + "+/", k=64)) for _ in range(rng.randint(2, 5)))
    return f"-----BEGIN {kind}-----\n{body}\n-----END {kind}-----"


def assign(language: str, name: str, value: str) -> str:
    if language == "python":
        q = rng.choice(('"', "'"))
        return f"{name} = {q}{value}{q}"
    if language == "js":
        q = rng.choice(('"', "'", "`"))
        kw = rng.choice(("const", "let", "var"))
        return f"{kw} {name} = {q}{value}{q};"
    if language == "go":
        return f'\t{name} := "{value}"'
    return f'{name} = "{value}"'


def splice(text: str, line: str) -> str:
    """Insert a line at a plausible position inside a real file."""
    lines = text.split("\n")
    if len(lines) < 3:
        return text + "\n" + line + "\n"
    at = rng.randint(1, len(lines) - 1)
    lines.insert(at, line)
    return "\n".join(lines)


CRED_NAMES = ["API_KEY", "apiKey", "SECRET_TOKEN", "db_password", "DB_PASSWORD",
              "clientSecret", "ACCESS_KEY", "authToken", "servicePassword",
              "PRIVATE_KEY_DATA", "session_secret"]

PLACEHOLDERS = ["your-api-key-here", "changeme", "xxxxxxxx", "<your-token>",
                "${{ secrets.GITHUB_TOKEN }}", "${API_KEY}", "{{ vault_password }}",
                "process.env.SECRET", "os.environ['TOKEN']", "TODO", "example",
                "$(cat /run/secrets/db)", "%(password)s"]

STATUS_SUFFIXES = ["_status", "_id", "_name", "_arn", "_path", "_type", "_enabled", "_url"]


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

@dataclass
class Case:
    rule: str          # rule id that should (or must not) fire
    probe: str
    filename: str
    content: str
    positive: bool     # True = defect planted, False = control
    extra: dict = field(default_factory=dict)
    # The unmodified seed. Findings the seed already produced are subtracted,
    # so a control is scored on what the harness added, not on what the real
    # file happened to contain.
    baseline: str = ""


def case_aws_key(seeds: Seeds) -> Case | None:
    lang = rng.choice(("python", "js", "go"))
    seed = seeds.pick(lang)
    if not seed:
        return None
    name, text = seed
    line = assign(lang, rng.choice(["AWS_ACCESS_KEY_ID", "awsKey", "ACCESS_KEY_ID"]), aws_key())
    return Case("arbiter/secrets.aws-access-key", "secrets", name, splice(text, line), True, baseline=text)


def case_private_key(seeds: Seeds) -> Case | None:
    return Case("arbiter/secrets.private-key", "secrets",
                rng.choice(("server.key", "id_rsa", "deploy.pem", "tls.key")),
                pem_block(), True)


def case_gh_token(seeds: Seeds) -> Case | None:
    lang = rng.choice(("python", "js"))
    seed = seeds.pick(lang)
    if not seed:
        return None
    name, text = seed
    token = rng.choice(("ghp_", "gho_", "ghs_")) + "".join(rng.choices(ALNUM, k=rng.randint(36, 40)))
    return Case("arbiter/secrets.gh-token", "secrets", name,
                splice(text, assign(lang, "GITHUB_TOKEN", token)), True, baseline=text)


def case_db_url(seeds: Seeds) -> Case | None:
    seed = seeds.pick("python")
    if not seed:
        return None
    name, text = seed
    scheme = rng.choice(("postgres", "postgresql", "mysql", "mongodb"))
    url = f"{scheme}://svc_{rng.randint(1,999)}:{strong_secret(20)}@db.internal:{rng.choice((5432,3306,27017))}/app"
    return Case("arbiter/secrets.pg-url", "secrets", name,
                splice(text, assign("python", "DATABASE_URL", url)), True, baseline=text)


def case_assigned_credential(seeds: Seeds) -> Case | None:
    lang = rng.choice(("python", "js", "go"))
    seed = seeds.pick(lang)
    if not seed:
        return None
    name, text = seed
    line = assign(lang, rng.choice(CRED_NAMES), strong_secret(rng.randint(16, 40)))
    return Case("arbiter/secrets.assigned-credential", "secrets", name, splice(text, line), True, baseline=text)


# -- secret controls --------------------------------------------------------

def control_placeholder(seeds: Seeds) -> Case | None:
    lang = rng.choice(("python", "js"))
    seed = seeds.pick(lang)
    if not seed:
        return None
    name, text = seed
    line = assign(lang, rng.choice(CRED_NAMES), rng.choice(PLACEHOLDERS))
    return Case("arbiter/secrets.assigned-credential", "secrets", name, splice(text, line), False, baseline=text)


def control_status_field(seeds: Seeds) -> Case | None:
    seed = seeds.pick("python")
    if not seed:
        return None
    name, text = seed
    sym = rng.choice(["access_key", "api_key", "secret", "token"]) + rng.choice(STATUS_SUFFIXES)
    line = assign("python", sym, rng.choice(["Inactive", "Active", "pending", "v2", "enabled"]))
    return Case("arbiter/secrets.assigned-credential", "secrets", name, splice(text, line), False, baseline=text)


def control_passphrase(seeds: Seeds) -> Case | None:
    seed = seeds.pick("js")
    if not seed:
        return None
    name, text = seed
    phrase = rng.choice(["keyboard cat", "my dev secret", "local testing only",
                         "some random words here", "not a real secret"])
    return Case("arbiter/secrets.assigned-credential", "secrets", name,
                splice(text, assign("js", "secret", phrase)), False, baseline=text)


def control_trivial_db_url(seeds: Seeds) -> Case | None:
    seed = seeds.pick("python")
    if not seed:
        return None
    name, text = seed
    u = rng.choice(("postgres", "root", "mysql", "admin"))
    url = f"{rng.choice(('postgres','mysql'))}://{u}:{u}@db:{rng.choice((5432,3306))}/app"
    return Case("arbiter/secrets.pg-url", "secrets", name,
                splice(text, assign("python", "DATABASE_URL", url)), False,
                {"expect_severity_at_most": "low"}, baseline=text)


def control_self_referential(seeds: Seeds) -> Case | None:
    seed = seeds.pick("python")
    if not seed:
        return None
    name, text = seed
    sym = rng.choice(["secret_key", "api_token", "client_secret"])
    return Case("arbiter/secrets.assigned-credential", "secrets", name,
                splice(text, assign("python", sym, sym)), False, baseline=text)


# -- terraform --------------------------------------------------------------

def _tf_bucket(acl: str, encrypted: bool) -> str:
    n = rng.randint(1, 99999)
    enc = ('\n  server_side_encryption_configuration {\n'
           '    rule {\n      apply_server_side_encryption_by_default {\n'
           '        sse_algorithm = "aws:kms"\n      }\n    }\n  }\n') if encrypted else ""
    return f'resource "aws_s3_bucket" "b{n}" {{\n  bucket = "bkt-{n}"\n  acl    = "{acl}"\n{enc}}}\n'


def case_public_bucket(seeds: Seeds) -> Case | None:
    return Case("arbiter/resource.public-object-store", "resource_policy", "main.tf",
                _tf_bucket(rng.choice(("public-read", "public-read-write")), rng.random() < 0.5), True)


def control_private_bucket(seeds: Seeds) -> Case | None:
    return Case("arbiter/resource.public-object-store", "resource_policy", "main.tf",
                _tf_bucket(rng.choice(("private", "log-delivery-write")), True), False)


def case_unencrypted_db(seeds: Seeds) -> Case | None:
    n = rng.randint(1, 99999)
    engine = rng.choice(("postgres", "mysql", "mariadb", "oracle-se2"))
    return Case("arbiter/resource.unencrypted-database", "resource_policy", "db.tf",
                f'resource "aws_db_instance" "d{n}" {{\n  identifier = "d{n}"\n'
                f'  engine     = "{engine}"\n  storage_encrypted = false\n}}\n', True)


def control_encrypted_db(seeds: Seeds) -> Case | None:
    n = rng.randint(1, 99999)
    prop = rng.choice(('storage_encrypted   = true',
                       'storage_encrypted   = true\n  kms_key_id = "arn:aws:kms:::key/abc"'))
    return Case("arbiter/resource.unencrypted-database", "resource_policy", "db.tf",
                f'resource "aws_db_instance" "d{n}" {{\n  engine = "postgres"\n  {prop}\n}}\n', False)


def case_open_ingress(seeds: Seeds) -> Case | None:
    n = rng.randint(1, 99999)
    port = rng.choice((22, 3389, 5432, 6379, 27017, 0))
    cidr = rng.choice(("0.0.0.0/0", "::/0"))
    return Case("arbiter/resource.unrestricted-ingress", "resource_policy", "sg.tf",
                f'resource "aws_security_group" "s{n}" {{\n  ingress {{\n'
                f'    from_port   = {port}\n    to_port     = {port}\n'
                f'    protocol    = "tcp"\n    cidr_blocks = ["{cidr}"]\n  }}\n}}\n', True)


def control_closed_ingress(seeds: Seeds) -> Case | None:
    n = rng.randint(1, 99999)
    cidr = rng.choice(("10.0.0.0/8", "172.16.0.0/12", "192.168.1.0/24"))
    return Case("arbiter/resource.unrestricted-ingress", "resource_policy", "sg.tf",
                f'resource "aws_security_group" "s{n}" {{\n  ingress {{\n'
                f'    from_port   = 443\n    to_port     = 443\n'
                f'    protocol    = "tcp"\n    cidr_blocks = ["{cidr}"]\n  }}\n'
                f'  egress {{\n    from_port = 0\n    to_port = 0\n'
                f'    protocol = "-1"\n    cidr_blocks = ["0.0.0.0/0"]\n  }}\n}}\n', False)


# -- kubernetes -------------------------------------------------------------

def _k8s_workload(security: str, kind: str = "Deployment") -> str:
    n = rng.randint(1, 99999)
    return (f"apiVersion: apps/v1\nkind: {kind}\nmetadata:\n  name: w{n}\n"
            f"spec:\n  template:\n    spec:\n      containers:\n"
            f"      - name: app\n        image: registry.example/app:1.2.3\n{security}")


HARDENED = ("        securityContext:\n          allowPrivilegeEscalation: false\n"
            "          runAsNonRoot: true\n          readOnlyRootFilesystem: true\n"
            "          capabilities:\n            drop: [\"ALL\"]\n"
            "        resources:\n          limits:\n            cpu: \"1\"\n"
            "            memory: 256Mi\n")


def case_privileged_container(seeds: Seeds) -> Case | None:
    return Case("arbiter/resource.privileged-container", "resource_policy", "deploy.yaml",
                _k8s_workload("        securityContext:\n          privileged: true\n",
                              rng.choice(("Deployment", "DaemonSet", "StatefulSet"))), True)


def case_host_namespace(seeds: Seeds) -> Case | None:
    flag = rng.choice(("hostPID", "hostIPC"))
    n = rng.randint(1, 99999)
    return Case("arbiter/resource.k8s-host-namespace", "resource_policy", "deploy.yaml",
                f"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: w{n}\n"
                f"spec:\n  template:\n    spec:\n      {flag}: true\n      containers:\n"
                f"      - name: app\n        image: app:1.0\n{HARDENED}", True)


def case_dangerous_capability(seeds: Seeds) -> Case | None:
    cap = rng.choice(("SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE", "SYS_MODULE"))
    sec = ("        securityContext:\n          allowPrivilegeEscalation: false\n"
           "          runAsNonRoot: true\n          readOnlyRootFilesystem: true\n"
           f"          capabilities:\n            add: [\"{cap}\"]\n            drop: [\"ALL\"]\n"
           "        resources:\n          limits:\n            cpu: \"1\"\n")
    return Case("arbiter/resource.k8s-dangerous-capabilities", "resource_policy",
                "deploy.yaml", _k8s_workload(sec), True)


def case_no_security_context(seeds: Seeds) -> Case | None:
    """The kustomizegoat shape: nothing dangerous present, everything absent."""
    n = rng.randint(1, 99999)
    kind = rng.choice(("Deployment", "StatefulSet", "DaemonSet", "Job"))
    return Case("arbiter/resource.k8s-no-security-context", "resource_policy", "deploy.yaml",
                f"apiVersion: apps/v1\nkind: {kind}\nmetadata:\n  name: w{n}\n"
                f"spec:\n  template:\n    spec:\n      containers:\n"
                f"      - name: app\n        image: registry.example/app:2.1\n", True)


def control_hardened_workload(seeds: Seeds) -> Case | None:
    return Case("arbiter/resource.k8s-no-security-context", "resource_policy", "deploy.yaml",
                _k8s_workload(HARDENED), False)


# -- TLS in transit ---------------------------------------------------------
#
# Each of these has a control that is the SAME resource correctly configured.
# A rule that cannot recognise the correct form fires on everything, and the
# recall number still reads 1.0000 -- which is the state six rules were in
# before controls were written for them.

_PLAINTEXT_DB = [
    ('azurerm_postgresql_server', 'sku_name = "GP_Gen5_2"'),
    ('azurerm_mysql_server', 'sku_name = "GP_Gen5_2"'),
    ('google_sql_database_instance', 'database_version = "POSTGRES_15"'),
]


def case_database_allows_plaintext(seeds: Seeds) -> Case | None:
    native, body = rng.choice(_PLAINTEXT_DB)
    return Case("arbiter/resource.database-allows-plaintext-connections",
                "resource_policy", "db.tf",
                f'resource "{native}" "d{rng.randint(1, 9999)}" {{\n'
                f'  name = "app-db"\n  {body}\n}}\n', True)


def control_database_requires_tls(seeds: Seeds) -> Case | None:
    native, body = rng.choice(_PLAINTEXT_DB)
    if native == "google_sql_database_instance":
        extra = ("  settings {\n    tier = \"db-f1-micro\"\n"
                 "    ip_configuration {\n      require_ssl = true\n    }\n  }\n")
    else:
        extra = "  ssl_enforcement_enabled = true\n"
    return Case("arbiter/resource.database-allows-plaintext-connections",
                "resource_policy", "db.tf",
                f'resource "{native}" "d{rng.randint(1, 9999)}" {{\n'
                f'  name = "app-db"\n  {body}\n{extra}}}\n', False)


_WEAK_TLS = ["TLS1_0", "TLS1_1", "1.0", "1.1",
             "Policy-Min-TLS-1-0-2019-07", "Policy-Min-TLS-1-1-2019-07"]
_STRONG_TLS = ["TLS1_2", "TLS1_3", "1.2", "1.3", "Policy-Min-TLS-1-2-2019-07"]


def case_weak_tls_version(seeds: Seeds) -> Case | None:
    return Case("arbiter/resource.weak-tls-version", "resource_policy", "st.tf",
                f'resource "azurerm_storage_account" "s{rng.randint(1, 9999)}" {{\n'
                f'  name = "stacct"\n'
                f'  customer_managed_key = azurerm_key_vault_key.k.id\n'
                f'  min_tls_version = "{rng.choice(_WEAK_TLS)}"\n}}\n', True)


def control_modern_tls_version(seeds: Seeds) -> Case | None:
    return Case("arbiter/resource.weak-tls-version", "resource_policy", "st.tf",
                f'resource "azurerm_storage_account" "s{rng.randint(1, 9999)}" {{\n'
                f'  name = "stacct"\n'
                f'  customer_managed_key = azurerm_key_vault_key.k.id\n'
                f'  min_tls_version = "{rng.choice(_STRONG_TLS)}"\n}}\n', False)


def case_no_https_redirect(seeds: Seeds) -> Case | None:
    return Case("arbiter/resource.no-https-redirect", "resource_policy", "app.tf",
                f'resource "azurerm_app_service" "a{rng.randint(1, 9999)}" {{\n'
                f'  name = "webapp"\n'
                f'  app_service_plan_id = azurerm_app_service_plan.p.id\n}}\n', True)


def control_https_only(seeds: Seeds) -> Case | None:
    return Case("arbiter/resource.no-https-redirect", "resource_policy", "app.tf",
                f'resource "azurerm_app_service" "a{rng.randint(1, 9999)}" {{\n'
                f'  name = "webapp"\n'
                f'  app_service_plan_id = azurerm_app_service_plan.p.id\n'
                f'  https_only = true\n}}\n', False)


# The control that matters most for the TLS work: a resource type whose
# provider does not express the control at all. Azure SQL always enforces TLS
# and has no property saying so, so a rule looking for one reports every Azure
# SQL database ever written -- the PersistentVolumeClaim mistake, one level
# finer.
def control_provider_does_not_express_tls(seeds: Seeds) -> Case | None:
    return Case("arbiter/resource.database-allows-plaintext-connections",
                "resource_policy", "sql.tf",
                f'resource "azurerm_mssql_database" "d{rng.randint(1, 9999)}" {{\n'
                f'  name = "app-db"\n  server_id = azurerm_mssql_server.s.id\n'
                f'  transparent_data_encryption_enabled = true\n}}\n', False)


# -- CI ---------------------------------------------------------------------

ACTIONS = ["actions/checkout", "actions/setup-python", "actions/setup-node",
           "docker/build-push-action", "aws-actions/configure-aws-credentials"]


def case_unpinned_action(seeds: Seeds) -> Case | None:
    ref = rng.choice(("v1", "v2", "v3", "v4", "main", "master", "latest"))
    return Case("arbiter/supply.unpinned-action", "supply_chain", "ci.yml",
                f"name: ci\non: [push]\njobs:\n  b:\n    runs-on: ubuntu-latest\n"
                f"    steps:\n      - uses: {rng.choice(ACTIONS)}@{ref}\n", True,
                {"dir": ".github/workflows"})


def control_pinned_action(seeds: Seeds) -> Case | None:
    sha = "".join(rng.choices("0123456789abcdef", k=40))
    return Case("arbiter/supply.unpinned-action", "supply_chain", "ci.yml",
                f"name: ci\non: [push]\njobs:\n  b:\n    runs-on: ubuntu-latest\n"
                f"    steps:\n      - uses: {rng.choice(ACTIONS)}@{sha}\n", False,
                {"dir": ".github/workflows"})


def case_dangerous_prt(seeds: Seeds) -> Case | None:
    return Case("arbiter/supply.pull-request-target", "supply_chain", "prt.yml",
                "name: prt\non:\n  pull_request_target:\njobs:\n  b:\n"
                "    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/checkout@v4\n        with:\n"
                "          ref: ${{ github.event.pull_request.head.sha }}\n"
                "      - run: npm ci && npm test\n", True,
                {"dir": ".github/workflows", "expect_severity": "high"})


def control_safe_prt(seeds: Seeds) -> Case | None:
    return Case("arbiter/supply.pull-request-target", "supply_chain", "prt.yml",
                "name: label\non:\n  pull_request_target:\n    types: [opened]\n"
                "jobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: amannn/action-semantic-pull-request@v5\n", False,
                {"dir": ".github/workflows", "expect_severity_at_most": "low"})


# -- docs -------------------------------------------------------------------

def case_broken_link(seeds: Seeds) -> Case | None:
    seed = seeds.pick("markdown")
    if not seed:
        return None
    name, text = seed
    target = f"{rng.choice(('docs','guide','notes'))}/{strong_secret(8).lower()}.md"
    return Case("arbiter/drift.broken-doc-link", "doc_drift", "README.md",
                text + f"\n\nSee [the guide]({target}).\n", True, baseline=text)


def control_valid_link(seeds: Seeds) -> Case | None:
    seed = seeds.pick("markdown")
    if not seed:
        return None
    import re as _re
    name, text = seed
    # Strip the seed's own links first. They point at files that exist in the
    # source repository but not in this one-file harness, so leaving them in
    # measures the harness rather than the rule.
    text = _re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = _re.sub(r"`[A-Za-z0-9_./-]+\.(?:py|tf|ts|js|json|ya?ml|md|sh|sql)`", "`file`", text)
    return Case("arbiter/drift.broken-doc-link", "doc_drift", "README.md",
                text + "\n\nSee [the site](https://example.invalid/guide) and "
                       "[the readme](README.md).\n", False, baseline=text)



# ---------------------------------------------------------------------------
# Controls for rules that had none.
#
# A rule with measured recall and no controls is the dangerous kind: it could
# be firing on everything and the numbers would look perfect. These six had
# recall of 1.0 and no measurement of what they do to innocent code.
# ---------------------------------------------------------------------------

def control_aws_key_lookalike(seeds: Seeds) -> Case | None:
    lang = rng.choice(("python", "js"))
    seed = seeds.pick(lang)
    if not seed:
        return None
    name, text = seed
    value = rng.choice([
        "AKIA" + "".join(rng.choices(ALNUM_UPPER, k=rng.randint(8, 14))),   # too short
        ("akia" + "".join(rng.choices(string.ascii_lowercase, k=16))),      # lowercase
        "${AWS_ACCESS_KEY_ID}",
        "AKIA_PLACEHOLDER_VALUE",
    ])
    return Case("arbiter/secrets.aws-access-key", "secrets", name,
                splice(text, assign(lang, "AWS_ACCESS_KEY_ID", value)), False, baseline=text)


def control_gh_token_lookalike(seeds: Seeds) -> Case | None:
    seed = seeds.pick("python")
    if not seed:
        return None
    name, text = seed
    value = rng.choice([
        "ghp_" + "".join(rng.choices(ALNUM, k=rng.randint(6, 20))),         # too short
        "${{ secrets.GITHUB_TOKEN }}",
        "ghp_your_token_here",
    ])
    return Case("arbiter/secrets.gh-token", "secrets", name,
                splice(text, assign("python", "GITHUB_TOKEN", value)), False, baseline=text)


def control_public_key(seeds: Seeds) -> Case | None:
    """A public key and a certificate are meant to be committed."""
    kind = rng.choice(("PUBLIC KEY", "CERTIFICATE", "RSA PUBLIC KEY"))
    body = "\n".join("".join(rng.choices(ALNUM + "+/", k=64)) for _ in range(rng.randint(2, 4)))
    return Case("arbiter/secrets.private-key", "secrets",
                rng.choice(("server.crt", "ca.pem", "id_rsa.pub")),
                f"-----BEGIN {kind}-----\n{body}\n-----END {kind}-----\n", False)


def control_unprivileged_container(seeds: Seeds) -> Case | None:
    return Case("arbiter/resource.privileged-container", "resource_policy", "deploy.yaml",
                _k8s_workload("        securityContext:\n          privileged: false\n"
                              "          allowPrivilegeEscalation: false\n"
                              "          runAsNonRoot: true\n"), False)


def control_no_host_namespace(seeds: Seeds) -> Case | None:
    n = rng.randint(1, 99999)
    flag = rng.choice(("hostPID: false", "hostIPC: false", "hostNetwork: false"))
    return Case("arbiter/resource.k8s-host-namespace", "resource_policy", "deploy.yaml",
                f"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: w{n}\n"
                f"spec:\n  template:\n    spec:\n      {flag}\n      containers:\n"
                f"      - name: app\n        image: app:1.0\n{HARDENED}", False)


def control_safe_capability(seeds: Seeds) -> Case | None:
    """Dropping ALL and adding back one harmless capability is the right shape."""
    cap = rng.choice(("NET_BIND_SERVICE", "CHOWN", "SETGID", "SETUID"))
    sec = ("        securityContext:\n          allowPrivilegeEscalation: false\n"
           "          runAsNonRoot: true\n          readOnlyRootFilesystem: true\n"
           f"          capabilities:\n            drop: [\"ALL\"]\n            add: [\"{cap}\"]\n"
           "        resources:\n          limits:\n            cpu: \"1\"\n")
    return Case("arbiter/resource.k8s-dangerous-capabilities", "resource_policy",
                "deploy.yaml", _k8s_workload(sec), False)


# ---------------------------------------------------------------------------
# Unquoted values — the formats where secrets actually leak
# ---------------------------------------------------------------------------

UNQUOTED_SHAPES = [
    (".env",         "{name}={value}\n",                       ""),
    ("config.yaml",  "service:\n  {name}: {value}\n",          ""),
    ("secret.yaml",  "apiVersion: v1\nkind: Secret\nmetadata:\n  name: s\ndata:\n  {name}: {value}\n", ""),
    ("setup.sh",     "#!/bin/sh\nexport {name}={value}\n",      ""),
    ("Dockerfile",   "FROM alpine:3.20\nENV {name}={value}\n",  ""),
    ("app.properties", "{name}={value}\n",                      ""),
    ("compose.yml",  "services:\n  web:\n    environment:\n      {name}: {value}\n", ""),
]


def case_unquoted_secret(seeds: Seeds) -> Case | None:
    fname, shape, _ = rng.choice(UNQUOTED_SHAPES)
    name = rng.choice(["API_KEY", "DB_PASSWORD", "client_secret", "api.key",
                       "ACCESS_KEY", "auth_token", "servicePassword"])
    value = strong_secret(rng.randint(12, 32))
    return Case("arbiter/secrets.assigned-credential", "secrets", fname,
                shape.format(name=name, value=value), True)


def control_unquoted_lookalike(seeds: Seeds) -> Case | None:
    """Name and value are paired coherently on purpose.

    Pairing them at random produced cases like `client_secret=my-tls-cert-2024`
    and counted the rule wrong for flagging it — but a credential-named symbol
    holding a hyphenated alphanumeric really is a credential as far as anyone
    can tell from the text. A control has to be something a careful reader
    would also call harmless, or it measures the generator, not the rule.
    """
    pairs = [
        ("secretName",         "my-tls-cert-2024"),
        ("secretKeyRef",       "db-credentials"),
        ("private_key_path",   "/etc/ssl/private/server.pem"),
        ("token_endpoint",     "https://auth.example.com/oauth/v2"),
        ("secret_version",     "1.24.3"),
        ("access_key_status",  "Inactive"),
        ("API_KEY",            "${AWS_SECRET}"),
        ("DB_PASSWORD",        "$DB_PASSWORD"),
        ("client_secret",      "${{ secrets.CLIENT_SECRET }}"),
        ("api_key",            "your-api-key-here"),
        ("password",           "changeme"),
        ("client_secret",      "DescribeSecret"),
        ("password",           "!vault|AES256abcdef"),
        ("db_password",        "null"),
        ("auth_token",         "_get_secret(name)"),
        ("secretProvider",     "vault"),
        ("token_format",       "jwt"),
    ]
    name, value = rng.choice(pairs)
    fname, shape, _ = rng.choice(UNQUOTED_SHAPES)
    return Case("arbiter/secrets.assigned-credential", "secrets", fname,
                shape.format(name=name, value=value), False)


def case_iam_action_is_not_a_secret(seeds: Seeds) -> Case | None:
    """CloudFormation policies list `secretsmanager: GetSecretValue`. The colon
    made it look like an assignment; it appeared eight times on AWS's own
    template repository."""
    action = rng.choice(("GetSecretValue", "DescribeSecret", "ListSecrets",
                         "PutSecretValue", "RotateSecret"))
    return Case("arbiter/secrets.assigned-credential", "secrets", "policy.yaml",
                "Statement:\n  - Effect: Allow\n    Action:\n"
                f"      - secretsmanager: {action}\n", False)


# ---------------------------------------------------------------------------
# Provider-issued tokens
#
# The prefix is assigned by the issuer, so the value carries its own evidence
# and the symbol name is irrelevant. That is the point of the family: the
# assigned-credential heuristic needs a credential-ish name, and a pull-request
# rehearsal walked straight past `STRIPE_KEY = "sk_live_..."` because a bare
# "key" is not credential-ish (sort_key, cache_key, primary_key).
#
# The controls are the part that matters. A prefix rule looks unfalsifiable
# until you ask what else in a real repository starts with `sk-`.
# ---------------------------------------------------------------------------

PROVIDER_TOKENS = [
    ("stripe-key",     lambda: rng.choice(("sk_live_", "rk_live_")) + "".join(rng.choices(ALNUM, k=rng.randint(24, 32)))),
    ("openai-key",     lambda: "sk-" + rng.choice(("", "proj-")) + "".join(rng.choices(ALNUM, k=rng.randint(40, 48)))),
    ("anthropic-key",  lambda: "sk-ant-api03-" + "".join(rng.choices(ALNUM + "_-", k=rng.randint(32, 64)))),
    ("google-api-key", lambda: "AIza" + "".join(rng.choices(ALNUM + "_-", k=35))),
    ("gitlab-token",   lambda: "glpat-" + "".join(rng.choices(ALNUM + "_-", k=rng.randint(20, 26)))),
    ("npm-token",      lambda: "npm_" + "".join(rng.choices(ALNUM, k=36))),
    ("sendgrid-key",   lambda: "SG." + "".join(rng.choices(ALNUM + "_-", k=22)) + "." + "".join(rng.choices(ALNUM + "_-", k=43))),
    ("pypi-token",     lambda: "pypi-AgEIcHlwaS5vcmc" + "".join(rng.choices(ALNUM + "_-", k=rng.randint(55, 80)))),
]


def case_provider_token(seeds: Seeds) -> Case | None:
    rule, make = rng.choice(PROVIDER_TOKENS)
    lang = rng.choice(("python", "js", "go"))
    seed = seeds.pick(lang)
    if not seed:
        return None
    name, text = seed
    # Deliberately an innocuous symbol name. If the harness wrote API_SECRET
    # it would be testing the assigned-credential rule instead, and this family
    # would look like it worked when it had never been exercised.
    symbol = rng.choice(("STRIPE_KEY", "client", "CFG", "AUTH", "provider_key",
                         "settings", "DEFAULT", "billing"))
    return Case(f"arbiter/secrets.{rule}", "secrets", name,
                splice(text, assign(lang, symbol, make())), True, baseline=text)


def case_slack_webhook(seeds: Seeds) -> Case | None:
    seed = seeds.pick(rng.choice(("python", "js")))
    if not seed:
        return None
    name, text = seed
    url = ("https://hooks.slack.com/services/T"
           + "".join(rng.choices(ALNUM_UPPER, k=8)) + "/B"
           + "".join(rng.choices(ALNUM_UPPER, k=8)) + "/"
           + "".join(rng.choices(ALNUM, k=24)))
    return Case("arbiter/secrets.slack-webhook", "secrets", name,
                splice(text, assign("python", "NOTIFY_URL", url)), True, baseline=text)


def control_provider_token_lookalike(seeds: Seeds) -> Case | None:
    """Things that start like a provider token and are not one.

    `sk-` is the dangerous prefix: it is three characters and it is also how
    people write scikit identifiers, Slovak locale keys and hyphenated slugs.
    """
    lang = rng.choice(("python", "js", "go"))
    seed = seeds.pick(lang)
    if not seed:
        return None
    name, text = seed
    value = rng.choice([
        "sk_live_" + "".join(rng.choices(ALNUM, k=rng.randint(4, 12))),  # too short
        "sk_test_" + "".join(rng.choices(ALNUM, k=32)),                  # test mode, not live
        "AIza" + "".join(rng.choices(ALNUM, k=rng.randint(8, 20))),      # wrong length
        "npm_" + "".join(rng.choices(ALNUM, k=rng.randint(8, 20))),      # wrong length
        "glpat-your-token-here",
        "${STRIPE_SECRET_KEY}",
        "sk-" + "".join(rng.choices(string.ascii_lowercase, k=rng.randint(4, 20))),
        "pypi-token-goes-here",
    ])
    return Case(f"arbiter/secrets.{rng.choice([r for r, _ in PROVIDER_TOKENS])}",
                "secrets", name, splice(text, assign(lang, "CONFIG", value)),
                False, baseline=text)


def control_slack_webhook_placeholder(seeds: Seeds) -> Case | None:
    seed = seeds.pick("python")
    if not seed:
        return None
    name, text = seed
    url = rng.choice([
        "https://hooks.slack.com/services/YOUR/WEBHOOK/URL",
        "https://hooks.slack.com/services/T000/B000/XXXX",
        "https://slack.com/api/chat.postMessage",
    ])
    return Case("arbiter/secrets.slack-webhook", "secrets", name,
                splice(text, assign("python", "NOTIFY_URL", url)), False, baseline=text)


POSITIVES = [case_aws_key, case_private_key, case_gh_token, case_db_url,
             case_assigned_credential, case_public_bucket, case_unencrypted_db,
             case_open_ingress, case_privileged_container, case_host_namespace,
             case_dangerous_capability, case_no_security_context,
             case_unpinned_action, case_dangerous_prt,
             case_broken_link, case_unquoted_secret,
             case_provider_token, case_slack_webhook]

CONTROLS = [control_placeholder, control_status_field, control_passphrase,
            control_trivial_db_url, control_self_referential, control_private_bucket,
            control_encrypted_db, control_closed_ingress, control_hardened_workload,
            control_pinned_action, control_safe_prt, control_valid_link,
            control_aws_key_lookalike, control_gh_token_lookalike,
            control_public_key, control_unprivileged_container,
            control_no_host_namespace, control_safe_capability,
            control_unquoted_lookalike, case_iam_action_is_not_a_secret,
            case_database_allows_plaintext, control_database_requires_tls,
            case_weak_tls_version, control_modern_tls_version,
            case_no_https_redirect, control_https_only,
            control_provider_does_not_express_tls,
            control_provider_token_lookalike,
            control_slack_webhook_placeholder]

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _probe_once(content: str, case: Case, workdir: Path, config: dict) -> list:
    for child in workdir.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink()
    target = workdir / case.extra.get("dir", "") / case.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)

    repo = RepoInfo(id="root", path=str(workdir))
    inv = build_inventory([repo])
    graph = build_graph(inv) if case.probe == "resource_policy" else []
    ctx = ProbeContext(repos=[repo], inventory=inv, graph=graph, config=config, system={})
    probe = probe_by_name(case.probe)
    try:
        return probe.run(ctx) or []
    except Exception:
        return []


def run_case(case: Case, workdir: Path, config: dict) -> list:
    """Findings attributable to the injected change alone."""
    mutated = _probe_once(case.content, case, workdir, config)
    if not case.baseline:
        return mutated
    before = {f.id for f in _probe_once(case.baseline, case, workdir, config)}
    return [f for f in mutated if f.id not in before]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="/tmp/corpus")
    ap.add_argument("--trials", type=int, default=20000)
    ap.add_argument("--knowledge", default="")
    ap.add_argument("--seeds-per-kind", type=int, default=400)
    args = ap.parse_args()

    print("\n  DEFECT INJECTION — mining seeds from the corpus")
    seeds = mine_seeds(Path(args.corpus), args.seeds_per_kind)
    print("   " + "  ".join(f"{k}={v}" for k, v in seeds.count().items() if v))

    config = {"quality": {}}
    results: dict[str, dict[str, int]] = {}
    misses: dict[str, list[str]] = {}
    alarms: dict[str, list[str]] = {}

    workdir = Path(tempfile.mkdtemp(prefix="arbiter-inject-"))
    t0 = time.time()
    generated = 0
    skipped = 0

    try:
        for i in range(args.trials):
            positive = (i % 2 == 0)
            maker = rng.choice(POSITIVES if positive else CONTROLS)
            case = maker(seeds)
            if case is None:
                skipped += 1
                continue
            generated += 1
            findings = run_case(case, workdir, config)
            hit = [f for f in findings if f.rule_id == case.rule]

            slot = results.setdefault(case.rule, {"detected": 0, "missed": 0,
                                                  "clean_pass": 0, "false_alarm": 0})
            if case.positive:
                if hit:
                    want = case.extra.get("expect_severity")
                    if want and hit[0].severity != want:
                        slot["missed"] += 1
                        misses.setdefault(case.rule, []).append(
                            f"severity {hit[0].severity} != {want}")
                    else:
                        slot["detected"] += 1
                else:
                    slot["missed"] += 1
                    if len(misses.setdefault(case.rule, [])) < 3:
                        misses[case.rule].append(case.content.strip().split("\n")[0][:70])
            else:
                cap = case.extra.get("expect_severity_at_most")
                tolerable = bool(cap) and hit and (
                    SEVERITY_ORDER.index(hit[0].severity) >= SEVERITY_ORDER.index(cap))
                if not hit or tolerable:
                    slot["clean_pass"] += 1
                else:
                    slot["false_alarm"] += 1
                    if len(alarms.setdefault(case.rule, [])) < 3:
                        alarms[case.rule].append(case.content.strip().split("\n")[0][:70])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    elapsed = time.time() - t0
    print(f"\n  {generated:,} trials in {elapsed:.1f}s "
          f"({generated / max(elapsed, 0.001):.0f}/s)"
          + (f", {skipped} skipped for want of seeds" if skipped else ""))

    from arbiter.claims import wilson_lower_bound
    print(f"\n  {'RULE':<48}{'POS':>6}{'RECALL':>9}{'LB':>8}"
          f"{'NEG':>6}{'SPEC':>8}{'LB':>8}")
    total = {"detected": 0, "missed": 0, "clean_pass": 0, "false_alarm": 0}
    for rule, s in sorted(results.items()):
        for k in total:
            total[k] += s[k]
        pos = s["detected"] + s["missed"]
        neg = s["clean_pass"] + s["false_alarm"]
        rec = s["detected"] / pos if pos else float("nan")
        spec = s["clean_pass"] / neg if neg else float("nan")
        rlb = wilson_lower_bound(s["detected"], pos) if pos else 0.0
        slb = wilson_lower_bound(s["clean_pass"], neg) if neg else 0.0
        print(f"  {rule[:47]:<48}{pos:>6}{rec:>9.4f}{rlb:>8.3f}"
              f"{neg:>6}{spec:>8.4f}{slb:>8.3f}")

    pos = total["detected"] + total["missed"]
    neg = total["clean_pass"] + total["false_alarm"]
    print(f"\n  overall recall      {total['detected']}/{pos} = "
          f"{total['detected'] / max(pos, 1):.4f}")
    print(f"  overall specificity {total['clean_pass']}/{neg} = "
          f"{total['clean_pass'] / max(neg, 1):.4f}")

    if misses:
        print("\n  MISSED DEFECTS (recall loss — each is a rule to fix)")
        for rule, examples in sorted(misses.items()):
            print(f"    {rule}")
            for e in examples[:3]:
                print(f"        {e}")
    if alarms:
        print("\n  FALSE ALARMS (a control that fired — each is a rule to fix)")
        for rule, examples in sorted(alarms.items()):
            print(f"    {rule}")
            for e in examples[:3]:
                print(f"        {e}")

    if args.knowledge:
        k = Knowledge.load(args.knowledge)
        for rule, s in results.items():
            for kind, n in s.items():
                if n:
                    record_synthetic(k, rule, kind, n)
        version = k.save(args.knowledge)
        print(f"\n  knowledge updated: {version} ({args.knowledge})")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
