# Running Arbiter on your own repositories

Everything up to now has been Arbiter measuring itself against public practice
repositories. This is the part where it looks at code that matters to you.

Nothing here needs my sandbox. It runs on your machine, against repositories
that never leave it.

**A note on licensing:** `LICENSE` reserves all rights and grants none — that's
deliberate, the operative terms are still being drafted (tracked as ARB-005 in
[docs/licensing.md](docs/licensing.md)). If you got a link to this repository
from me, you have my go-ahead to run it against your own code; that's the
actual, current position while the formal grant is unsettled, not just a gap
in the paperwork.

**A note if you're on Windows:** the `arbiter` CLI itself is pure Python and
runs the same in PowerShell, cmd.exe, or a terminal — every command below
works as written. The handful of `.sh` helper scripts (`install_tools.sh`
below, and a few others under `tools/`) are bash, not PowerShell, so run those
specific steps from Git Bash (installed alongside Git for Windows) or WSL.
Nothing else in this walkthrough needs bash.

---

## Before you start: what this will and will not tell you

The first run on a real repository produces a lot of findings, and most of them
are not bugs you introduced last week. They are the accumulated state of a
codebase that has been worked on. That is normal and it is why the second step
below is "take a baseline" rather than "fix everything".

What the first run is actually for is calibration. You look at thirty findings,
you say which ones are real, and the tool records your verdicts. That is the
only way it learns what your code looks like, and it is worth more than any
amount of tuning I can do against public repositories.

---

## 1. Install

If you're starting fresh, with no repository open yet:

```bash
git clone <your arbiter repo>
cd arbiter
pip install -e .
```

If you're already working from inside the repository you want scanned —
this session's working directory is the target, not a separate clone of
Arbiter — skip the clone and just get `arbiter` on the path instead:

```bash
pip install git+<your arbiter repo>@main
```

or, if you have a local checkout of Arbiter elsewhere on the same machine:

```bash
pip install -e /path/to/arbiter
```

Either way, stay where you are. Step 2 runs in the repository you're already
in, not in Arbiter's own.

Optional, and worth doing once:

```bash
./tools/install_tools.sh      # five external analyzers, pinned versions
```

That script lives in Arbiter's own repo, so run it from there (or point it
at a local checkout) if you took the second path above. It's optional either
way, because Arbiter reports what it could not run — without the analyzers
the coverage figure is lower and nothing pretends otherwise.

---

If the repository already carries the OmniEngineering workspace, `./omni
arbiter install --source ../arbiter` installs Arbiter and registers it with
the workspace's gate and MCP configuration in one step (see `SETUP.md`,
"With OmniEngineering"); the rest of this guide applies unchanged.

## 2. First full scan

Run from the root of the repository you want scanned. If you were already
there for step 1, this is the same directory — otherwise, `cd` there first:

```bash
cd /path/to/your-repo
arbiter scan . --out .arbiter/first-run --format json,html,console
```

Open `.arbiter/first-run/report.html`. Read four things, in this order:

1. **The coverage figure.** If it is low, the NOT ASSESSED list says why —
   usually a missing analyzer or a stack Arbiter did not detect. Fix that
   before reading anything else, because every other number is conditional
   on it.
2. **The NOT ASSESSED list itself.** This is the part other scanners do not
   print. A check that could not run is not a check that passed.
3. **Critical and high findings.** There should be few. If there are many,
   something is miscalibrated and that is useful to know on day one.
4. **The assurance dimension.** It scores at weight zero, so it does not move
   the grade. It counts how much of the repository has been excluded from
   analysis — `# noqa`, `nosec`, `eslint-disable`, `checkov:skip`, excluded
   paths, tests that assert nothing. It is the answer to "how much does a
   clean report from any tool actually mean here".

---

## 3. Take a baseline

```bash
arbiter baseline .arbiter/first-run/report.json --out .arbiter/baseline.json
git add .arbiter/baseline.json && git commit -m "arbiter baseline"
```

Everything in the baseline is labelled `existing` from now on. Everything else
is `new`. This is what lets a gate block on what a change introduced without
blocking on the history of the repository.

Baselines are keyed by a fingerprint that excludes line numbers, so
reformatting a file does not resurrect every finding in it.

---

## 4. Adjudicate thirty findings

This is the step that makes the tool yours.

```bash
arbiter review .arbiter/first-run/report.json --html .arbiter/review.html
```

Open the file. It is self-contained — no server, no network, no build step,
nothing loaded from a CDN. It shows one finding at a time with its evidence and
you mark it: real, not real, or skip. When you are done it gives you a block of
text to copy. Save it as `review.md` and feed it back:

```bash
arbiter review .arbiter/first-run/report.json --apply review.md
```

Or stay in the terminal:

```bash
arbiter review .arbiter/first-run/report.json --interactive
```

The sampler picks what to show you. It puts rules with no verdicts yet ahead of
rules already proven, spreads across files so you are not answering thirty
questions about one module, and never asks you about a finding you have already
judged. Thirty verdicts is enough to move real calibration; a hundred is
better.

What your verdicts do: a rule you call wrong repeatedly gets demoted in
confidence and severity, not deleted. It keeps reporting, more quietly, and
`arbiter learn` shows you exactly what changed and on how many observations.
Ten out of ten is treated as evidence of a rate above roughly 0.72, not as
evidence of perfection.

---

## 5. Put it on pull requests

```bash
cp examples/pull-request-gate/arbiter.yaml       /path/to/your-repo/arbiter.yaml
mkdir -p /path/to/your-repo/.github/workflows
cp examples/pull-request-gate/arbiter-pr.yml     /path/to/your-repo/.github/workflows/
```

Edit one line in the workflow — the `pip install git+https://github.com/OWNER/arbiter.git@main`
— to point at wherever you pushed this repository.

The workflow runs two different jobs, and the difference matters:

| | pull request | nightly / main |
|---|---|---|
| what it reads | the changed files, plus manifests, lockfiles, CI and Terraform | everything |
| how long | seconds | a minute per half-million lines |
| what it blocks on | anything critical, anything new at high | nothing; it reports |
| grade | **withheld** | reported |
| refreshes the baseline | no | yes, from main only |

The pull-request job passes `--changed`, so Arbiter reads the diff and records
every check that needs the whole repository as *not assessed*, with the partial
scan named as the reason. The grade is withheld outright, because a number that
looks like a repository grade but describes a diff is exactly the thing this
tool exists not to produce.

**The pull-request job runs Arbiter's own probes and no external analyzer.**
ruff, bandit, checkov, semgrep and gitleaks are all repo-scoped, so all five are
recorded as not assessed on a `--changed` run. They are held back not because
they reason across files but because nobody has measured whether they answer the
same way from a subset, and Arbiter will not assume that on their behalf. If you
want semgrep's opinion on a branch, run a full scan; the nightly job already
does.

So: **a green pull-request check means "this change introduced nothing that
crosses a threshold". It never means "this repository is clean."** The nightly
job is the one whose result may be quoted as being about the repository.

Measured on Traefik, 171 changed files out of 2,293: 59 seconds to 6 seconds,
and the file-scoped findings are identical to the full scan's on the files both
read — 158 and 158, nothing missing and nothing extra.

### One thing to know about the pull-request report

Arbiter reads a few files the change did not touch — manifests, lockfiles, CI
workflows, Terraform — because its rules genuinely reason across them. Findings
that land in those files are real, and they are not this change's fault, so
they are tagged `outside-this-change` and the console says how many there are.

The baseline is what stops them reaching the gate: once a finding is in the
baseline it is `existing`, and the `new: high` rule ignores it. That is why
step 3 comes before step 5.

### Try it locally first

You do not have to push a workflow to find out what it will say:

```bash
cd /path/to/your-repo
git fetch origin main
arbiter gate . --changed origin/main --baseline .arbiter/baseline.json
echo "exit code: $?"
```

---

## 6. Comparing two related repositories (system mode)

If you have two codebases that are supposed to mean the same thing — a
migration from an old stack to a new one, a rewrite, a service split out of a
monolith — that is the case Arbiter's system mode exists for. Write an
`arbiter-system.yaml`:

```yaml
system: my-system
repos:
  - id: old
    path: ../old-repo
    role: source
  - id: new
    path: ../new-repo
    role: target
```

```bash
arbiter scan --system arbiter-system.yaml --out .arbiter/system
```

The seam checks then run across both: an endpoint documented in one and absent
from the other, a database column dropped on one side and still referenced on
the other, an OpenAPI path with no registered route. Those are `interface` and
`drift` findings, and they are repo-scoped — which is why they do not run on
pull requests and do run nightly.

Two honest caveats. The contract checks report `spec-not-compared` at info
level when fewer than three routes are readable, rather than claiming a clean
comparison. And the dropped-column check reports at low confidence, because
static analysis cannot see a column referenced through a string built at
runtime.

---

## 7. Things Arbiter will not do

- **It never executes your code.** No `npm install`, no `terraform init`, no
  importing your Python. Everything is read, parsed or fed to an analyzer that
  also does not execute it. For Terraform this means you generate the plan and
  Arbiter reads the JSON: `terraform plan -out=tf.bin && terraform show -json tf.bin > plan.json`,
  then `arbiter scan . --tfplan plan.json`. A plan supersedes reading the
  source, because a plan has the variables resolved and the source does not.
- **Secret values are masked in every output** — report, SARIF, HTML, console.
  The finding tells you where and what kind; it does not reprint the
  credential into a build log.
- **It will not grade what it did not inspect.** Below the coverage threshold
  the overall score is withheld, and the report says why.

---

## 8. When something looks wrong

It will. Two paths, and the first one is better:

```bash
arbiter feedback f:8c41d2ae9b07 --false-positive --note "generated file, not ours"
```

That is a data point. Enough of them and the rule recalibrates, and I can see
the pattern the next time we work on it.

The blunt path, for when a rule is simply not applicable to your repository:

```yaml
suppress:
  - rule: "arbiter/secrets.*"
    path: "tests/fixtures/**"
    reason: "test vectors, committed on purpose"
    expires: 2027-01-01
```

Suppressed findings stay in the report, marked and attributed with the reason
and the expiry. They are not deleted, and suppression can never raise the
coverage figure — that is a machine-checked invariant, CI-6.

---

## What to send back

File it as a GitHub issue on this repository — that's the channel
`CONTRIBUTING.md` sets up for exactly this: defect reports, reproductions and
doc corrections. Send code snippets or a description directly to me instead of
attaching a patch; `CONTRIBUTING.md` explains why.

If you want the next session to be useful, the three most valuable things are:

1. `.arbiter/knowledge.json` after you have adjudicated a few dozen findings.
   It contains your verdicts and no source code.
2. The coverage figure and the NOT ASSESSED list from the first full scan.
   Low coverage on a real repository is a gap in Arbiter, and it is the kind
   of gap public practice repositories do not expose.
3. Any finding you looked at and could not decide about. Those are worth more
   than the clear ones — an ambiguous finding usually means the rule is
   reporting the wrong thing, not that you are missing context.
