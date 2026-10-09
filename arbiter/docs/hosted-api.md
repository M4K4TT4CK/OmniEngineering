# Hosted API

> **Status: built, not deployed.** Tracked as **ARB-018**. The service layer
> (`src/arbiter/service.py`) and the HTTP surface (`src/arbiter/api.py`) exist
> and are tested. Nothing has been exposed to a network. It binds to localhost
> by default, and must not face an external caller before the custody terms in
> [Custody](#custody-is-the-real-gate) are settled.

## Contents

- [What changed and why](#what-changed-and-why)
- [The shape](#the-shape)
- [The operation that does not exist](#the-operation-that-does-not-exist)
- [Custody is the real gate](#custody-is-the-real-gate)
- [Licensing, corrected](#licensing-corrected)
- [Access is handed out by hand, one key per user](#access-is-handed-out-by-hand-one-key-per-user)
- [How a person uses it: `arbiter remote`](#how-a-person-uses-it-arbiter-remote)
- [The endpoints](#the-endpoints)
- [TLS, with no plaintext mode](#tls-with-no-plaintext-mode)
- [What is built](#what-is-built)
- [What is not built](#what-is-not-built)

Running it for real is [pilot-runbook.md](pilot-runbook.md); what a tester is
told about their code is [pilot-terms.md](pilot-terms.md).

---

## What changed and why

On 2026-09-12 this project chose an MCP server over a hosted API and wrote the
reasoning into `.ai/project-context.md`. That decision is now reversed.

MCP relocates the installation rather than removing it. An agent can call
`arbiter_scan` without anyone typing a CLI command, but only on a machine where
Arbiter, Python and the optional analyzers are already installed. "No local
install" was the requirement, and MCP does not meet it. Everyone who cannot
install it still cannot use it.

MCP is not withdrawn. It remains the correct surface for source that must not
leave the machine it sits on, which is a real and common constraint. It becomes
one of two front doors rather than the only one.

## The shape

Both front doors call `src/arbiter/service.py`. Nothing about containment is
decided in a front door, because a rule written twice is a rule that will
eventually be written differently.

```
  MCP tools  ──┐
               ├──►  service.py  ──►  arbiter console script (subprocess)
  HTTP API  ───┘         │
                         └─ Workspace, archive ingest, profile and path checks
```

| Layer | Responsibility |
|---|---|
| `service.py` | workspace lifecycle, archive ingest, path containment, profile checks, the three operations |
| `mcp.py` | tool schemas and dispatch; no containment logic of its own |
| `api.py` | keys, request limits, TLS enforcement, the audit line; no containment logic of its own |

Three operations, and only three: `scan`, `gate` and `review_queue`.
`service.OPERATIONS` names them, and a test asserts the set is exactly that.

Arbiter runs as a subprocess rather than an import. `run_scan` takes fifteen
parameters and `src/arbiter/__init__.py` exports only `__version__`, so there is
no stable Python API to bind to yet. The subprocess also means a probe that
wedges or dies takes down a subprocess, not the server.

## The operation that does not exist

No function in `service.py` and no tool in `mcp.py` records an adjudication
verdict, and the HTTP surface must never grow one.

Calibration reads a single ledger: findings a person looked at and judged.
Injection trials measure whether a rule fires against faults Arbiter generated
itself; corpus discrimination measures whether a rule separates broken code from
working code. Both are Arbiter measuring Arbiter. The adjudication ledger is the
one signal in the system that the system did not generate.

`learn.record()` refuses to re-adjudicate a fingerprint, so a wrong mark is
permanent. An automated caller marking in a loop would convert measured
precision into the tool's opinion of itself, at machine speed and irreversibly.

So `review_queue` writes a queue with every mark blank and stops. A person marks
it and runs `arbiter review --apply` locally, against a ledger on their own disk.
`test_the_service_layer_exposes_no_way_to_record_a_verdict` asserts the absence
rather than trusting a reviewer to notice the capability returning.

## Custody is the real gate

Hosting means customer source lives, however briefly, on a disk the customer
does not control. That is the substantive change, and it is a contractual and
security question rather than an engineering one.

What the code already does:

- **Workspaces are isolated and temporary.** `Workspace` creates a directory
  outside the server tree, owner-only on POSIX and inside the per-user temp
  directory on Windows, with `source/` and `output/` as siblings,
  and removes it on exit including after a failure. Siblings matter: a scan
  whose output lands inside the scanned tree reports on its own previous HTML,
  which was 27.7% of unsuppressed findings when it happened.
- **Uploaded archives are treated as hostile.** Members that are absolute,
  traverse upward, are links, are device nodes, or exceed the size and count
  bounds are refused, and the archive is refused whole rather than partially
  extracted. A link is the cheapest way to make a scan read a file outside the
  upload and quote it back in a finding's evidence snippet.
- **The network stays off.** `check_profile` refuses `connected` and `audit`,
  which declare `network: True`, unless the operator explicitly allows it. The
  hosted door never will. The MCP schemas offer only `offline` and `ci`.
- **The scanned repository is never executed.** No build, test, hook or entry
  point from the target runs at any stage. Probes read files.

What the code cannot settle:

- **A report is sensitive even after redaction.** `report.py` refuses to reprint
  a secret's value and `test_no_output_format_reprints_a_secret` holds it to
  that. But a report still names the file and the kind of credential, which is a
  map to what to steal. Storing reports is therefore a decision that needs an
  explicit retention answer, not a default.
- **Concentration.** Many customers' source in one place is a materially larger
  target than any single local install. That is an argument for short retention
  and hard tenant isolation, and it is why the HTTP layer owns authentication
  and tenancy rather than the service layer.
- **Terms.** Confidentiality, retention, deletion, incident notification and
  sub-processor disclosure all have to exist in writing before an external
  caller uploads anything.

## Licensing, corrected

The original objection recorded against hosting was that "LGPL obligations
differ again for network use". That overstates it in the wrong direction.

LGPL-2.1 obligations attach to *conveying a copy*. It has no network-use clause
— that is the AGPL, and `semgrep`'s CLI is not under it. Running `semgrep`
server-side and returning findings over HTTP conveys no copy to the caller, so
**L-6, the redistribution review that blocks the air-gapped bundle, does not
gate hosting.** Hosting triggers fewer third-party obligations than the bundle
does, not more.

What does still need counsel is L-3's term structure — a hosted service is
priced and terminated differently from a delivered copy — and the custody terms
above. See [licensing.md](licensing.md).

## Access is handed out by hand, one key per user

It is free, and there is nothing to apply for. Access is issued by hand because
there is no identity or billing system to do it any other way — not because
anybody is being vetted, and not because a recipient has to justify what they
want to scan. Nobody is asked what is in the repository.

The owner mints a key for one user and sends it to them:

```
arbiter api key add --user "dana@acme.example"
arbiter api key list
arbiter api key revoke 4a0df464a689
```

A key is scoped to a user, and that is the whole model — no roles, no tiers, no
per-repository or per-organisation scope. One user holds at most one live key,
so a key identifies a person rather than a pool. Minting over a live key is
refused unless `--replace` is passed, which revokes the old one in the same
command, so rotation is one deliberate act and never leaves two keys quietly
working for the same person.

Sharing a key defeats everything below it: the caps are counted per key, so two
people sharing one get half the allowance each and look like one caller in any
log; and revoking it for the person who left also cuts off the person who
stayed. Adding somebody means minting them their own.

The raw key is printed once and never stored — only its SHA-256 hash goes to
disk, so the key file is not itself worth stealing and losing it means reissuing
rather than a breach. Each key carries a short id so it can be revoked or named
in a log without anyone writing the secret down. Keys live at
`~/.arbiter/keys.json`, or wherever `ARBITER_KEYS` points.

The side effect is worth keeping: something that cannot be signed up for cannot
be used at scale by a stranger.

### The limits are capacity and leak containment, not a tier

Nobody is charged, so nothing here is metering. Each limit exists for one
specific failure, and each is set well above real use rather than near it —
somebody working through twenty repositories in an afternoon should never meet
one.

| Limit | Default | What it is for |
|---|---|---|
| Expiry | 90 days | a key leaked and forgotten stops working on its own |
| Requests per key | 120 an hour | a leaked key running flat out is capped |
| Concurrent scans per key | 2 | one key cannot take the whole machine |
| Concurrent scans in total | 4 | the box stays up when everyone arrives at once |

A key that never expires and is never throttled is a standing, unlimited grant
to whoever ends up holding it — a ticket, a shell history, somebody who moved
on. The concurrency numbers are the ones that decide whether the machine stays
up, because a scan unpacks an archive and runs several analyzers.

`GET /v1/health` publishes all of them, so a recipient can see what they have
without asking or discovering it through a `429`.

Over any of them the answer is `429 Too Many Requests` with a `Retry-After`
header. The first three are per key, so one recipient cannot exhaust another's.
The fourth is the server's own ceiling, and it exists because the per-key one
multiplies: five testers with two slots each is ten concurrent scans, each
unpacking an archive and running several analyzers. A caller who is over their
own share is told that rather than told the service is busy — the two have
different answers, one being "wait for your own scan" and the other "wait for
somebody else's".

`--no-expiry` mints a permanent key. It exists for something like a build
server, and it is a deliberate exception rather than the default.

Expired, revoked and unknown keys all get the same `401` message. Distinguishing
them would confirm to a stranger that a key they guessed had once existed.

By default the counts are held in memory, which is the honest scope of one
process: they do not survive a restart and are not shared, so two instances
behind one address would each grant the full budget. That is fine for a
single-process pilot. For more than one process, choose the file limiter.

### Sharing the limits between processes: `--limiter file`

```bash
arbiter api serve --cert cert.pem --key key.pem --limiter file
arbiter mcp --http --root /srv/work --cert cert.pem --key key.pem --limiter file
```

`--limiter file` counts in a SQLite file, `limiter.db`, beside the key file
(`~/.arbiter/limiter.db`, or next to whatever `--keys` / `$ARBITER_KEYS`
names). Every process serving the same keys then draws on one budget: uvicorn
workers, two `arbiter api serve` instances behind one proxy, or the API and the
MCP door side by side. The same four caps apply with the same `429` answers;
`/v1/health` publishes them as before.

Each decision — "has this key a request left this hour", "is there a slot" —
is one `BEGIN IMMEDIATE` transaction, so SQLite hands the write lock to one
process at a time and two workers cannot both see one slot left and both take
it. A slot is a row stamped with the time it was taken and deleted when the
scan ends, including when it fails. A process killed mid-scan cannot delete its
row, so a row older than the scan ceiling plus a minute
(`service.DEFAULT_TIMEOUT`, 900 s) is treated as released by whichever decision
comes next; a crash costs one slot for at most that long. The hourly counts
survive a restart, which the memory limiter's do not.

`memory` stays the default because it needs no file and because one process is
the common case. The two never mix: a server started with `--limiter memory`
beside one started with `--limiter file` is two budgets again, which is why
the choice is a start-up flag rather than something a request can change.
Startup prints the path of the file in use. The file is per deployment, not
per key, and holds only key ids and timestamps — nothing a caller sent.

### One line per request, about the caller and not their code

Keeping none of a customer's source is the promise. Being unable to say who
called, when, and how it ended is a separate thing and not worth having: it is
what answers a leaked key, a disputed bill, or "did you run anything for us last
Tuesday". So each request appends one JSON line to `~/.arbiter/audit.log`, or
wherever `--audit` or `ARBITER_AUDIT` points:

```json
{"bytes_in": 41233, "event": "scan", "key": "4a0df464a689", "ms": 8142,
 "status": 200, "ts": "2026-09-13T06:40:11Z", "user": "dana@acme.example"}
```

Seven fields, and that is the whole record: no file name, no finding, no
evidence snippet, no fragment of the archive. A log that quoted findings would
rebuild on disk, permanently, exactly what the request path takes care to
delete. Failures are recorded too — a log holding only successes cannot show
somebody hammering the service — and a refused key is logged as `auth_failed`
with no key written down, because a rejected key is still somebody's near-miss
secret.

The file is created owner-read-only where the platform honours that, which means
POSIX; on Windows `chmod` only toggles the read-only attribute and the file
stays world-readable, so a deployment there has to restrict the directory
itself. If the disk is full or read-only the write fails loudly on stderr and
the scan still runs: a broken log should not become a failed request. `--no-audit` turns it off entirely, which means giving up the
ability to answer what ran for whom.

## How a person uses it: `arbiter remote`

The endpoints below are what the service exposes. Almost nobody should have to
touch them directly. The client is the way in:

```
pip install arbiter-eval          # PyYAML and nothing else
export ARBITER_SERVER=https://arbiter.example.com
export ARBITER_API_KEY=arb_...    # issued by hand, sent to you out of band

arbiter remote health             # what this server is, and what it allows
arbiter remote scan .             # the same output as a local scan
arbiter remote gate .             # exits non-zero when policy fails
```

Settings come from flags first, then `ARBITER_SERVER` and `ARBITER_API_KEY`,
then `~/.arbiter/client.json` — so a one-off `--server` never loses to a stale
file.

What makes this worth having is what the caller did not install. `arbiter remote
scan` sends the directory and renders the answer through the same functions
`arbiter scan` uses, so `--out`, `--format` and `--limit` mean what they always
meant and the console output is the same text. None of the five analyzers, the
grammar pack or the rule engine is on that machine. The transport is `urllib`
from the standard library rather than `requests` or `httpx`, because a thin
client that drags in a dependency tree is not a thin client.

Three refusals happen on the caller's side, before anything is sent:

- **`http://` is refused outright.** The server refuses plaintext too, but its
  refusal arrives after the key has already crossed the network in the clear.
- **Certificate verification cannot be switched off.** `--cacert` adds a root to
  trust, for the self-signed case below; nothing subtracts one.
- **An oversized target is refused locally.** `/v1/health` publishes the
  100 MiB cap, so uploading for two minutes to earn a 413 is avoidable.

The upload also leaves behind what the scanner would never have read — `.git`,
`node_modules`, `.venv` and the rest of `inventory.SKIP_DIRS`. That keeps
uploads small, but the reason it matters is custody: source that never left is
source nobody has to be trusted with.

`arbiter remote gate` takes no `--out` or `--format`, because `/v1/gate` answers
with the verdict and the claim ledger rather than a report. And there is no
`arbiter remote` command that records an adjudication verdict, because there is
no endpoint that does — see below.

## The endpoints

| Endpoint | Takes | Returns |
|---|---|---|
| `POST /v1/scan` | an uploaded archive | the report |
| `POST /v1/gate` | an uploaded archive | pass or fail, with the claim ledger |
| `POST /v1/review-queue` | a report the caller already has | a queue with every mark blank, or an empty one when there is nothing left to ask about |
| `GET /v1/health` | nothing; no key needed | version, and that it retains nothing |

Everything but `/v1/health` needs an `X-API-Key` header. Uploads are capped at
`MAX_UPLOAD_BYTES` (100 MB) and refused before anything is written.

## TLS, with no plaintext mode

Every request carries an API key in a header and a copy of somebody's source in
the body. Over plaintext both are readable by anything on the path, and the key
is replayable forever. So plaintext is not offered as a degraded mode: the
server refuses to start without TLS, and refuses individual requests that arrive
over it anyway with `426 Upgrade Required`.

There is one arrangement, and nothing else starts:

```
arbiter api serve --cert fullchain.pem --key privkey.pem --host 0.0.0.0
```

A proxy may still sit in front — and for a pilot it should, since it can get a
real certificate and carry a body limit — but it does not remove that
requirement. The hop from the proxy to this process is a socket too, so it
carries its own certificate and the proxy verifies it. There is no flag that
binds a plaintext port and takes `X-Forwarded-Proto` as proof the request was
secure somewhere upstream; that header is not consulted anywhere in this
project. A header is not a transport, and a loopback listener is still a
listener.

The offered ciphers are restricted to forward-secret AEAD suites, which leaves
nothing a TLS 1.0 or 1.1 client can negotiate in practice. Note the limit
precisely: uvicorn builds its own SSL context and exposes no minimum-version
setting, so a hard version floor cannot be asserted from inside this module — it
comes from the platform's OpenSSL policy, or from a proxy in front, where it is
configurable.

Every response carries `Strict-Transport-Security` for two years including
subdomains, so a client that once reached us over TLS will not try plaintext
afterwards. The default port is 8443.

Certificate issuance and renewal are the operator's, not this module's.

### If you have no certificate

Not having one is not a reason to fall back to plaintext, and there is no flag
that does. Three ways to get a certificate, in the order they are worth trying:

**Let something else obtain it for you.** A reverse proxy that handles ACME —
Caddy is a single binary and needs a two-line config — gets a real certificate
from Let's Encrypt for the public name and renews it on its own. Arbiter behind
it still holds a certificate of its own for the loopback hop, but that one never
faces a caller, so a self-signed one generated once is enough and the proxy is
told to trust exactly it. `deploy/` does this: see
[pilot-runbook.md](pilot-runbook.md). This needs a domain name pointing at the
machine and inbound port 80 and 443.

**A tunnel, if the machine has no public address.** `cloudflared tunnel` (or
Tailscale Funnel) terminates TLS at the provider's edge on a hostname they
issue, and forwards to loopback — over HTTPS to Arbiter's own certificate, which
for `cloudflared` means an `originRequest` with `caPool` set and `noTLSVerify`
left alone. The trade is that the provider terminates TLS, so they can see the
traffic —
which is a custody question, not just a convenience one, and belongs in the
answer to [Custody](#custody-is-the-real-gate).

**Self-signed, for a pilot with people you can talk to.** One command, valid a
year, with the hostname in a subject alternative name so clients accept it:

```
openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
  -keyout privkey.pem -out fullchain.pem \
  -subj "/CN=arbiter.internal" \
  -addext "subjectAltName=DNS:arbiter.internal,IP:127.0.0.1"

arbiter api serve --cert fullchain.pem --key privkey.pem
```

In Git Bash on Windows, prefix that with `MSYS_NO_PATHCONV=1`. Without it the
shell rewrites `/CN=arbiter.internal` into a filesystem path and `openssl`
refuses the subject. Pass the certificate paths to `arbiter` in Windows form
(`C:\...`), not as `/tmp/...`, which Python does not resolve.

The encryption is real; what is missing is any proof of who is on the other
end. No browser or client trusts it by default, so each recipient must be given
`fullchain.pem` out of band and point at it explicitly — `curl --cacert
fullchain.pem https://...`. If anyone reaches for `curl -k` or
`verify=False`, the authentication is gone and a machine on the path can
impersonate the server and collect the API keys. Do not hand a self-signed
certificate to a customer for that reason; keep it for your own testing and for
a pilot you can walk somebody through.

Whichever route, `privkey.pem` is a credential: owner-read-only, never in the
repository, and rotated if it is ever copied anywhere.

Source arrives as an upload and only as an upload. The server does not clone
from a caller's repository, because that would mean holding credentials to their
source — a far larger thing to ask for, and to protect, than a tarball held for
the length of one request.

## What is built

- `service.py`: `Workspace`, `extract_archive`, `resolve_within`,
  `check_profile`, `scan`, `gate`, `review_queue`.
- `api.py`: key issuance and verification, per-key and whole-server limits
  (in memory, or shared between processes through SQLite with
  `--limiter file`), the request log, the three request handlers, and a
  FastAPI application built only when actually serving.
- `mcp.py`: tool schemas and dispatch over the same service layer.
- `client.py`: the caller's half — settings resolution, the plaintext and
  certificate refusals, the archive builder that skips `SKIP_DIRS`, and one
  function per endpoint. Standard-library `urllib`, no new dependency.
- `arbiter api serve` and `arbiter api key add|list|revoke`; `arbiter mcp`;
  `arbiter remote scan|gate|review-queue|health`.
- Tests covering the refusals, the workspace lifecycle, archive ingest, key
  handling, keeping nothing, and the absence of any verdict-recording operation
  — on the server and in the client alike.

FastAPI and uvicorn are an optional extra (`pip install 'arbiter-eval[api]'`),
imported only inside `create_app`, so a plain install still depends on PyYAML
alone and the whole module stays testable without them.

## What is not built

- Certificate issuance and renewal, which are the operator's job. TLS itself is
  enforced — see above.
- Any deployment. Nothing here has been exposed to a network, and
  [pilot-runbook.md](pilot-runbook.md) is the arrangement to stand up when it
  is.
- A body limit before authentication. An unauthenticated upload is read by the
  framework before the key is checked, so a `401` still costs bandwidth and
  memory. The fix belongs at the proxy, which is one of the reasons to put one
  in front.
- Terms that counsel has seen. [pilot-terms.md](pilot-terms.md) is a draft
  describing what the code does, not an agreement.
- Whether a customer's own human adjudications should feed calibration. Open by
  decision, not oversight — see above. If it is ever answered yes, what travels
  is the verdict and the rule id, never the fingerprint.
