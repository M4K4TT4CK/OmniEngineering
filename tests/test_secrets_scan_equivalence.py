"""The keyword-anchored assigned-credential scan finds exactly what
`_ASSIGNED_SECRET.finditer` finds (ARB-076).

The scan tries the full pattern only within `_ASSIGNED_SECRET_PREFIX`
characters before a keyword. That is only sound while the keyword list and the
prefix bound agree with the pattern, so the first test reads them out of the
pattern itself: change one without the others and it fails here, not silently
in a scan.
"""
from __future__ import annotations

import random
import re
from pathlib import Path

from arbiter import probes as P


def _spans(matches) -> list[tuple]:
    return [(m.span(), m.groups()) for m in matches]


def _same(text: str) -> None:
    assert _spans(P._assigned_secret_matches(text)) == _spans(P._ASSIGNED_SECRET.finditer(text)), text[:200]


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def test_keyword_list_and_prefix_bound_match_the_pattern():
    pattern = _squash(re.sub(r"#[^\n]*", "", P._ASSIGNED_SECRET.pattern))
    words = _squash(P._ASSIGNED_SECRET_WORD.pattern).replace("(?ix)", "")
    bound = P._ASSIGNED_SECRET_PREFIX
    # The symbol group opens with the prefix, then the keyword alternation.
    assert f"([A-Za-z0-9_]{{0,{bound}}}(?:{words})" in pattern, pattern
    assert P._ASSIGNED_SECRET.flags & re.IGNORECASE
    assert P._ASSIGNED_SECRET_WORD.flags & re.IGNORECASE
    # No anchor or lookbehind: a match at a position depends on the text from
    # there on alone, which is what makes `match` at candidate starts exact.
    # (`[^...]` is a negated class, not an anchor.)
    assert not re.search(r"\(\?<[=!]|\\A|\\b|\\B|\$|(?<!\[)\^", pattern)


def test_hand_written_cases():
    cases = [
        'password = "hunter2hunter2"',
        'DB_PASSWORD="s3cr3t-value-long"',
        "x" * 23 + 'token = "abcdefghij"',
        "x" * 24 + 'token = "abcdefghij"',
        "x" * 25 + 'token = "abcdefghij"',
        "x" * 60 + 'apiKeySecret: \'abcdefghijkl\'',
        'clientSecret := `abcdefghijklmnop` and access_key = "AKIAABCDEFGHIJ"',
        'pwdpwdpwd = "aaaaaaaa" secret="bbbbbbbbbb"secret="cccccccccc"',
        # Unicode case folding: KELVIN SIGN folds to k, LONG S to s.
        'api_Key = "abcdefghijk"', 'paſſword = "abcdefghij"',
        'token token token = "short"', "", "password", 'credential=""',
        'private.key: "-----BEGIN-----"\nprivate-key = "abcdefgh12"',
    ]
    for c in cases:
        _same(c)


def test_random_text_around_keywords():
    rng = random.Random(76)
    pieces = ["password", "PASSWD", "pwd", "Secret", "token", "api_key", "apikey", "access.key",
              "client-secret", "private_key", "Credential", "x", "_", "9", "a" * 20, " ", "=",
              ":", ":=", "::", '"', "'", "`", "\n", "value123", "abcdefghijklmnop", "K",
              "ſ", "-"]
    for _ in range(3000):
        _same("".join(rng.choice(pieces) for _ in range(rng.randint(1, 40))))


def test_real_source_files():
    root = Path(P.__file__).resolve().parents[2]
    files = sorted((root / "src").rglob("*.py")) + sorted((root / "tests").rglob("*.py"))
    files += sorted((root / "fixtures").rglob("*"))[:400] if (root / "fixtures").is_dir() else []
    for f in files:
        if f.is_file():
            _same(f.read_text(encoding="utf-8", errors="replace"))
