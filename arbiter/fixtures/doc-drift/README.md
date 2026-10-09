# Doc-drift precision fixture

Five paths are named below. Four describe something other than a file this
repository is missing, and `doc_drift` must leave them alone. The fifth is
real drift and must still be reported. See ARB-048.

1. Generated output. The build writes `build/report.json`; `.gitignore` lists
   `build/`, so no checkout ever carries it.
2. Scan output. A run writes `arbiter-out/REPORT.md`, which is never committed.
3. Another host. The rendered guide lives on [the mirror](git://mirror.example.org/guide/docs/guide.md).
4. Another repository. The handbook is `docs/handbook.md` in the handbook
   repository (https://github.com/example/handbook); this checkout does not
   carry it, and the hard wrap between the path and its URL is deliberate.
5. Real drift. The entry point is `src/missing.py`, which was never here.
