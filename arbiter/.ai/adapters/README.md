# Adapters

An adapter is guidance for a *class* of consuming environment: what it can
and cannot be trusted to do with the `.ai/` scaffold, and what to change
about the loading order in `.ai/context-manifest.json` as a result.

This is a different layer from `.ai/entrypoints/`. An entrypoint is
generated text — `./omni sync` writes it verbatim into a root pointer file
(`CLAUDE.md`, `AGENTS.md`, `.cursorrules`, `.kiro/steering/omnicontext.md`,
and so on) so a specific tool finds routing instructions where it looks by
convention. An adapter is authored text that is never templated into a root
file; it exists to be read once by whoever is setting a session up, human or
assistant, before deciding which context profile and which entrypoint apply.
Several entrypoints can point at the same adapter (any tool without a native
entrypoint uses `.ai/entrypoints/universal.md`, but the operational caveats
for a locally-hosted model and for an OpenRouter-routed model are not the
same, even though both fall back to that one entrypoint).

`.ai/context-manifest.json`'s `adapter_prompts` key records which adapter
applies to which consuming pattern. Read the adapter named there before
assuming `.ai/entrypoints/universal.md` alone is enough.

| Adapter | Use |
| --- | --- |
| `generic-llm.md` | Chat-only interface, no filesystem or tool access; context must be pasted in by hand. |
| `local-model.md` | Self-hosted model, usually small context window and weaker instruction-following. |
| `model-router.md` | Gateway that may route different turns of one session to different backend models. |
| `openrouter.md` | OpenRouter specifically: many providers behind one API, wildly different limits. |
| `deepseek.md` | DeepSeek models specifically: reasoning/chat split, system-prompt handling. |
| `kiro.md` | Kiro's spec-driven workflow, and how it maps onto this repo's `REQ-###` registry. |

None of these replace `.ai/entrypoints/fallback-contract.md`. The fallback
contract is the operating rules; an adapter only tells you what to trust
about the channel it is running over before you apply those rules.
