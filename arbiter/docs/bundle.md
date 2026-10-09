# The Air-Gapped Bundle

A directory that installs Arbiter on a machine with no network. Build it where
there is one, carry it over, run one script.

## Contents

- [What is in it](#what-is-in-it)
- [What is not in it, and why](#what-is-not-in-it-and-why)
- [Building](#building)
- [Verifying](#verifying)
- [Installing offline](#installing-offline)

---

## What is in it

```text
wheels/arbiter_eval-0.1.0-py3-none-any.whl     Arbiter, packs included
wheels/pyyaml-6.0.3-cp313-...whl               the runtime dependency (tomli too, below 3.11)
knowledge.json                                 the checkout's calibration knowledge, when present
install.sh  install.ps1                        pip install --no-index --find-links wheels arbiter-eval
README.txt                                     the three steps, and the sentence below about analyzers
MANIFEST.json                                  version, Python floor, build time, SHA-256 per file
```

The rule packs are not copied beside the wheel: `pyproject.toml` lists
`packs/**` as package data, so they are already inside it. `knowledge.json`
travels only when `.arbiter/knowledge.json` exists in the checkout the bundle
was built from, and the manifest says which it was.

A bundle is built for the Python and platform of the machine that built it.
PyYAML ships a compiled wheel per interpreter version and platform, and `pip
download` fetches the one that matches the builder — so build on a machine that
matches the target, or build with the target's interpreter.

## What is not in it, and why

The five external analyzers — ruff, bandit, semgrep, checkov and gitleaks — are
**not included**. The manifest states it in words:

> `"analyzers": "not included: redistribution review pending (ARB-005); install ruff, bandit, semgrep, checkov and gitleaks from your own mirror"`

Shipping them would be a redistribution, and the per-component redistribution
review (L-6 in [licensing.md](licensing.md), tracked as ARB-005) has not been
done; semgrep's LGPL-2.1 terms in particular need a person to review and
record it in [NOTICE.md](../NOTICE.md). A bundle of Arbiter alone conveys
nothing but Arbiter, which is why it is not blocked.

On the air-gapped machine, install the analyzers from your own mirror and
Arbiter's adapters find them on `PATH` as usual. A scan that cannot find one
reports that probe as *not assessed*; it never passes it.

## Building

```bash
arbiter bundle build --out ./arbiter-bundle
arbiter bundle build --out ./arbiter-bundle --source /path/to/checkout
arbiter bundle build --out ./arbiter-bundle --no-deps-download
```

`--out` must be a new or empty directory: a leftover file would be one the
manifest does not name, and `verify` would refuse the result. `--source` names
the checkout to build from; by default it is the one the running `arbiter` was
imported from, and an installed copy that was not a checkout says so and asks
for the flag.

Two pip steps run, and only the second needs a network:

1. `pip wheel --no-deps` of the checkout. This runs offline when `setuptools`
   is importable by the interpreter running the build (the wheel is then built
   without pip's isolated environment; the manifest records
   `wheel_built_offline`). Without it, pip fetches a build backend first.
2. `pip download --only-binary=:all:` of the runtime dependencies as declared in
   `pyproject.toml`. This needs an index — PyPI, or a local mirror named through
   `PIP_INDEX_URL` or `PIP_FIND_LINKS`. `--no-deps-download` skips it for a
   target that already holds PyYAML; the manifest then says
   `"dependencies_included": false` and the README says what to provide.

## Verifying

```bash
arbiter bundle verify ./arbiter-bundle
```

Every hash in `MANIFEST.json` is recomputed. The verdict is one line, and the
exit code is `1` on any of:

| Problem | Meaning |
|---|---|
| `missing` | a file the manifest names is not there |
| `altered` | a file's SHA-256 no longer matches |
| `extra (not in manifest)` | a file is present that the manifest does not name |

An extra file is a failure rather than a warning because `pip install
--find-links wheels` installs whatever wheel it finds there; a file nobody
listed is exactly what a verifier exists to notice. The manifest itself is the
one file not hashed.

Verify before carrying the bundle across, from the machine that built it; and
again after installing, from the installed copy, if the transfer is the part you
do not trust.

## Installing offline

```bash
./install.sh                          # POSIX; PYTHON=/path/to/python selects the interpreter
.\install.ps1                         # PowerShell; $env:PYTHON does the same
arbiter --version
```

Both scripts run `pip install --no-index --find-links wheels arbiter-eval` and
nothing else. A virtualenv is a good target. Then, if you want the calibration
the checkout carried, copy `knowledge.json` to `.arbiter/knowledge.json` in
the repository you scan; see [calibration.md](calibration.md) for what it holds
and how a scan pins it.
