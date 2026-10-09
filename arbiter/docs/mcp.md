# The MCP surface

Arbiter as tools an agent can call, over two transports: **stdio** for one agent
on one machine, and **HTTPS** for several people sharing one. Tracked as
**ARB-010**.

This is one of two front doors over `service.py`, which holds the containment
rules. The other is [the hosted API](hosted-api.md). Nothing about where output
may go, whether the network is reachable, or what a caller may not do is decided
in the MCP layer — that would mean deciding it twice.

## Contents

- [Which surface you actually want](#which-surface-you-actually-want)
- [The tools](#the-tools)
- [The tool that does not exist](#the-tool-that-does-not-exist)
- [stdio: one agent, one machine](#stdio-one-agent-one-machine)
- [HTTPS: more than one caller](#https-more-than-one-caller)
- [Paths are the hard part](#paths-are-the-hard-part)
- [Running it](#running-it)
- [What is built](#what-is-built)
- [What is not built](#what-is-not-built)

---

## Which surface you actually want

Three things can reach Arbiter, and they are not interchangeable:

| You want | Use | Source lives |
|---|---|---|
| An agent to scan code on the machine it runs on | `arbiter mcp` (stdio) | that machine |
| Several people's agents to share one build machine | `arbiter mcp --http` | that machine |
| To scan without installing Arbiter at all | [`arbiter remote`](hosted-api.md) | uploaded, then deleted |

MCP relocates the installation rather than removing it. Both transports read
paths on the server's own filesystem, so the source has to already be there.
Removing the installation is what the hosted API is for; MCP remains the right
surface for source that must not leave the machine it sits on.

## The tools

Four, and their schemas are plain data in `mcp.TOOLS` so a test can assert on
them without the SDK installed.

| Tool | Takes | Returns |
|---|---|---|
| `arbiter_scan` | `target`, `output_dir`, `profile`, `only`, `skip` | the full report JSON |
| `arbiter_gate` | `target`, `output_dir`, `profile`, `only`, `skip` | pass or fail, with the claim ledger |
| `arbiter_review_queue` | `report_path`, `output_dir`, `limit`, `rule` | a queue with every mark blank |
| `arbiter_review_draft` | `report_path`, `output_dir`, `verdicts`, `limit`, `rule` | `review-draft.md`: the same queue with the proposed marks and a reason under each; counts, the unknown ids, and the ledger entries the `y` marks would draft, as text. Records nothing |

`verdicts` is a list of `{"id": "f:…", "mark": "y" | "n" | "?", "reason": "…"}`.
A `y` or `n` without a reason is refused: the reason is what the person reads.
An id that is not in the queue is reported back, not raised, and gets no mark.
The draft takes no path argument the queue does not already take, so it is
confined over HTTPS exactly as the others are and writes only under
`output_dir`.

`profile` is restricted to `offline` and `ci` on both scanning tools. Both run
with the network off. An agent cannot ask for the network by naming a profile,
and `service.check_profile` refuses one that would.

## The tool that does not exist

There is no tool that records a verdict, here or in `service.py`, and there must
not be one. `learn.record()` refuses to re-adjudicate a fingerprint, so a mark is
permanent. An agent marking findings in a loop would fill the calibration ledger
with the model's opinion of the model's own output — and the ledger's whole value
is that it is the one signal in the system the system did not generate.

`arbiter review --apply` is deliberately outside this surface. The server draws
queues. A person marks them.

### Why drafting is allowed and applying is not

`arbiter_review_draft` goes one step further than the queue and no further. It
writes the queue with an assistant's marks filled in and a reason beneath each,
in the exact format `arbiter review --apply` reads, as a file whose first line
says every mark was proposed by an assistant and nothing has been recorded. That
is a file a person reads. It has no permanence: the knowledge file is read and
never saved, no failure ledger is opened — the entries the `y` marks *would*
draft come back as text — and the draft is not consulted by anything until a
person runs `arbiter review --apply review-draft.md --reviewer <name>`, having
read it. The reviewer named on that command answers for every mark it records.

Applying is different in kind, not degree. The moment a tool could record, the
ledger would hold the model's opinion of the model's output, permanently,
because `learn.record()` refuses re-adjudication. So the line sits between a
file and the ledger, and only the CLI, in a person's hands, crosses it. Over
HTTPS the draft lands under the caller's confined directory like any other
output, so the tool is as safe there as `arbiter_review_queue`.

## stdio: one agent, one machine

```bash
arbiter mcp
```

No key, and none is wanted. The server is a subprocess of the agent that started
it, on a machine where that agent already has whatever privileges the process
has. Asking it for a key would be theatre, and confining its paths would stop it
doing the job it was started for.

## HTTPS: more than one caller

```bash
arbiter mcp --http --root /srv/arbiter/work \
  --cert /etc/ssl/arbiter.pem --key /etc/ssl/arbiter.key
```

Three things change, and none of them is new policy — they are `api.py`'s rules,
reached from here, so a key means the same thing whichever door it arrives at.

**Every call carries a key.** The same file `arbiter api key add` writes and
`/v1/scan` reads. Send it as `Authorization: Bearer arb_…` (what MCP clients do)
or `X-API-Key: arb_…` (what everything else that talks to Arbiter does); both are
accepted. Unknown, revoked and expired all get the same sentence, because saying
which would confirm that a guessed key once existed. Revoking a key stops it on
the next request, on both surfaces at once.

**The limiter and the audit line apply per key.** The resolved caller reaches
`dispatch`, which takes the same per-key concurrency slot `/v1/scan` takes and
writes the same audit line — `mcp_scan`, `mcp_gate`, `mcp_review_queue`,
`mcp_review_draft`, with the key id, the user, the status and the duration. So a key's budget is one budget
rather than one per front door. The line holds no path, no finding and no
fragment of anybody's code.

**There is no plaintext mode, and no plaintext port.** This process holds the
certificate, including when a proxy sits in front of it — the hop from that proxy
is a socket too. Without `--cert` and `--key` it fails to start, a request
arriving over plaintext is refused with `426`, and every response carries HSTS.
`X-Forwarded-Proto` is not consulted, here or in the API.

### One flag the proxy arrangement needs

The MCP transport checks the `Host` header as its defence against DNS rebinding,
and allows loopback names until told otherwise. A proxy forwards the *public*
hostname, which is not one of them, so every real request comes back
`421 Invalid Host header`. Name the hostname:

```bash
arbiter mcp --http --root /srv/arbiter/work \
  --cert /etc/ssl/arbiter.pem --key /etc/ssl/arbiter.key \
  --allowed-host arbiter.example.com
```

Nothing can detect that arrangement from inside — a forwarded request looks like
any other — so a wall of `421`s is what a missing `--allowed-host` looks like.
That is the first thing to check if every call fails and none of them reaches the
audit log.

## Paths are the hard part

`target` is read and `output_dir` is written, both on the machine running the
server. For a local agent that is the entire point. For a remote caller it would
be an arbitrary file read with a scanner attached: `target: "/etc"` comes back as
findings quoting what is in there, and the report names files and what kind of
credential sits in them.

So the HTTPS transport **refuses to start without `--root`**, and rewrites every
path argument to sit beneath `root/<key id>`:

- A relative path is joined onto that directory, so the natural thing to send —
  `"target": "myrepo"` — is also the correct one.
- An absolute path outside it, or one climbing out with `..`, is refused by
  `service.resolve_within`, which resolves symlinks before comparing so a link
  pointing out of the sandbox is caught rather than followed.
- One directory **per key**, so confinement separates callers from each other
  rather than putting everyone in one shared box.

The arguments treated this way are listed in `mcp.PATH_ARGUMENTS` rather than
guessed at from their values, and a test fails if a tool grows a path argument
that is not in that list — because that argument would be unconfined, and
silently.

What this buys is a shared build machine several people can point an agent at.
What it does not buy is scanning source that is not already on that machine. For
that the source has to travel, which is the hosted API and `arbiter remote`.

## Running it

```bash
pip install 'arbiter-eval[mcp]'        # brings the SDK, starlette and uvicorn

arbiter mcp                            # stdio, one local agent, no key
arbiter api key add --user "dana"      # issue access by hand, one key per user
arbiter mcp --http --root /srv/arbiter/work --cert cert.pem --key key.pem
```

| Flag | Means |
|---|---|
| `--http` | serve over HTTPS instead of stdio; every call needs a key |
| `--root` | the directory every caller's paths stay inside; required with `--http` |
| `--keys` | key file (default `~/.arbiter/keys.json`, or `$ARBITER_KEYS`) |
| `--host`, `--port` | where to bind; loopback and 8444 by default |
| `--path` | URL path for the endpoint; `/mcp` by default |
| `--allowed-host` | hostname callers reach this server by; repeatable, needed when a proxy forwards a public name |
| `--cert`, `--key` | TLS certificate and private key; required, including behind a proxy |
| `--audit`, `--no-audit` | where request lines go, or not keeping them |
| `--limiter` | `memory` (default, one process) or `file`: the caps counted in `limiter.db` beside the key file, shared by every process serving the same keys — see [hosted-api.md](hosted-api.md#sharing-the-limits-between-processes---limiter-file) |

The SDK is pinned at `mcp>=2.2`. That is a hard floor rather than caution: the
server API changed shape there — handlers became constructor arguments instead of
decorators — so a 1.x install fails at startup rather than degrading.

Sessions are **stateless**: nothing is kept between requests, so there is no
session store holding one caller's state for another to resume, and nothing to
expire. It matches what the rest of the service promises.

## What is built

- `mcp.py`: the tool schemas, `dispatch`, `confine`, the stdio transport and the
  HTTPS one, with `build_http_app` separated from `serve_http` so the whole
  request path can be tested without binding a socket or holding a certificate.
- `arbiter mcp` and `arbiter mcp --http`.
- `arbiter_review_draft` (ARB-050): `service.review_draft` draws the queue
  `review_queue` draws, fills in proposed marks and reasons, and writes
  `review-draft.md` under `output_dir`; `ledger.preview_entries` renders what
  the `y` marks would draft without writing a ledger.
- Tests covering both transports: the key refusals, revocation reaching both
  doors, plaintext, HSTS, the per-key audit line, path confinement including
  between two callers, and the continued absence of any verdict-recording tool —
  extended in `tests/test_review_draft.py` to every tool in `TOOLS`, each of
  which must leave the knowledge file byte-identical.

## What is not built

- Any OAuth flow. Keys are issued by hand, which is the same model the hosted
  API uses and the same reason: there is no identity system here to do it
  another way.
- Uploads over MCP. A caller can only name paths already on the server; source
  that has to travel goes through [the hosted API](hosted-api.md).
- Any deployment. Nothing here has been exposed to a network.
