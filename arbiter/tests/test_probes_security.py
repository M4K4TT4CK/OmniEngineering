"""Secrets and workflow-security probes: credential shapes, placeholders,
provider-issued tokens, PEM material and disabled security checks.
"""
from __future__ import annotations

import pytest
from helpers import _scan_text

from arbiter.engine import run_scan
from arbiter.policy import load_config
from arbiter.probes import _entropy, _mask


def test_secret_values_are_masked():
    assert _mask("AKIAIOSFODNN7EXAMPLE") == "AKIA************MPLE"
    assert "hunter2" not in _mask("hunter2-secret-value")


def test_entropy_ranks_random_above_words():
    assert _entropy("Zx91qKp4vWmTn83LcRd7") > _entropy("passwordpassword")


def test_underscored_credential_names_are_caught(legacy_report):
    hits = [f for f in legacy_report.findings if "DB_PASSWORD" in f.evidence]
    assert hits, "DB_PASSWORD must match even though it is not a bare `password`"


# --------------------------------------------------------------------------
# Tuning regressions.
#
# Every test below encodes a false positive that a public repository actually
# produced. They exist so a future rule edit cannot quietly reintroduce it.
# --------------------------------------------------------------------------

def test_test_fixture_keys_are_downgraded_not_hidden(tmp_path):
    """psf/requests ships four private keys under tests/certs. They are real
    keys and deliberate; reporting them as critical was wrong."""
    key = "-----BEGIN PRIVATE KEY-----\nMIIBVQIBADAN\n-----END PRIVATE KEY-----\n"
    found = _scan_text(tmp_path, "tests/certs/server.key", key, ["secrets"])
    assert len(found) == 1
    assert found[0].severity == "medium" and found[0].confidence == "low"
    assert "test fixture" in found[0].title


def test_production_key_stays_critical(tmp_path):
    key = "-----BEGIN PRIVATE KEY-----\nMIIBVQIBADAN\n-----END PRIVATE KEY-----\n"
    found = _scan_text(tmp_path, "deploy/server.key", key, ["secrets"])
    assert found and found[0].severity == "critical"


def test_passphrase_examples_are_not_credentials(tmp_path):
    """expressjs/express: `secret: 'keyboard cat'` in its own examples."""
    found = _scan_text(tmp_path, "app.js", "app.use(session({ secret: 'keyboard cat' }))\n", ["secrets"])
    assert not found, "a value containing spaces is a passphrase example, not a secret"


def test_github_expressions_are_not_credentials(tmp_path):
    """hashicorp/terraform-provider-random: `token: ${{ secrets.GITHUB_TOKEN }}`."""
    found = _scan_text(tmp_path, ".github/workflows/x.yml",
                       "jobs:\n  a:\n    steps:\n      - with:\n          token: ${{ secrets.GITHUB_TOKEN }}\n",
                       ["secrets"])
    assert not found


def test_real_high_entropy_credential_still_found(tmp_path):
    found = _scan_text(tmp_path, "cfg.py", 'API_KEY = "Zx91qKp4vWmTn83LcRd7Qa2B"\n', ["secrets"])
    assert found and found[0].severity == "high"


def test_safe_pull_request_target_is_informational(tmp_path):
    """All three terraform-aws-modules repos use this trigger safely for
    PR-title linting. Flagging it high was a false positive; flagging it "low"
    still deducted score for something that appears twelve times on
    well-maintained repositories and once on the deliberately vulnerable ones.
    It reports, at zero weight."""
    found = _scan_text(tmp_path, ".github/workflows/pr-title.yml",
                       "on:\n  pull_request_target:\n    types: [opened]\njobs:\n  a:\n"
                       "    steps:\n      - uses: amannn/action-semantic-pull-request@v5\n",
                       ["supply_chain"])
    prt = [f for f in found if "pull-request-target" in f.rule_id]
    assert prt, "the safe form is still reported"
    assert prt[0].severity == "info"
    from arbiter.core import SEV_WEIGHT
    assert SEV_WEIGHT[prt[0].severity] == 0.0


def test_dangerous_pull_request_target_is_high(tmp_path):
    found = _scan_text(tmp_path, ".github/workflows/bad.yml",
                       "on:\n  pull_request_target:\njobs:\n  a:\n    steps:\n"
                       "      - uses: actions/checkout@v4\n"
                       "        with:\n          ref: ${{ github.event.pull_request.head.sha }}\n"
                       "      - run: npm install && npm test\n",
                       ["supply_chain"])
    prt = [f for f in found if "pull-request-target" in f.rule_id]
    assert prt and prt[0].severity == "high" and prt[0].confidence == "high"


def test_status_fields_are_not_credentials(tmp_path):
    """terraform-aws-modules: `access_key_status = "Inactive"` matched the
    keyword `access_key` but holds a status, not a key."""
    found = _scan_text(tmp_path, "examples/main.tf",
                       'resource "x" "y" { access_key_status = "Inactive" }\n', ["secrets"])
    assert not found


def test_self_referential_fixture_values_are_not_credentials(tmp_path):
    """pallets/flask: `secret_key = "secret_key"` in a test."""
    found = _scan_text(tmp_path, "tests/test_x.py", 'app.secret_key = "secret_key"\n', ["secrets"])
    assert not found


def test_weak_hardcoded_password_is_still_found(tmp_path):
    """Terragoat's Azure SQL password has low entropy precisely because it is
    weak. An entropy floor high enough to silence example passphrases also
    silenced this, which was the wrong trade."""
    found = _scan_text(tmp_path, "infra/sql.tf",
                       'resource "a" "b" { administrator_login_password = "Aa12issue5678" }\n',
                       ["secrets"])
    assert found, "a weak credential is still a credential"


# --------------------------------------------------------------------------
# Defects found by breadth and injection testing.
#
# Each of these was a real gap: the evaluator claimed to cover a language or a
# platform and did not. They are regressions waiting to happen.
# --------------------------------------------------------------------------

def test_go_short_declaration_is_seen(tmp_path):
    """`apiKey := "..."` was invisible: the regex matched the colon of `:=`
    and then failed on the equals. Recall on the rule was 0.48."""
    found = _scan_text(tmp_path, "main.go",
                       'package main\n\nfunc init() {\n\tapiKey := "Zx91qKp4vWmTn83LcRd7Qa"\n}\n',
                       ["secrets"])
    assert found, "Go short variable declarations must be scanned"


def test_template_literal_is_a_string(tmp_path):
    """A secret in a JavaScript backtick string is still a secret."""
    found = _scan_text(tmp_path, "app.js",
                       'const authToken = `Zx91qKp4vWmTn83LcRd7Qa`;\n', ["secrets"])
    assert found


def test_hyphenated_placeholders_are_not_secrets(tmp_path):
    for value in ("your-api-key-here", "replace-me-placeholder", "token-goes-here",
                  "sample-secret-value", "redacted-for-docs"):
        found = _scan_text(tmp_path, f"c_{abs(hash(value))}.py",
                           f'API_KEY = "{value}"\n', ["secrets"])
        assert not found, f"{value} is a placeholder, not a credential"


def test_local_dev_database_urls_are_downgraded(tmp_path):
    """docker/awesome-compose's four highest findings were all
    postgres://postgres:postgres@db:5432."""
    found = _scan_text(tmp_path, "main.py",
                       'DATABASE_URL = "postgres://postgres:postgres@db:5432/app"\n', ["secrets"])
    assert found and found[0].severity == "low" and found[0].confidence == "low"


def test_real_database_credentials_are_not_downgraded(tmp_path):
    found = _scan_text(tmp_path, "main.py",
                       'DATABASE_URL = "postgres://svc_42:Zx91qKp4vWmTn83LcRd7@db.internal:5432/app"\n',
                       ["secrets"])
    assert found and found[0].severity == "high"


# --------------------------------------------------------------------------
# Unquoted values.
#
# A .env file, a Kubernetes Secret, a docker-compose file, an `export` line and
# a Dockerfile `ENV` all write secrets without quotes — and those are the
# places secrets most commonly leak. Every one of them was invisible.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name,content", [
    ("dotenv",        "API_KEY=Zx91qKp4vWmTn83LcRd7\n"),
    ("yaml",          "config:\n  api_key: Aa12QwErTy90ZxCv\n"),
    ("k8s_secret",    "apiVersion: v1\nkind: Secret\nmetadata:\n  name: s\ndata:\n  password: cXVvYml0ZQ==\n"),
    ("shell_export",  "#!/bin/sh\nexport DB_PASSWORD=c5nkQ2p9Lm4Tx\n"),
    ("dockerfile_env","FROM alpine\nENV API_KEY=Zx91qKp4vWmTn83LcRd7\n"),
    ("properties",    "api.key=Zx91qKp4vWmTn83LcRd7\n"),
])
def test_unquoted_secrets_are_found(tmp_path, name, content):
    ext = {"dotenv": ".env", "yaml": ".yaml", "k8s_secret": ".yaml", "shell_export": ".sh",
           "dockerfile_env": "Dockerfile", "properties": ".properties"}[name]
    fname = ext if ext == "Dockerfile" else f"{name}{ext}"
    found = _scan_text(tmp_path, fname, content, ["secrets"])
    assert found, f"an unquoted secret in {name} must be found"


@pytest.mark.parametrize("label,content", [
    # An IAM action in a CloudFormation policy. The colon made it look like an
    # assignment and `GetSecretValue` like a value.
    ("iam_action",   "Statement:\n  - Action:\n      - secretsmanager: GetSecretValue\n"),
    # An expression being assigned, not a literal.
    ("code_expr",    "search_tokens=_get_search_tokens(text)\n"),
    # References to a secret, not the secret.
    ("secret_ref",   "volumes:\n  - secretName: my-tls-cert-2024\n"),
    ("secret_path",  "private_key_path=/etc/ssl/private/server.pem\n"),
    ("token_url",    "token_endpoint=https://auth.example.com/oauth/v2\n"),
    # Substitution, not a value.
    ("env_ref",      "API_KEY=${AWS_SECRET}\n"),
    ("bare_env_ref", "API_KEY=$AWS_SECRET\n"),
    ("yaml_tag",     "password: !vault|AES256abcdef\n"),
    ("version",      "secret_version=1.24.3\n"),
])
def test_unquoted_look_alikes_stay_quiet(tmp_path, label, content):
    found = _scan_text(tmp_path, f"{label}.yaml", content, ["secrets"])
    assert not found, f"{label} is not a credential: {[f.evidence for f in found]}"


def test_purely_alphabetic_unquoted_values_are_words(tmp_path):
    """A real secret essentially always carries a digit or a symbol. Without
    this, every `secretsmanager: DescribeSecret` in an IAM policy was a
    finding."""
    assert not _scan_text(tmp_path, "p.yaml", "  client_secret: DescribeSecret\n", ["secrets"])
    assert _scan_text(tmp_path, "q.yaml", "  client_secret: DescribeSecret9\n", ["secrets"])


# ---------------------------------------------------------------------------
# Two false positives found by widening the corpus, both of them criticals on
# well-maintained code -- the single worst kind of finding this tool can
# produce, because a critical is what turns somebody's build red.
# ---------------------------------------------------------------------------

def test_pem_header_with_a_placeholder_body_is_not_a_key(tmp_path):
    """Argo CD's operator manual shows how to register a repository
    credential. The key body in that example is three literal dots. Matching
    the BEGIN line alone reported it as a critical, high-confidence leaked
    private key."""
    doc = ("apiVersion: v1\nstringData:\n  sshPrivateKey: |\n"
           "    -----BEGIN OPENSSH PRIVATE KEY-----\n"
           "    ...\n"
           "    -----END OPENSSH PRIVATE KEY-----\n")
    assert not _scan_text(tmp_path, "manifests/creds.yaml", doc, ["secrets"])


@pytest.mark.parametrize("body", [
    "...", "<your-key-here>", "[REDACTED]", "xxxxxxxxxxxx", "{{ .Values.key }}",
    "YOUR PRIVATE KEY", "paste your key", "snip",
])
def test_written_placeholders_are_not_keys(tmp_path, body):
    doc = (f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----\n")
    found = _scan_text(tmp_path, f"k{abs(hash(body))}.pem", doc, ["secrets"])
    assert not [f for f in found if "private-key" in f.rule_id], \
        f"{body!r} is a stand-in, not key material"


def test_real_key_material_is_still_reported(tmp_path):
    """The check must only reject what is demonstrably a stand-in. A real body,
    including a deliberately truncated fixture one, still reports."""
    real = ("-----BEGIN RSA PRIVATE KEY-----\n"
            "MIICXAIBAAKBgQDVgc+zdNmcxwg4xqdoiy/WpnYj0WFvE7A/zy0EfvnUhxhLAXlC\n"
            "bjQw5Sqxa8IwQVr4G/mR7wTTJtf/Nrt5bP+E2D4W9MtuL7tzZ9KS/7v3D3nninMP\n"
            "-----END RSA PRIVATE KEY-----\n")
    found = _scan_text(tmp_path, "deploy/server.key", real, ["secrets"])
    assert [f for f in found if f.severity == "critical"]

    short = "-----BEGIN PRIVATE KEY-----\nMIIBVQIBADAN\n-----END PRIVATE KEY-----\n"
    assert _scan_text(tmp_path, "deploy/short.key", short, ["secrets"])


def test_unterminated_pem_block_is_kept(tmp_path):
    """A block with no END marker cannot be read, so it is treated as real.
    The conservative direction is the one that keeps findings."""
    doc = ("-----BEGIN RSA PRIVATE KEY-----\n"
           "MIICXAIBAAKBgQDVgc+zdNmcxwg4xqdoiy/WpnYj0WFvE7A/zy0EfvnUhxhLAXlC\n")
    assert _scan_text(tmp_path, "deploy/partial.key", doc, ["secrets"])


def test_pem_header_quoted_in_code_is_not_a_key(tmp_path):
    """`ecdsa`'s own keys.py searches for the PEM header as a byte string to
    parse a real key someone else handed it; there is no key here, just code
    and prose that happen to contain a colon somewhere in the next few
    thousand characters. A no-END-marker block used to grab that trailing
    text as its "body" and call any colon in it an encrypted preamble,
    reporting the library's own source as a leaked key -- found scanning a
    CDK Lambda asset that vendored the package."""
    doc = ('        private_key_index = string.find(b"-----BEGIN EC PRIVATE KEY-----")\n'
           "        if private_key_index == -1:\n"
           "            private_key_index = string.index(b\"-----BEGIN PRIVATE KEY-----\")\n"
           "            return cls.from_der(\n"
           "                der.unpem(string[private_key_index:]),\n")
    found = _scan_text(tmp_path, "ecdsa/keys.py", doc, ["secrets"])
    assert not [f for f in found if "private-key" in f.rule_id]


def test_secrets_probe_skips_a_vendored_bundle_under_a_renamed_cdk_output_dir(tmp_path):
    """End-to-end version of the two classify() tests above, against the
    probe a real system's false positive actually came through."""
    key = "-----BEGIN PRIVATE KEY-----\nMIIBVQIBADAN\n-----END PRIVATE KEY-----\n"
    vendored = tmp_path / "cdk.out.chk" / "asset.abc123" / "vendored.py"
    vendored.parent.mkdir(parents=True)
    vendored.write_text(f'KEY = "{key}"\n')
    (tmp_path / "real.py").write_text(f'KEY = "{key}"\n')

    found = run_scan([str(tmp_path)], load_config(None), only=["secrets"], use_adapters=False).active()
    paths = {f.location.path for f in found if "private-key" in f.rule_id}
    assert "real.py" in paths
    assert not any("cdk.out.chk" in p for p in paths)


@pytest.mark.parametrize("path", [
    "integration/resources/tls/consul.key",   # traefik
    "e2e/certs/server.key",
    "acceptance/tls/key.pem",
    "hack/certs/dev.key",
    "docs/operator-manual/repo-creds.yaml",   # argo-cd
])
def test_non_deployment_paths_downgrade_real_keys(tmp_path, path):
    """Traefik commits real TLS keys under integration/resources/tls so its
    integration suite has something to serve. Same category as the keys under
    psf/requests' tests/certs -- a different word for the directory should not
    change the verdict from medium to critical."""
    real = ("-----BEGIN RSA PRIVATE KEY-----\n"
            "MIICXAIBAAKBgQDVgc+zdNmcxwg4xqdoiy/WpnYj0WFvE7A/zy0EfvnUhxhLAXlC\n"
            "bjQw5Sqxa8IwQVr4G/mR7wTTJtf/Nrt5bP+E2D4W9MtuL7tzZ9KS/7v3D3nninMP\n"
            "-----END RSA PRIVATE KEY-----\n")
    found = [f for f in _scan_text(tmp_path, path, real, ["secrets"])
             if "private-key" in f.rule_id]
    assert found, "still reported — downgraded, never hidden"
    assert found[0].severity == "medium" and found[0].confidence == "low"
    assert "fixture" in found[0].title


def test_production_paths_are_not_caught_by_the_widened_pattern(tmp_path):
    """`integration` and `e2e` were added to the non-deployment paths. They
    must match a directory, not a word inside a filename."""
    real = ("-----BEGIN RSA PRIVATE KEY-----\n"
            "MIICXAIBAAKBgQDVgc+zdNmcxwg4xqdoiy/WpnYj0WFvE7A/zy0EfvnUhxhLAXlC\n"
            "-----END RSA PRIVATE KEY-----\n")
    for path in ("src/integration_client.key", "deploy/e2e-gateway.pem"):
        found = [f for f in _scan_text(tmp_path, path, real, ["secrets"])
                 if "private-key" in f.rule_id]
        assert found and found[0].severity == "critical", path


@pytest.mark.parametrize("code,label", [
    ("requests.get(url, verify=False)", "python tls"),
    ("const a = {rejectUnauthorized: false}", "node tls"),
    ("cfg := &tls.Config{InsecureSkipVerify: true}", "go tls"),
    ("ssh -o StrictHostKeyChecking=no host", "ssh host key"),
])
def test_disabled_security_checks_are_caught(tmp_path, code, label):
    d = tmp_path / label.replace(" ", "_")
    d.mkdir()
    (d / "app.py").write_text(code + "\n")
    rep = run_scan([str(d)], load_config(None), only=["authored"], use_adapters=False)
    hits = [f for f in rep.active() if "security-check-disabled" in f.rule_id]
    assert hits, label


def test_a_disabled_check_under_a_test_path_is_downgraded_not_hidden(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text("requests.get(u, verify=False)\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                   use_adapters=False)
    hits = [f for f in rep.active() if "security-check-disabled" in f.rule_id]
    assert hits and hits[0].severity == "low" and hits[0].confidence == "low"


# ---------------------------------------------------------------------------
# Disabled-check false positives, found by the corpus run.
#
# Six high-severity findings on well-maintained code, which is the worst kind
# of defect this tool can produce. Three causes, and the first is the same
# mistake the import scanner made with docstrings, in a second place.
# ---------------------------------------------------------------------------

def test_commented_out_code_is_not_a_disabled_check(tmp_path):
    """Traefik's healthcheck has a whole commented-out TLS block. It was
    reported as a high-severity disabled check."""
    (tmp_path / "a.go").write_text(
        "func ping() {\n"
        "\t// TODO Handle TLS on ping etc...\n"
        "\t// tr := &http.Transport{\n"
        "\t// \tTLSClientConfig: &tls.Config{InsecureSkipVerify: true},\n"
        "\t// }\n"
        "}\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                   use_adapters=False)
    assert not [f for f in rep.active() if "security-check-disabled" in f.rule_id]


def test_a_comment_discussing_the_setting_is_not_the_setting(tmp_path):
    (tmp_path / "a.py").write_text(
        "# we could not connect even with verify=False, so the server is down\n"
        "r = requests.get(url)\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                   use_adapters=False)
    assert not [f for f in rep.active() if "security-check-disabled" in f.rule_id]


def test_a_docstring_listing_the_dangerous_settings_is_not_the_setting(tmp_path):
    """authored.py's own module docstring enumerates `verify=False`,
    `rejectUnauthorized: false` and `InsecureSkipVerify: true` as the shapes
    it looks for, and the first self-gate reported them as three high
    findings. Comments were already blanked; docstrings were not."""
    (tmp_path / "a.py").write_text(
        '"""Looks for verify=False, rejectUnauthorized: false and\n'
        'InsecureSkipVerify: true in the code it scans."""\n'
        "import requests\n"
        "\n"
        "def fetch(url):\n"
        "    return requests.get(url, verify=False)\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                   use_adapters=False)
    hits = [f for f in rep.active() if "security-check-disabled" in f.rule_id]
    assert len(hits) == 1
    assert hits[0].location.start_line == 6


def test_an_opt_in_insecure_mode_is_downgraded_not_hidden(tmp_path):
    """An insecure mode the operator has to ask for is a feature, not a
    default. Still reported, because the mode existing is worth knowing."""
    (tmp_path / "a.go").write_text(
        "func client(insecure bool) *http.Client {\n"
        "\tif insecure {\n"
        "\t\tcfg := &tls.Config{InsecureSkipVerify: true}\n"
        "\t\t_ = cfg\n\t}\n\treturn nil\n}\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                   use_adapters=False)
    hits = [f for f in rep.active() if "security-check-disabled" in f.rule_id]
    assert hits, "still reported"
    assert hits[0].severity == "low" and "opt-in-guarded" in hits[0].tags


def test_pinned_ca_alongside_a_disabled_hostname_check_is_downgraded(tmp_path):
    """Disabling the library's hostname check while pinning a CA is a
    deliberate mutual-TLS arrangement, not an absence of verification. The
    resource rules already honour compensating controls; this is the same idea
    for code."""
    (tmp_path / "a.go").write_text(
        "transport := &dynamic.ServersTransport{\n"
        "\tInsecureSkipVerify: true,\n"
        "\tRootCAs:            c.getRoot(),\n"
        "\tCertificates:       certs,\n}\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                   use_adapters=False)
    hits = [f for f in rep.active() if "security-check-disabled" in f.rule_id]
    assert hits and hits[0].severity == "medium" and "compensated" in hits[0].tags


def test_an_unguarded_uncompensated_disabled_check_still_reports_high(tmp_path):
    """The three downgrades must not swallow the case the rule is for."""
    (tmp_path / "a.py").write_text("r = requests.get(url, verify=False)\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["authored"],
                   use_adapters=False)
    hits = [f for f in rep.active() if "security-check-disabled" in f.rule_id]
    assert hits and hits[0].severity == "high" and hits[0].confidence == "high"


@pytest.mark.slow
def test_no_criticals_or_highs_from_authored_on_well_maintained_go(tmp_path):
    """The end state: the probe produces nothing build-breaking on Traefik,
    which is the tuning-set repository that exposed two of the three causes."""
    import os
    traefik = "/tmp/corpus/traefik"
    if not os.path.isdir(traefik):
        pytest.skip("corpus not cloned")
    rep = run_scan([traefik], load_config(None), only=["authored"], use_adapters=False)
    bad = [f for f in rep.active() if f.severity in ("critical", "high")]
    assert not bad, [(f.location.short(), f.rule_id) for f in bad]


# ===========================================================================
# Provider-issued tokens
#
# Added after a pull-request rehearsal planted a live Stripe key in a billing
# module and the scan came back clean. The symbol was STRIPE_KEY, and the
# assigned-credential heuristic does not treat a bare "key" as credential-ish
# because sort_key and cache_key are everywhere. For this family the name was
# never the evidence -- the issuer-assigned prefix is.
# ===========================================================================

PROVIDER_TOKEN_CASES = [
    ("stripe-key",     'K = "sk_live_' + "A" * 28 + '"'),
    ("stripe-key",     'K = "rk_live_' + "B" * 28 + '"'),
    ("openai-key",     'K = "sk-proj-' + "C" * 44 + '"'),
    ("anthropic-key",  'K = "sk-ant-api03-' + "D" * 40 + '"'),
    ("google-api-key", 'K = "AIza' + "E" * 35 + '"'),
    ("gitlab-token",   'K = "glpat-' + "F" * 22 + '"'),
    ("npm-token",      'K = "npm_' + "G" * 36 + '"'),
    ("sendgrid-key",   'K = "SG.' + "H" * 22 + "." + "I" * 43 + '"'),
    ("pypi-token",     'K = "pypi-AgEIcHlwaS5vcmc' + "J" * 60 + '"'),
    ("slack-webhook",  'U = "https://hooks.slack.com/services/TABCDEFGH/BABCDEFGH/' + "K" * 24 + '"'),
]


@pytest.mark.parametrize("rule,line", PROVIDER_TOKEN_CASES,
                         ids=[f"{r}-{i}" for i, (r, _) in enumerate(PROVIDER_TOKEN_CASES)])
def test_provider_tokens_are_caught_whatever_the_symbol_is_called(tmp_path, rule, line):
    (tmp_path / "billing.py").write_text(f"import os\n{line}\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["secrets"], use_adapters=False)
    ids = {f.rule_id for f in rep.findings}
    assert f"arbiter/secrets.{rule}" in ids, ids


PROVIDER_TOKEN_LOOKALIKES = [
    'K = "sk_test_' + "A" * 32 + '"',          # test mode, not a live key
    'K = "sk_live_short"',                      # too short to be issued
    'K = "AIzaTooShort"',                       # wrong length
    'K = "npm_short"',                          # wrong length
    'K = "glpat-your-token-here"',
    'K = "${STRIPE_SECRET_KEY}"',
    'K = "sk-slovak-locale"',                   # `sk-` is also a language tag
    'U = "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"',
    'U = "https://slack.com/api/chat.postMessage"',
]


@pytest.mark.parametrize("line", PROVIDER_TOKEN_LOOKALIKES)
def test_provider_token_lookalikes_do_not_fire(tmp_path, line):
    """A prefix rule looks unfalsifiable until you ask what else starts that way."""
    (tmp_path / "config.py").write_text(f"import os\n{line}\n")
    rep = run_scan([str(tmp_path)], load_config(None), only=["secrets"], use_adapters=False)
    fired = {f.rule_id for f in rep.findings
             if f.rule_id.split(".")[-1] in
             {"stripe-key", "openai-key", "anthropic-key", "google-api-key",
              "gitlab-token", "npm-token", "sendgrid-key", "pypi-token",
              "slack-webhook"}}
    assert not fired, f"{line} fired {fired}"
