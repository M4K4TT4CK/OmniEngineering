# Project Context: arbiter

Decision history for this repository. `CLAUDE.md` routes here. Where this
file and `.ai/core-context.md` (generic operating principles) overlap, this
file wins for anything project-specific. Append dated sections; do not
rewrite earlier ones.

## What this is

Arbiter (`arbiter-eval`) is a polyglot repository and system evaluator. It
scans one repository or a system of several and reports findings across
security, compliance, quality, drift and cross-repo interface dimensions,
together with how much of its own rubric it was actually able to run.

Pipeline and module ownership are in `README.md` "Layout". Native probes
need nothing installed; external analyzers (ruff, bandit, checkov, semgrep,
gitleaks) are wrapped through declarative adapter manifests in
`src/arbiter/packs/`. An A/B harness (`arbiter ab`) compares configurations,
tools and builds of Arbiter itself, and an offline learning loop
(`tools/train_cycle.sh`, `.github/workflows/train.yml`) calibrates rules
against a corpus of public repositories with a held-out set.

Design rules that constrain every change (from `README.md`):

1. Coverage is reported, never assumed — a probe that could not run is
   *not assessed*, never a silent pass.
2. Deterministic and inferred findings never blend; every finding carries
   `provenance`.
3. The tool never executes the target.
4. Language support arrives as probes and adapters, not core changes.
5. No claim outruns its basis; report invariants are machine-checked.
6. Adaptation is deliberate — a scan reads one pinned knowledge version.

## 2026-09-12 — OmniEngineering and Headroom adopted (ARB-001)

**What changed.** Added the OmniEngineering workspace (`.ai/`, `./omni`,
`make_ai.py`, `CLAUDE.md`, `CHANGELOG.md`) and installed Headroom's Claude
Code integration at local scope.

**Why it was done by hand rather than `omni adopt`.** The only OmniEngineering
source on this machine is the STEP-Migration repo, and `omni adopt` copies its
whole `.ai/` directory — including that project's 80-entry requirements
registry, code-notes, archives and CUI-marked project history. Instead:

- `make_ai.py`, `omni`, `.ai/rules/`, `.ai/schemas/`, `.ai/.ignore` and
  `.ai/core-context.md` came from `~/dev/STEP-Migration` (newest CLI), with
  STEP values removed from the ruleset `configuration` block, the
  requirements schema description and `core-context.md` section 5.
- `.ai/playbooks/`, `.ai/checklists/` and `.ai/knowledge/swebok/` came from
  the `Documents/GitHub/STEP-Migration` copy, which predates STEP's ARB-080
  deletion of those generic files. They were checked for STEP-specific text
  and contain none.
- Context brief, manifest, configuration, this file and the requirements
  registry were written fresh for arbiter.

**Known gap, resolved 2026-09-17 (ARB-028).** `.ai/adapters/*`,
`.ai/checklists/public-release.md`, `.ai/rules/fallback-llm-rules.json` and
`.ai/rules/hci-ui-rules.json` were required by `make_ai.py` but existed in no
local copy, so `omni doctor` reported them and `omni sync` stopped before
writing root shims. No OmniEngineering upstream ever became available on this
machine (checked both `~/dev/STEP-Migration` and
`~/Documents/GitHub/STEP-Migration` again before writing these — neither has
them), so they were authored directly for arbiter instead of continuing to
wait: seven adapter guides, the public-release checklist (marked not
applicable while arbiter stays proprietary), and two rule packs matching
`.ai/schemas/rulepack.schema.json`. They are arbiter-specific, not upstream
content, and are wired into `.ai/context-manifest.json`.

**Headroom.** `headroom init claude` (headroom-ai 0.37.0, installed in the
STEP-Migration venv and exposed on PATH via `WindowsApps\headroom.cmd`) wrote
`.claude/settings.local.json`: it routes Claude Code through the proxy at
`127.0.0.1:8787` and adds SessionStart and PreToolUse (Bash|PowerShell) hooks
that keep the proxy running. That file holds machine-specific paths, so it is
git-ignored. It takes effect after Claude Code restarts. Because Headroom
lives in another project's venv, recreating that venv would break these hooks.

## 2026-09-12 — Documentation, corpus figures, REQ lifecycle, licensing (ARB-002…005)

**Documentation (ARB-002).** `README.md` was 900 lines and was the only product
document. It is now an overview; granular material lives in `docs/`, one file
per category. Claims were re-verified against source rather than copied forward:
the dimension weights, severity and confidence factors, probe skip precedence,
fingerprint construction and CLI surface in the new docs all came from reading
`core.py`, `policy.py`, `engine.py` and `cli.py`. `SETUP.md` became an
installation guide — its previous first-person narrative, including a paragraph
retracting an earlier mistaken claim, was not repository documentation.

**Corpus figures (ARB-003).** The docs quoted three different corpus sizes. The
tooling is authoritative and self-consistent: `tools/corpus.py` and
`tools/fetch_corpus.sh` both describe 41 repositories — 16 vulnerable, 20 clean,
5 examples — with 5 held out (2/2/1), leaving 36 tuned. The old "39" was
14 tuned-vulnerable plus 20 and 5 *untuned* counts, which is why it never
reconciled. `--counts` now derives this offline so documentation cites a command
instead of a memory. The measured lines/findings table predates the two Go
repositories and is labelled rather than silently updated, because it cannot be
recomputed without cloning the corpus.

**Requirement lifecycle (ARB-004).** The registry appears in every context
profile in `.ai/context-manifest.json`, so every entry is a permanent
per-session cost — the STEP-Migration workspace this scaffold came from carries
80. Terminal requirements now sweep into `.ai/requirements/archive.json`, which
no profile loads. The subtle part is ID allocation: `next_requirement_id` read
only the active registry, so archiving alone would have silently reused retired
IDs and broken CHANGELOG references. It now scans both files, `omni doctor`
errors on collision, and archiving a non-terminal requirement is refused. The
three-digit ID format caps the project at 999, so reuse would corrupt history
rather than conserve anything.

Policy: a requirement is archived once it is `completed` or `withdrawn` **and**
its outcome is recorded in `CHANGELOG.md` and this file. Rationale lives here
permanently; the registry holds only live work. Not every change earns a REQ —
routine maintenance rides an existing one or is a CHANGELOG line.

**Licensing (ARB-005).** Three of its four acceptance criteria are closed and
the fourth cannot be closed here. `LICENSE` exists at the repository root and
`pyproject.toml` references it, copyright is asserted to Nicholas J Pauken, and
`LICENSE` §3 assigns scan output to the user. `LICENSE` is deliberately an
interim notice rather than the operative grant: it reserves all rights and
grants none, which is a defensible position to hand someone a copy under and is
not a licence. `docs/licensing.md` states eight requirements (L-1…L-8) and the
questions still open. Two points are load-bearing: scan output must belong to
the user, since reports are meant for accreditation packages and PR comments;
and `semgrep` is LGPL-2.1, so the air-gapped bundle cannot ship until the
redistribution review in L-6 is done and recorded with a date and a reviewer.
That review is a legal determination, so engineering cannot close this one —
it needs counsel, and a signature from someone who did not perform the review
would be worse than the gap it filled.

## 2026-09-12 — Adapter timeouts on Windows (ARB-006)

**What changed.** `Adapter._kill_group` delegates to a new `Adapter._kill_tree`
when `os.killpg` is absent; there it runs `taskkill /T /F /PID`, falling back to
`proc.kill()` if `taskkill` is unavailable or fails. The POSIX path keeps its
SIGTERM-then-SIGKILL group signalling unchanged, minus a branch that had become
unreachable.

**Why not the one-line fix.** The obvious repair is
`getattr(_signal, "SIGKILL", _signal.SIGTERM)`, and it is wrong. It clears the
`AttributeError` without delivering what the function promises: Windows has no
`os.setsid` or `os.killpg` either, so there is no group, and signalling the
direct child leaves its descendants running. Measured on this machine with the
same shape as the test — a shell that backgrounds a grandchild and sleeps —
`proc.kill()` let the grandchild run to completion and write its marker;
`taskkill /T /F` did not. The one-line version would have converted a crash
into a passing-looking kill that still orphaned the workers, which is the
original checkov failure wearing a different hat.

**Residual risk.** `taskkill /T` resolves the tree from the parent PID at kill
time, so a grandchild already orphaned by an intermediate process that exited
first is not reachable; a Windows Job object would close that, at the cost of
`ctypes` plumbing or a new dependency. Not worth it until something demonstrates
the gap. CI is `ubuntu-latest` only (`.github/workflows/train.yml`, single job,
no matrix), so this branch is exercised only by developers on Windows — which is
why a crash on every adapter timeout survived to be found by hand.

## 2026-09-12 — The bundle commits, recorded after the fact (ARB-007)

**What this is.** Six commits (`8775017`…`54f9a13`) were authored in a session
that could not push, travelled here as a git bundle, and were fast-forwarded
onto `main` and pushed. They changed probes, packs, gates, scoring and the CLI
surface, and none of them had a CHANGELOG entry or a section here. ARB-007 is
the recording, not the work; the work itself was unticketed.

**Written from the diffs, not the commit messages.** Those messages are long and
persuasive and were written by a session holding context this one does not, which
is the same reason ARB-002 re-verified its claims against source. Verified here
before being restated: the seven file-scoped probes and the conservative `repo`
default (`probes.py`, `authored.py`); CI-11's presence in `claims.INVARIANTS`
and its two reasoned exemptions; that `withheld`/`withheld_reason` are declared
fields on `Scorecard`, serialized, round-tripped and guarded by CI-3, so a
withheld grade is machine-checked rather than a rendering convention; the nine
token patterns and their severities; `exclude_native` handling in
`probe_resource_policy`; and the `(path, mtime_ns, size)` cache key.

**Relayed, not verified.** The performance and training figures those commits
cite — Traefik 59s → 6s, 158 findings against 158 on the files both scans read,
4401/4401 injection recall, 1653/1653 after the cache fix — cannot be reproduced
on this machine: the corpus is not cloned and there is no Traefik checkout. They
are recorded as that session's measurements and are not restated as fact in
`CHANGELOG.md`.

**Gap found while reading, not fixed.** `adapters.py:342` registers every
adapter-backed probe without a `scope`, so all five external analyzers (ruff,
bandit, checkov, semgrep, gitleaks) inherit the `repo` default and are recorded
as not-assessed in any partial scan. That is the right default — nothing has
established that those tools give subset-exact answers — but it means the
pull-request gate runs native probes only, and neither `docs/ci.md` nor
`RUNNING-ON-YOUR-OWN-CODE.md` says so. Worth either declaring the scope
deliberately per adapter or documenting the limitation; it should not stay
implicit in a default.

**Also outstanding.** `HANDOFF.md` tells a fresh session to expect `310 passed`.
That has never matched this machine, where the suite is 307 passed and 3 skipped
after ARB-006.

## 2026-09-12 — Two defects the first calibration queue exposed (ARB-008, ARB-009)

**How they were found.** Preparing the first adjudication queue meant scanning
Arbiter with itself. One rule, `drift.doc-references-missing-file`, produced 187
of the 230 unsuppressed findings — 81%. A rule that dominates a self-scan that
heavily is either the most important rule in the system or broken, and reading
the evidence settled it before any verdict was marked.

**Why it was worth stopping for (ARB-008).** `docs/calibration.md` states that
one finding moves the statistics once and re-adjudicating a fingerprint is
refused. Observations are therefore the one input in this system that cannot be
bought twice. Measured by scanning Arbiter with itself before and after the
change, with the output directory removed so that neither run could read the
other's: 187 findings before, 28 after, and **all 20** findings already queued
were in the vanishing set. Adjudicating that queue would
have spent a fifth of the rule's lifetime evidence recording, permanently, the
behaviour of a bug — and recorded it flatteringly, since a corrected rule would
never have emitted them. The fix is one line; the reason to make it first is
that the alternative is not reversible.

**The defect.** `lstrip("./")` strips a character set, not a prefix. Every path
beginning with a dot lost it, so `.ai/context-brief.md` was tested as
`ai/context-brief.md` and neither the exact nor the suffix membership test could
match. The sibling rule twenty lines above already used `_normalize_relative()`,
which resolves `.` and `..` segments properly and returns empty for a path that
escapes the repository root; the two rules now agree. Of the 28 survivors, 20
are real drift — including the `.ai/rules/fallback-llm-rules.json` and
`.ai/rules/hci-ui-rules.json` gap recorded under ARB-001, which the noise had
been burying. The remaining 8 name files under `.arbiter/` that exist on disk,
and are a different defect; see below.

**No margin.** 20 genuine findings against a `MIN_OBSERVATIONS` of 20 is exactly
the threshold and not one finding more. Repairing any of the drift the rule now
correctly reports removes queue material with it. If this rule is to be proven,
it must be adjudicated before its findings are fixed, or adjudicated against a
second repository.

**Two defects the verification turned up, neither fixed here.** Both were found
because the before/after numbers refused to reconcile, which is the argument for
insisting that they do.

*Arbiter scans its own output directory.* `arbiter-out/` is in `.gitignore`, but
nothing excludes it from a scan, and a scan reads the working tree rather than
the index. So the run read the previous run's `report.json` and `review-queue.md`:
40 of 109 unsuppressed findings — 37% — came from its own output. Two distinct
loops. `assurance.blanket-suppression` matched `# noqa` and `checkov:skip`
inside the evidence strings of the earlier report, inventing 23 findings. And
`drift.doc-references-missing-file` read the *old rule's mangled paths* out of
the old review queue and reported them as missing files, so the defect's output
became the next scan's input. Any tool that writes its output beneath the
directory it scans has this problem; the output directory is known at scan time
and should be excluded from the walk.

*`.arbiter/` is invisible to the inventory.* `.arbiter` is in `inventory.SKIP_DIRS`,
so `.arbiter/knowledge.json` and `.arbiter/external-severity.json` — both tracked
in git and present on disk — are not in `ctx.inventory.files`. Documentation that
names them is therefore reported as referring to missing files: 8 of the 28
surviving findings, and 6 of the 20 in the queue. The skip is defensible for
probes that would otherwise scan a 322 KB severity table as source, but
`doc_drift` resolves references against that same inventory and cannot tell
"absent" from "deliberately not walked". A file the tool wrote itself should not
read as documentation drift.

**A caution about measuring rules out of band.** The before/after figures above
were first estimated with a standalone script that applied the rule's predicate
to `git ls-files`. It disagreed with the real scan in both directions and its
numbers were wrong. Two reasons, both instructive: findings are deduplicated on
`Finding.id`, whose fingerprint includes `evidence` but deliberately not the line
number, so one path named eleven times in a file is one finding, not eleven; and
the scan resolves against the walked inventory, which `SKIP_DIRS` and
`MAX_FILE_BYTES` make narrower than the git index in some places and the
untracked working tree makes wider in others. Measure a rule by running the
scanner.

**Encoding (ARB-009).** Reading the generated queue back failed on byte `0x97`,
an em dash written as cp1252. Thirteen `write_text()` calls omitted `encoding=`,
so every text artifact took the console codepage. Precisely: the JSON writers
were never corrupt, because `json.dumps` escapes non-ASCII by default — the
damage was confined to the HTML, markdown, PR-comment and review-queue
renderings, which emit em dashes directly. The JSON calls were made explicit
anyway, so correctness stops depending on a default that could be changed by
passing `ensure_ascii=False` for readability. The matching reads are explicit
too, since an artifact that cannot be read back on another machine is not
evidence. Deliberately out of scope: `graph.py` and `review_ui.py` read files
belonging to the *scanned* repository with `errors="replace"`, which is a
different question — what to do about a target file that is not UTF-8 — and
should not be answered by a change aimed at Arbiter's own output. Also left
alone: the YAML and TOML configuration readers in `policy.py`, `controls.py`,
`ab.py` and `adapters.py`. TOML is specified as UTF-8, so `adapters.py:291`
reading a pack manifest through the platform default is a latent defect of the
same family, worth a separate look.

**Why this class of bug survives.** Both are Windows-only, and CI is
`ubuntu-latest` with no matrix — the same reason the ARB-006 timeout crash lived
as long as it did. Three platform defects have now been found by hand on this
machine. A Windows job in CI would have caught all three.

## 2026-09-12 — Licensing taken as far as engineering can take it (ARB-005)

**What changed.** `LICENSE`, `CONTRIBUTING.md`, a `license = { file = "LICENSE" }`
reference and an `authors` entry in `pyproject.toml`, and a README section that
cites the licence instead of apologising for its absence. L-2 and L-8 are closed;
L-1 is closed provisionally.

**Why an interim notice rather than waiting for the real one.** L-3 — the grant
itself — follows from two decisions counsel cannot make: who owns the copyright,
and whether the model is per-seat, per-repository or site. Until those are
answered, the best available instrument is one that asserts ownership and refuses
permission. That is a worse licence than none only if someone mistakes it for a
grant, which is why the file says three times that it is not one.

**The one clause drafted in full.** L-4, ownership of scan output, is stated
now rather than deferred, because it only gives rights away: it can be written
without knowing the commercial model, it cannot become more generous later, and
the product is unusable for its stated purpose — accreditation packages,
pull-request comments — if ownership of the output is ambiguous. It is marked as
intended to survive into the operative licence unchanged.

**A problem the git history creates.** All 33 commits are authored
`Claude Opus 5 <noreply@anthropic.com>`; no human appears anywhere in the record,
and `pyproject.toml` had no `authors` field. L-2 asks the owner to assert
copyright while the repository's own evidence attests that a model wrote it. The
ordinary position is that the person directing the work is the author and that
purely machine-generated material is not copyrightable at all, but the record
points the wrong way and should be raised with counsel alongside questions 1 and
2. Future commits should carry the owner as author with the model as
co-author, which is the accurate description of both.

## 2026-09-12 — MCP before a Python API (ARB-010)

**The decision.** Expose Arbiter as an MCP server that shells out to the
`arbiter` console script, ahead of stabilising a Python API.

**Why this order, having first argued the opposite.** The initial reasoning was
that an MCP server would import the package, so a designed API had to come first.
That dependency does not exist: the console script at `pyproject.toml:22` is
already a published surface documented in `docs/cli.md`, and shelling out to it
keeps the process isolation the scanner relies on. Meanwhile
`src/arbiter/__init__.py` exports only `__version__` and `run_scan` takes twelve
parameters, so stabilising the API first means freezing a surface before anything
has used it. Better to let observed MCP traffic shape it.

**Why not a hosted API.** Hosting requires customer source to leave the customer's
machine, which contradicts the `offline` and `ci` profiles that declare
`network: False`, and it is the one distribution scenario `docs/licensing.md`
marks as needing counsel — LGPL obligations differ again for network use. MCP
relocates the installation rather than removing it, which is worth saying out
loud, because "no local install" is what was asked for and MCP does not strictly
deliver it.

**The constraint that matters most.** No MCP tool may record an adjudication.
`arbiter review --apply` is deliberately outside the surface. The ledger's value
is that it is the one signal the system did not generate; an automated caller
marking verdicts would convert measured precision into the tool's opinion of
itself, at machine speed.

## 2026-09-12 — Three defects found while preparing to adjudicate (ARB-011, ARB-012, ARB-013)

**How they surfaced.** Building the first real adjudication queue, not running
the rules against anything. Preparing to measure the tool has now produced more
tool defects than any scan of a target, because it is the first occasion on
which someone reads the output closely enough to disbelieve it.

**ARB-011, the expensive one.** A scan reads the working tree, and `--out`
defaults to the relative path `arbiter-out`, which for a self-scan sits inside
that tree. Two identical commands, the second reading the first's output: 28 of
101 unsuppressed findings located inside the output directory, 27.7%. Twenty-four
of those were `assurance.blanket-suppression` — the exact rule queued for
adjudication. A rendered report is full of the strings the suppression rules
search for, so this contamination is not uniform noise; it lands hardest on the
rule being measured. Had the queue been built from a default-path scan, most of
it would have been the previous run's HTML, and twenty adjudications would have
calibrated the rule against Arbiter's own output — a precision figure derived
from a person marking the tool's echo.

**ARB-012, and a reasoning error worth recording.** Nine sites read target files
with `read_text(errors="replace")` and no encoding. ARB-009 excluded them
deliberately, reasoning that `errors="replace"` was the design intent. The
design intent is never crashing on a target file; `errors=` governs failure
handling and says nothing about which codec is used. cp1252 decodes nearly every
byte without raising, so the exclusion did not preserve robustness, it preserved
silent corruption — an em dash surfaced in a review queue as mojibake. The
mistake was not an oversight but a stated justification that had not been
checked, which is the harder kind to catch.

**ARB-013, and the rule's yield.** `.arbiter` is in `SKIP_DIRS`, so its tracked
files never entered the inventory and prose naming them read as drift: 8 of 31
findings. Fixed by asking disk instead. Two findings for `.arbiter/baseline.json`
survive and are correct — it is documented but genuinely absent — which is the
evidence that the fix is precise rather than blanket. The remaining 22 name
AGENTS.md, LLM_CONTEXT.md, TRADEMARKS.md and the two rulepack JSON files under
.ai/rules, every one of which omni doctor independently reports missing. Two
tools with no shared code agreeing is the strongest available evidence that this
rule earns its place. (Those filenames are deliberately not in backticks here:
backticking a list of known-absent files would manufacture fresh drift findings
in the next scan.)

**What the numbers did.** 75 unsuppressed findings before, 69 after. The suite
went from 311 to 315. `assurance.blanket-suppression` rose from 21 to 23,
entirely because the new regression tests contain two `# noqa` fixtures —
Arbiter detecting its own test data. That is the same self-reference the queue is
already full of, and a standing reminder that this repository is an
unrepresentative target for the suppression rules specifically.

**Still not adjudicated.** The ledger holds 15 rules and zero observations. No
verdict has been recorded by anyone, and none was recorded here.

## 2026-09-12 — The queue could not name what it was asking about (ARB-014, ARB-015)

**How they surfaced.** By fetching the 41-repository corpus and scanning 36 of
them. The five held out by `tools/corpus.py` stayed out: adjudication decides a
rule, and the holdout exists precisely so some repositories are never read while
deciding one. Spending it on the first batch would not be recoverable.

**What the corpus settled.** Six of the fifteen registered rules can reach the
twenty-observation line from this sample — `secrets.assigned-credential` (287
findings across 24 repositories), `supply.unpinned-action` (214/26),
`drift.broken-doc-link` (193/17), `resource.k8s-no-security-context` (135/7),
`secrets.private-key` (46/6) and `resource.unencrypted-database` (20/3). Nine
cannot, and two fired zero times across 36 repositories. This is the answer the
previous session could not get: scanning Arbiter with itself, the only rules
reaching twenty were doc-drift and blanket-suppression, and both were largely
the tool detecting itself.

**ARB-014, and a claim made without checking.** `Location.short()` builds its
prefix from the Location rather than the Finding, and `doc_drift` set the id on
the Finding alone, so all 330 drift findings rendered as bare paths. The
requirement as first written stated that the three sibling probes already did
this correctly and `doc_drift` was the outlier. That was false, and it was
written from two matching lines in a grep rather than from measurement. The
reverse holds: `assurance` sets it at every construction site, while `secrets`
attributed 0 of 377, `supply_chain` 0 of 1,817 and `resource_policy` 7 of 1,040.
The registry entry now carries the correction instead of the claim. That is the
second consecutive session in which the defect was a stated justification rather
than an oversight — ARB-012 was the same shape.

**ARB-015, and why the renderer and not the probes.** Roughly twenty
construction sites across eight probes omit the id, and several are repo-level
or cross-repo where the right value is a judgement, not a substitution. The
Finding carries the id in all 3,564 cases, so `review.where()` qualifies the
path from there and both front ends share one helper. Measured afterwards: 120
of 120 queue lines across the six queues name their repository, checked against
the real corpus directory names rather than a regex that assumed the line
format — an earlier measurement in this session was wrong for exactly that
reason. The probe sites remain wrong, so SARIF, HTML and console output are
still unqualified. Recorded as residual risk, not closed.

**What the queues are not.** `select()` spreads across distinct files, not
across repositories or populations, and it shows: k8s-no-security-context draws
20 of 20 from one repository, unencrypted-database 18 of 20, private-key 16 of
20. Two queues are almost entirely teaching material, which corpus.py states is
useless as a false-positive measure — usually right about the file and silent
about the rule. Twenty adjudications drawn from one repository of one population
would yield a precision figure for that repository, not for the rule. The
sampler has no notion of either axis, and that is now the limiting factor on
queue quality rather than anything about attribution.

**Still not adjudicated.** The ledger holds 15 rules and zero observations. Six
queues of twenty were generated and no verdict was marked. `knowledge.save()`
runs only under `--apply`, so this is guaranteed by the code path and not only
by restraint. The suite went from 315 to 317.

## 2026-09-12 — Attribution at the source (ARB-016)

**Closing what ARB-015 deferred.** The renderer fix qualified queue lines from
the Finding, which made adjudication possible without waiting on the probes, and
left SARIF, the HTML report and the console still unqualified. That was recorded
as residual risk on the requirement and in the changelog rather than quietly
dropped, which is the only reason it was cheap to pick up again. Eighteen of the
twenty construction sites in `src/arbiter/probes.py` took the id already in
scope on the enclosing Finding — a substitution, not a decision.

**The two that were not substitutions.** The `interface` rule for unused IAM
grants accumulated service names into a set, discarding the site each grant was
written at, so the finding could cite only the string `iam:` plus a service and
had to guess a repository: the alphabetically first infrastructure one, which is
wrong whenever the grant is not in it. It now records a Location exactly as the
neighbouring collections for reads, provides, ports and SDK calls already did,
and the finding cites the file. The two `house_rules` path rules compared
against every path in the scan flattened into a single set. That made them the
only two sites in the tree that set no repository on the Finding at all, so they
reported against the `root` default whatever they matched, and it meant one
repository's LICENSE satisfied a required-path rule for all thirty-six. Both now
ask the question per repository. This is a behaviour change, not only an
attribution one, and it is recorded as risk on ARB-016: a forbidden path present
in three repositories is now three findings, and a required path missing from
three is now three rather than none.

**What the measurement says.** The same 36-repository corpus, the same four
probes, before and after: 3,564 findings both times, with the identical
per-probe distribution — `secrets` 377, `resource_policy` 1,040, `doc_drift`
330, `supply_chain` 1,817. Unattributed locations went from 3,227 to 0, and no
finding changed the repository it belongs to. That combination is the point: if
the totals had moved, the fix would have been changing what is reported rather
than what it is reported against. A self-scan covers the probes the corpus run
does not exercise — `quality`, `authored`, `assurance` — at 126 findings, 0
unattributed. The holdout stayed held out.

**What it does not fix.** The sampler still spreads across files rather than
repositories or populations, which the previous section named as the limiting
factor on queue quality. Nothing here touches that. Attribution was never the
reason two of the six queues are almost entirely teaching material.

**Still not adjudicated.** The ledger holds 15 rules, zero observations and zero
adjudications, re-verified after this work. The suite went from 317 to 320.

## 2026-09-12 — The sampler was measuring repositories (ARB-017)

**The defect the previous section named.** Attribution was never why two of the
six queues were nearly useless. `select()` spread within a rule across distinct
files, and one repository supplies plenty of distinct files, so the spread was
satisfied without leaving the repository. Measured on the six queues:
k8s-no-security-context drew 19 of 20 from one repository and all 20 from the
examples population, unencrypted-database 18 of 20, private-key 16 of 20. A
queue like that answers whether the rule is right about one repository. The
ledger has no field for that distinction — it records the verdict against the
rule — so the error would have been laundered into a precision figure and then
into the gate.

**The fix, and what it deliberately does not do.** Selection round-robins across
repositories and keeps file spread as the secondary axis inside each, so a
repository with thirty findings and one with a single finding are equals on the
first pass. Population is not an input. That label lives in `tools/corpus.py`,
and a library that ranks findings for adjudication must not import the test
harness to do its ranking — the coupling would be backwards, and it would only
work for repositories the corpus happens to know. Repository spread is the proxy
the library can compute from what a Finding already carries, which is why
ARB-016 mattered first.

**Measured on the same corpus, same four probes.** Average distinct repositories
per queue 6.0 to 12.2; largest single-repository share 64.2% to 30.0%;
k8s-no-security-context from one population to all three (8 examples, 7
vulnerable, 5 clean) across 7 repositories rather than 2. Two queues moved
little and the reason is worth recording: unencrypted-database still draws 18 of
20 from terragoat, and private-key 9 of 20 from traefik, because only three and
six repositories in the corpus produce those findings at all. Spread cannot
exceed the pool. Those two rules need corpus breadth, not a better sampler, and
the average across six queues would hide that if it were the only number kept.

**Why the old queues were discarded rather than marked.** The six queues on disk
were drawn by the old sampler. They were entirely unmarked — 120 of 120 lines
blank, verified before touching them — so nothing human was lost by regenerating
them. Had they been marked and applied, the skew would have been permanent:
`record()` refuses to re-adjudicate a fingerprint, so a verdict recorded against
a badly drawn sample cannot be withdrawn by drawing a better one.

**Still not adjudicated.** The ledger holds 15 rules, zero observations, zero
adjudications, at version `k:c52cc1b05ee9`. An `--apply` run was requested this
session and not performed: every mark in every queue was blank, so it would have
recorded nothing and rewritten the ledger's version for no signal, and the only
way it could have recorded anything is if the marks had been supplied by the
assistant. That is the one thing this ledger cannot survive. The suite went from
320 to 321.

## 2026-09-12 — A hosted API, reversing the MCP-only decision (ARB-018)

**The decision.** Build toward running Arbiter as a service the customer calls
instead of installs. MCP stays, as one of two front doors over a shared service
layer, rather than as the answer.

**Why the earlier reasoning did not hold.** The entry above chose MCP and said
plainly that it "relocates the installation rather than removing it, which is
worth saying out loud, because 'no local install' is what was asked for and MCP
does not strictly deliver it." That caveat was the whole requirement. An agent
calling `arbiter_scan` still needs Arbiter, Python and the optional analyzers
already present on the machine, so everyone who could not install it still
cannot use it. The owner's judgement is that this restriction binds.

**The objection that was simply wrong.** The earlier entry recorded that hosting
"is the one distribution scenario `docs/licensing.md` marks as needing counsel —
LGPL obligations differ again for network use." LGPL-2.1 obligations attach to
conveying a copy and it has no network-use clause; that is the AGPL, and
`semgrep`'s CLI is not under it. Running `semgrep` server-side conveys no copy,
so L-6 — the redistribution review that blocks the air-gapped bundle — does not
gate hosting at all. Hosting triggers *fewer* third-party obligations than the
bundle. `docs/licensing.md` now records the correction.

**The objection that was only half right.** Hosting was also rejected because it
"contradicts the `offline` and `ci` profiles that declare `network: False`."
Those profiles describe what a scan may reach *while running*, not where the
process lives. A hosted scan runs with the network capability off exactly as a
local one does, and `service.check_profile` now refuses `connected` and `audit`
unless an operator explicitly allows them, which the hosted door never will.

**What actually gates this, and it is not licensing.** Custody of customer
source. Three things follow: workspaces are created outside the server tree and
removed when the scan ends; uploaded archives are hostile input and members that
are absolute, traverse upward, are links or are oversized are refused whole; and
a report remains sensitive after redaction, because `report.py` withholds a
secret's value but still names the file and the kind of credential, which is a
map to what to steal. Retention is therefore an explicit decision rather than a
default, and concentrating many customers' source in one place is a materially
larger target than any single local install.

**What carries over unchanged.** No surface records an adjudication verdict.
`review --apply` is outside both front doors, `service.OPERATIONS` is exactly
`scan`, `gate` and `review_queue`, and a test asserts the absence rather than
trusting a reviewer to notice the capability returning. The ledger's value is
that it is the one signal the system did not generate, and `learn.record()`
makes a wrong mark permanent.

**State.** The service layer, the HTTP surface and their tests all exist, on
FastAPI as an optional extra so a plain install still depends on PyYAML alone.
Nothing has been exposed to a network, and nothing should be before the custody
terms are written. `docs/hosted-api.md` holds the design.

**Keys are scoped to a user, and nothing else** (decided 2026-09-12). No roles,
no tiers, no per-repository or per-organisation scope, and one user holds at
most one live key. A key that identifies a pool rather than a person makes the
per-key limits meaningless — two keys is twice the allowance — and makes
revocation ambiguous, since cutting somebody off means finding every key they
hold and missing one leaves them in. Rotation is `--replace`, which revokes the
old key in the same command so a half-rotation cannot happen quietly.

**One record is kept, and it is about the caller.** Keeping no source is the
promise; being unable to say who called is a different thing and not worth
having. `AuditLog` writes one JSON line per request — key id, user, operation,
status, bytes, milliseconds — and nothing about the code. A log that quoted
findings would rebuild on disk, permanently, exactly what the request path
deletes. This is the one deliberate exception to "keep nothing", and it is
narrow on purpose.

**Free, with nothing to apply for** (decided 2026-09-13). This is not a paid
service and is not being positioned as one yet. It goes to people the owner
knows, so they can test it against their own repositories and cloud builds.
Nobody is charged, nobody is asked what they intend to scan, and nothing has to
be approved. Keys are issued by hand because there is no identity or billing
system to do it any other way — that is a mechanism, not a vetting step, and the
documents say so. The one side effect worth keeping is that something which
cannot be signed up for cannot be used at scale by a stranger.

**Limits are capacity and leak containment, never metering.** The per-key caps
(120 requests an hour, two concurrent scans) multiply by the number of testers,
so a whole-server ceiling of four concurrent scans sits above them. The hourly
figure was raised from 30 on 2026-09-13 for the reason above: a limit a friend
can feel while testing twenty repositories in an afternoon is a restriction
wearing capacity's clothes. The concurrency caps stayed, because those are what
decide whether the machine survives. Every limit is published on `GET
/v1/health` so a recipient sees what they have without asking or meeting a 429.
The counts live in one process's memory: they do not survive a restart and are
not shared, so running several instances behind one address would multiply every
limit. Known gap, acceptable for a single-machine pilot, not beyond it.

**Running it is a documented procedure, not code** (2026-09-12).
`docs/pilot-runbook.md` holds the arrangement — unprivileged container with a
memory limit because the analyzers parse hostile input, a TLS-terminating proxy
with a body limit because an unauthenticated upload is read before the key is
checked, one key per tester, and what the request log is for.
`docs/pilot-terms.md` is what a tester is told about their code; it was approved
for pilot use on 2026-09-13 and goes out as written, but it has not been through
counsel, so it covers a pilot and nothing that looks like a customer. ARB-005
still owns the reviewed version and the L-3 term structure.

`deploy/` holds the arrangement itself: a Dockerfile, a `compose.yaml` and a
`Caddyfile`. Two things there are load-bearing rather than incidental. No socket
in it speaks plaintext, including the hop from Caddy to Arbiter: a one-shot
`certs` service generates a self-signed certificate for `arbiter.internal`,
Arbiter serves with it, and Caddy verifies against exactly that certificate
instead of skipping the check. Sharing Caddy's network namespace is a second
layer on top, not what keeps the hop private. And the image must
stay private: running semgrep server-side conveys no copy, which is the whole
reason hosting escapes L-6, but pushing the image to a public registry would
convey copies and put those obligations back.

## 2026-09-13 — Hosted surfaces completed (ARB-018, ARB-019, ARB-010)

The hosted API closed first because its service, identity, rate-limit and audit
layers are the shared base. The remote CLI and authenticated HTTPS MCP transport
then closed as independent consumers of that base; neither depends on the
other. The rebased branch passed all 53 requirement-specific tests on Windows
and all 399 tests in a Linux-native checkout with the `dev`, `api` and `mcp`
extras installed: 396 passed and 3 skipped. ARB-018, ARB-019 and ARB-010 were
then archived in that order. ARB-005 remains the only active requirement and
still belongs to counsel rather than engineering.

## 2026-09-14 — Verdicts carry a name (ARB-020)

**What changed.** `Knowledge.adjudicated` held `{finding_id: "true_positive"}`
or `"false_positive:note"` and nothing else. It now holds a `Verdict` record
with verdict, reviewer, timestamp and entry point. `record()` takes `reviewer`
as a keyword-only argument with no default, so the type system refuses an
unattributed mark at every call site rather than trusting three callers to
remember.

**Why it mattered more than it looked.** The ledger already refused to
re-adjudicate a fingerprint, which makes a wrong mark permanent — that was a
deliberate and correct decision. What had not been noticed is what permanence
costs when combined with anonymity: a mark you cannot correct *and* cannot
attribute is one you cannot even discount later, because there is no way to
identify which verdicts came from a batch you have lost confidence in. The two
properties are fine separately and bad together.

**What this is not.** It is not proof a person made the mark, and the
documentation says so in those words. `review_ui._getch()` still falls back to
`input()`, nothing calls `isatty()`, and `review --apply` reads a file that
anything able to write markdown can produce. The rule that verdicts come from
people remains procedural — enforced in assistant instructions, not in code.
ARB-021 adds the guard against the accidental case and will not change that
sentence.

**Reviewer resolution stops at two sources.** `--reviewer`, then
`git config user.email`, then refusal. A fallback to the OS username was
considered and rejected: it would write somebody's name into a permanent record
without that person choosing it, which is worse than refusing.

**A real defect the migration test caught.** `from_dict` carried the file's
`schema_version` forward, so a schema 1 ledger read and re-saved was written
back still declaring schema 1 — the migration happened and then denied itself.
Reading *is* the migration, so `from_dict` now always returns a current-schema
object. Nothing outside `learn.py` reads this field, so the blast radius was
future migrations rather than present behaviour.

**Cost to reproducibility, recorded rather than glossed.** `schema_version` is
inside `to_dict`, which feeds `version_hash()`, so the committed ledger moved
`k:0f258c4ce717` → `k:a37f38560140` with zero adjudications and no evidence
change. Any scan pinning the old hash fails until re-pinned. That is the pin
working correctly. On merging with training run 2 the schema-2 migration was
re-applied to that run's ledger instead of text-merging two versions of the
same artifact, landing at `k:2e88d7a07351` — the hash on `main`.

**State.** 398 passed, 3 skipped on this machine, up from 396/3 by the two
tests ARB-020 required. The adjudicated ledger is still empty, so every path
here is exercised by tests and by nothing else.

## 2026-09-14 — Adapter scope is declared, not inherited (ARB-022)

**What was wrong, and it was not the behaviour.** `register_adapters` built
every adapter-backed `Probe` without a `scope` argument, so all five external
analyzers took `Probe`'s conservative `repo` default. That default is correct —
nothing has ever measured whether ruff, bandit, checkov, semgrep or gitleaks
return the same findings from a subset of the files. But it was inherited
rather than chosen, and an inherited default is not a decision. The next person
to write an adapter copies a manifest, and a manifest that never mentions scope
does not show that the question was asked.

Each manifest now declares `scope = "repo"` with the reason beside it,
`load_adapter` refuses a value that is neither `file` nor `repo`, and a test
fails if a shipped manifest omits the key *or* claims `file`. That last
assertion is the real mechanism: declaring `file` asserts subset-exactness, so
the test is an evidence gate that fails the day someone claims it without a
measurement to point at.

**The defect found on the way, which was a false statement in the output.**
Every repo-scoped probe skipped in a partial scan reported the same reason:
"this check reads relationships between files and cannot answer from a subset."
That is true of `drift`, `interface` and the suppression counters. It is false
of semgrep, which reasons about one file at a time and is held back only
because nobody has checked whether running it on a subset gives the same
answer. The report was stating a mechanism it had not established, about a tool
it does not own — the exact failure mode this project exists to refuse, sitting
in its own skip reason. `Probe.scope_reason` now carries the real one, defaulted
to the relationships wording for native probes and overridden for adapters.

**The documentation gap was the user-visible half.** `docs/ci.md` explained file
and repo scope carefully and never named the external analyzers.
`RUNNING-ON-YOUR-OWN-CODE.md` described the pull-request job without saying it
runs native probes only. A reader of either would reasonably believe their PR
gate runs semgrep. Both now say plainly that a pull-request gate runs Arbiter's
own probes and no third-party analyzer at all, and both give the reason as
absence of measurement rather than as a property of the tools.

**What this is not.** Nothing about what runs changed, and this must never be
described as making partial scans cover more. It makes an existing limit
visible and gives it an honest reason.

**State.** 403 passed, 3 skipped, up from 400/3 by the three tests ARB-022
required.

## 2026-09-14 — Adjudicating needs a terminal (ARB-021)

**The hole ARB-020 left open.** Every verdict now carries a name, but nothing
checked that a person was on the other end when it was made. `cmd_feedback`
recorded happily from a fully non-interactive invocation, and
`review_ui._getch()` falls back to `input()` when raw terminal mode is
unavailable, so a piped stdin fed the keypress loop without complaint. Combined
with a ledger that refuses re-adjudication, that meant a stray script could
write a permanent mark nobody could correct or even remember making.

`arbiter feedback` and `arbiter review --interactive` now require
`sys.stdin.isatty()` and refuse otherwise.

**Why the override is deliberately easy.** `feedback --batch` walks straight
through and records the entry point `feedback-batch`. An override an agent
cannot pass is an override a person cannot pass either, so making it hard would
cost legitimate batch imports and buy nothing — the value is the record it
leaves, not the resistance it offers. `NON_INTERACTIVE_ENTRY_POINTS`
(`feedback-batch`, `review-apply`, `import`) names the set that could have been
produced by something that was not a person, which is the question worth being
able to ask of the ledger later.

**Why `review --interactive` has no override.** There is no coherent one. A
keypress interface driven by something that is not a keyboard is a batch import
wearing another name, and `--apply` already is that path and already records
itself as one. So the refusal points there instead of inventing a second way to
say the same thing.

**What this is not, stated in the docs in these words.** A guard against
accident, not proof of personhood. Anything determined allocates a
pseudo-terminal and walks through, and that is not a gap to close later — no
check available to a local CLI distinguishes a person from a program that wants
to look like one. The claim stays exactly as small as the mechanism.

**State.** 406 passed, 3 skipped, up from 403/3 by the three tests ARB-021
required.

## 2026-09-14 — The nightly job owns its write-back (ARB-023)

**Why it needed a requirement at all.** The job produces the only evidence this
project accumulates, and it had never been covered by one. Four defects were
repaired in it on 2026-09-13 as unplanned work. The expensive one: `training/`
matched a gitignore entry, so `git add -A .arbiter training` exited 1, and
`bash -e` failed the job two seconds after a fifty-five-minute cycle finished
successfully. Run 1 measured everything and committed nothing, and the failure
read as though the measurement had broken rather than the bookkeeping.

**The shape of the fix is ordering, not cleverness.** The check is instant and
the cycle is not, so the check goes first. `tools/check_writeback.sh` names the
five accumulating files in one place, asserts none of them is ignored, and
dry-runs the exact `git add` the job ends with. CI runs it as its own step
before the cycle so the log says which thing broke; `train_cycle.sh` runs it too
so a laptop cannot skip what CI enforces. A write-back that cannot happen is a
reason not to start.

**The second route to the same loss.** A push that loses a race to another
commit throws away the night just as completely as a gitignore mistake. The job
now rebases onto whatever landed while it ran and fails the step if that does
not work, rather than exiting 0 having saved nothing.

**What the tests actually pin.** One reproduces the original arrangement — a
temporary repository with `training/` ignored as a directory — and asserts the
check refuses it, then restores the negations and asserts it passes. Another
reads `.gitignore` and the script and fails if they stop agreeing, because two
places naming the same five files drift silently and the drift surfaces months
later as a lost night. A third asserts the check appears before the cycle in
both callers, which is the whole point and the easiest thing to undo by
accident.

**State.** 410 passed, 3 skipped, up from 406/3 by the four tests ARB-023
required.

## 2026-09-14 — The Windows code paths run in CI (ARB-024)

**The gap was structural, not a missing test.** Neither workflow had ever run
on Windows — `pr-check.yml` was ubuntu-only and `train.yml` is a single ubuntu
job — so every Windows-specific branch was verified by one developer's machine
or not at all. ARB-006 is what that costs: a crash on every adapter timeout on
Windows, because `os.killpg` is absent there and the process-group path raised
`AttributeError` while the analyzer outlived the timeout that was supposed to
kill it. It was found by hand.

**Two different answers were needed, and neither substitutes for the other.**
A matrix gets a real Windows runner over the whole suite, which no amount of
monkeypatching can imitate. But `_kill_tree` is unreachable on a machine that
*has* process groups, so on Linux it would still never execute. Four of the
seven new tests therefore take the platform away rather than waiting for one:
delete `os.killpg`, then assert the fallback kills a real subprocess, reaches
for `taskkill /T` instead of killing only the direct child — checkov and
semgrep fan out into workers, and killing the parent alone is the same failure
by another route — and still kills the process when `taskkill` is missing or
refuses with a permissions error.

**Where the matrix went, and why not the other workflow.** On `pr-check.yml`,
which takes about a minute. Putting it on the nightly cycle would have spent
four hours to learn what one minute already reports, and the corpus
measurements are not platform-sensitive; the code paths around them are.
`fail-fast: false` matters more than it looks: the default cancels the Windows
job the moment Linux fails, which suppresses exactly the result the matrix
exists to produce.

**Skips are printed rather than counted.** Both jobs run `pytest -rs`. Windows
legitimately skips what Linux does not, and an unlisted skip is
indistinguishable in a log from a test that was never collected. Three skip
here today: tree-sitter twice and the uncloned corpus once.

**Verified before depending on it.** `tools/integrity.py` was run on Windows
first — 354,294 reports enumerated, 0 integrity failures, exit 0 — rather than
adding a CI step on the assumption it would work.

**What is not done.** The check is non-advisory (no `continue-on-error`
anywhere in the job) but it is not *required*, because nothing on this
repository requires any check: `repos/:owner/:repo/rulesets` returns `[]` and
`main` reports "Branch not protected". Making it required is a repository
setting, not a file, and the last ruleset here blocked a push earlier the same
day. ARB-024 stays pending on that one point.

**State.** 417 passed, 3 skipped, up from 410/3.

## 2026-09-17 — Testing the reading, and testing the measurement (ARB-030)

**What prompted it.** A question about whether the testing parameters were
actually dialed in, and whether the self-testing was good enough for senior
engineers to rely on routinely. The audit covered every measurement tool, the
knowledge ledger, the 426-test suite and the whole bug history.

**The finding that frames everything else.** The ledger holds 227,999
synthetic observations across 27 rules and zero human verdicts.
`calibrated_confidence()` reads only the adjudicated ledger and returns
`None` below twenty observations, so no rule is proven and every confidence
number a report prints is the rule author's guess. Nothing in this change
touches that, and nothing automated can: a verdict has to come from someone
looking at a real finding, `record()` makes a wrong mark permanent, and 162
fix-pairs and 346 contested findings are already queued and waiting. The work
here is about what the tool can check on its own.

**Five causes, nineteen bugs.** Every historical defect that made Arbiter
wrong about real code fell into one of: platform and path assumptions,
encoding defaults, attribution that did not propagate, heuristic proxies with
edges nobody had hit, and errors in the measurement code itself. Each had been
fixed with a test for the one case that failed, which is why the missing
`repo_id` had to be found three separate times (ARB-014, ARB-015, ARB-016) and
why the path-separator assumption came back in ARB-029. The new tests state
the property over the whole output of a scan instead, so a new probe or a new
construction site is covered the day it is written.

**Three defects the audit found, none of them reported by anything.**
Fingerprints were not stable across platforms, which matters because
adjudications are permanent and keyed by fingerprint -- a verdict recorded on
one machine could never match the same finding on another. The nightly
external-severity measurement wrote to a file nothing read, so 818 checks were
measured every night and none of it reached a scan; merging the current report
adds 110 checks that had no measured severity at all. And a severity table
documented a band its own code cannot produce.

**Two more the new tests found immediately.** Seven configuration reads used
the platform default codec -- the same defect as ARB-009 and ARB-012, in call
sites those fixes never touched. And `_is_binary` treated any NUL byte as
binary, so every UTF-16 file was classified as data and never read; a
credential in a file saved as "Unicode" was invisible. Headerless UTF-16 is
still left as data deliberately, because telling it from a real binary means
guessing and a wrong guess turns a binary into mojibake to scan.

**One defect introduced and caught here.** Moving file reading from text to
bytes is what made the byte-order-mark check possible, and it dropped Python's
newline translation along with it, silencing six secret formats on every CRLF
file. The existing suite caught it. The first test written to cover it did
not, because it used an AWS key -- recognised by its own shape anywhere, so
found with or without the carriage return. `tools/mutate_tests.py` reported
that test as passing while the defect was present, which is the argument for
that tool in one line: a test that has never failed is not evidence.

**What was deliberately not done.** No threshold was retuned.
`docs/testing-parameters.md` grades each one derived, reasoned or arbitrary,
and most are arbitrary -- the honest grade for a number nobody has tested an
alternative against. Changing any of them changes published evidence, so the
table records them and leaves the decision alone. `MIN_OBSERVATIONS = 20` is
the one worth attacking first, since it is currently the reason no rule in the
tool is proven.

## 2026-10-09 — The workspace re-synced to its upstream (ARB-031)

**What changed.** The OmniEngineering scaffold this repo carried was a fork:
`make_ai.py` was closest to an upstream commit from 2026-06-23, with three
local additions (a categories-keyed registry, an `archive.json` lifecycle
with a `withdrawn` status, and a live MCP server check in doctor), and no
`.ai/omni-version.json`, so `omni update` had nothing to merge against.
Upstream had meanwhile added an executable completion gate, a requirement
CLI, a failure ledger, test-suite registration, a five-layer code graph, an
MCP server of its own, git hooks and a 3-way `omni update`. The re-sync
recorded that 2026-06-23 commit as the merge base, ran `omni update`, and
resolved ten conflicts by hand; see the CHANGELOG entry for the file-level
outcome. The user chose, when asked, to do all of this holistically: both
directions, flat registry, all shims and legal files, full enforcement with
the training job exempt, the ledger backfilled, the graph built locally, and
both MCP servers registered.

**Why the registry format moved rather than the tooling.** Every new
upstream command (`gate`'s registry check, `graph`, `mcp serve`, `draft`,
`complete`) reads a flat `requirements` array with a `category` field. Teaching
all of them a second shape would have been more code than the conversion and
two formats to keep working forever. The conversion was mechanical and
lossless: each entry gained `category` from its former key, ids and
statuses are unchanged, and `python omni requirement list --all` shows all 30.
The archive is now `requirements-archive.json`; upstream's `omni requirement
archive` already allocates ids across both files, which was the point of
ARB-004's version, and it gained the `withdrawn` status and the refusal to
archive live work from arbiter's.

**Why fixtures are excluded from the graph's code layer, not from `.ai/.ignore`.**
`.ai/.ignore` is the list of files an assistant must not read, and the brief
tells a session to open the specific fixture a test names. The graph only
had `.gitignore` and `.ai/.ignore` as exclusions, so the first build parsed
`fixtures/legacy-platform` and attributed its planted defects' symbols to
arbiter. `exclude_code_globs` was added upstream for exactly this: a tracked,
readable directory that is nevertheless not the project's source.

**What was found stale.** Five playbooks and checklists still carried
STEP-Migration text (`python3 app.py`, `tests/validate_stacks.py`, pyright,
"`./omni map` is intentionally never run against this repo") that
contradicted `.ai/project-configuration.md`, which says `omni map` is safe
here. They had survived ARB-001's check for STEP-specific content because
the check looked for CUI and project names, not for commands. They are
upstream's versions now, and `core-context.md`'s matching paragraph is
corrected.

**Known limits.** The gate's `co_changed` rules require `CHANGELOG.md` and
the registry to change with any other file; `training/**` and `.arbiter/**`
are exempt because the nightly job commits there without a requirement by
design (ARB-023), and `python omni waive` is the escape for anything else.
`omni requirement complete` only demands a ledger entry when the category
matches defect/bug/fix/regression, which arbiter's categories do not; record
defects deliberately. The graph needs `tree-sitter-python` and
`tree-sitter-bash` installed; without them every other command still works.
Doctor's remaining WARNs are listed in `.ai/project-configuration.md`. The
OmniEngineering suite has one pre-existing failure in this container
(`test_gate_hook`'s background rebuild, which needs the tree-sitter grammar
on the system interpreter) that is unrelated to the ported changes.

## 2026-10-09 — Arbiter evaluates itself (ARB-032 … ARB-037)

**The gap.** After the workspace re-sync, a self-scan was the obvious next
question, and the answer was embarrassing in a useful way: the grade was
withheld at 22% coverage, three high findings were the `authored` probe
reading its own docstring, and the repository that ships a GitHub Action had
never run that action on itself. The user chose to close the whole list:
self-gate first, then the noise, then the capability gaps.

**Why the self-gate runs on Linux only.** The analyzers do not all install
on the Windows runner, and a gate that passed at lower coverage would answer
a different question from the one `arbiter.yaml` asks. The Windows job keeps
the suite, integrity, mutation, doctor and `omni gate`; `omni gate` on Windows
runs `arbiter gate --changed` with native probes only, which is honest as
long as the Linux job runs the full one.

**Why adapter output is filtered against the inventory rather than each
tool taught its exclusions.** ARB-026 and ARB-029 taught bandit, checkov and
semgrep the inventory's exclusions through their own flags; gitleaks was not
taught and walked `__pycache__` and a git-ignored graph file into four
critical findings. Enforcing the boundary once, on the way in, means the next
adapter cannot miss it. The tools still do their own traversal; what reaches
the report is what the inventory decided was in scope.

**Why not-applicable leaves the denominator, and why that is not a loophole.**
`ProbeOutcome.applicable` already existed (ARB-022 added it for the control
matrix) and the engine already set it; only `compute_scorecard` ignored it.
The honest distinction is between a probe with no question to answer (seams
on one repository) and a probe prevented from answering (a missing binary).
The first now leaves the denominator; the second never does. CI-12 makes the
distinction machine-checked: a report that pairs `applicable=False` with the
engine's own prevented-reason wording, or with a `ran` or `error` status,
fails verification, and `tools/integrity.py` breaks it on every run to prove
the check is live. Coverage on this repository is 99% with the analyzers
installed, and the 1% is the `judgement` probe the offline profile forbids,
which is exactly right.

**Suppressions, not exclusions, for the tooling and the corpus.**
`arbiter.yaml` now suppresses quality and semgrep findings on
OmniEngineering's three tooling files, all analyzer findings under
`fixtures/`, gitleaks under `tests/`, and semgrep's Python 3.6 compatibility
rules project-wide. Every entry carries its reason and an expiry, and the
findings stay in the report marked as suppressed. That is the feature: the
report can say how much of what it did not act on was a decision.

**The two gates now meet.** OmniEngineering gained a `command` validation
type (its ARB-032), and `completion.arbiter_gate` in this repository's
completion rulepack runs `arbiter gate --changed <base>` inside `omni gate`
whenever source, tests, tools or policy change. The pre-commit hook, the
Claude Code Stop hook and CI all run it, so a change to Arbiter cannot be
reported complete while Arbiter itself rejects it.

**What is still open.** ARB-005 (licensing) needs counsel. ARB-024's last
criterion, making the check required, is a repository setting. The control
packs' identifiers were transcribed, not reproduced, and must be verified
against the licensed PCI DSS and TSC texts before an audit package cites them.


## 2026-10-09 — The suite runs in two tiers (ARB-046)

**The problem.** `tests/test_arbiter.py` had grown to 5,516 lines and 438
collected ids, and the whole suite took about eleven minutes on an idle
machine and twenty-seven under load, which is too long for a check that runs
on every commit. Almost all of that time was a few dozen tests that run a
real analyzer through the adapters or scan more than the fixtures, and none
of those tells the Windows matrix anything.

**The split.** The file is fourteen files by area (`test_probes_security`,
`test_probes_quality`, `test_probes_drift`, `test_probes_iac`,
`test_policy_gate`, `test_report_output`, `test_review_learn`,
`test_graph_system`, `test_adapters`, `test_incremental_scan`,
`test_tools_training`, `test_service_api`, `test_api_app`, `test_mcp_api`),
every one under `arbiter.yaml`'s 900-line limit, moved by a script that
carried whole top-level functions with their decorators, section comments
and the imports each file needs. Helpers two files share live in
`tests/helpers.py`; test files never import from each other. The tfplan
fixtures joined `legacy_report` and `system_report` in `conftest.py` at
session scope. The proof is `pytest --collect-only`: 557 ids before, 557
after, the same set modulo the path. One test,
`test_inferred_findings_do_not_gate_by_default`, had been defined twice and
only the second was ever collected; the shadowed copy is gone rather than
revived. The failure ledger's regression-test references follow the tests to
their new files, because `omni doctor` checks they exist.

**The tiers.** `slow` is a registered marker. It goes on a test when the
test invokes a real external analyzer through the adapters or scans
something larger than the fixtures, confirmed against `--durations`; it
never goes on a test because it is merely unhurried. Eight tests carry it today: three service and API scans that spawn a
fresh `arbiter` process, each of which re-probes the analyzers' versions;
one end-to-end client scan through every analyzer; the corpus check; and
three full scans that ask for `use_adapters=False` and get the adapters
anyway, because the flag only skips registering them and an earlier test in
the same process has registered them already. That last one is an engine
quirk worth its own fix; the marker works around it in the suite without
touching `src/`. Measured here under load: the fast tier in under two
minutes and the slow tier in about five, against eleven minutes for the
whole suite before the split.
`pr-check.yml` runs `-m "not slow"` on every matrix cell and `-m slow` once
in a parallel Linux `slow-tier` job with the analyzers installed, so the
pull request waits for the slower of the two rather than their sum.
`.ai/test-suites.json` names the fast tier as the gate's suite and the slow
tier as an integration suite the gate does not run. `train.yml` still runs
everything nightly.
