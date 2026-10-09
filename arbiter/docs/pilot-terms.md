# What happens to your code

> **Approved for the pilot on 2026-09-13. Not legal advice.** This is the plain
> statement to send each tester with their key, and it goes out as written. It
> describes what the software actually does, which is verifiable in
> [hosted-api.md](hosted-api.md) and in the test suite. It has not been through
> counsel — ARB-005 tracks that, along with the L-3 term structure — so it is
> not a contract and must not be presented as a signed agreement. Anything
> beyond a pilot needs the counsel-reviewed version first.

You are about to upload a copy of source code — often your employer's — to a
service somebody else runs. Here is what happens to it.

## What it costs, and what we ask of you

Nothing, and nothing. There is no charge now and no invoice later for what you
run during this period. You do not have to say what you intend to scan, justify
why you want access, or get anything approved. Scan whatever you like — work
repositories, side projects, whatever cloud build you are curious about — as
often as you like.

There are limits on the service, and they are about keeping the machine
standing rather than metering you: 120 requests an hour, two scans at once,
uploads up to 100 MB. `GET /v1/health` lists them. If you hit one, it is a
capacity answer rather than a judgement, and telling us is more useful than
working around it.

What we would like back is what broke, what was wrong, and what was missing. You
are testing this, not buying it.

## What we do with it

You upload an archive. It is unpacked into a temporary directory, scanned, and
the directory is deleted when the request ends, including when the scan fails.
The report comes back in the response.

That is the whole lifecycle. There is no database, no object store, no backup
and no copy kept for debugging. You cannot come back tomorrow for yesterday's
report, because there is nowhere it could have been kept.

## What we keep

One line per request, holding: which key called, which person that key belongs
to, which operation, whether it succeeded, how many bytes you sent, and how long
it took. Nothing about the contents — no file names, no findings, no fragments
of your code.

We keep it so we can answer questions about access later: whether a key was
used after somebody left, how much you actually ran, whether anyone else tried
to use your key.

## What we never ask for

Credentials to your repository. Arbiter scans what you upload and cannot reach
anything else. Giving a scanner access to your source control is a much larger
thing to grant, and to protect, than a tarball held for the length of one
request.

## What we do while scanning

We read files. Nothing from your upload is executed: no build, no test suite, no
git hook, no entry point, no package install. The scan does not reach the
network — the profiles that would allow it are refused on this service.

## About the report you get back

It names the file each credential sits in, and what kind of credential it is. It
never reprints the secret's value; a test enforces that. But it is still a map
of where to look, so treat the report as sensitive: once it is in your hands,
where it goes is your decision, and we have no copy to leak.

## Your key

It belongs to you and only you. It is shown once when it is issued and stored
only as a hash, so nobody — us included — can recover it from the key file.

Do not share it. The limits are counted per key, so two people sharing one get
half each and look like one caller in the record; and if one of you leaves and
we revoke it, the other stops working too. If somebody else needs access, ask
and they get their own.

Tell us immediately if it might have leaked. Revoking takes one command and
issuing a replacement takes one more. Keys expire after 90 days regardless.

## Transport

Every request goes over HTTPS. There is no plaintext mode — the server refuses
to start without TLS and refuses individual plaintext requests. Do not disable
certificate verification in your client: at that point anything on the path can
impersonate the service and collect your key along with your code.

## What is not settled yet

- **Where the machine is, and who else can reach it.** Ask, and we will tell
  you. It is one machine, run by one person, for this pilot.
- **Breach notification.** No formal timetable exists yet. In practice: you will
  hear from us the same day, by name, because there are few enough of you to
  call.
- **Whether any of this survives the pilot.** Nothing here is a commitment to
  keep running the service, and you should not build anything that depends on it
  being there next quarter.

## The honest summary

We hold your code for the length of one request and then it is gone. We keep a
record of who asked, not of what was in it. The riskiest object in this whole
arrangement is the report, and we hand that to you and keep nothing.
