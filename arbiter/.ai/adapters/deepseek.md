# Adapter: DeepSeek

Applies to DeepSeek's own API or a provider serving DeepSeek models
directly (as opposed to routed through `openrouter.md`).

## Reasoning and chat are different endpoints

`deepseek-reasoner` returns chain-of-thought in a separate field from the
final answer, and that reasoning content is typically not meant to be fed
back into the next turn as part of the conversation history the way a
normal assistant message is. Don't paste the reasoning trace back into
`.ai/`-scoped instructions expecting it to carry rule compliance forward —
carry forward only the final answer, and restate any rule that needs to
apply to the next step explicitly.

## System-prompt weight

Put the controlling instruction set — the fallback contract, the active
`REQ-###` id, minimum access scope — in the first user turn as well as any
system-role message, not the system role alone. Don't rely on system-prompt
adherence being as strong as it is for a hosted frontier model tuned
specifically for long system instructions; restate rather than assume.

## Context sizing

Follow `local-model.md`'s profile guidance (default to `minimum`, widen only
after confirming the specific model's actual context length) unless running
against a DeepSeek endpoint explicitly documented with a larger window.
