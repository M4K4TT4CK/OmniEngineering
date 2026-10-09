"""Generated inputs for the hand-rolled parsers.

Arbiter reads a subset of HCL with its own brace matcher and splitter rather
than a grammar, which is a reasonable trade -- but every bug those functions
have had was an edge nobody thought to write a fixture for. A block written on
one line vanished, and a disk that *was* encrypted was reported unencrypted,
because the assignment pattern required the value to reach end-of-line. A
secret in a Go short declaration was invisible because `:=` was not an
assignment. A token ending in `-` escaped a `\\b` word boundary that does not
exist after a hyphen.

Fixtures cannot cover a grammar; they cover the sentences someone thought of.
These tests state what must be true of *any* input and let the generator look
for the counterexample, so a failure arrives as the smallest input that
produces it rather than as a bug report from a real repository.

The properties are deliberately about not-lying and not-hanging rather than
about parse correctness: a parser for a subset is allowed to decline input it
does not handle, but it is never allowed to hang, crash, or invent structure.
"""
from __future__ import annotations

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from arbiter.core import Finding, Location, normalize_snippet
from arbiter.graph import _coerce, _match_brace, _parse_hcl_body, _split_top_level

# The pull-request gate is a blocking check; the budget matters more there
# than exhaustiveness, and the nightly cycle is where a long run belongs.
CI = settings(max_examples=150, deadline=None,
              suppress_health_check=[HealthCheck.too_slow])

# Characters chosen to hit the parser's own special cases rather than random
# unicode: quotes, escapes, comment markers, braces, brackets and separators.
HCL_CHARS = st.sampled_from(list('{}[]"\\#/,= \n\tabc01_.-'))
hcl_text = st.text(HCL_CHARS, max_size=120)


# --------------------------------------------------------------------------
# The brace matcher
# --------------------------------------------------------------------------

@CI
@given(hcl_text)
def test_brace_matching_never_crashes_or_runs_off_the_end(text):
    idx = text.find("{")
    if idx < 0:
        return
    end = _match_brace(text, idx)
    assert isinstance(end, int)
    assert idx < end <= len(text), "a match must advance and stay inside the text"


@CI
@given(st.integers(min_value=0, max_value=4), hcl_text)
def test_balanced_braces_match_to_the_end_of_the_block(depth, filler):
    """Nesting a balanced block deeper must not lose the closing brace."""
    filler = filler.replace("{", "").replace("}", "").replace('"', "")
    body = "{" * (depth + 1) + filler + "}" * (depth + 1)
    assert _match_brace(body, 0) == len(body)


@CI
@given(hcl_text)
def test_a_brace_inside_a_string_is_not_structure(text):
    """Quoting is the parser's own special case, so generate against it."""
    text = text.replace('"', "").replace("\\", "")
    body = '{ name = "' + text.replace("\n", " ") + '" }'
    assert _match_brace(body, 0) == len(body)


# --------------------------------------------------------------------------
# The splitter
# --------------------------------------------------------------------------

@CI
@given(hcl_text)
def test_splitting_never_invents_or_loses_content(text):
    parts = _split_top_level(text)
    assert isinstance(parts, list)
    # Every piece came out of the input; nothing was manufactured.
    for part in parts:
        assert part.strip(", ") in text or part.strip() == ""


@CI
@given(st.lists(st.text(st.sampled_from("abc01_"), min_size=1, max_size=6),
                min_size=1, max_size=6))
def test_plain_values_split_back_into_the_list_they_came_from(values):
    assert [p.strip() for p in _split_top_level(",".join(values))] == values


@CI
@given(st.lists(st.text(st.sampled_from("abc01_"), min_size=1, max_size=5),
                min_size=1, max_size=4))
def test_a_separator_inside_brackets_is_not_a_separator(values):
    """`[a, b]` is one value, not two -- the case that makes the function
    exist rather than a call to str.split."""
    assert len(_split_top_level("[" + ",".join(values) + "]")) == 1


# --------------------------------------------------------------------------
# Value coercion
# --------------------------------------------------------------------------

@CI
@given(st.text(max_size=40))
def test_coercing_any_value_returns_something_usable(raw):
    value = _coerce(raw)
    assert isinstance(value, (str, int, float, bool, list))


@CI
@given(st.integers(min_value=-10**9, max_value=10**9))
def test_integers_round_trip(n):
    assert _coerce(str(n)) == n


@CI
@given(st.text(st.sampled_from("abc01_ -."), max_size=20))
def test_a_quoted_value_keeps_exactly_what_was_quoted(inner):
    """Rules match on property values, so a stray quote or a trimmed character
    is the difference between a rule firing and not."""
    assert _coerce('"' + inner + '"') == inner


@CI
@given(hcl_text)
def test_parsing_a_body_never_crashes(text):
    result = _parse_hcl_body(text)
    assert isinstance(result, dict)


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

@CI
@given(st.lists(st.text(st.sampled_from("abc01_-."), min_size=1, max_size=8),
                min_size=1, max_size=5))
def test_a_path_has_one_fingerprint_whichever_separator_wrote_it(segments):
    """Stated over generated paths rather than one example.

    Adjudications are permanent and keyed by fingerprint, so a path that
    hashes differently per platform splits one defect into two identities and
    a verdict recorded on one machine can never match the other.
    """
    posix = "/".join(segments)
    windows = chr(92).join(segments)
    a = Finding(rule_id="r", title="t", evidence="e", location=Location(path=posix))
    b = Finding(rule_id="r", title="t", evidence="e", location=Location(path=windows))
    assert a.id == b.id


@CI
@given(st.text(max_size=60))
def test_normalizing_a_snippet_is_stable(text):
    """Applying it twice must equal applying it once, or a fingerprint depends
    on how many times the text has been through the pipeline."""
    once = normalize_snippet(text)
    assert normalize_snippet(once) == once


@CI
@given(st.text(max_size=60), st.integers(min_value=1, max_value=10**6))
def test_a_line_number_is_never_part_of_identity(evidence, line):
    base = Finding(rule_id="r", title="t", evidence=evidence,
                   location=Location(path="a.tf", start_line=1))
    moved = Finding(rule_id="r", title="t", evidence=evidence,
                    location=Location(path="a.tf", start_line=line))
    assert base.id == moved.id
