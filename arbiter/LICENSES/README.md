# License Guide

This repository separates the OmniEngineering workspace license from the license
of user projects that adopt the workspace.

This guide is not legal advice.

## OmniEngineering License

OmniEngineering itself is licensed under the Apache License, Version 2.0. That
license allows broad use, copying, modification, distribution, sublicensing,
private use, commercial use, and forked development, subject to its conditions.

The Apache License does not grant trademark rights. Project identity is handled
by `TRADEMARKS.md`.

Use these files together:

- `LICENSE`: Apache License, Version 2.0.
- `NOTICE`: attribution and community return request.
- `TRADEMARKS.md`: name, logo, and anti-confusion rules.
- `CONTRIBUTING.md`: contribution expectations.

## User Project License

Projects built with OmniEngineering remain the user's projects. OmniEngineering
does not claim ownership of a user's source code, product, data, requirements,
architecture, docs, or generated implementation.

Each adopter should choose a license for their own project. Use
`USER-PROJECT-LICENSE-TEMPLATE.md` as a starting point.

## Recommended Pattern

For this repository:

- Keep `LICENSE`, `NOTICE`, `TRADEMARKS.md`, and `CONTRIBUTING.md`.
- Keep OmniEngineering identity on official distributions.
- Rename modified public forks unless they are clearly presented as forks.

For downstream projects:

- Add the downstream project's own `LICENSE`.
- Keep OmniEngineering attribution if OmniEngineering files are copied or
  adapted.
- State that the downstream project's code and content are licensed separately.
- Do not imply official OmniEngineering endorsement.

## Why Not A Custom Restrictive Open-Source License?

The project goal is broad adoption. Standard open-source licenses support that
better than custom copyright restrictions. Restrictions against commercial use,
fields of use, or redistribution would conflict with normal open-source
expectations.

The correct protection against repackaging confusion is trademark and identity
policy, not blocking people from using, forking, or modifying the workspace.
