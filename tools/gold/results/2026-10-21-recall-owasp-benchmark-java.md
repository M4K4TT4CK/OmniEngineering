# Recall on owasp-benchmark-java

Arbiter `d57e479`, source https://github.com/OWASP-Benchmark/BenchmarkJava.git @ `a0f07b845190` (GPL-2.0 (fetched at a pinned commit, never redistributed or built)). Overall recall 0.02, false-positive rate 0.02, score (TPR-FPR) -0.001.
On the CWEs Arbiter's rules claim: recall 0.045, false-positive rate 0.043, score 0.003.

| CWE | category | claimed | real | caught | recall | safe | flagged | FPR | score |
|---|---|:-:|---:|---:|---:|---:|---:|---:|---:|
| CWE-22 | pathtraver | no | 133 | 0 | 0.0 | 135 | 0 | 0.0 | 0.0 |
| CWE-78 | cmdi | yes | 126 | 0 | 0.0 | 125 | 0 | 0.0 | 0.0 |
| CWE-79 | xss | no | 246 | 0 | 0.0 | 209 | 0 | 0.0 | 0.0 |
| CWE-89 | sqli | yes | 272 | 28 | 0.103 | 232 | 27 | 0.116 | -0.013 |
| CWE-90 | ldapi | no | 27 | 0 | 0.0 | 32 | 0 | 0.0 | 0.0 |
| CWE-327 | crypto | no | 130 | 0 | 0.0 | 116 | 0 | 0.0 | 0.0 |
| CWE-328 | hash | no | 129 | 0 | 0.0 | 107 | 0 | 0.0 | 0.0 |
| CWE-330 | weakrand | yes | 218 | 0 | 0.0 | 275 | 0 | 0.0 | 0.0 |
| CWE-501 | trustbound | no | 83 | 0 | 0.0 | 43 | 0 | 0.0 | 0.0 |
| CWE-614 | securecookie | no | 36 | 0 | 0.0 | 31 | 0 | 0.0 | 0.0 |
| CWE-643 | xpathi | no | 15 | 0 | 0.0 | 20 | 0 | 0.0 | 0.0 |
