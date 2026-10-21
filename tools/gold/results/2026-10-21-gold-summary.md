# Gold-standard benchmark

Arbiter `d57e479`, 24 repositories, 6085.9 KLOC, 12468 findings (2.049/KLOC), 97 rules fired, **16 over budget**.

## Rules by findings per KLOC

| rule | findings | /KLOC | budget | in tests | repos | over |
|---|---:|---:|---:|---:|---:|:-:|
| `arbiter/typescript.test-without-assertion` | 729 | 1.1122 | 0.03 | 729 | 4 | **yes** |
| `arbiter/authored.undeclared-import` | 2325 | 0.382 | 0.03 | 53 | 18 | **yes** |
| `arbiter/secrets.assigned-credential` | 1696 | 0.2787 | 0.03 | 589 | 23 | **yes** |
| `arbiter/ast.function-too-long` | 1572 | 0.2583 | 0.5 | 769 | 24 |  |
| `arbiter/ast.high-complexity` | 1185 | 0.1947 | 0.5 | 159 | 21 |  |
| `arbiter/java.test-without-assertion` | 446 | 0.1481 | 0.03 | 446 | 5 | **yes** |
| `arbiter/quality.file-too-long` | 595 | 0.0978 | 0.5 | 0 | 24 |  |
| `arbiter/drift.broken-doc-link` | 582 | 0.0956 | 0.1 | 2 | 12 |  |
| `arbiter/python.sql-injection` | 65 | 0.0873 | 0.03 | 0 | 2 | **yes** |
| `arbiter/supply.unpinned-npm-dep` | 417 | 0.0685 | 0.1 | 8 | 9 |  |
| `arbiter/ruby.stub-method` | 46 | 0.0667 | 0.03 | 0 | 2 | **yes** |
| `arbiter/ruby.vacuous-spec` | 43 | 0.0623 | 0.03 | 43 | 1 | **yes** |
| `arbiter/ast.deep-nesting` | 377 | 0.0619 | 0.5 | 34 | 19 |  |
| `arbiter/authored.stub-on-production-path` | 309 | 0.0508 | 0.03 | 0 | 5 | **yes** |
| `arbiter/ruby.check-then-act-unlocked` | 34 | 0.0493 | 0.03 | 0 | 3 | **yes** |
| `arbiter/assurance.blanket-suppression` | 248 | 0.0407 | 0.03 | 118 | 13 | **yes** |
| `arbiter/assurance.test-asserts-nothing` | 244 | 0.0401 | 0.03 | 244 | 13 | **yes** |
| `arbiter/secrets.private-key` | 240 | 0.0394 | 0.03 | 91 | 21 | **yes** |
| `arbiter/typescript.fetch-no-timeout` | 25 | 0.0381 | 0.03 | 0 | 8 | **yes** |
| `arbiter/supply.unpinned-action` | 195 | 0.032 | 0.1 | 0 | 14 |  |
| `arbiter/ruby.sql-injection` | 22 | 0.0319 | 0.03 | 0 | 1 | **yes** |
| `arbiter/ruby.rescue-exception` | 21 | 0.0304 | 0.03 | 0 | 3 | **yes** |
| `arbiter/ruby.swallowed-error` | 21 | 0.0304 | 0.03 | 0 | 4 | **yes** |
| `arbiter/assurance.permanently-skipped-test` | 157 | 0.0258 | 0.03 | 157 | 15 |  |
| `arbiter/authored.security-check-disabled` | 146 | 0.024 | 0.03 | 116 | 15 |  |

## Repositories

| repo | lang | KLOC | findings | /KLOC | at pin |
|---|---|---:|---:|---:|:-:|
| go-caddy | go | 114.6 | 245 | 2.137 | yes |
| go-containerd | go | 246.1 | 411 | 1.67 | yes |
| go-coredns | go | 105.9 | 371 | 3.504 | yes |
| go-etcd | go | 194.5 | 274 | 1.408 | yes |
| go-grpc | go | 322.2 | 539 | 1.673 | yes |
| java-httpclient | java | 173.7 | 149 | 0.858 | yes |
| java-keycloak | java | 1187.3 | 2229 | 1.877 | yes |
| java-netty | java | 582.1 | 529 | 0.909 | yes |
| java-spring-security | java | 546.2 | 2140 | 3.918 | yes |
| java-tomcat | java | 645.0 | 964 | 1.495 | yes |
| py-aiohttp | python | 85.0 | 139 | 1.636 | yes |
| py-celery | python | 102.4 | 364 | 3.556 | yes |
| py-cryptography | python | 69.9 | 266 | 3.808 | yes |
| py-django | python | 464.5 | 713 | 1.535 | yes |
| py-urllib3 | python | 29.5 | 71 | 2.41 | yes |
| ruby-puma | ruby | 26.0 | 83 | 3.186 | yes |
| ruby-rack | ruby | 18.8 | 20 | 1.064 | yes |
| ruby-rails | ruby | 499.8 | 711 | 1.423 | yes |
| ruby-rubygems | ruby | 159.3 | 170 | 1.067 | yes |
| ts-axios | typescript | 41.7 | 262 | 6.287 | yes |
| ts-jose | typescript | 22.5 | 344 | 15.32 | yes |
| ts-nest | typescript | 167.6 | 254 | 1.515 | yes |
| ts-otel-js | typescript | 145.4 | 384 | 2.641 | yes |
| ts-undici | typescript | 136.0 | 836 | 6.145 | yes |
