# Gold-standard benchmark

Arbiter `8cf1ee7`, 23 repositories, 4898.6 KLOC, 6477 findings (1.322/KLOC), 93 rules fired, **7 over budget**.

## Rules by findings per KLOC

| rule | findings | /KLOC | budget | in tests | repos | over |
|---|---:|---:|---:|---:|---:|:-:|
| `arbiter/ast.function-too-long` | 1384 | 0.2825 | 0.5 | 699 | 23 |  |
| `arbiter/ast.high-complexity` | 1102 | 0.225 | 0.5 | 151 | 20 |  |
| `arbiter/java.test-without-assertion` | 233 | 0.12 | 0.03 | 233 | 4 | **yes** |
| `arbiter/drift.broken-doc-link` | 576 | 0.1176 | 0.1 | 2 | 11 | **yes** |
| `arbiter/quality.file-too-long` | 525 | 0.1072 | 0.5 | 0 | 23 |  |
| `arbiter/ast.deep-nesting` | 345 | 0.0704 | 0.5 | 26 | 18 |  |
| `arbiter/supply.unpinned-npm-dep` | 278 | 0.0568 | 0.1 | 8 | 8 |  |
| `arbiter/assurance.test-asserts-nothing` | 231 | 0.0472 | 0.03 | 231 | 12 | **yes** |
| `arbiter/secrets.private-key` | 226 | 0.0461 | 0.03 | 86 | 20 | **yes** |
| `arbiter/supply.unpinned-action` | 195 | 0.0398 | 0.1 | 0 | 14 |  |
| `arbiter/python.sql-injection` | 29 | 0.0389 | 0.03 | 0 | 2 | **yes** |
| `arbiter/assurance.blanket-suppression` | 178 | 0.0363 | 0.03 | 110 | 10 | **yes** |
| `arbiter/typescript.test-without-assertion` | 18 | 0.0334 | 0.03 | 18 | 2 | **yes** |
| `arbiter/authored.security-check-disabled` | 144 | 0.0294 | 0.03 | 116 | 14 |  |
| `arbiter/secrets.assigned-credential` | 132 | 0.0269 | 0.03 | 72 | 17 |  |

## Repositories

| repo | lang | KLOC | findings | /KLOC | at pin |
|---|---|---:|---:|---:|:-:|
| go-caddy | go | 114.6 | 240 | 2.094 | yes |
| go-containerd | go | 246.1 | 399 | 1.621 | yes |
| go-coredns | go | 105.9 | 299 | 2.824 | yes |
| go-etcd | go | 194.5 | 255 | 1.311 | yes |
| go-grpc | go | 322.2 | 504 | 1.564 | yes |
| java-httpclient | java | 173.7 | 119 | 0.685 | yes |
| java-netty | java | 582.1 | 474 | 0.814 | yes |
| java-spring-security | java | 546.2 | 282 | 0.516 | yes |
| java-tomcat | java | 645.0 | 865 | 1.341 | yes |
| py-aiohttp | python | 85.0 | 131 | 1.542 | yes |
| py-celery | python | 102.4 | 214 | 2.091 | yes |
| py-cryptography | python | 69.9 | 232 | 3.321 | yes |
| py-django | python | 464.5 | 468 | 1.008 | yes |
| py-urllib3 | python | 29.5 | 48 | 1.629 | yes |
| ruby-puma | ruby | 26.0 | 69 | 2.649 | yes |
| ruby-rack | ruby | 18.8 | 18 | 0.958 | yes |
| ruby-rails | ruby | 499.8 | 570 | 1.14 | yes |
| ruby-rubygems | ruby | 159.3 | 94 | 0.59 | yes |
| ts-axios | typescript | 41.7 | 238 | 5.711 | yes |
| ts-jose | typescript | 22.5 | 94 | 4.186 | yes |
| ts-nest | typescript | 167.6 | 146 | 0.871 | yes |
| ts-otel-js | typescript | 145.4 | 343 | 2.359 | yes |
| ts-undici | typescript | 136.0 | 375 | 2.757 | yes |
