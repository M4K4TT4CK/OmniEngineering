"""Same meaning, different spelling: the answer must not move.

A metamorphic test needs no known-correct answer. It takes one input, changes
something that does not change what the code *means*, and requires the verdict
to stay put. That suits a scanner, because the expensive question -- "is this
finding right?" -- needs a person, while "does re-encoding this file change
the answer?" does not.

Every case here is drawn from a real defect. Secrets were invisible unless
quoted, so `.env`, Kubernetes Secrets, `docker-compose`, `export VAR=`,
Dockerfile `ENV` and `.properties` all went unread. Arbiter's own output was
written in the platform codepage, and scanned files were read in it. A
placeholder with a hyphen was reported as a credential because `\\w` does not
match `-`. Each was fixed with a test for the one spelling that failed; these
ask the question across the whole set, so the next spelling is covered before
it is found in the wild.
"""
from __future__ import annotations

import pytest

from arbiter.core import Finding, Location
from arbiter.engine import run_scan
from arbiter.policy import load_config

KEY = "AKIAIOSFODNN7EXAMPLE"
# An opaque credential with no recognisable provider shape, so the rule that
# has to fire is the one reading `name = value` rather than one matching a
# known key format.
GENERIC = "Zx91qKp4vWmTn83LcRd7"


def _scan_tree(files: dict[str, bytes], root) -> dict[str, list]:
    """Write every case into one tree, scan once, group findings by file.

    A scan per case is the obvious shape and costs about three seconds each,
    which puts a parametrized suite over a minute and a half. These cases are
    independent files, so one scan answers all of them, and grouping the
    result by path keeps each case reporting its own failure.
    """
    for name, raw in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    # Skipping the external analyzers is not just about their results: probing
    # whether each one is installed costs about a hundred seconds on a machine
    # where they are not, and these cases are about Arbiter's own reading.
    found = run_scan([str(root)], dict(load_config(None)), only=["secrets"],
                     skip=["checkov", "semgrep", "bandit", "ruff", "gitleaks"]).active()
    by_path: dict[str, list] = {name: [] for name in files}
    for f in found:
        by_path.setdefault(f.location.path, []).append(f)
    return by_path


def _rule_ids(findings) -> set[str]:
    return {f.rule_id for f in findings}


# --------------------------------------------------------------------------
# Re-encoding the same file
# --------------------------------------------------------------------------

LINE = f'aws_access_key_id = "{KEY}"\n'

ENCODINGS = [
    ("utf-8", LINE.encode("utf-8")),
    ("utf-8-bom", b"\xef\xbb\xbf" + LINE.encode("utf-8")),
    ("utf-16-with-bom", LINE.encode("utf-16")),
    ("utf-16-be-with-bom", b"\xfe\xff" + LINE.encode("utf-16-be")),
    ("crlf", LINE.replace("\n", "\r\n").encode("utf-8")),
    ("no-trailing-newline", LINE.rstrip("\n").encode("utf-8")),
    ("latin-1-accents", ("# café\n" + LINE).encode("latin-1")),
    ("utf-8-accents", ("# café\n" + LINE).encode("utf-8")),
]


@pytest.fixture(scope="module")
def encoded_scan(tmp_path_factory):
    root = tmp_path_factory.mktemp("encodings")
    return _scan_tree({f"{label}.tf": raw for label, raw in ENCODINGS}, root)


@pytest.mark.parametrize("label,raw", ENCODINGS, ids=[e[0] for e in ENCODINGS])
def test_a_credential_is_found_however_the_file_is_encoded(encoded_scan, label, raw):
    """Re-encoding changes bytes, not meaning.

    A UTF-16 file is the case that bit: every ASCII character is followed by a
    NUL, the NUL made it look binary, binary files are never read, and the
    scan reported nothing at all for a file it had not opened.
    """
    found = encoded_scan[f"{label}.tf"]
    assert "arbiter/secrets.aws-access-key" in _rule_ids(found), (
        f"credential missed when the file is encoded as {label}")


def test_headerless_utf16_is_left_as_unread_rather_than_called_clean(tmp_path):
    """The limit, stated on purpose rather than discovered later.

    UTF-16 with no byte-order mark is indistinguishable from binary without
    guessing, and guessing wrong renders a real binary as mojibake to scan.
    Such a file stays classified as data, so it is counted as unassessed
    rather than reported as clean -- the scanner declines instead of
    claiming.
    """
    from arbiter.inventory import _is_binary
    headerless = LINE.encode("utf-16-le")
    assert _is_binary(headerless), "headerless UTF-16 should stay classified as data"
    assert not _is_binary(LINE.encode("utf-16")), "a declared BOM means text"


# --------------------------------------------------------------------------
# Re-spelling the same assignment
# --------------------------------------------------------------------------

CARRIERS = [
    ("double_quoted", "conf.tf", f'aws_access_key_id = "{KEY}"\n'),
    ("single_quoted", "conf.py", f"aws_access_key_id = '{KEY}'\n"),
    ("unquoted", "conf.env", f"aws_access_key_id={KEY}\n"),
    ("shell_export", "env.sh", f"export AWS_ACCESS_KEY_ID={KEY}\n"),
    ("dockerfile_env", "Dockerfile", f"ENV AWS_ACCESS_KEY_ID={KEY}\n"),
    ("properties", "app.properties", f"aws.access.key.id={KEY}\n"),
    ("yaml_plain", "values.yaml", f"aws_access_key_id: {KEY}\n"),
    ("yaml_quoted", "v2.yaml", f'aws_access_key_id: "{KEY}"\n'),
    ("json", "conf.json", f'{{"aws_access_key_id": "{KEY}"}}\n'),
    ("spaced", "spaced.tf", f'aws_access_key_id   =   "{KEY}"\n'),
]


# Carriers for the line-ending cases. These deliberately do not reuse the set
# above: an AWS key is recognised by its own shape anywhere it appears, so it
# is found with or without a trailing carriage return and would prove nothing.
# The rule that breaks is the one reading `name = value`, which ends at the
# end of the line, and it needs a name that reads as a credential and a value
# with no provider shape of its own.
CRLF_CARRIERS = [
    ("dotenv", "api.env", f"API_KEY={GENERIC}\n"),
    ("yaml", "api.yaml", f"config:\n  api_key: {GENERIC}\n"),
    ("shell_export", "api.sh", f"#!/bin/sh\nexport DB_PASSWORD={GENERIC}\n"),
    ("dockerfile_env", "Dockerfile", f"FROM alpine\nENV API_KEY={GENERIC}\n"),
    ("properties", "api.properties", f"api.key={GENERIC}\n"),
]


@pytest.fixture(scope="module")
def carrier_scan(tmp_path_factory):
    root = tmp_path_factory.mktemp("carriers")
    files = {name: text.encode("utf-8") for _, name, text in CARRIERS}
    for label, name, text in CRLF_CARRIERS:
        files[f"crlf_{name}"] = text.replace("\n", "\r\n").encode("utf-8")
    return _scan_tree(files, root)


@pytest.mark.parametrize("label,name,text", CARRIERS, ids=[c[0] for c in CARRIERS])
def test_one_credential_is_found_in_every_carrier(carrier_scan, label, name, text):
    """The same secret, written the way each format writes it.

    The existing suite covers these formats with a different value in each.
    Holding the value fixed is what makes it metamorphic: any difference in
    the verdict is attributable to the spelling and nothing else.
    """
    assert carrier_scan[name], f"a credential written as {label} was not found"


@pytest.mark.parametrize("label,name,text", CRLF_CARRIERS,
                         ids=[c[0] for c in CRLF_CARRIERS])
def test_every_carrier_survives_windows_line_endings(carrier_scan, label, name, text):
    """CRLF is not a different meaning, and it is what Windows writes.

    Reading a file as bytes rather than as text drops Python's newline
    translation, and six secret formats went silent the moment this project
    made that change -- the ones whose patterns end at the end of a line.
    """
    assert carrier_scan[f"crlf_{name}"], (
        f"a credential written as {label} was missed with CRLF line endings")


# --------------------------------------------------------------------------
# The false-positive direction
# --------------------------------------------------------------------------

NOT_SECRETS = [
    ("hyphenated_placeholder", "your-api-key-here"),
    ("angle_placeholder", "<YOUR_ACCESS_KEY>"),
    ("brace_placeholder", "${AWS_ACCESS_KEY_ID}"),
    ("shell_ref", "$AWS_ACCESS_KEY_ID"),
    ("changeme", "changeme"),
    ("xxx", "xxxxxxxxxxxxxxxxxxxx"),
    ("example_marker", "EXAMPLE_KEY_DO_NOT_USE"),
    ("redacted", "REDACTED"),
    ("ellipsis", "..."),
]


@pytest.fixture(scope="module")
def placeholder_scan(tmp_path_factory):
    root = tmp_path_factory.mktemp("placeholders")
    return _scan_tree(
        {f"{label}.env": f"aws_access_key_id={value}\n".encode("utf-8")
         for label, value in NOT_SECRETS}, root)


@pytest.mark.parametrize("label,value", NOT_SECRETS, ids=[c[0] for c in NOT_SECRETS])
def test_placeholders_are_not_reported_as_credentials(placeholder_scan, label, value):
    """A placeholder is what a manual writes where the key goes.

    This direction is the one that costs a user their trust: a scanner that
    reports the example in a README as a leaked credential gets switched off.
    `your-api-key-here` was reported exactly once, because the pattern used
    `\\w`, which does not match a hyphen.
    """
    found = placeholder_scan[f"{label}.env"]
    assert not found, (
        f"{label} is a placeholder, not a credential: "
        f"{[f.evidence for f in found]}")


# --------------------------------------------------------------------------
# Cosmetic edits must not move identity
# --------------------------------------------------------------------------

@pytest.mark.parametrize("label,evidence", [
    ("reindented", "    aws_key  =  \"AKIA\""),
    ("collapsed_spaces", "aws_key = \"AKIA\""),
    ("recased", "AWS_KEY = \"AKIA\""),
    ("trailing_space", "aws_key = \"AKIA\"   "),
])
def test_reformatting_does_not_change_a_findings_identity(label, evidence):
    """Fingerprints outlive reformatting or the baseline is noise.

    `status: new` is what a gate blocks on. If re-indenting a file re-keys
    every finding in it, the next pull request is a wall of false novelty and
    the gate gets turned off -- which is the same outcome as having no gate.
    """
    base = Finding(rule_id="r", title="t", evidence="aws_key = \"AKIA\"",
                   location=Location(path="a.tf", start_line=1))
    other = Finding(rule_id="r", title="t", evidence=evidence,
                    location=Location(path="a.tf", start_line=99))
    assert base.id == other.id, f"{label} changed the fingerprint"


def test_a_changed_value_is_a_different_finding():
    """The other half, or the rule above would be satisfied by a constant.

    A changed port or CIDR is a different finding, not the same one moved.
    """
    a = Finding(rule_id="r", title="t", evidence='cidr = "10.0.0.0/8"',
                location=Location(path="a.tf"))
    b = Finding(rule_id="r", title="t", evidence='cidr = "0.0.0.0/0"',
                location=Location(path="a.tf"))
    assert a.id != b.id
