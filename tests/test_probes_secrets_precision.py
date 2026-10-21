"""secrets.assigned-credential on exemplary code (ARB-065).

1,696 reports on gold, false in every one sampled. Each test pins one cause
and keeps a real-looking secret that must still be reported beside it.
"""
from __future__ import annotations

from pathlib import Path

from arbiter.engine import run_scan
from arbiter.policy import load_config


def _scan(tmp_path: Path, files: dict[str, str], probe: str):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    rep = run_scan([str(tmp_path)], load_config(None), only=[probe], use_adapters=False,
                   use_cache=False)
    return rep.active()


def _credentials(tmp_path, files):
    return {(f.location.path, f.location.start_line) for f in _scan(tmp_path, files, "secrets")
            if f.rule_id == "arbiter/secrets.assigned-credential"}


# --- secrets.assigned-credential -------------------------------------------------

def test_an_identifier_on_the_right_is_not_a_literal(tmp_path):
    """urllib3's connection.py:747, `self.key_password = key_password`."""
    hits = _credentials(tmp_path, {
        "src/conn.py": ("class C:\n    def __init__(self, key_password):\n"
                        "        self.key_password = key_password\n"
                        "        self.api_token = self._load_token_from(path)\n"),
        "src/Realm.java": "class R { void f() { this.accessTokenSecret = realm.getTokenSecret2(); } }\n",
        ".env": "DB_PASSWORD=pR0d-s3cret-9x\n",
    })
    assert hits == {(".env", 1)}, "only the unquoted value in a .env file is a literal"


def test_constants_holding_key_names_urls_and_headers_are_not_secrets(tmp_path):
    hits = _credentials(tmp_path, {
        "src/consts.py": (
            'TOKEN_URL = "https://oauth2.googleapis.com/token"\n'
            'KEY_PASSWORD_KEY = "keyPassword"\n'
            'AUTH_TOKEN_HEADER = "X-Auth-Token"\n'
            'ATTR_GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"\n'
            'SECRET_PATH = "/v1/auth/secret"\n'
            'SMTP_PASSWORD = "q8Vz!m2Lr7#tKw4P"\n'
            'PASSWORD_TOM_9 =\n    "somethingVeryRandomWhichNoOneWillEverTypeInAsPasswordForTom"\n'
            'JWT_SECRET = "super-secret-key"\n'),
    })
    # WebGoat's camel-case passphrase and a kebab-case weak secret are values,
    # not key names.
    assert hits == {("src/consts.py", 6), ("src/consts.py", 7), ("src/consts.py", 9)}


def test_i18n_message_bundles_are_skipped_but_config_properties_are_not(tmp_path):
    hits = _credentials(tmp_path, {
        "core/src/main/resources/messages_de.properties":
            "BindAuthenticator.emptyPassword=Leeres-Passwort3!\n",
        "core/src/main/resources/LocalStrings.properties":
            "realm.tokenSecret=Ung\u00fcltiges-Token4\n",
        "app/src/main/resources/application.properties":
            "spring.datasource.password=Pr0d-Db-Pa55\n",
    })
    assert hits == {("app/src/main/resources/application.properties", 1)}


def test_values_echoing_their_own_name_and_test_data_are_not_secrets(tmp_path):
    """spring-security: `ELT_ENCRYPTION_CREDENTIAL = "encryption-credential"`;
    django's tests: `User(password="some-gibbberish")`. A generated-looking key
    pasted into a test is still reported."""
    hits = _credentials(tmp_path, {
        "src/Parser.java": (
            'class P {\n  static final String ELT_ENCRYPTION_CREDENTIAL = "encryption-credential";\n'
            '  static final String INVALID_ID_TOKEN_ERROR_CODE = "invalid_id_token";\n'
            '  static final String API_TOKEN = "token-Xy7#Qp2z";\n}\n'),
        "tests/test_models.py": ('u = User(password="some-gibbberish")\n'
                                 'client = Client(token="test-token")\n'
                                 'LIVE = dict(api_key="a9F$kQ2!vL7zR4#m")\n'
                                 'wallet = dict(private_key="0x5bcc3e9d38baa06e7bfaab80ae5957bbe8ef059e")\n'),
        "docs/howto.txt": 'DB_PASSWORD = "Pr0d-Db-Pa55"\n',
    })
    assert hits == {("src/Parser.java", 4), ("tests/test_models.py", 3),
                    ("tests/test_models.py", 4)}


def test_attribute_name_constants_hashes_and_code_between_literals(tmp_path):
    """spring-security's XML parsers, encoder tests and toString()."""
    hits = _credentials(tmp_path, {
        "config/LdapParser.java": (
            'class L {\n  static final String ATT_USER_PASSWORD = "password-attribute";\n'
            '  static final String ENCODED = "x";\n'
            '  String s() { return "CredentialsNonExpired=").append(this.credentialsNonExpired).append(";"; }\n'
            '  String bcrypt = "$2a$10$dXJ3SW6G7P50lGmMkkmwe.20cQQubK3.HZWzG3YB1tlRy.fqvM/BG";\n'
            '  String ldapPassword = "{SSHA}PpuEwfdj7M1rs0C2W4ssSM2XEN/Y6S5U";\n'
            '  String userPassword = "{noop}B0bs-Pa55word";\n}\n'),
    })
    # `{noop}` is a plaintext password with an encoder id in front.
    assert hits == {("config/LdapParser.java", 7)}
