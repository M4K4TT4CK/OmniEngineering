# Adapter: OpenRouter

A specific instance of `model-router.md`: one API in front of many
providers' models, each with its own context limit, its own tokenizer, and
its own truncation behavior. The general router caveats apply; this adds
what's specific to OpenRouter.

## Per-model variance is the whole risk

Two OpenRouter model ids can differ by an order of magnitude in context
length, and a request that overflows the selected model's limit does not
always come back as an error — some providers truncate the prompt silently
rather than reject it. That means a context profile that "worked" on one
call can lose the tail of `.ai/` content on the next call, with no signal
that anything was dropped.

- Prefer the `minimum` context profile by default over OpenRouter, and only
  widen it after confirming the specific model id's context length.
- Put anything load-bearing — the active `REQ-###` id, the minimum access
  scope, the fallback contract's numbered rules — early in the prompt, not
  at the end, since truncation (where it happens) drops from the end.
- Don't assume prompt caching. OpenRouter's own caching support is
  per-provider and not guaranteed for every model behind it; budget each
  call as if it pays full price for repeated context.

## Everything else

Once a model id is pinned for the session, `local-model.md`'s guidance on
instruction-following and JSON-mode assumptions applies if the selected
model is a smaller or open-weight one; treat a frontier hosted model behind
OpenRouter like any other hosted session instead.
