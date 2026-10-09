# Adapter: Generic LLM (chat-only, no tools)

Applies to any interface where the model cannot read the filesystem, cannot
run `omni doctor` or `omni sync` itself, and only sees what a human or a
calling script pastes into the conversation. `.ai/entrypoints/universal.md`
is the entrypoint text for this case; this file is the operational caveats
that entrypoint doesn't have room for.

## What changes versus a tool-using session

- Nothing here is discoverable by the model on its own. Paste
  `.ai/context-brief.md` and `.ai/project-context.md` before the first
  question; without them the model has no way to know a `REQ-###` registry
  or a fallback contract exists at all.
- `.ai/context-manifest.json`'s `required_read_order` and `context_profiles`
  are written for a session that can open files by path. Over a paste-only
  channel, resolve the smallest applicable profile yourself and paste only
  those files — pasting `deep_policy` in full into a chat box is usually
  most of the conversation's context budget gone before the actual question.
- The model cannot allocate a `REQ-###` id with `python omni requirement add`
  and cannot check `.ai/requirements/requirements.json` for collisions. If
  work needs an id, assign it yourself before the conversation starts, or
  treat anything the model proposes as a draft that still needs registering.
- It cannot run `omni doctor` or the test suite. Any "validation performed"
  claim from a generic-LLM session is a claim about what *should* pass, not
  a report of what did. Say so explicitly rather than letting the fallback
  contract's output-format rules imply otherwise.
- There is no `.ai/.ignore` enforcement. Do not paste `.env`, credentials, or
  full file trees into a chat-only interface; the model has no mechanism to
  decline what it's already been given.

## What doesn't change

The fallback contract (`.ai/entrypoints/fallback-contract.md`) still applies
in full — minimum access scope, one change at a time, no completion claims
without stated validation. A chat-only channel makes those rules harder to
enforce mechanically, not optional.
