# Running the pilot

> **Status: not yet deployed.** This is the arrangement to stand up for a pilot
> with a handful of named testers, in the order the steps have to happen. It
> assumes the decisions already recorded in [hosted-api.md](hosted-api.md):
> uploads only, nothing retained, TLS only, one key per user.

## Contents

- [Before anybody uploads](#before-anybody-uploads)
- [A box and a name](#a-box-and-a-name)
- [The machine](#the-machine)
- [Standing it up](#standing-it-up)
- [Issuing keys](#issuing-keys)
- [What to send each tester](#what-to-send-each-tester)
- [While it runs](#while-it-runs)
- [Shutting it down](#shutting-it-down)

---

## Before anybody uploads

Every tester gets [pilot-terms.md](pilot-terms.md) with their key — approved for
the pilot on 2026-09-13 and sent as written. Get a reply saying they have read
it; an email is enough here, and keep it. Testers are uploading their employer's
code, and most of them will need something to point at when somebody asks why
that was allowed.

It has not been through counsel, so it covers a pilot and nothing more. Anything
that starts to look like a customer rather than a tester needs the reviewed
version first.

## A box and a name

**Where it runs is a custody decision before it is a cost one.** A tester
uploading their employer's code may have to say which country it was processed
in, so pick the region first and the price second. If the testers are European,
host in the EU; if they are American and their counsel asks, host in the US.
Either way, write the answer down before somebody asks.

For a pilot: **a single small Linux VM, 2 vCPU and 4 GB, Ubuntu LTS.** Hetzner
(CX22, around €4 a month, EU or US regions) is the cheapest sensible option;
DigitalOcean and Lightsail cost several times that for the same shape and are
easier if you already have an account there. Do not use a shared-tenancy
platform-as-a-service for this — the scan needs real CPU for several minutes,
and most of them will kill it.

Open ports 22, 80 and 443, and nothing else. Port 80 is not optional: Let's
Encrypt's HTTP challenge uses it, and Caddy redirects from it.

**The name.** Buy a domain anywhere — Cloudflare Registrar sells at cost,
Porkbun and Namecheap are fine — and add one record:

| Type | Name | Value | TTL |
|---|---|---|---|
| A | `arbiter` | the server's IPv4 | 300 |
| AAAA | `arbiter` | the server's IPv6, if it has one | 300 |

A short TTL while you are setting up means a mistake costs five minutes rather
than a day. Check it with `dig +short arbiter.example.com` from somewhere other
than the server, and wait until it answers before starting Caddy — a
certificate request against a name that does not resolve yet burns a Let's
Encrypt rate limit.

**If the DNS is on Cloudflare, leave the record grey-clouded (DNS only).** The
orange cloud proxies the traffic, which means Cloudflare terminates TLS and can
see the uploads — a custody change your testers were not told about — and the
free plan caps request bodies at 100 MB, which is exactly the size of upload
Arbiter accepts, so large archives would fail in a way that looks like our bug.

## The machine

Run the server as an unprivileged user in a container, with a memory limit. The
scanned repository is never executed — no build, no test, no hook, no entry
point from the upload runs at any stage — but the analyzers still parse
attacker-chosen files in a subprocess with a 900-second timeout and no memory
limit of their own. A parser bug on a pathological file is the plausible way to
lose the box, and a container with a cap turns that into one failed request.

Sizing for the defaults: four concurrent scans, each unpacking an archive of up
to 100 MB and running several analyzers. Two cores and 4 GB is enough for a
pilot; disk needs room for four extracted trees at once, which is far more than
the archives themselves.

## Standing it up

`deploy/` holds the whole arrangement: Caddy terminates TLS and Arbiter never
sees the network. On the server, with Docker installed and the DNS record
already resolving:

```
git clone <this repository> arbiter && cd arbiter
$EDITOR deploy/Caddyfile          # hostname, and an email address you read
docker compose -f deploy/compose.yaml up -d --build
docker compose -f deploy/compose.yaml logs -f caddy   # watch the certificate arrive
```

Caddy fetches the certificate on first start and renews it thereafter; there is
no certificate handling in Arbiter at all. The proxy also carries the request
body limit, which matters because FastAPI reads an upload before the API key is
checked — without a ceiling upstream, an unauthenticated stranger can make the
server swallow 100 MB before it answers `401`.

Two details in `deploy/compose.yaml` are load-bearing rather than incidental:

- **The hop from Caddy to Arbiter is HTTPS too.** Nothing in this arrangement
  speaks plaintext, including inside the machine. A one-shot `certs` service
  generates a self-signed certificate for `arbiter.internal` the first time you
  bring the stack up; Arbiter serves with it, and Caddy verifies against that
  one certificate rather than skipping the check. It is never seen by a caller,
  so it needs no CA and no renewal — delete the `arbiter-certs` volume to
  replace it. Arbiter also shares Caddy's network namespace, so only Caddy can
  reach port 8443; that is a second layer, not the thing making the hop private.
- **The container is unprivileged, read-only, capped and capability-free.**
  Nothing from an upload is executed, but the analyzers parse attacker-chosen
  files, and a parser bug on a pathological one is the plausible way to lose the
  machine. Memory, CPU and process count are bounded so that becomes one failed
  request.

Keep the image private. Running semgrep server-side conveys no copy of it, which
is why hosting is less encumbered than the air-gapped bundle; pushing the image
to a public registry would convey copies and put L-6's obligations back. See
[licensing.md](licensing.md).

If the machine has no public address, a tunnel (`cloudflared`, Tailscale Funnel)
terminates TLS at the provider's edge and forwards to loopback the same way —
to Arbiter's own certificate, which for `cloudflared` means an `originRequest`
with `caPool` pointing at `backend.pem` and `noTLSVerify` left alone. The
provider then sees the traffic, which is a custody change your testers were not
told about — so either tell them or do not do it.

Do not hand a self-signed certificate to a tester. It works, but the first trust
error teaches them `curl -k`, and at that point anything on the path can
impersonate the service and collect their key.

## Issuing keys

One key per person, named for the person, minted inside the running container so
it lands in the same key file the service reads:

```
docker compose -f deploy/compose.yaml exec arbiter \
    arbiter api key add --user "dana@acme.example"
docker compose -f deploy/compose.yaml exec arbiter arbiter api key list
```

The key is printed once. Send it over something that expires — a password
manager share, or a message that self-deletes — not a ticket or a mailing list.
Only its SHA-256 hash is stored, so losing the key file means reissuing rather
than a breach.

Minting a second key for somebody who already holds a live one is refused. To
rotate, `--replace` issues the new key and revokes the old one in the same
command. If a key might have leaked, revoke first and ask questions after:

```
docker compose -f deploy/compose.yaml exec arbiter arbiter api key revoke 4a0df464a689
```

Revocation takes effect on the next request. Anything already running finishes.

## What to send each tester

- The URL, and that every call needs `X-API-Key`.
- Their key, over a channel that expires.
- [pilot-terms.md](pilot-terms.md).
- That it is free and there is nothing to apply for: scan whatever they like,
  as often as they like, without telling anybody what is in it.
- The limits, so a `429` is not a support ticket: 120 requests an hour, two
  scans at once, uploads up to 100 MB, keys expire in 90 days. `GET /v1/health`
  lists them, so nobody has to ask. They are there to keep one leaked key or one
  busy afternoon from taking the machine down, not to meter anything.
- That the report they get back names the file each credential sits in and what
  kind it is. The value is never reprinted, but the report is still a map of
  where to look, and where they store it is now their decision.
- That there is no history: the report comes back in the response and nowhere
  else, so they should keep the one they want.

## While it runs

The audit log gets one line per request — key, user, operation, outcome, bytes,
milliseconds, and nothing about their code. In this deployment it is
`/data/audit.log` inside the container, on the `arbiter-data` volume:

```
docker compose -f deploy/compose.yaml exec arbiter tail -f /data/audit.log
```

Outside a container it defaults to `~/.arbiter/audit.log`, or wherever
`--audit` points. It is created owner-read-only on Linux; on Windows that call
only sets the read-only attribute, so restrict the directory instead. Read the
log for the things that are invisible otherwise:

- `auth_failed` lines in any volume mean a key leaked or somebody is probing.
- Repeated `429`s mean the limits are wrong for real use, not that a tester is
  misbehaving. Change them deliberately rather than raising them per complaint.
- `status` 500 is an Arbiter bug, and the line gives you the key and the time to
  ask its owner what they were scanning.

Restarting the server resets the rate-limit counters, since they live in memory.
Running two instances behind one address doubles every per-key limit, so do not,
until the counts move to shared storage.

## Shutting it down

Revoke every key first, then stop the containers. Keys outlive the deployment
otherwise, and a key that still verifies against a service nobody is watching is
the worst of both.

```
docker compose -f deploy/compose.yaml exec arbiter arbiter api key list
docker compose -f deploy/compose.yaml exec arbiter arbiter api key revoke <id>   # each one
docker compose -f deploy/compose.yaml down
```

Copy the audit log off the machine before removing anything — it is the only
record that the pilot happened and the only thing that can answer a later
question about it, and it holds nothing of anybody's source:

```
docker compose -f deploy/compose.yaml cp arbiter:/data/audit.log ./pilot-audit.log
```

`docker compose down -v` additionally destroys the volumes, which takes the keys,
the audit log and Caddy's certificates with it. That is the right end state once
the log is copied off, and the wrong command to run before.
