# Gold-standard benchmark

Arbiter `bc3b91f`, 24 repositories, 6085.9 KLOC, 6960 findings (1.144/KLOC), 99 rules fired, **3 over budget** (3 of them accepted breaches).

## Rules by findings per KLOC

Budgets are held against the raw rate. The weighted rate, for information, counts a low-confidence finding 0.5 (the scorecard's low/medium factor).

| rule | findings | /KLOC | weighted /KLOC | budget | in tests | repos | over |
|---|---:|---:|---:|---:|---:|---:|:-:|
| `arbiter/ast.function-too-long` | 1572 | 0.2583 | 0.2583 | 0.5 | 769 | 24 |  |
| `arbiter/ast.high-complexity` | 1185 | 0.1947 | 0.1947 | 0.5 | 159 | 21 |  |
| `arbiter/quality.file-too-long` | 595 | 0.0978 | 0.0978 | 0.5 | 0 | 24 |  |
| `arbiter/drift.broken-doc-link` | 582 | 0.0956 | 0.0956 | 0.1 | 2 | 12 |  |
| `arbiter/supply.unpinned-npm-dep` | 417 | 0.0685 | 0.0685 | 0.1 | 10 | 9 |  |
| `arbiter/java.test-without-assertion` | 199 | 0.0661 | 0.033 | 0.03 | 199 | 5 | accepted |
| `arbiter/ast.deep-nesting` | 377 | 0.0619 | 0.0619 | 0.5 | 34 | 19 |  |
| `arbiter/supply.unpinned-action` | 273 | 0.0449 | 0.0449 | 0.1 | 0 | 14 |  |
| `arbiter/typescript.test-without-assertion` | 22 | 0.0336 | 0.0336 | 0.03 | 22 | 2 | accepted |
| `arbiter/assurance.permanently-skipped-test` | 192 | 0.0315 | 0.0315 | 0.03 | 192 | 11 | accepted |
| `arbiter/python.sql-injection` | 21 | 0.0282 | 0.0282 | 0.03 | 0 | 1 |  |
| `arbiter/assurance.blanket-suppression` | 150 | 0.0246 | 0.0246 | 0.03 | 69 | 11 |  |
| `arbiter/ruby.command-injection` | 17 | 0.0246 | 0.0246 | 0.03 | 0 | 3 |  |
| `arbiter/ruby.rescue-exception` | 17 | 0.0246 | 0.0246 | 0.03 | 0 | 2 |  |
| `arbiter/secrets.assigned-credential` | 142 | 0.0233 | 0.0117 | 0.03 | 105 | 15 |  |

## Accepted breaches

Over budget by a recorded decision; the run fails again if the rate passes the ceiling.

| rule | /KLOC | budget | ceiling | within ceiling | reason |
|---|---:|---:|---:|:-:|---|
| `arbiter/java.test-without-assertion` | 0.0661 | 0.03 | 0.073 | yes | Known breach: 199 findings (0.066/KLOC) after the by-design exclusions in c4d8bfe; the rest are tests that only call the code, which is what the rule exists to report. Narrowing further would drop true findings. |
| `arbiter/typescript.test-without-assertion` | 0.0336 | 0.03 | 0.038 | yes | Accepted overage: 22 findings (0.034/KLOC), all read: 16 only call the code, 6 are compile-time type checks with no generic signal. |
| `arbiter/assurance.permanently-skipped-test` | 0.0315 | 0.03 | 0.035 | yes | Accepted overage: 192 findings (0.0315/KLOC) after 1047341; the remainder are real tests with bodies that are switched off. |

## Repositories

| repo | lang | KLOC | findings | /KLOC | at pin |
|---|---|---:|---:|---:|:-:|
| go-caddy | go | 114.6 | 232 | 2.024 | yes |
| go-containerd | go | 246.1 | 386 | 1.568 | yes |
| go-coredns | go | 105.9 | 305 | 2.88 | yes |
| go-etcd | go | 194.5 | 247 | 1.27 | yes |
| go-grpc | go | 322.2 | 481 | 1.493 | yes |
| java-httpclient | java | 173.7 | 113 | 0.65 | yes |
| java-keycloak | java | 1187.3 | 849 | 0.715 | yes |
| java-netty | java | 582.1 | 487 | 0.837 | yes |
| java-spring-security | java | 546.2 | 238 | 0.436 | yes |
| java-tomcat | java | 645.0 | 842 | 1.305 | yes |
| py-aiohttp | python | 85.0 | 185 | 2.177 | yes |
| py-celery | python | 102.4 | 220 | 2.149 | yes |
| py-cryptography | python | 69.9 | 98 | 1.403 | yes |
| py-django | python | 464.5 | 452 | 0.973 | yes |
| py-urllib3 | python | 29.5 | 36 | 1.222 | yes |
| ruby-puma | ruby | 26.0 | 74 | 2.841 | yes |
| ruby-rack | ruby | 18.8 | 17 | 0.904 | yes |
| ruby-rails | ruby | 499.8 | 599 | 1.198 | yes |
| ruby-rubygems | ruby | 159.3 | 102 | 0.64 | yes |
| ts-axios | typescript | 41.7 | 199 | 4.775 | yes |
| ts-jose | typescript | 22.5 | 112 | 4.988 | yes |
| ts-nest | typescript | 167.6 | 143 | 0.853 | yes |
| ts-otel-js | typescript | 145.4 | 294 | 2.022 | yes |
| ts-undici | typescript | 136.0 | 249 | 1.83 | yes |
