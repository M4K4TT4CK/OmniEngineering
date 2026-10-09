# Evidence

What Arbiter claims about itself, the measurement behind each claim, and the
bound on each. Figures here are produced by the tools in `tools/` and refreshed
by the nightly cycle; see [ci.md](ci.md#continuous-training).

## Contents

- [Confidence, honestly bounded](#confidence-honestly-bounded)
- [Claim integrity](#claim-integrity)
- [Injection training](#injection-training)
- [Corpus tuning](#corpus-tuning)
- [Severity earned by measurement](#severity-earned-by-measurement)
- [Evidence the tool did not generate for itself](#evidence-the-tool-did-not-generate-for-itself)

---

## Confidence, honestly bounded

Six nines of *verdict correctness on arbitrary code* is not achievable, and a
tool that claims it is choosing a flattering denominator. By Rice's theorem no
static analyzer is both sound and complete on arbitrary programs, and separately,
a claim needs evidence: roughly 3,000,000 clean observations per rule to assert
an error rate below 10⁻⁶ at 95% confidence.

What *is* achievable is claim integrity — **the tool never asserts something it
did not verify.** That is a property of Arbiter's own execution rather than of
the code under analysis, so it is decidable and enumerable.

## Claim integrity

Every report carries a claim ledger: each assertion with the checks that support
it and the checks that abstained. Ten invariants forbid a claim from outrunning
its basis, and the engine verifies its own report before writing it.

```bash
arbiter verify arbiter-out/report.json --show-claims
python tools/integrity.py --probes 6
```

| Evidence | Result |
|---|---|
| Reports enumerated exhaustively over the bounded state space | **3,188,646** |
| Integrity failures | **0** |
| Invariants declared / proven enforced by mutation | **10 / 10** |

Exhaustive over that space is a stronger statement than six nines within it —
and says nothing outside it, which is why the bound is stated rather than
extrapolated.

For verdict correctness, `arbiter learn` reports measured per-rule precision with
a Wilson lower bound, and a rule under 20 adjudicated observations reports as
**unproven** rather than inheriting a flattering estimate from a handful of
samples.

## Injection training

Rules are trained by planting known defects into real files from the corpus and
measuring what is found, alongside controls that look like defects and are not.
Seeds come from the corpus rather than from templates, because effective sample
size is bounded by generator diversity, not by trial count.

```bash
python tools/inject.py --trials 40000 --knowledge .arbiter/knowledge.json
```

| | Result |
|---|---|
| Trials | 30,000 across 15 rules |
| Recall | **15,000 / 15,000** |
| Specificity | **15,000 / 15,000** |
| Rules with controls | **15 / 15** |
| Per-rule Wilson lower bound | 0.995 – 0.999 |

Every rule has controls. That row matters more than the other two: a rule with
measured recall and no controls could be firing on everything and the numbers
would still look perfect. Six rules were in exactly that state until controls
were written for them.

Read the result honestly. It measures whether a rule detects defects drawn from a
generator, against controls drawn from the same generator. It is evidence about
rule mechanics, not about code in the wild — which is why synthetic observations
are kept in a separate ledger from human adjudication and `arbiter learn`
reports them apart. Calibration reads only the adjudicated ledger.

### What injection actually found

| Defect | Consequence |
|---|---|
| `:=` did not match | Every Go short variable declaration was invisible to the secret detector. Recall on that rule was **0.48**. |
| Backticks were not strings | A secret in a JavaScript template literal was invisible. |
| `Job`, `CronJob`, `Pod` were not `compute` | A third of injected Kubernetes workload defects matched no rule at all. |
| Hyphenated placeholders | `your-api-key-here` was reported as a credential, because `\w` does not match a hyphen. |
| Kubernetes PVCs scored by AWS properties | `unencrypted-volume` fired on every PersistentVolumeClaim in Kubernetes' own examples — 20 false positives. |
| No rules for absent hardening | Kubernetes defaults are the insecure ones. A deliberately vulnerable Kubernetes repository produced **two** findings; it now produces twelve. |
| Unquoted values were invisible | `.env` files, Kubernetes Secrets, `docker-compose`, `export VAR=`, Dockerfile `ENV` and `.properties` — the formats where secrets most commonly leak — were **all** unreadable, because the pattern required quotes. |
| Six rules had no controls | Recall was measured; false-alarm rate was not measured at all. |

Injection and corpus breadth find different things, and neither substitutes for
the other. Injection finds rules that fail *mechanically* — a pattern that
cannot match what it claims to match. Breadth finds rules that work mechanically
and are still wrong about real code, which is the harder failure to see: a
critical finding on three literal dots passes every injection trial ever
written, because the generator never thinks to plant a placeholder.

## Corpus tuning

Rules are tuned against public repositories spanning seventeen stack labels,
split into three populations. A rule that fires on `flask` is telling you about
the rule, not about the code.

Composition is defined in `tools/corpus.py` and is the authoritative source for
these counts:

```bash
python tools/corpus.py --counts     # needs nothing cloned
```

| Population | Total | Held out | Tuning set |
|---|---|---|---|
| **Deliberately vulnerable** | 16 | 2 | 14 |
| **Well-maintained production** | 20 | 2 | 18 |
| **Teaching material** | 5 | 1 | 4 |
| **All** | **41** | **5** | **36** |

The measured figures below come from the last recorded corpus run. Its
vulnerable population was 14 repositories — it predates the two Go repositories
added after `tools/worklist.py` reported Go as 811,194 lines of well-maintained
code with 62 lines of broken code to compare against. Rerun `tools/corpus.py` to
refresh them.

| Population | Repos measured | Lines | Findings | Per KLOC | Critical | High |
|---|---|---|---|---|---|---|
| **Deliberately vulnerable** | 14 | 661,814 | 1,091 | 1.65 | 16 | 71 |
| **Well-maintained production** | 20 | 2,309,276 | 3,630 | 1.57 | **0** | **0** |
| **Teaching material** | 5 | 596,206 | 2,211 | 3.71 | 0 | 14 |

On the severities that gate a build, the separation is total: sixteen criticals
and seventy-one highs across the broken repositories, and **not one of either**
across 2.3 million lines of well-maintained production code in twenty projects.

The overall per-KLOC rates are almost identical between the first two
populations, and that is not a failure. Most findings are low-severity style and
hygiene notes that any large codebase accumulates. What matters is not how often
a rule speaks but how hard it pushes, and on that measure the populations are
cleanly separated.

```bash
python tools/corpus.py         --counts             # composition, nothing cloned
python tools/corpus.py         --root /tmp/corpus   # population summary
python tools/discriminate.py   --root /tmp/corpus   # per-rule discrimination
python tools/worklist.py       --out training/WORKLIST.md   # what to fix next
```

### Every stack needs a broken counterpart

The vulnerable population started at five repositories covering Terraform,
CloudFormation, Node and Kubernetes. Eight of the thirteen languages had nothing
broken to compare against at all, so any rule covering them could not be
measured — it could only be asserted. Java, Ruby, PHP, Python, TypeScript,
Docker, CDK and a second, larger Kubernetes project were added for that reason.

Widening found defects that repetition could not:

| Defect | Consequence |
|---|---|
| A PEM header with a placeholder body counted as a key | Argo CD's operator manual shows how to register a credential; the key body in the example is **three literal dots**. Arbiter reported it as a critical, high-confidence leaked private key — the worst finding it can produce, on nothing. |
| `integration/`, `e2e/` and `hack/` were not recognised as test paths | Traefik commits real TLS keys under `integration/resources/tls` so its integration suite has something to serve. Identical in kind to the keys under `psf/requests`' `tests/certs/`, which were already handled — a different word for the directory changed the verdict from medium to **critical**. |
| A credential inside documentation counted as production | A key in a manual illustrates where the key goes. `role == "docs"` now downgrades the same way a test path does. |

All three were criticals on well-maintained code. All three are now regression
tests naming the repository they came from, in `tests/test_probes_security.py`.

## Severity earned by measurement

`tools/discriminate.py` measures every rule against both populations and reports
whether its severity is supported. Three things about that measurement were
wrong before the tool existed, and each one produced confident nonsense.

**The denominator.** A Kubernetes rule can only fire on Kubernetes manifests.
Measured against whole-repository size it looks immaculate inside a
400,000-line Go project containing forty lines of YAML — not because the rule is
good, but because 399,960 lines were never eligible to fail. The denominator is
now the lines written in the languages that rule actually fires on, counted
separately in each population. Reports carry `loc_by_language` so this is
computable from any saved scan.

**The numerator.** Arbiter already knows a manifest under `testdata/` is not a
deployment: it reports the finding, labels it, and drops its severity and
confidence so it barely moves the grade. Counting that at full weight measures
the rule against a claim the tool never made. In Argo CD, **195 of 203** findings
for one rule were exactly this. Findings are now counted by what they are worth —
severity weight times confidence factor, the same arithmetic the scorecard uses.

**The comparison itself.** The broken repositories are broken in the *security*
sense. Nobody publishes a repository that is deliberately badly documented, so
for quality and drift rules there is no broken population and the ratio measures
nothing. `ast.function-too-long` fires five times more per line on
well-maintained code than on the goats — but the goats are small teaching apps
and the well-maintained repositories are mature production codebases. That ratio
is a statement about codebase age. Only security and compliance rules get a
verdict; everything else gets its number printed and no conclusion drawn.

With all three corrected, every scoring security rule separates the populations:

| Rule | Weighted ratio | Severity |
|---|---|---|
| `secrets.aws-access-key` | 2457× | critical |
| `resource.k8s-host-path-volume` | 704× | medium |
| `resource.unencrypted-database` | 473× | high |
| `secrets.private-key` | 14.2× | critical |
| `secrets.jwt` | 10.7× | medium |
| `secrets.assigned-credential` | 10.7× | high |
| `supply.unpinned-action` | 10.5× | low |
| `resource.k8s-no-security-context` | 7.6× | medium |
| `resource.k8s-not-run-as-non-root` | 6.6× | medium |
| `resource.k8s-privilege-escalation-not-disabled` | 6.5× | medium |
| twelve rules that never fire on good code | ∞ | — |

Three rules were demoted to `info` on this evidence, which reports them at zero
score weight rather than deleting them:

| Rule | Ratio | Why |
|---|---|---|
| `supply.unpinned-npm-dep` | 1.1× raw | Caret ranges are just as common in good Node as in bad. Libraries are *supposed* to declare ranges. |
| `supply.unpinned-python-dep` | 0.0× | Fired only on well-maintained code. |
| `supply.pull-request-target` (safe form) | 1.5× | The rule already separates the dangerous form — a workflow that checks out the PR head — and scores that `high`. The benign form appeared twelve times on good repositories and once on broken ones. |

The Kubernetes hardening rules were nearly demoted on the *raw* count, at 1.5×.
Weighted, they run 6.5× to 7.6×: almost every clean-code hit was a `testdata/`
manifest the tool had already discounted. They stayed at medium, and the reason
is on record so the question is not reopened from the raw counts.

## Evidence the tool did not generate for itself

Everything above, Arbiter made. Injection plants faults from patterns somebody
chose; corpus discrimination compares two populations that were also chosen.
Both share a blind spot: a rule can pass all of it and still be wrong about real
code.

**Real fix pairs** (`tools/fixpairs.py`) close that. Walk a repository's
history, scan each commit and its parent, and find a finding present in the
parent and gone in the child at the same site. Somebody who knew the system
decided something needed changing; nobody wrote that commit to be found by a
scanner. A rule that fires on both sides of a commit that plainly fixed the
thing is wrong, and nothing else in the pipeline would have said so.

Commit messages are deliberately *not* the filter — libraries are full of
feature commits mentioning encryption that fix nothing, and real remediations
get committed as "update manifests". The finding appearing and then disappearing
is the signal.

**Disagreement mining** (`tools/disagree.py`) decides which findings are worth a
person's attention. Where two independent analyzers looked at the same line and
reached different conclusions, exactly one is wrong, so a verdict there resolves
real uncertainty instead of confirming a settled one. Four buckets — contested,
severity disagreement, corroborated (agreed, deliberately *not* queued) and
coverage gap (an external tool checks something Arbiter has no rule for, which
is a list of rules worth writing rather than a disagreement).

**Held-out repositories.** Five repositories, never used to tune a rule and
never read while deciding one — two vulnerable, two well-maintained and one
teaching, so every population is represented and the held-out numbers stay
comparable to the tuned ones. Every figure in this project was previously
measured on repositories the rules were tuned against, which is how a tool ends
up fitted to its own practice set. The
corpus tool reports both rates — and prints its own caveat, because a per-KLOC
rate over a handful of repositories is as much about what those repositories
contain as about whether the rules generalize. The figure that survives a small
sample is the count of build-breaking findings, and that one is zero.
