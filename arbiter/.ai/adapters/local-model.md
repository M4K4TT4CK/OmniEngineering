# Adapter: Local Model

Applies to a self-hosted model (Ollama, llama.cpp, LM Studio, or similar)
reached over a local or private endpoint rather than a hosted provider API.
Builds on `generic-llm.md` — assume no filesystem access there too unless
the harness running the model explicitly gives it one.

## Context window

Assume the window is smaller than a hosted frontier model's, and assume it
is shared with the model's own reasoning, not just the prompt. Use the
`minimum` context profile in `.ai/context-manifest.json` unless the specific
model's context length is known and measured against the profile's actual
token count. Never start from `deep_policy` on a local model without
checking first — `.ai/rules/universal-engineering-ruleset.json` alone is
large enough to leave little room for the task.

## Instruction-following

Treat every rule in the loaded rulepacks as something that needs restating
in the immediate task instruction, not something that will be reliably
recalled from a system prompt several turns back. Smaller local models
drift off a long standing instruction set faster than hosted models do.
Concretely: repeat the specific rule that matters for the current step (for
example, the minimum-access-scope line from the fallback contract) in the
same message as the task, rather than trusting it survived from context
loaded earlier in the session.

## What not to assume

- No prompt caching. Every message that repeats `.ai/` content costs full
  tokens again; keep repeated context short and prefer referencing a rule by
  id over re-pasting its full text.
- No guaranteed JSON mode. If a rulepack or `requirements.json` needs to be
  read, expect to parse looser output, or pre-summarize the file rather than
  pasting raw JSON and expecting structured compliance back.
- No guaranteed long-context recall. Verification claims from a local-model
  session should be treated as lower-confidence than from a hosted model
  with a larger, better-attended window, even when both follow the same
  fallback contract.
