# Recall on owasp-benchmark-python

Arbiter `d57e479`, source https://github.com/OWASP-Benchmark/BenchmarkPython.git @ `f1291485808b` (GPL-3.0 (fetched at a pinned commit, never redistributed or run)). Overall recall 0.111, false-positive rate 0.067, score (TPR-FPR) 0.044.
On the CWEs Arbiter's rules claim: recall 0.221, false-positive rate 0.132, score 0.089.

| CWE | category | claimed | real | caught | recall | safe | flagged | FPR | score |
|---|---|:-:|---:|---:|---:|---:|---:|---:|---:|
| CWE-22 | pathtraver | no | 65 | 0 | 0.0 | 103 | 0 | 0.0 | 0.0 |
| CWE-78 | cmdi | yes | 13 | 7 | 0.538 | 7 | 7 | 1.0 | -0.462 |
| CWE-79 | xss | no | 31 | 0 | 0.0 | 58 | 0 | 0.0 | 0.0 |
| CWE-89 | sqli | yes | 5 | 5 | 1.0 | 11 | 0 | 0.0 | 1.0 |
| CWE-90 | ldapi | no | 16 | 0 | 0.0 | 13 | 0 | 0.0 | 0.0 |
| CWE-94 | codeinj | yes | 20 | 20 | 1.0 | 33 | 33 | 1.0 | 0.0 |
| CWE-328 | hash | yes | 71 | 0 | 0.0 | 80 | 0 | 0.0 | 0.0 |
| CWE-330 | weakrand | yes | 99 | 0 | 0.0 | 227 | 0 | 0.0 | 0.0 |
| CWE-501 | trustbound | no | 18 | 0 | 0.0 | 19 | 0 | 0.0 | 0.0 |
| CWE-502 | deserialization | yes | 18 | 18 | 1.0 | 36 | 12 | 0.333 | 0.667 |
| CWE-601 | redirect | no | 13 | 0 | 0.0 | 21 | 0 | 0.0 | 0.0 |
| CWE-611 | xxe | no | 8 | 0 | 0.0 | 20 | 0 | 0.0 | 0.0 |
| CWE-614 | securecookie | no | 24 | 0 | 0.0 | 15 | 0 | 0.0 | 0.0 |
| CWE-643 | xpathi | no | 51 | 0 | 0.0 | 135 | 0 | 0.0 | 0.0 |
