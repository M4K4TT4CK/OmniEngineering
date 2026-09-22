# Language tooling routes

Use repository-configured tools first. Add a tool only when it materially tests an identified risk and fits the project's dependency policy.

## JavaScript and TypeScript

- Unit/integration: the configured test runner
- Property testing: `fast-check` when state-space exploration is justified
- Runtime behavior: strict unhandled rejection settings and representative concurrency
- Supply chain: lockfile-aware package audit plus manual review of reachable impact

## Python

- Unit/integration: `pytest`
- Property testing: Hypothesis
- Static/type: Ruff, mypy, or the repository's configured alternatives
- Security/dependencies: Bandit and `pip-audit`, validated against reachability

## Go

- Correctness/races: `go test ./...` and `go test -race ./...`
- Fuzzing: native `go test -fuzz`
- Static/security: `go vet`, Staticcheck, and `govulncheck` when installed

## Rust

- Correctness: `cargo test --all-targets`
- Lint: Clippy with repository-compatible warning policy
- Fuzzing: `cargo-fuzz` when a bounded target exists
- Undefined behavior: Miri where supported
- Supply chain: `cargo audit` or organization-standard tooling

## C and C++

- Compile with project-supported strict warnings
- Memory/undefined behavior: AddressSanitizer and UndefinedBehaviorSanitizer
- Races: ThreadSanitizer where compatible
- Fuzzing: libFuzzer, AFL++, or the repository-standard harness
- Static analysis: compiler analyzer, clang-tidy, or organization-standard tooling

Do not run multiple heavy fuzzers or long benchmarks merely for breadth. State time, corpus, seed, sanitizer, platform, and limitations so results can be reproduced.
