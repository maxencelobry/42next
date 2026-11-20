# 42next: notes for Claude sessions

42next is a zero-config tester for 42 projects. `bin/42next.js` (Node, no dependencies) holds the menu, detection, cleaner and updater. The real tests live in `testers/<project>/run.py` (Python stdlib only, 3.9+). They share `testers/common/core.py`.

## Owner rules (decided with the owner, keep them)

- The owner writes in informal French: answer in French. Everything the tool prints, the README and the code comments are in English.
- Commits: author `mlobry <maxence.lobry@learner.42.tech>` (set in the local git config of the repo, never the owner's personal account), with `Co-Authored-By: Claude <noreply@anthropic.com>`. When a task is finished and checked, commit and push to `main` without asking (the owner asked for this, 2026-11-16).
- Concrete and direct. No bling: no achievements, animations, stats, fun facts, settings or i18n. A clean interface is fine.
- Zero config. Everything is auto-detected or passed as an argument.
- No exam mode. The README points to https://42exam.net/ for exams.
- A check must come from the subject PDF (or the moulinette report). Nothing from memory, evaluation sheets or the internet. If it is not in the subject, it is not a KO.
- Memory checks: only where Tripouille, francinette and 42-cli look (the moulinette does not look elsewhere). That means valgrind on each test binary, per-case leak tracking (malloc wrapper), and ASan as a fallback when valgrind is missing.
- Never modify the tested project: build and run in a temporary copy (`core.Workspace`). Never leave `__pycache__` / `.mypy_cache` behind (`python -B`, mypy cache in /tmp).
- New projects are added as the owner progresses in the Common Core, when they drop the subject PDF in ~/Downloads.
- Only the current subject counts (the owner, 2026-11-20). Something from an older subject is a KO wherever the current subject says otherwise, and is noted with `rep.old_subject(...)` so `finish()` lists it under "older subject". Pass the version to `core.header(..., subject="1.3")` (Python specs: the "Subject vX" at the start of `note`). An extra the current subject does not forbid (a `bonus` rule) is only listed there, not a KO.

## Severity, the same in every tester

- `KO`: the subject or the moulinette requires it and it is broken.
- `WARN`: up to the evaluator, or not written in the subject. Examples: a Python builtin outside the subject's authorized list, libft over-allocation, a reworded message.
- `SKIP`: a tool is missing, or the part was skipped on purpose (`--quick`).

Decided cases: `super()` is accepted in Garden Guardian ex3/ex4. For push_swap with 2 or 3 numbers, using more operations than the BFS minimum is a KO ("smallest list possible" in the subject). For 4 or 5 numbers it is shown as info only.

## Tester contract

```
python3 -B run.py --project DIR [--only g1,g2] [--list] [--quick] [--verbose] [--keep] [--no-color]
```

- `--list` prints one `id<TAB>label` line per group. Group names are shared: `files`, `norm`, `makefile`, `functions`, `readme`, `repo`, then the tests (one group per function / conversion / exercise).
- Output: `core.header()` line, `== section ==`, `KO`/`WARN`/`SKIP` with the exact call, expected and got, then `RESULT x/y`.
- Exit codes: 0 all passed, 1 failure, 2 setup problem.
- Env from the menu: `NEXT42_FAILED=path` makes `Report.finish()` write the failed group ids (one per line, `*` if a failed section has no group id). Map a section to its group with `rep.section(name, gid=...)` or `rep.alias`. `NEXT42_PROJECT_LABEL` replaces the path in `core.header()` (last-commit runs).
- Python projects: `testers/py/run_tests.py` plus one JSON spec per subject in `testers/py/specs/`.

## Checks before a commit

`flake8 testers`, `mypy --cache-dir /tmp/x --ignore-missing-imports <file>` on each run.py, `node --check bin/42next.js`, `bash -n` on the shell scripts. Then run each tester on a real project and on a copy with injected bugs.
