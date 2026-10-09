# Adapter: Model Router

Applies when requests go through a gateway that picks the backend model per
request — by cost, latency, load, or an explicit routing rule — rather than
one fixed model for the whole session. This is the general case; see
`openrouter.md` for that specific router.

## The failure mode this adapter exists to prevent

A router can change which model answers turn N+1 without changing anything
visible in the conversation transcript. Context that was "established" on an
earlier turn — a `REQ-###` id, a stated minimum access scope, which
rulepacks are loaded — may not actually be attended to by whichever model
answers next, even though the text is still there. Treat each turn as a
possible cold start for anything load-bearing:

- Restate the active `REQ-###` id in the task instruction rather than
  relying on it having been "said already."
- Re-read `.ai/project-map.md` before broad traversal if there's any chance
  the session has been running long enough to route across a model change;
  don't assume an earlier turn's read of it still holds.
- Do not assume prompt caching survives a route change — a router that
  switches providers is switching the KV cache along with it, so a "cheap"
  repeated turn may not be cheap.

## Context sizing

Size the context profile to the smallest model the router is configured to
be able to select, not the largest. A profile that fits the router's biggest
possible backend but not its cheapest one will work most of the time and
then silently degrade on whichever turn got routed small.
