# Multi-Repository Systems

A single repository is a system of one. Point Arbiter at a manifest to evaluate
several together and get findings on the seams between them — the defects that
exist in no repository individually and that per-repo tooling cannot see.

## The manifest

```yaml
system: platform
repos:
  - id: infra
    path: ./infra
    role: infrastructure
  - id: app
    path: ./app
    role: application

shared_constants:
  - name: VECTOR_DIMENSION       # a stated contract: disagreement is high, not medium
```

```bash
arbiter scan --system arbiter-system.yaml
```

Declaring a constant under `shared_constants` raises disagreement on it from
medium to high. The manifest is the only place the system can state that two
repositories are *supposed* to agree about something.

## The six seam checks

| Check | Catches |
|---|---|
| `constant-disagreement` | The same named constant with different values in two repos. High when the manifest declares it a shared contract, medium otherwise. |
| `env-var-never-provided` | The application reads a variable that nothing in the system sets. |
| `env-var-provided-but-unused` | Infrastructure provisions a variable no code reads — usually a removed feature. |
| `permission-not-granted` | The application calls an AWS service that no IAM policy in the system grants. |
| `permission-unused` | A granted service with no call site: unused privilege. |
| `port-not-exposed` | The application targets a port no infrastructure resource exposes. |

Every cross-repo finding cites spans in **both** repositories, so the report
shows the disagreement rather than asserting it:

```text
high  app:src/embeddings.py:5   `VECTOR_DIMENSION` has different values in different repositories
                                infra=512, app=1024
high  app:src/embeddings.py:9   `OPENSEARCH_ENDPOINT` is read at runtime but nothing provides it
high  app:src/embeddings.py:11  Application calls `bedrock` but no IAM policy in the system grants it
med   app:src/embeddings.py:14  Application targets port 9200, which no infrastructure resource exposes
low   infra:iam:dynamodb        IAM grants `dynamodb` but no application code calls it
low   infra:stack.py:12         `LEGACY_FEATURE_FLAG` is provisioned but nothing reads it
```

## A stated limitation

The permission checks see SDK call sites. An application that reaches a service
over raw HTTP therefore reads as an unused grant. That limitation is why those
findings carry low confidence — the check is useful and is not authoritative,
and the confidence encodes the difference rather than a footnote nobody reads.

## Inside one repository

The same idea applied within a single repository — an OpenAPI document versus
the routes actually registered, migrations versus the model the code queries —
is the `contract` probe. See
[probes.md](probes.md#contracts-between-artifacts).
