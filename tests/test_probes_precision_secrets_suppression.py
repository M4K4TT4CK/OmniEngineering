"""Round 2 precision (ARB-065): blanket-suppression, private-key, assigned-credential.

Each test pins one false-positive pattern read on the gold set, and keeps the
true positive beside it that must still be reported.
"""
from __future__ import annotations

from pathlib import Path

from arbiter.engine import run_scan
from arbiter.policy import load_config

KEY = ("-----BEGIN RSA PRIVATE KEY-----\n"
       "MIICXAIBAAKBgQDVgc+zdNmcxwg4xqdoiy/WpnYj0WFvE7A/zy0EfvnUhxhLAXlC\n"
       "bjQw5Sqxa8IwQVr4G/mR7wTTJtf/Nrt5bP+E2D4W9MtuL7tzZ9KS/7v3D3nninMP\n"
       "-----END RSA PRIVATE KEY-----\n")


def _scan(tmp_path: Path, files: dict[str, str], probe: str):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    rep = run_scan([str(tmp_path)], load_config(None), only=[probe], use_adapters=False,
                   use_cache=False)
    return rep.active()


def _hits(tmp_path, files, probe, rule):
    return {(f.location.path, f.location.start_line): f
            for f in _scan(tmp_path, files, probe) if f.rule_id == rule}


# --- assurance.blanket-suppression ------------------------------------------------

BLANKET = "arbiter/assurance.blanket-suppression"


def test_a_blanket_suppression_with_a_written_reason_is_not_reported(tmp_path):
    """opentelemetry-js `// @ts-expect-error access internal property for
    testing`, axios `// @ts-expect-error -- the encoding opt-in only accepts
    booleans`, celery `# type: ignore  # whole point here is ...`. TypeScript
    cannot name an error, so a reason is all its author can give."""
    hits = _hits(tmp_path, {
        "src/a.ts": ("// @ts-expect-error access internal property for testing\n"
                     "x.y = 1;\n"
                     "// @ts-expect-error -- the encoding opt-in only accepts booleans\n"
                     "x.z = 2;\n"
                     "// @ts-ignore\n"
                     "x.w = 3;\n"
                     "// @ts-expect-error TS2345\n"
                     "x.v = 4;\n"),
        "src/b.py": ("import os  # type: ignore  # whole point here is that this doesn't match\n"
                     "import sys  # noqa\n"),
    }, "assurance", BLANKET)
    # A bare directive, or one followed by a lone token, is still blanket.
    assert set(hits) == {("src/a.ts", 5), ("src/a.ts", 7), ("src/b.py", 2)}


def test_a_directive_quoted_in_a_comment_is_not_a_suppression(tmp_path):
    """jose's JSDoc shows `* // @ts-ignore` in a usage example, and its
    tools/declaration-comments.js mentions "stray `// @ts-ignore` lines"."""
    hits = _hits(tmp_path, {
        "src/remote.ts": ("/**\n * Example:\n * // @ts-ignore\n * fetch()\n */\n"
                          "// three stray `// @ts-ignore` lines in the output\n"
                          "fetch('http://x') // eslint-disable-line\n"),
    }, "assurance", BLANKET)
    assert set(hits) == {("src/remote.ts", 7)}, "a `://` URL does not open a comment"


# --- secrets.private-key ------------------------------------------------------------

PRIVATE_KEY = "arbiter/secrets.private-key"


def test_a_key_file_in_a_fixture_data_directory_is_not_reported(tmp_path):
    """grpc-go testdata/x509, etcd tests/fixtures/*.key.insecure, cryptography
    vectors/: 146 of 260 gold reports. A key under another test path, or
    written into test code, is still reported at low."""
    hits = _hits(tmp_path, {
        "testdata/x509/server1_key.pem": KEY,
        "tests/fixtures/server.key.insecure": KEY,
        "vectors/cryptography_vectors/asymmetric/rsa.pem": KEY,
        "tests/certs/server.key": KEY,
        "testdata/keys.go": f'package t\n\nconst k = `{KEY}`\n',
        "deploy/server.key": KEY,
    }, "secrets", PRIVATE_KEY)
    assert set(hits) == {("tests/certs/server.key", 1), ("testdata/keys.go", 3),
                         ("deploy/server.key", 1)}
    assert hits[("tests/certs/server.key", 1)].confidence == "low"
    assert hits[("testdata/keys.go", 3)].confidence == "low"
    assert hits[("deploy/server.key", 1)].severity == "critical"


def test_test_suites_the_inventory_misses_are_test_material(tmp_path):
    """keycloak testsuite/, urllib3 dummyserver/certs, celery t/unit, jose's
    tap/fixtures.ts and caddy's caddytest/caddy.localhost.key were critical."""
    hits = _hits(tmp_path, {
        "testsuite/servers/keystore/client.key": KEY,
        "dummyserver/certs/server.key": KEY,
        "t/unit/security/__init__.py": f'KEY1 = """{KEY}"""\n',
        "tap/fixtures.ts": f"export const k = `{KEY}`\n",
        "caddytest/caddy.localhost.key": KEY,
        "src/latest/server.key": KEY,
        "deploy/test-gateway.pem": KEY,
    }, "secrets", PRIVATE_KEY)
    lows = {k for k, f in hits.items() if f.confidence == "low"}
    assert lows == {("testsuite/servers/keystore/client.key", 1),
                    ("dummyserver/certs/server.key", 1),
                    ("t/unit/security/__init__.py", 1), ("tap/fixtures.ts", 1),
                    ("caddytest/caddy.localhost.key", 1)}
    assert hits[("src/latest/server.key", 1)].severity == "critical"
    assert hits[("deploy/test-gateway.pem", 1)].severity == "critical"


# --- secrets.assigned-credential ------------------------------------------------------

CREDENTIAL = "arbiter/secrets.assigned-credential"


def test_key_names_and_message_ids_with_a_truncated_symbol(tmp_path):
    """keycloak's Messages.java, Details.java and OIDCLoginProtocolFactory,
    spring-security's claim names, tomcat's `j_password` form field, rails'
    `_aj_hash_with_indifferent_access`, and dotted names carrying a digit."""
    hits = _hits(tmp_path, {
        "src/Messages.java": (
            "class M {\n"
            '  static final String EXPIRED_ACTION_TOKEN_NO_SESSION = "expiredActionTokenNoSessionMessage";\n'
            '  String LOGOUT_TRIGGERED_BY_ACTION_TOKEN = "logout_triggered_by_action_token";\n'
            '  String CONFIG_ALLOW_USERINFO_WITH_LIGHTWEIGHT_ACCESS_TOKEN = "allow-userinfo-with-lightweight-access-token";\n'
            '  static final String FORM_PASSWORD = "j_password";\n'
            '  static final String CSRF_TOKEN_SESSION_KEY = "org.apache.tomcat.manager2.CsrfFilter.token";\n'
            '  static final String JWT_SECRET = "super-secret-key";\n'
            '  static final String DB_PASSWORD = "correct_horse_battery_staple";\n'
            '  static final String API_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N";\n'
            "}\n"),
        "src/access_token.go": ('package x\n\nconst accessTokenCredsTypeURL = '
                                '"type.googleapis.com/envoy.extensions.grpc_service.call_credentials.access_token.v3.AccessTokenCredentials"\n'),
    }, "secrets", CREDENTIAL)
    # A weak secret spelled as words, a snake_case passphrase sharing no word
    # with its symbol, and a JWT all stay reported.
    assert set(hits) == {("src/Messages.java", 7), ("src/Messages.java", 8),
                         ("src/Messages.java", 9)}


def test_references_interpolation_fetch_modes_hashes_and_type_annotations(tmp_path):
    hits = _hits(tmp_path, {
        "ca/openssl.cnf": "private_key       = $dir/private/ca.key.pem\n",
        "pom.xml": '<x token="$${project.version}"/>\n',
        "lib/signer.rb": 'credential_info = "#{date}/#{s3_config.region}/s3/aws4_request"\n',
        "web/api.js": "fetch(u, { credentials: 'same-origin' });\nconst withCredentials = 'include';\n",
        "test/Pbkdf2Tests.java": (
            'class T {\n  String encodedPassword = "ab1146a8458d4ce4e65789e5a3f60e423373cfa10b01abd2";\n'
            '  String PWD_MD5 = "5f4dcc3b5aa765d61d8327deb882cf99";\n'
            '  String DEFAULT_ENCODED_SECRET_KEY = "bCzY/M48bbkwBEWjmNSIEPfwApcvXOnkCxORBEbPr+4=";\n}\n'),
        "index.d.cts": "interface Cancel {\n  token: CancelToken;\n}\n",
        "deploy/app.cnf": "private_key = Xk9-mQ2vL7pR!\n",
    }, "secrets", CREDENTIAL)
    # A base64 key is not a password hash, and a literal in a config file is
    # still a literal.
    assert set(hits) == {("test/Pbkdf2Tests.java", 4), ("deploy/app.cnf", 1)}


def test_data_files_in_fixture_directories_are_not_read_for_credentials(tmp_path):
    """cryptography's RFC 8032 vectors (`SECRET = 6c82...`) and coredns's
    plugin/sign/testdata/*.private. A credential in test code is still read."""
    hits = _hits(tmp_path, {
        "vectors/asymmetric/Ed448/rfc8032.txt":
            "SECRET = 6c82a562cb808d10d632be89c8513ebf6c929f34ddfa8c9f63c9960ef6e348a3\n",
        "plugin/sign/testdata/K.private": "PrivateKey: rm7EdHRca//6xKpJzeoLt/mrfgQnltJ0WpQGtOG59yo=\n",
        "testdata/conf_test.go": 'package t\n\nconst apiKey = "a9F$kQ2!vL7zR4#mX8wq"\n',
    }, "secrets", CREDENTIAL)
    assert set(hits) == {("testdata/conf_test.go", 3)}
