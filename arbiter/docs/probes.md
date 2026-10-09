# Detection Reference

Every check Arbiter can run, what it requires, and what it catches. Probes that
cannot run are recorded as *not assessed* with a reason; they are never silently
treated as passes.

## Contents

- [Native probes](#native-probes)
- [External adapters](#external-adapters)
- [Infrastructure as code](#infrastructure-as-code)
- [The resource graph](#the-resource-graph)
- [Machine-authored code](#machine-authored-code)
- [Contracts between artifacts](#contracts-between-artifacts)
- [Assurance: is the checking switched on?](#assurance-is-the-checking-switched-on)
- [The judgement pass](#the-judgement-pass)
- [Two findings that read history](#two-findings-that-read-history)
- [What doc_drift leaves alone](#what-doc_drift-leaves-alone)

---

## Native probes

| Probe | Dimension | Requires | Catches |
|---|---|---|---|
| `secrets` | security | nothing | AWS keys, private keys, GitHub/Slack tokens, credentialed URLs, high-entropy literals assigned to credential-named symbols |
| `resource_policy` | security, compliance | nothing | 13 provider-neutral rules over the normalized resource graph — unencrypted storage, public buckets, open ingress, plaintext listeners, missing retention |
| `quality` | quality | nothing | Oversized files, TODO clusters, repositories with no tests |
| `ast_metrics` | quality | tree-sitter | Function length, cyclomatic complexity and nesting depth measured from a real parse tree, in 11 languages |
| `house_rules_ast` | quality, security | tree-sitter | Your own tree-sitter queries from `arbiter.yaml` |
| `supply_chain` | supply chain | nothing | Unpinned Python/npm dependencies, GitHub Actions on mutable refs, `pull_request_target` |
| `doc_drift` | drift | nothing | Broken documentation links, files described in prose that do not exist, documented environment variables nothing reads |
| `interface` | interface | 2+ repos | Six cross-repo seam checks — see [systems.md](systems.md). On a single repository it is recorded as not applicable (`n/a` in the NOT ASSESSED block) and leaves the coverage denominator; a probe that was *prevented* from running stays in it, and claim invariant CI-12 refuses a report that marks a prevented probe as not applicable. |
| `house_rules` | quality, drift | nothing | Your own rules from `arbiter.yaml` |
| `assurance` | assurance | nothing | Whether the *checking* is switched on — see below |
| `authored` | supply chain, security | nothing | Defects characteristic of machine-drafted code — see below |
| `contract` | drift | nothing | Contracts declared in one artifact and implemented in another — see below |
| `governance` | drift | a failure ledger | The ledger held against the change — see [Probe scopes](#probe-scopes). `governance.open-failure-untested`: an open or mitigated entry's affected file changed without any of its regression tests (`--changed` only). `governance.regression-test-missing`: a fixed entry whose regression test file is gone. Not applicable (`n/a`) without `.ai/failures/failure-ledger.json` |
| `judgement` | drift | a model | Claims in prose the code contradicts. Inferred, never gates |

List what can run in a given checkout, and why anything cannot:

```bash
arbiter probes ./repo
```

### Probe scopes

Every probe declares how much of the tree it needs to answer honestly, which
is what decides its fate under `--changed` (see [ci.md](ci.md#scanning-only-what-changed)):

| Scope | Meaning | In a partial scan |
|---|---|---|
| `file` | every finding depends only on the file it is in | runs over the changed files; the answer is complete for those files |
| `repo` | the answer depends on relationships between files | recorded as not assessed, because a subset gives a wrong answer rather than a smaller one |
| `change` | reads a small, fixed set of files it names itself (the failure ledger, the requirement registry) plus the set of changed paths | runs; a full scan is the same probe answered with the empty change |

`governance` is the one change-scoped probe. In a `--changed` scan the run
also reads the commits since the base, once per repository, and tags every
finding in the change with `req:<ID>` for the requirement ids cited by the
commits that touched the finding's own file (the registry's
`requirement_id_prefix`, old ids resolved through its `id_aliases`), marked
`req-scope:commits`: that is attribution. A file no such commit cited falls
back to every id the range cites, marked `req-scope:open`, which is context
rather than a claim about which requirement introduced the finding; a full
scan carries only the `req-scope:open` marker. The Markdown report and the
pull-request comment add a **By requirement** table (counts by severity and
the first three titles per id, with an `unattributed` row for in-change
findings no commit attributed) and a note under it saying which scope each
count came from. Titles only, never evidence.

## External adapters

Adapters for `ruff`, `bandit`, `checkov`, `semgrep` and `gitleaks` normalize
third-party output into the same `Finding` shape. Adapters are declarative
manifests in `src/arbiter/packs/adapters/`, not code.

Arbiter does not vendor or redistribute these analyzers; install them
separately (`./tools/install_tools.sh` pins known-good versions). Any that are
absent report as skipped with the binary named, and coverage drops accordingly.

External findings often arrive without a severity. How Arbiter assigns one from
measurement rather than invention is described in
[calibration.md](calibration.md#calibrating-someone-elses-tool).

---

## Infrastructure as code

### Terraform: supply a plan

Reading `.tf` source is a fallback. A plan has resolved variables, expanded
`for_each` and `count`, and flattened modules — source has none of that.

```bash
terraform plan -out=tfplan.bin
terraform show -json tfplan.bin > tfplan.json
arbiter scan . --tfplan tfplan.json          # or drop tfplan.json in the repo
```

Arbiter never generates the plan itself: `terraform init` downloads and executes
provider code, and the tool does not execute what it scans.

On the bundled `fixtures/tfplan`, where one of two `for_each` bucket instances
gets a public ACL from a conditional expression:

| | Source only | With the plan |
|---|---|---|
| Public bucket | **missed** — the conditional is unparseable literally | found, cited at `modules/storage/main.tf:5` |
| Unencrypted volume (`encrypted = var.encrypt_volumes`) | **false high** — reads the variable name as a value | *not assessed*, unknown until apply |
| Destroyed security group with open ingress | flagged | ignored; the plan destroys it |
| Bucket instances seen | 1 | 2 |

Three details make this work:

- **Findings still cite source.** Plan JSON has no file or line, so each plan
  resource is joined back to the HCL block that declared it by address, and the
  finding points at code you can open.
- **Unknown is not absent.** A value in `after_unknown` cannot be judged.
  Treating it as missing is exactly how a scanner reports an encrypted bucket as
  unencrypted, so the check returns *unknown* and is reported as not assessed —
  never as a pass, never as a finding.
- **Siblings link through `configuration`.** By the time
  `bucket = aws_s3_bucket.data.id` reaches `after`, it is a resolved string or
  an unknown; the reference only survives in the plan's configuration section.

A repository read from source alone gets an `info` finding saying so.

## The resource graph

Terraform, CloudFormation, CDK-synthesized templates and Kubernetes manifests
normalize into one `Resource` shape with a provider-neutral `kind`, so a rule is
written once:

```yaml
- id: unencrypted-database
  match_kinds: [database]
  assert: property_truthy
  any_of: [storage_encrypted, StorageEncrypted, encrypted, KmsKeyId]
  severity: high
  controls: [NIST-800-53r5:SC-28]
```

That fires on an RDS instance, an Azure PostgreSQL server and a Cloud SQL
instance without a line of provider-specific code.

---

## Machine-authored code

A growing share of new code is drafted by a model, and model-drafted code fails
in ways human-drafted code mostly does not: confident, fluent, plausible-looking
code that refers to things which do not exist. A model is the worst available
reviewer for this — asked to check its own hallucinated import, it reads the
import, finds it plausible (it generated it precisely because it was plausible)
and passes.

These failures are decidable, so they are a deterministic probe rather than part
of the judgement pass:

| Check | What it catches |
|---|---|
| `undeclared-import` / `import-of-nonexistent-package` | A module nothing declares. See the two modes below. |
| `stub-on-production-path` | `return True  # TODO`, `raise NotImplementedError`, a `pass` body. Severity keyed to whether the function name says it decides something: a stub in `verify_signature` is not an unfinished feature, it is an always-yes. |
| `security-check-disabled` | `verify=False`, `InsecureSkipVerify: true`, `StrictHostKeyChecking=no`. Written to make an example work, then never removed. |
| `docstring-promises-absent-behaviour` | A docstring saying the function validates or retries, in a body that does neither. |

The import check ships in **two modes**, and the split is the honest part:

- **Offline** it knows only that a manifest does not declare something. That is
  mostly a stale manifest, so it reports at zero weight and says the distinction
  needs a registry.
- **Connected** it asks the registry whether the package exists at all. A name
  that resolves to nothing is **critical** — the import cannot ever have worked,
  so the name was invented, and an unclaimed package name sitting in a shipped
  import is an invitation to whoever registers it first.

```console
$ arbiter scan ./app --profile connected
  critical  import-of-nonexistent-package  fastapi_auth_middleware_helper
  critical  import-of-nonexistent-package  secure_token_validator
```

Zero false criticals across the corpus.

---

## Contracts between artifacts

A repository usually holds two descriptions of the same thing, in different
languages, maintained by different habits, and checked against each other by
nobody: an OpenAPI document and the routes the server registers; a set of
migrations and the model the application queries. Each half is valid on its own
terms — the spec parses, the routes compile, the migrations apply — and no
linter compares them, because each tool sees one side. The failure shows up at
runtime as a 404 against a documented endpoint.

This is the cross-repo seam idea moved inside a single repository, and it is
deterministic rather than a question for a model: a path is either in the route
table or it is not.

Every check is biased hard toward silence. Route parameters are normalized
across five spellings (`{id}`, `:id`, `<int:id>`, `(?P<id>…)`), paths assembled
from variables are skipped rather than guessed at, and a spec whose code side
yields fewer than three recognisable routes reports **`spec-not-compared`** at
`info` instead of declaring every path missing — because "the code implements
none of the spec" is nearly always a parser limitation wearing the costume of a
finding.

---

## Assurance: is the checking switched on?

A clean report has two possible causes that look identical from the outside: the
analyzers ran over everything and found nothing, or large parts of the tree were
excluded, hundreds of findings were silenced by inline comments, and the tests
that would have caught a regression assert nothing.

Every scanner hands you the same green tick for both. They have to — they honour
the suppression comments that hide findings from them, and a test that asserts
nothing still passes. The silencing is invisible to the thing being silenced.

Measured across ten corpus repositories: **445 suppression comments**, 169 in one
project, and not one of them appears in any tool's output.

The `assurance` dimension reports three things:

1. inline suppressions across fifteen tools' ignore syntaxes, with the blanket
   ones — an ignore naming no rule — separated out
2. configuration that excludes parts of the tree from analysis
3. tests that cannot fail

It carries **weight zero**. A repository with four hundred `# noqa` comments is
not insecure, it is *unmeasured*, and scoring those the same way is the
conflation the dimension exists to expose.

---

## The judgement pass

A model is worth adding only for questions with no definite shape: does the
README describe behaviour the code no longer has, does a comment contradict the
function under it. It is not here to re-find secrets — those have decidable
answers, and swapping a decidable check for a probabilistic one is a downgrade
dressed as an upgrade.

```yaml
profile: connected          # offline and ci forbid model calls outright
judgement:
  provider: anthropic       # reads ANTHROPIC_API_KEY from the environment
  model: claude-sonnet-4-5
```

Three rules it obeys:

- **Inferred findings never blend with deterministic ones.** Everything from
  this pass is `provenance: inferred`, and the gate ignores inferred findings
  unless `gate.gate_inferred` is set. A model's opinion should not turn a build
  red on its own.
- **No provider configured is *not assessed*, never a pass.** The easy
  implementation returns an empty list when there is no key — and an empty list
  is indistinguishable from "looked, found nothing". So the probe reports skipped
  with the reason, and coverage drops by exactly what was not checked.
- **A finding citing a file the model was not shown is discarded**, not reported
  with a caveat. Secrets are masked before anything leaves the machine, and
  model-stated confidence never reaches `high` — confidence asserted by a model
  is a different quantity from confidence measured from adjudicated outcomes,
  and sharing a scale would be a category error.

## Two findings that read history

`drift.doc-references-missing-file` asks git whether a path named in prose was
ever committed. A path that was tracked and is gone is reported at `info` as
*removed*: a changelog or decision record is right to name it. A path that was
never there keeps the `low` finding, because that is a typo or a rename the
document missed. `assurance.blanket-suppression` does not read documentation
at all: a README explaining what `# noqa` means is not a suppression.

## What doc_drift leaves alone

A path named in prose is not drift when it is something a checkout never
carries. `drift.doc-references-missing-file` and `drift.broken-doc-link` skip a
path the root `.gitignore` matches (comments, `dir/`, `*.ext`, `path/**`, a
leading `/` and `!` re-inclusion are read; nested ignore files are not), a path
under the output directory — the `out:` key of `arbiter.yaml`, this run's `--out`
when it sits inside the scanned tree, or any `arbiter-out` segment — and a link
whose target carries a scheme (`git://`, `ssh://`). A backticked path is also
skipped when an `http(s)://` URL sits on its line or on the adjacent line of the
same paragraph, because `docs/handbook.md` named beside a link to another
repository describes that repository; a relative *link* is a claim about this
tree whatever else the line says, so it is not excused that way. A plainly
missing file still fires. The `.gitignore` shapes honoured are deliberately the
exact-path and directory ones: a pattern read too broadly hides real drift
(ARB-048).

