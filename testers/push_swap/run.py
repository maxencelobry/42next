#!/usr/bin/env python3
"""42next tester for push_swap (subject v1.2: strategies, disorder, --bench).

    python3 -B run.py --project DIR [--only g1,g2] [--list] [--no-color]
                      [--verbose] [--keep]
    extra options (after the standard ones):
      --runs N        random draws per perf test (p100 / p500 / perf)
      --size N        size for the free perf run (group "perf")
      --seed S        base seed, to replay a run exactly
      --strategy X    simple|medium|complex|adaptive for the free perf run
      --disorder D    0..1: free perf run on inputs with this exact disorder
      --timeout S     timeout per execution (default 10s)
      --checker PATH  official checker to cross-check with (auto-detected)

Every sorting result is replayed on an exact simulator (the reference); the
official checker, when one runs on this machine, is used as a second opinion.
The project is never written to: everything runs in a core.Workspace copy.
"""

import argparse
import itertools
import os
import re
import shlex
import shutil
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from random import Random

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
sys.path.insert(0, HERE)

import core  # noqa: E402
from core import Report, run  # noqa: E402
import sim  # noqa: E402

GROUPS = [
    ("norm", "Norminette (project + libft)"),
    ("makefile", "Makefile rules, flags, relink"),
    ("functions",
     "External functions (read write malloc free exit) + globals"),
    ("readme",
     "README.md requirements (+ algorithms, disorder, contributions)"),
    ("repo", "Repository hygiene"),
    ("errors", "Error handling: \"Error\\n\" on stderr, nothing on stdout"),
    ("identity", "Nothing to do: no args, 1 number, already sorted"),
    ("small", "Small stacks: every permutation of 2, 3, 4, 5"),
    ("p100", "Performance: 100 random numbers"),
    ("p500", "Performance: 500 random numbers"),
    ("disorder", "Low / medium / high disorder inputs + adaptive regimes"),
    ("strategies", "Selectors --simple --medium --complex --adaptive"),
    ("bench", "--bench output: format, values, stderr only"),
    ("checker", "Official checker cross-check + bonus checker if present "
                "(no bonus in v1.2: WARN only)"),
    ("leaks", "Valgrind: normal runs and error paths"),
    ("perf",
     "Free perf run (--size N --runs N [--strategy X] [--disorder D])"),
]

THRESHOLDS = {100: (2000, 1500, 700), 500: (12000, 8000, 5500)}
STRATEGIES = ("simple", "medium", "complex", "adaptive")
ALLOWED = {"read", "write", "malloc", "free", "exit"}
CHECKER_DIRS = [os.path.expanduser("~/Downloads/checker"),
                os.path.expanduser("~/Downloads")]
CHECKER_NAMES = ["fedora_checker", "checker_linux", "ubuntu_checker",
                 "checker_os", "checker_Mac"]


def parse_extra(extra):
    p = argparse.ArgumentParser(prog="run.py (extra)")
    p.add_argument("--runs", type=int, default=None)
    p.add_argument("--size", type=int, default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--strategy", choices=STRATEGIES, default=None)
    p.add_argument("--disorder", type=float, default=None)
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--checker", default=None)
    opts, unknown = p.parse_known_args(extra)
    if unknown:
        print(core.red("unknown option(s): " + " ".join(unknown)))
        sys.exit(2)
    return opts


# --------------------------------------------------------------------------
# context: binary, checker, display helpers
# --------------------------------------------------------------------------

class Ctx:
    def __init__(self, rep, ws, opts):
        self.rep = rep
        self.ws = ws
        self.opts = opts
        self.binary = ws.path("push_swap")
        self.timeout = opts.timeout
        self.quick = getattr(opts, "quick", False)
        self.checker = None
        self.checker_name = None
        self.fail_dir = os.path.join(tempfile.gettempdir(),
                                     "42next-push_swap-inputs")
        self._pruned = False

    def ps(self, argv, timeout=None):
        return run([self.binary, *argv], cwd=self.ws.dir,
                   timeout=timeout or self.timeout)

    def official(self, nums, out):
        """Official checker verdict on stdout `out`, or None."""
        if not self.checker:
            return None
        res = run([self.checker, *map(str, nums)], stdin=out,
                  timeout=self.timeout * 2)
        if res.timed_out:
            return "timeout"
        return (res.out + res.err).decode("utf-8", "replace").strip() or "?"

    def cmd(self, argv, max_len=200):
        """A copyable command line; long inputs are saved to a file."""
        line = "./push_swap " + shlex.join(argv) if argv else "./push_swap"
        if len(line) <= max_len:
            return line
        if not os.path.isdir(self.fail_dir):
            os.makedirs(self.fail_dir, exist_ok=True)
        elif not self._pruned:
            # the saved inputs are for replaying today's output: drop the
            # ones older than a day so the folder does not grow forever
            old = time.time() - 24 * 3600
            for f in os.listdir(self.fail_dir):
                p = os.path.join(self.fail_dir, f)
                try:
                    if os.path.getmtime(p) < old:
                        os.remove(p)
                except OSError:
                    pass
        self._pruned = True
        flags = [a for a in argv if a.startswith("--")]
        nums = [a for a in argv if not a.startswith("--")]
        fd, path = tempfile.mkstemp(prefix=f"n{len(nums)}-", suffix=".txt",
                                    dir=self.fail_dir)
        with os.fdopen(fd, "w") as fh:
            fh.write(" ".join(nums) + "\n")
        fl = (" ".join(flags) + " ") if flags else ""
        return f'ARG="$(cat {path})"; ./push_swap {fl}$ARG'


def detect_checker(ctx):
    cands = []
    if ctx.opts.checker:
        cands.append(os.path.abspath(ctx.opts.checker))
    for d in [ctx.ws.project] + CHECKER_DIRS:
        for n in CHECKER_NAMES:
            cands.append(os.path.join(d, n))
    for c in cands:
        if not os.path.isfile(c):
            continue
        local = ctx.ws.tmp("official_" + os.path.basename(c))
        shutil.copy(c, local)
        os.chmod(local, 0o755)
        res = run([local, "2", "1"], stdin=b"sa\n", timeout=5)
        if res.rc == 0 and res.out.strip() == b"OK":
            ctx.checker, ctx.checker_name = local, c
            return


# --------------------------------------------------------------------------
# one sorting run, fully explained
# --------------------------------------------------------------------------

class Outcome:
    def __init__(self, nums, argv, res, bench=False):
        self.nums = nums
        self.argv = argv
        self.res = res
        self.problems = []
        self.parsed = None
        self.sim = None
        self.nops = 0
        if res.timed_out or res.crashed:
            self.problems.append(res.status())
            return
        if res.rc == 127 and not res.out:
            self.problems.append("could not run: "
                                 + res.err.decode("utf-8", "replace"))
            return
        if not bench and res.err:
            self.problems.append("unexpected output on stderr: "
                                 + core.crepr(res.err, 80))
        self.parsed = sim.parse_ops(res.out)
        ops = self.parsed.ops
        self.nops = len(ops)
        if not self.parsed.ok:
            p = self.parsed
            self.problems.append(f"stdout line {p.bad_line}: {p.why} "
                                 f"{core.crepr(p.bad_text, 40)}")
        self.sim = sim.Sim(nums, ops)
        if self.parsed.ok and not self.sim.sorted:
            self.problems.append("not sorted after the operations"
                                 if not self.sim.b else
                                 f"stack b not empty ({len(self.sim.b)} left)")

    @property
    def ok(self):
        return not self.problems

    def detail(self, ctx, limit=None):
        lines = ["input:   " + ctx.cmd(self.argv)]
        lines += ["problem: " + p for p in self.problems]
        if self.sim is not None:
            s = self.sim
            when = (f"state after the {self.nops} valid op(s) before line "
                    f"{self.parsed.bad_line}" if not self.parsed.ok
                    else f"final state after {self.nops} op(s)")
            focus = s.first_unsorted()
            lines.append(f"{when} (top first):")
            lines.append("  a = " + sim.show_stack(s.a, focus))
            lines.append("  b = " + sim.show_stack(s.b))
            if focus is not None:
                a = list(s.a)
                lines.append(f"  first unsorted pair: a[{focus}]={a[focus]} "
                             f"> a[{focus + 1}]={a[focus + 1]}")
        if limit is not None:
            lines.append(f"operations: {self.nops} (limit < {limit})")
        if ctx.checker and self.sim is not None:
            verdict = ctx.official(self.nums, self.res.out)
            lines.append(f"official checker: {verdict}")
        if self.res.rc not in (0, None) and not self.res.crashed:
            lines.append(f"exit code: {self.res.rc}")
        return "\n".join(lines)


def sort_run(ctx, nums, flags=(), after=False, bench=False, timeout=None):
    nargs = [str(n) for n in nums]
    argv = nargs + list(flags) if after else list(flags) + nargs
    return Outcome(nums, argv, ctx.ps(argv, timeout), bench)


def sort_many(ctx, inputs, flags=()):
    """sort_run on many inputs at the same time, results in order."""
    if not inputs:
        return []
    workers = max(1, min(len(inputs), os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda nums: sort_run(ctx, list(nums), flags),
                             inputs))


def strat_flag(name):
    return [f"--{name}"] if name else []


# --------------------------------------------------------------------------
# static groups
# --------------------------------------------------------------------------

def g_norm(ctx):
    ctx.rep.section("norm")
    core.check_norm(ctx.rep, ctx.ws.dir)


_SUBMAKE = re.compile(
    r"(?:^|[\s;&|(])(?:\S*/)?g?make\b[^\n]*?"
    r"(?:\s-[a-zA-Z]*C\s*|\s--directory[= ]?\s*)['\"]?(?:\S*/)?libft/?"
    r"(?:['\"\s;&|)]|$)"
    r"|\bcd\s+['\"]?(?:\S*/)?libft/?['\"]?\s*(?:&&|;)\s*(?:\S*/)?g?make\b",
    re.M)


def libft_through_make(directory, text):
    """Does the default goal run make in libft/? Asked to make itself: a
    dry run (-n) that remakes everything (-B) and prints the directories
    (-w). $(MAKE) lines still run under -n, so the sub-make says "Entering
    directory .../libft"; a plain `make -C libft` or `cd libft && make` is
    only printed. Falls back to reading the Makefile if the dry run fails."""
    res = run(["make", "-n", "-B", "-w"], cwd=directory, timeout=60,
              env={"LC_ALL": "C", "MAKEFLAGS": ""})
    if res.rc == 0:
        out = (res.out + res.err).decode("utf-8", "replace")
        libdir = os.path.realpath(os.path.join(directory, "libft"))
        for m in re.finditer(r"Entering directory [`'\"]([^'\"\n]+)['\"]",
                             out):
            if os.path.realpath(m.group(1)) == libdir:
                return True
        return bool(_SUBMAKE.search(out))
    recipes = "\n".join(ln for ln in text.splitlines()
                        if ln.startswith("\t"))
    return "libft" in text.lower() and bool(
        re.search(r"\$[({]MAKE[)}]|(?:^|[\s;&|])make\b", recipes))


def build(ctx, with_checks):
    if with_checks:
        ctx.rep.section("makefile")
        # v1.2 has no bonus part: `make bonus` is built (for the checker
        # tests, WARN only) but not checked
        ok = core.check_makefile(ctx.rep, ctx.ws.dir, "push_swap")
        text = core.makefile_text(ctx.ws.dir) or ""
        if "bonus" in core.makefile_targets(text):
            run(["make", "bonus"], cwd=ctx.ws.dir, timeout=300)
        if os.path.isdir(ctx.ws.path("libft")):
            has_mk = os.path.isfile(ctx.ws.path("libft", "Makefile"))
            uses = has_mk and libft_through_make(ctx.ws.dir, text)
            ctx.rep.check("libft built through its own Makefile", uses,
                          detail=None if uses else
                          ("no libft/Makefile" if not has_mk else
                           "`make` never runs make in libft/")
                          + "\nsubject: the Makefile must compile libft "
                          "with its Makefile")
        return ok
    res = run(["make"], cwd=ctx.ws.dir, timeout=300)
    if res.rc != 0 or not os.path.exists(ctx.binary):
        ctx.rep.section("build")
        ctx.rep.check("make builds push_swap", False,
                      detail=core.short_errors((res.out + res.err).decode(
                          "utf-8", "replace")))
        return False
    if core.makefile_targets(core.makefile_text(ctx.ws.dir) or "") & {"bonus"}:
        run(["make", "bonus"], cwd=ctx.ws.dir, timeout=300)
    return True


GLOBAL_IGNORE = {"__dso_handle", "__TMC_END__", "__bss_start", "_edata",
                 "_end", "__data_start", "data_start", "_IO_stdin_used",
                 "__FRAME_END__", "_DYNAMIC", "_GLOBAL_OFFSET_TABLE_",
                 "completed", "__init_array_start", "__init_array_end",
                 "__fini_array_start", "__fini_array_end",
                 "__do_global_dtors_aux_fini_array_entry",
                 "__frame_dummy_init_array_entry", "__GNU_EH_FRAME_HDR",
                 "_fp_hw", "__abi_tag", "stdin", "stdout", "stderr",
                 "environ", "__environ", "_environ"}


def g_functions(ctx, binaries):
    rep = ctx.rep
    rep.section("functions")
    core.check_functions(rep, binaries[:1], ALLOWED,
                         label=f"{os.path.basename(binaries[0])}: "
                         "external functions", sources=ctx.ws.dir)
    if not core.have("nm"):
        return
    for b in binaries[1:]:
        # a bonus checker: no bonus part in v1.2, so only a WARN
        syms = core.external_symbols([b]) or []
        bad = [s for s in syms if s not in ALLOWED
               and s not in core.COMPILER_ALIASES]
        if bad:
            rep.warn(f"{os.path.basename(b)}: external functions outside "
                     "read write malloc free exit (no bonus part in subject "
                     "v1.2)", "forbidden: " + ", ".join(bad))
    for b in binaries[:1]:
        res = run(["nm", b], timeout=30)
        glob, stat = [], []
        for line in res.out.decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) != 3 or parts[1] not in "BDCbd":
                continue
            name = parts[2].split("@")[0]
            if name in GLOBAL_IGNORE or name.startswith(("__", "_ZN")):
                continue
            if "." in name:                 # static local (e.g. gnl buffer)
                continue
            (glob if parts[1] in "BDC" else stat).append(name)
        rep.check(f"{os.path.basename(b)}: no global variable", not glob,
                  detail="global(s): " + ", ".join(sorted(glob))
                  if glob else None)
        if stat:
            rep.warn(f"{os.path.basename(b)}: file-scope static variable(s)",
                     ", ".join(sorted(stat))
                     + " (the subject forbids globals; "
                     "a file-scope static is one too for most graders)")


def g_readme(ctx):
    rep = ctx.rep
    rep.section("readme")
    core.check_readme(rep, ctx.ws.project)
    path = os.path.join(ctx.ws.project, "README.md")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    low = text.lower()
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    # the subject allows the campus language: "currículo de 42 por ..."
    m = re.search(r"(?:curriculum|cursus|curr[ií]culo)\b.*?\b(?:by|par|por)"
                  r"\s+(.+?)[._*\s]*$", first, re.I)
    logins = []
    if m:
        logins = [x for x in re.split(r"[,\s]+(?:and\s+)?", m.group(1))
                  if x]
    if len(logins) < 2:
        rep.warn("first line lists both learners' logins",
                 f"found {logins or 'none'}; push_swap is a 2-learner project")
    rep.check("explains the algorithms (simple / medium / complex)",
              all(w in low for w in ("simple", "medium", "complex"))
              or len(re.findall(r"algorit", low)) >= 2,
              detail="subject: detailed explanation + justification of "
                     "the algorithms selected")
    # 6.3.3: "You must document in your repository (e.g., README.md) the
    # rationale for your thresholds": another .md of the repository counts
    docs = low + "\n" + other_docs(ctx.ws.project)
    rep.check("documents the adaptive strategy + disorder thresholds",
              bool(re.search(r"adapt(?:at)?i[vf]", docs))
              and bool(re.search(r"disorder|d[ée]sordre|desorden|desordem",
                                 docs)),
              detail="subject 6.3.3: rationale for the thresholds, internal "
                     "techniques per regime")
    if not re.search(r"0[.,]2|20 ?%", docs) \
            or not re.search(r"0[.,]5|50 ?%", docs):
        rep.warn("README does not show the 0.2 / 0.5 thresholds explicitly")
    if not re.search(r"o\((n|log)", docs):
        rep.warn("no complexity bound (O(...)) found in README",
                 "subject: brief complexity argument (upper bounds) for "
                 "time and space")
    contrib = re.search(r"contribu|who did what|r[ée]partition|reparto|"
                        r"division of (the )?(work|labou?r)|"
                        r"divisi[oó]n del trabajo|divis[aã]o d[eo] trabalho|"
                        r"split of (the )?work|work split", low)
    rep.check("README documents each learner's contributions",
              bool(contrib), detail=None if contrib else
              'subject 6.1: "The repository must clearly indicate both '
              'learners\' contributions in the README.md file" (add e.g. a '
              '"Contributions" section)')


def other_docs(project):
    """Lower-case text of the .md / .txt files of the repository other
    than README.md (libft/ excluded)."""
    parts = []
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "libft"]
        for f in files:
            if f.lower().endswith((".md", ".txt")) and f != "README.md":
                try:
                    with open(os.path.join(root, f), encoding="utf-8",
                              errors="replace") as fh:
                        parts.append(fh.read(200000).lower())
                except OSError:
                    pass
    return "\n".join(parts)


def g_repo(ctx):
    rep = ctx.rep
    rep.section("repo")
    core.check_repo(rep, ctx.ws.project, binaries=("push_swap", "checker"))
    odd = []
    for root, dirs, files in os.walk(ctx.ws.project):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), ctx.ws.project)
            if f in ("Makefile", "README.md", ".gitignore") or \
                    f.endswith((".c", ".h")):
                continue
            if rel.startswith("libft" + os.sep) and f.endswith(".md"):
                continue
            odd.append(rel)
    if odd:
        rep.warn("files outside 'Makefile, *.h, *.c' (+ README)",
                 ", ".join(sorted(odd)[:12]))


# --------------------------------------------------------------------------
# errors / identity
# --------------------------------------------------------------------------

ERROR_CASES = [
    ("non-numeric", ["1", "abc", "3"]),
    ("non-numeric (subject example)", ["--adaptive", "0", "one", "2", "3"]),
    ("duplicate (subject example)", ["--simple", "3", "2", "3"]),
    ("duplicate 1 1", ["1", "1"]),
    ("duplicate after conversion +1 1", ["+1", "1"]),
    ("duplicate after conversion -0 0", ["-0", "0"]),
    ("duplicate after conversion 007 7", ["007", "7"]),
    ("lone +", ["+"]),
    ("lone -", ["-"]),
    ("--5", ["--5"]),
    ("+-1", ["+-1"]),
    ("-+1", ["-+1"]),
    ("12-3", ["12-3"]),
    ("trailing junk 3x", ["1", "2", "3x"]),
    ("decimal 1.5", ["1.5", "2"]),
    ("INT_MAX + 1 (2147483648)", ["2147483648"]),
    ("INT_MAX + 1 among others", ["1", "2147483648", "3"]),
    ("INT_MIN - 1 (-2147483649)", ["-2147483649"]),
    ("2^32 (wraps to 0)", ["4294967296", "1"]),
    ("2^32 + 1 vs 1 (wraps to 1)", ["4294967297", "1"]),
    ("20 digits", ["99999999999999999999"]),
    ("argument \" \"", [" "]),
    ("empty argument \"\"", [""]),
    ("empty argument among numbers", ["1", "", "2"]),
    ("unknown flag", ["--foo", "2", "1"]),
    ("--bench with duplicate", ["--bench", "1", "1"]),
    ("duplicate far away", [str(i) for i in range(50)] + ["0"]),
]

STRING_ERROR_CASES = [
    ("duplicate inside a string", ["3 2 1 3"]),
    ("duplicate across string + arg", ["3 2 1", "2"]),
    ("junk inside a string", ["1 2 abc"]),
    ("overflow inside a string", ["1 2147483648"]),
]


def error_check(ctx, name, argv):
    res = ctx.ps(argv)
    probs = []
    if res.timed_out or res.crashed:
        probs.append(res.status())
    else:
        if res.out:
            probs.append("stdout must be empty, got "
                         + core.crepr(res.out, 60))
        if res.err != b"Error\n":
            probs.append('stderr must be exactly "Error\\n", got '
                         + core.crepr(res.err, 60))
    ctx.rep.check(f"{name}", not probs,
                  detail=None if not probs else
                  "input: " + ctx.cmd(argv) + "\n" + "\n".join(probs))
    return not probs


def strings_supported(ctx):
    res = ctx.ps(["3 2 1"])
    if res.err or res.timed_out or res.crashed:
        return False, res
    return True, res


def g_errors(ctx):
    rep = ctx.rep
    rep.section("errors")
    for name, argv in ERROR_CASES:
        error_check(ctx, name, argv)
    ok, res = strings_supported(ctx)
    if not ok:
        rep.warn('one-string input "3 2 1" refused',
                 "the v1.2 subject does not require it; evaluators often "
                 "test ARG=\"3 2 1\" unquoted, which still works")
        return
    rep.info('one-string input "3 2 1" accepted: testing string parsing')
    for name, argv in [("string \"3 2 1\"", ["3 2 1"]),
                       ("multiple spaces \"3   2  1\"", ["3   2  1"]),
                       ("leading/trailing spaces \"  3 2 1  \"",
                        ["  3 2 1  "]),
                       ("string + args \"3 2\" 1", ["3 2", "1"]),
                       ("args + string 5 \"3 2\" 1", ["5", "3 2", "1"])]:
        nums = [int(x) for a in argv for x in a.split()]
        out = Outcome(nums, argv, ctx.ps(argv))
        # the subject defines no string argument: refusing one with Error
        # is a choice, a wrong result on an accepted one is a bug
        if (not out.ok and not out.res.out.strip()
                and out.res.err.strip() == b"Error"):
            rep.warn(f"{name} refused (Error)",
                     f"input:   {ctx.cmd(argv)}\nthe subject does not "
                     "define string arguments, so this is up to the "
                     "evaluator")
            continue
        rep.check(f"{name} sorts", out.ok,
                  detail=None if out.ok else out.detail(ctx))
    for name, argv in STRING_ERROR_CASES:
        error_check(ctx, name, argv)


def g_identity(ctx):
    rep = ctx.rep
    rep.section("identity")
    res = ctx.ps([])
    probs = []
    if res.timed_out or res.crashed:
        probs.append(res.status())
    if res.out or res.err:
        probs.append(f"stdout {core.crepr(res.out, 40)} "
                     f"stderr {core.crepr(res.err, 40)}")
    rep.check("no argument: no output at all", not probs,
              detail="input: ./push_swap\n" + "\n".join(probs)
              if probs else None)
    if res.rc not in (0, None) and not probs:
        rep.info(f"no argument: exit code {res.rc} "
                 "(not specified by the subject)")
    rng = Random(4242)
    cases = [
        ("one number", [42]),
        ("one negative number", [-7]),
        ("INT_MIN alone", [-2147483648]),
        ("sorted 2", [1, 2]),
        ("sorted 3", [-5, 0, 7]),
        ("sorted INT_MIN..INT_MAX", [-2147483648, 0, 2147483647]),
        ("sorted 5", [1, 2, 3, 4, 5]),
        ("sorted 100", sorted(sim.random_input(rng, 100))),
        ("sorted 500", sorted(sim.random_input(rng, 500))),
    ]
    for name, nums in cases:
        out = sort_run(ctx, nums)
        good = out.ok and out.nops == 0 and not out.res.out
        if out.ok and out.nops:
            out.problems.append(f"already sorted but printed {out.nops} op(s)")
        rep.check(f"{name}: no output", good,
                  detail=None if good else out.detail(ctx))
    for st in STRATEGIES:
        out = sort_run(ctx, [1, 2, 3, 4, 5], strat_flag(st))
        if out.ok and out.nops:
            out.problems.append(f"already sorted but printed {out.nops} op(s)")
        rep.check(f"--{st} on sorted 5: no output", out.ok,
                  detail=None if out.ok else out.detail(ctx))
    # a selector is a parameter: the "no parameters" rule does not cover it.
    # Nothing to sort, so no operation; "Error" on stderr is up to the
    # evaluator
    res = ctx.ps(["--simple"])
    said = (f"input: ./push_swap --simple\nstdout {core.crepr(res.out, 40)} "
            f"stderr {core.crepr(res.err, 40)}")
    bad = res.out or res.timed_out or res.crashed
    rep.check("selector alone (--simple): no operation printed", not bad,
              detail=(said + ("\n" + res.status() if res.timed_out
                              or res.crashed else "")) if bad else None)
    if not bad and res.err:
        rep.warn("selector alone (--simple): writes on stderr",
                 said + "\n(not specified by the subject: an empty stack "
                 "is already sorted, \"Error\" is up to the evaluator)")


# --------------------------------------------------------------------------
# small
# --------------------------------------------------------------------------

SMALL_VALUES = {5: [-2147483648, -42, 0, 7, 2147483647]}


def _apply(state, op):
    a, b = list(state[0]), list(state[1])
    for stack, name in ((a, "a"), (b, "b")):
        if op in ("s" + name, "ss") and len(stack) >= 2:
            stack[0], stack[1] = stack[1], stack[0]
        if op in ("r" + name, "rr") and stack:
            stack.append(stack.pop(0))
        if op in ("rr" + name, "rrr") and stack:
            stack.insert(0, stack.pop())
    if op == "pa" and b:
        a.insert(0, b.pop(0))
    if op == "pb" and a:
        b.insert(0, a.pop(0))
    return tuple(a), tuple(b)


def optimal_ops(n):
    """Fewest operations to sort every permutation of 0..n-1 (BFS).

    The operation set is closed under inverse, so the distance from the
    sorted state to a state is the distance from that state to sorted.
    """
    start = (tuple(range(n)), ())
    dist = {start: 0}
    todo = [start]
    for state in todo:
        for op in OPS:
            nxt = _apply(state, op)
            if nxt not in dist:
                dist[nxt] = dist[state] + 1
                todo.append(nxt)
    return {p: dist[(p, ())] for p in itertools.permutations(range(n))}


OPS = ("sa", "sb", "ss", "pa", "pb", "ra", "rb", "rr", "rra", "rrb", "rrr")


def g_small(ctx):
    rep = ctx.rep
    rep.section("small")
    for n in (2, 3, 4, 5):
        values = SMALL_VALUES.get(n, list(range(1, n + 1)))
        best = optimal_ops(n)
        fails, counts, opt, worse = [], [], [], []
        perms = list(itertools.permutations(values))
        if ctx.quick and len(perms) > 24:
            perms = Random(5).sample(perms, 24)
        for p, out in zip(perms, sort_many(ctx, perms)):
            rank = tuple(sorted(p).index(v) for v in p)
            if not out.ok:
                fails.append(out)
                continue
            counts.append(out.nops)
            opt.append(best[rank])
            if out.nops > best[rank]:
                worse.append((out, best[rank]))
        what = "sampled" if ctx.quick and n == 5 else "all"
        rep.check(f"{n} numbers: {what} {len(perms)} permutations sorted",
                  not fails,
                  detail=None if not fails else
                  f"{len(fails)} failing; first ones:\n"
                  + "\n".join(f.detail(ctx) for f in fails[:3]))
        if not counts:
            continue
        rep.info(f"{n} numbers: your ops min {min(counts)} avg "
                 f"{statistics.mean(counts):.2f} max {max(counts)} | "
                 f"fewest possible avg {statistics.mean(opt):.2f} "
                 f"max {max(opt)}")
        if n <= 3:
            worse.sort(key=lambda w: w[1] - w[0].nops)
            lines = [f"{ctx.cmd(o.argv)} -> {o.nops} ops, {b} possible"
                     for o, b in worse[:4]]
            rep.check(f"{n} numbers: smallest list of operations possible",
                      not worse, detail=None if not worse else
                      "the subject asks for the smallest list possible:\n"
                      + "\n".join(lines))
    rng = Random(55)
    for flag in STRATEGIES:
        inputs = []
        for n in (2, 3, 5):
            perms = list(itertools.permutations(range(n)))
            if ctx.quick and n == 5:
                perms = Random(6).sample(perms, 12)
            inputs += perms
        for _ in range(10):
            inputs.append(sim.random_input(rng, rng.randint(6, 12)))
        bad = [o for o in sort_many(ctx, inputs, strat_flag(flag))
               if not o.ok]
        rep.check(f"--{flag}: all permutations of 2, 3, 5 + random 6..12",
                  not bad, detail=None if not bad else
                  f"{len(bad)} failing; first:\n" + bad[0].detail(ctx))


# --------------------------------------------------------------------------
# performance
# --------------------------------------------------------------------------

def tier(size, ops):
    if size not in THRESHOLDS:
        return ""
    p, g, e = THRESHOLDS[size]
    if ops < e:
        return core.green("excellent")
    if ops < g:
        return core.green("good")
    if ops < p:
        return core.yellow("pass")
    return core.red("over the limit")


def perf(ctx, size, runs, seed, flags=(), target=None, label=None):
    rep = ctx.rep
    counts, fails, cross = [], [], []
    limit = THRESHOLDS.get(size, (None,))[0]
    timeout = max(ctx.timeout, size / 50.0)
    start = time.monotonic()
    for i in range(runs):
        rng = Random(seed + i)
        nums = (sim.with_disorder(rng, size, target) if target is not None
                else sim.random_input(rng, size))
        out = sort_run(ctx, nums, flags, timeout=timeout)
        name = f"{label or size} run {i + 1}/{runs} (seed {seed + i})"
        if not out.ok:
            fails.append(out)
            rep.check(name, False, detail=out.detail(ctx, limit))
            continue
        counts.append(out.nops)
        if limit is not None:
            rep.check(f"{name}: {out.nops} ops", out.nops < limit,
                      detail=None if out.nops < limit
                      else out.detail(ctx, limit))
        else:
            rep.check(f"{name}: {out.nops} ops", True)
        if ctx.checker:
            verdict = ctx.official(nums, out.res.out)
            if verdict != "OK":
                cross.append(f"seed {seed + i}: official checker said "
                             f"{verdict}")
    took = time.monotonic() - start
    if counts:
        mean = statistics.mean(counts)
        line = (f"{len(counts)} sorted run(s), seed {seed}: "
                f"min {min(counts)}  "
                f"avg {mean:.1f}  max {max(counts)}")
        if len(counts) > 1:
            line += f"  stdev {statistics.pstdev(counts):.1f}"
        rep.info(line + f"  ({took:.1f}s)")
        if size in THRESHOLDS:
            p, g, e = THRESHOLDS[size]
            rep.info(f"worst case {max(counts)} -> "
                     f"{tier(size, max(counts))}   "
                     f"average -> {tier(size, mean)}   "
                     f"(pass < {p}, good < {g}, excellent < {e})")
        rep.info(f"replay: --seed {seed} --runs {runs}")
    for c in cross:
        rep.warn("official checker disagrees with the simulator", c)
    return counts


def base_seed(ctx, salt):
    if ctx.opts.seed is not None:
        return ctx.opts.seed
    return (int(time.time()) % 100000) * 10 + salt


def g_p(ctx, size, default_runs):
    ctx.rep.section(f"p{size}")
    if ctx.quick:
        default_runs = 3 if size == 100 else 1
    perf(ctx, size, ctx.opts.runs or default_runs, base_seed(ctx, size % 7))


def g_perf(ctx):
    o = ctx.opts
    size = o.size or 100
    runs = o.runs or 10
    flags = strat_flag(o.strategy)
    lab = f"size {size}" + (f" --{o.strategy}" if o.strategy else "") + \
        (f" disorder {o.disorder:.2f}" if o.disorder is not None else "")
    ctx.rep.section(f"perf ({lab})", gid="perf")
    if size < 1:
        ctx.rep.check("size >= 1", False)
        return
    perf(ctx, size, runs, base_seed(ctx, 3), flags, o.disorder, lab)


# --------------------------------------------------------------------------
# --bench parsing
# --------------------------------------------------------------------------

def parse_class(text):
    t = text.lower().replace(" ", "").replace("*", "").replace("^", "")
    t = t.replace("²", "2").replace("√", "sqrt").replace("·", "")
    t = t.replace("×", "").replace("{", "(").replace("}", ")")
    flat = t.replace("(", "").replace(")", "")    # n^(3/2) -> n3/2
    if "sqrt" in t or "n1.5" in flat or "n3/2" in flat:
        return "nsqrtn"
    if "log" in t:
        return "nlogn"
    if re.search(r"o\(n2\)|o\(nn\)", t):
        return "n2"
    return None


# n log n < n√n < n²: a method in a smaller class is also in the bigger ones
CLASS_RANK = {"nlogn": 0, "nsqrtn": 1, "n2": 2}


def adaptive_class_problem(got, regime):
    """6.3.3 gives an upper bound per regime ("must run in O(...)"):
    announcing the regime class is right, a tighter class still meets the
    bound ("warn"), a bigger one breaks it ("ko"). None when equal."""
    if got == regime:
        return None
    return "warn" if CLASS_RANK[got] < CLASS_RANK[regime] else "ko"


class Bench:
    def __init__(self, err):
        self.text = err.decode("utf-8", "replace")
        t = self.text
        m = re.search(r"disorder\s*:\s*(-?\d+(?:\.(\d+))?)\s*%", t)
        self.disorder = m.group(1) if m else None
        self.decimals = len(m.group(2) or "") if m else None
        m = re.search(r"strategy\s*:\s*(.+)", t)
        self.strategy = m.group(1).strip() if m else None
        self.klass = parse_class(self.strategy) if self.strategy else None
        # the subject asks for "the total number of operations"; its example
        # names it total_ops, another label is only reworded
        m = re.search(r"total_ops\s*:\s*(\d+)", t)
        self.total_label = "total_ops" if m else None
        if not m:
            m = re.search(r"(total[ _-]*(?:op(?:eration)?s?|moves?)|"
                          r"op(?:eration)?s[ _-]*total|"
                          r"(?<![\w-])(?:op(?:eration)?s|moves))\s*:\s*(\d+)",
                          t, re.I)
            self.total_label = m.group(1) if m else None
        self.total = int(m.groups()[-1]) if m else None
        pat = r"\b(" + "|".join(sim.OPS) + r")\s*:\s*(\d+)"
        self.counts = {op: int(v) for op, v in re.findall(pat, t)}


def check_bench(ctx, nums, flags, expect_name, expect_class, tag):
    """Run with --bench, check every field. Returns nothing."""
    rep = ctx.rep
    plain = sort_run(ctx, nums, [f for f in flags if f != "--bench"])
    out = sort_run(ctx, nums, flags, bench=True)
    cmd = ctx.cmd(out.argv)
    if not out.ok:
        rep.check(f"{tag}: sorts with --bench", False, detail=out.detail(ctx))
        return
    probs, warns = [], []
    if plain.ok and plain.res.out != out.res.out:
        probs.append("stdout differs from the same run without --bench "
                     f"({plain.nops} vs {out.nops} ops)")
    b = Bench(out.res.err)
    if not b.text.strip():
        rep.check(f"{tag}: bench block on stderr", False,
                  detail=f"input: {cmd}\nstderr is empty")
        return
    if any(not ln.startswith("[bench]")
           for ln in b.text.splitlines() if ln.strip()):
        warns.append("some stderr lines do not start with \"[bench]\"")
    if "[bench]" in out.res.out.decode("utf-8", "replace"):
        probs.append("bench output found on stdout (must be stderr only)")
    exact = sim.disorder(nums) * 100
    if b.disorder is None:
        probs.append('no "disorder: XX.XX%" line')
    else:
        if b.decimals != 2:
            probs.append(f"disorder must have 2 decimals, got {b.disorder}%")
        got = Fraction(b.disorder)
        if abs(got - exact) > Fraction(1, 100):
            probs.append(f"disorder {b.disorder}% but the input's disorder is "
                         f"{float(exact):.4f}% (measure it before any move)")
    if b.strategy is None:
        probs.append('no "strategy: <name> / <class>" line')
    else:
        # the subject wants "the name of the strategy used"; it calls them
        # Simple / Medium / Complex / Adaptive but does not impose the word
        name = re.sub(r"\bO\s*\(.*", "", b.strategy, flags=re.I)
        if not re.search(r"[A-Za-z]", name):
            probs.append(f"strategy {b.strategy!r}: no strategy name "
                         "(subject: the name of the strategy used)")
        elif expect_name.lower() not in b.strategy.lower():
            other = [s for s in ("Simple", "Medium", "Complex", "Adaptive")
                     if re.search(rf"\b{s}\b", name, re.I)]
            picked = (expect_name == "Adaptive" and other
                      and b.klass is not None and b.klass == expect_class)
            if picked:
                # names the method the adaptive picked for this disorder
                # regime: the subject example says "Adaptive / O(...)"
                warns.append(f"strategy {b.strategy!r}: names the method "
                             "the adaptive strategy picked, the subject "
                             "example shows \"Adaptive / "
                             f"{sim.CLASS_NAMES[expect_class]}\"")
            elif other:
                probs.append(f"strategy {b.strategy!r}: {expect_name} was "
                             f"selected, the bench names {other[0]}")
            else:
                warns.append(f"strategy {b.strategy!r} does not say "
                             f"{expect_name!r} (the subject's name for it)")
        if b.klass is None:
            warns.append("could not read a complexity class in "
                         f"{b.strategy!r}")
        elif expect_class and b.klass != expect_class:
            why = adaptive_class_problem(b.klass, expect_class) \
                if expect_name == "Adaptive" else None
            if expect_name != "Adaptive" or why == "ko":
                probs.append(f"class {b.strategy!r}: expected "
                             f"{sim.CLASS_NAMES[expect_class]} here")
            else:
                warns.append(f"class {b.strategy!r}: tighter than the "
                             f"{sim.CLASS_NAMES[expect_class]} bound of "
                             "this disorder regime (fine for the bound; the "
                             "subject example shows the regime's class)")
    if b.total is None:
        probs.append('no "total_ops: N" line')
    elif b.total != out.nops:
        probs.append(f"total_ops {b.total} but stdout has {out.nops} ops")
    if b.total is not None and b.total_label != "total_ops":
        warns.append(f"total named {b.total_label!r}, the subject example "
                     "says \"total_ops\"")
    missing = [op for op in sim.OPS if op not in b.counts]
    if missing:
        probs.append("missing counters: " + ", ".join(missing))
    wrong = [f"{op}: {b.counts[op]} (real {out.sim.count.get(op, 0)})"
             for op in sim.OPS if op in b.counts
             and b.counts[op] != out.sim.count.get(op, 0)]
    if wrong:
        probs.append("wrong counters: " + ", ".join(wrong))
    detail = None
    if probs:
        detail = (f"input: {cmd}\n" + "\n".join(probs) + "\nstderr was:\n"
                  + "\n".join("  " + ln for ln in b.text.splitlines()[:8]))
    rep.check(f"{tag}: bench values", not probs, detail=detail)
    for w in warns:
        rep.warn(f"{tag}: {w}", f"input: {cmd}")
    if rep.verbose and not probs:
        for ln in b.text.splitlines():
            rep.info(ln)


def g_bench(ctx):
    rep = ctx.rep
    rep.section("bench")
    rng = Random(77)
    ex = [4, 67, 3, 87, 23]
    r20 = sim.random_input(rng, 20, -1000, 1000)
    r100 = sim.random_input(rng, 100)
    r500 = sim.random_input(rng, 500)
    adaptive = sim.regime
    check_bench(ctx, ex, ["--bench", "--adaptive"], "Adaptive",
                adaptive(sim.disorder(ex)), "subject example 4 67 3 87 23")
    check_bench(ctx, r100, ["--bench"], "Adaptive",
                adaptive(sim.disorder(r100)), "100 random, --bench only")
    check_bench(ctx, r500, ["--bench"], "Adaptive",
                adaptive(sim.disorder(r500)), "500 random, --bench only")
    for st, cl in (("simple", "n2"), ("medium", "nsqrtn"),
                   ("complex", "nlogn")):
        check_bench(ctx, r20, ["--bench", f"--{st}"], st.capitalize(), cl,
                    f"20 random --bench --{st}")
        check_bench(ctx, r100, [f"--{st}", "--bench"], st.capitalize(), cl,
                    f"100 random --{st} --bench")
    srt = [1, 2, 3, 4, 5]
    out = sort_run(ctx, srt, ["--bench"], bench=True)
    b = Bench(out.res.err)
    if out.ok and (b.total != 0 or b.disorder is None):
        rep.warn("already sorted + --bench: no bench block / total_ops != 0",
                 f"input: {ctx.cmd(out.argv)}\n"
                 f"stderr: {core.crepr(out.res.err, 120)}")
    rep.check("already sorted + --bench: no operation on stdout",
              out.ok and out.nops == 0, detail=None if out.ok and out.nops == 0
              else out.detail(ctx))
    res = ctx.ps([str(n) for n in r20])
    rep.check("without --bench: stderr stays empty", not res.err,
              detail=None if not res.err else
              f"input: {ctx.cmd([str(n) for n in r20])}\nstderr: "
              + core.crepr(res.err, 120))
    out = sort_run(ctx, r20, ["--bench"], after=True, bench=True)
    if not out.ok or not Bench(out.res.err).total:
        rep.warn("flag after the numbers (./push_swap 3 2 1 --bench) "
                 "not handled",
                 out.detail(ctx) if not out.ok else "no bench output")


# --------------------------------------------------------------------------
# strategies + disorder
# --------------------------------------------------------------------------

def g_strategies(ctx):
    rep = ctx.rep
    rep.section("strategies")
    rng = Random(ctx.opts.seed or 2024)
    sizes = (10, 50, 100, 500)
    inputs = {n: [sim.random_input(rng, n)
                  for _ in range(2 if n == 500 else 3)]
              for n in sizes}
    table = {}
    for st in STRATEGIES:
        bad = []
        for n in sizes:
            vals = []
            for nums in inputs[n] + [sorted(inputs[n][0], reverse=True)]:
                out = sort_run(ctx, nums, strat_flag(st),
                               timeout=max(ctx.timeout, 30))
                if out.ok:
                    vals.append(out.nops)
                else:
                    bad.append(out)
            table[(st, n)] = statistics.mean(vals) if vals else None
        rep.check(f"--{st} sorts random + reversed inputs of "
                  f"{', '.join(map(str, sizes))}",
                  not bad, detail=None if not bad else
                  f"{len(bad)} failing; first:\n" + bad[0].detail(ctx))
    if all(table[(st, n)] is None for st in STRATEGIES for n in sizes):
        rep.old_subject("no --simple / --medium / --complex / --adaptive "
                        "selector: v1.2 asks for the four strategies")
    rep.info("average ops (random + reversed):")
    rep.info(f"{'':>10}" + "".join(f"{n:>10}" for n in sizes)
             + "   ratio 500/100")
    expect = {"simple": 25.0, "medium": 11.2, "complex": 6.7}
    for st in STRATEGIES:
        row = "".join(f"{table[(st, n)]:>10.0f}" if table[(st, n)] is not None
                      else f"{'KO':>10}" for n in sizes)
        a, b = table[(st, 100)], table[(st, 500)]
        ratio = b / a if a and b else None
        rep.info(f"--{st:<8}" + row + (f"   x{ratio:.1f}" if ratio else ""))
        if ratio and st in expect:
            if st == "complex" and ratio > 12:
                rep.warn("--complex grows like O(n²)?",
                         f"ops x{ratio:.1f} from 100 to 500 numbers "
                         "(n log n gives ~x6.7, n√n ~x11.2, n² ~x25)")
            if st == "medium" and ratio > 18:
                rep.warn("--medium grows like O(n²)?",
                         f"ops x{ratio:.1f} from 100 to 500 numbers "
                         "(n√n gives ~x11.2)")
    rep.info("expected growth 100 -> 500: n² x25, n√n x11.2, n log n x6.7")
    out = sort_run(ctx, [5, 4, 3, 2, 1], ["--simple"])
    rep.check(f"subject example --simple 5 4 3 2 1 ({out.nops} ops)", out.ok,
              detail=None if out.ok else out.detail(ctx))
    out = sort_run(ctx, [4, 67, 3, 87, 23], ["--complex"])
    rep.check(f"subject example --complex 4 67 3 87 23 ({out.nops} ops)",
              out.ok,
              detail=None if out.ok else out.detail(ctx))
    out = sort_run(ctx, inputs[100][0], ["--complex"], after=True)
    if not out.ok:
        rep.warn("selector after the numbers (./push_swap 3 2 1 --complex) "
                 "not handled", out.detail(ctx))
    out = sort_run(ctx, inputs[10][0], ["--simple", "--complex"])
    if not out.ok:
        rep.warn("two selectors (--simple --complex) not handled",
                 out.detail(ctx) + "\n(not specified by the subject)")


DISORDER_TARGETS = [(0.0, "sorted"), (0.05, "low"), (0.15, "low"),
                    (0.19, "low, near 0.2"), (0.2, "exactly 0.2"),
                    (0.35, "medium"), (0.49, "medium, near 0.5"),
                    (0.5, "exactly 0.5"), (0.75, "high"), (0.95, "high"),
                    (1.0, "reversed")]


def g_disorder(ctx):
    rep = ctx.rep
    rep.section("disorder")
    rep.info("adaptive regimes: < 0.2 O(n²), [0.2, 0.5) O(n√n), "
             ">= 0.5 O(n log n)")
    rep.info(f"{'disorder':>10}{'n=100 ops':>12}{'n=500 ops':>12}"
             "   bench class")
    seed = ctx.opts.seed or 31337
    unread = []     # runs whose --bench gave no readable complexity class
    for target, label in DISORDER_TARGETS:
        cells, classes = [], []
        for n in (100, 500):
            rng = Random(seed + n + int(target * 1000))
            nums = sim.with_disorder(rng, n, target)
            d = sim.disorder(nums)
            out = sort_run(ctx, nums, ["--bench"], bench=True,
                           timeout=max(ctx.timeout, 30))
            name = f"n={n} disorder {float(d):.4f} ({label})"
            if not out.ok:
                rep.check(name + ": sorted", False, detail=out.detail(ctx))
                cells.append("KO")
                continue
            rep.check(name + ": sorted", True)
            cells.append(str(out.nops))
            b = Bench(out.res.err)
            if b.klass is None:
                classes.append("?")
                if d != 0:
                    unread.append((name, b.strategy))
                continue
            classes.append(sim.CLASS_NAMES[b.klass])
            if d == 0:
                continue
            want = sim.regime(d)
            why = adaptive_class_problem(b.klass, want)
            what = f"{name}: adaptive announces {sim.CLASS_NAMES[want]}"
            said = (f"input: {ctx.cmd(out.argv)}\nbench said: {b.strategy}")
            if why == "warn":
                rep.warn(what + f" (it says {sim.CLASS_NAMES[b.klass]})",
                         said + "\nsubject 6.3.3: the method of this regime "
                         f"must run in {sim.CLASS_NAMES[want]}; a tighter "
                         "class meets that bound, the evaluator judges the "
                         "design")
                continue
            rep.check(what, why is None,
                      detail=None if why is None else
                      said + "\nsubject 6.3.3: the method of this regime "
                      f"must run in {sim.CLASS_NAMES[want]}, "
                      f"{sim.CLASS_NAMES[b.klass]} is above that bound")
        second = cells[1] if len(cells) > 1 else ""
        rep.info(f"{target:>10.2f}{cells[0]:>12}{second:>12}"
                 f"   {' / '.join(classes)}")
    rep.info("note: ops at low disorder are not comparable to the "
             "random-input "
             "thresholds; the class is read from --bench")
    if unread:
        said = sorted({repr(s) if s else "no \"strategy:\" line"
                       for _, s in unread})
        rep.warn(f"adaptive regime not checked on {len(unread)} of "
                 f"{2 * (len(DISORDER_TARGETS) - 1)} runs: no complexity "
                 "class could be read from --bench",
                 "bench said: " + ", ".join(said[:4])
                 + f"\nfirst run: {unread[0][0]}"
                 "\nwrite the class as O(n²) / O(n√n) / O(n log n) "
                 "(or n^2, n*sqrt(n), n^1.5, n log n)")


# --------------------------------------------------------------------------
# checker (official cross-check + bonus)
# --------------------------------------------------------------------------

def bonus_checker_path(ctx):
    for name in ("checker", "checker_bonus"):
        p = ctx.ws.path(name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def g_checker(ctx):
    rep = ctx.rep
    rep.section("checker")
    if ctx.checker:
        rep.info(f"official checker: {ctx.checker_name}")
        rng = Random(9)
        disagree = []
        total = 0
        for n in (2, 3, 5, 10, 100, 500):
            for _ in range(3):
                nums = sim.random_input(rng, n)
                out = sort_run(ctx, nums, timeout=max(ctx.timeout, 30))
                if out.parsed is None:
                    continue
                total += 1
                verdict = ctx.official(nums, out.res.out)
                mine = "OK" if out.ok else "KO"
                if verdict != mine:
                    disagree.append(f"{ctx.cmd(out.argv)}: simulator {mine}, "
                                    f"official {verdict}")
                elif not out.ok:
                    rep.check(f"official checker on {n} numbers", False,
                              detail=out.detail(ctx))
        rep.check(f"official checker agrees with the simulator ({total} runs)",
                  not disagree, detail="\n".join(disagree[:4]) or None)
    else:
        rep.skip("official checker", "none found that runs here "
                 "(looked in ~/Downloads/checker, the project; "
                 "--checker PATH)")
    chk = bonus_checker_path(ctx)
    if not chk:
        rep.skip("bonus checker", "no ./checker built (no bonus part in v1.2)")
        return
    rep.old_subject("a bonus ./checker: v1.2 has no bonus part")
    test_bonus_checker(ctx, chk)


def test_bonus_checker(ctx, chk):
    """No bonus part in v1.2: a ./checker is tested, a failure is a WARN."""
    rep = ctx.rep

    def soft(name, ok, detail=None):
        if ok:
            rep.check(name, True)
        else:
            rep.warn(name + "  (no bonus part in subject v1.2)", detail)

    def call(args, stdin):
        return run([chk, *args], cwd=ctx.ws.dir, stdin=stdin,
                   timeout=ctx.timeout)

    def expect(name, args, stdin, out=None, err=None):
        res = call(args, stdin)
        probs = []
        if res.timed_out or res.crashed:
            probs.append(res.status())
        else:
            if out is not None and res.out != out:
                probs.append(f"stdout: expected {core.crepr(out)}, "
                             f"got {core.crepr(res.out)}")
            if err is not None and res.err != err:
                probs.append(f"stderr: expected {core.crepr(err)}, "
                             f"got {core.crepr(res.err)}")
        cmd = f"printf {shlex.quote(stdin)} | ./checker " + shlex.join(args)
        soft(f"bonus checker: {name}", not probs,
             detail=None if not probs else cmd + "\n" + "\n".join(probs))

    expect("sa sorts 2 1 -> OK", ["2", "1"], "sa\n", b"OK\n", b"")
    expect("no op on sorted -> OK", ["1", "2", "3"], "", b"OK\n", b"")
    expect("no op on unsorted -> KO", ["2", "1"], "", b"KO\n", b"")
    expect("wrong ops -> KO", ["3", "2", "1"], "ra\n", b"KO\n", b"")
    expect("b not empty -> KO", ["1", "2", "3"], "pb\n", b"KO\n", b"")
    expect("full sequence -> OK", ["3", "1", "2"], "pb\nsa\npa\nrra\nra\nsa\n",
           None, None)
    expect("invalid op -> Error", ["2", "1"], "xx\n", b"", b"Error\n")
    expect("op with space -> Error", ["2", "1"], " sa\n", b"", b"Error\n")
    expect("op without \\n at the end", ["2", "1"], "sa", None, None)
    expect("duplicate args -> Error", ["1", "1"], "", b"", b"Error\n")
    expect("overflow arg -> Error", ["2147483648"], "", b"", b"Error\n")
    expect("no argument -> nothing", [], "", b"", b"")
    rng = Random(5)
    nums = sim.random_input(rng, 100)
    out = sort_run(ctx, nums)
    if out.ok:
        res = call([str(n) for n in nums], out.res.out)
        soft("bonus checker: push_swap 100 output -> OK",
             res.out == b"OK\n",
             detail="got: " + core.crepr(res.out + res.err))
    if ctx.quick:
        return
    res, leaks = core.valgrind([chk, "3", "2", "1"], cwd=ctx.ws.dir,
                               stdin=b"sa\nrra\n", timeout=60)
    if leaks:
        soft("bonus checker: no leak", leaks.clean,
             detail=None if leaks.clean else leaks.describe() + "\n"
             + leaks.first_error())


# --------------------------------------------------------------------------
# leaks
# --------------------------------------------------------------------------

def g_leaks(ctx):
    rep = ctx.rep
    rep.section("leaks")
    if ctx.quick:
        rep.skip("valgrind", "--quick")
        return
    if not core.have("valgrind"):
        rep.skip("valgrind", "valgrind not installed")
        return
    rng = Random(8)
    r5 = [str(n) for n in sim.random_input(rng, 5)]
    r100 = [str(n) for n in sim.random_input(rng, 100)]
    r500 = [str(n) for n in sim.random_input(rng, 500)]
    normal = [("5 random", r5), ("100 random", r100), ("500 random", r500),
              ("100 --bench", ["--bench"] + r100),
              ("already sorted", ["1", "2", "3"]),
              ("one number", ["42"]), ("no argument", [])]
    normal += [(f"100 --{s}", [f"--{s}"] + r100) for s in STRATEGIES]
    if strings_supported(ctx)[0]:
        normal.append(("one string \"3 2 1\"", ["3 2 1"]))
        normal.append(("string + args", ["3 2", "1", "-5 7"]))
    errors = [("error: duplicate", ["1", "2", "1"]),
              ("error: duplicate at the end of 100", r100 + [r100[0]]),
              ("error: non-numeric", ["1", "abc"]),
              ("error: non-numeric after many", r100 + ["x"]),
              ("error: overflow", ["1", "2147483648"]),
              ("error: empty argument", [""]),
              ("error: bad string", ["1 2 x"]),
              ("error: --bench + duplicate", ["--bench", "5", "5"])]
    cases = normal + errors

    def one(case):
        return core.valgrind([ctx.binary, *case[1]], cwd=ctx.ws.dir,
                             timeout=max(120, ctx.timeout * 6))

    # every valgrind run at the same time, reported in order
    workers = max(1, min(len(cases), os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, cases))
    for (name, argv), (res, leaks) in zip(cases, results):
        cmd = ctx.cmd(argv, 120).replace(
            "./push_swap", "valgrind --leak-check=full ./push_swap", 1)
        if res.timed_out or res.crashed:
            rep.check(name, False, detail=f"{cmd}\n{res.status()}")
            continue
        rep.check(name, leaks.clean,
                  detail=None if leaks.clean else
                  f"{cmd}\n{leaks.describe()}\n{leaks.first_error()}")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    args = core.parse_args("push_swap tester (subject v1.2)", GROUPS)
    opts = parse_extra(args.extra)
    opts.quick = getattr(args, "quick", False)
    if not os.path.isdir(args.project):
        print(core.red(f"project not found: {args.project}"))
        return 2
    rep = Report(args.verbose)
    sel = (lambda g: core.selected(args, g)
           and (g != "perf" or "perf" in args.only or opts.size is not None))
    needs_bin = [g for g, _ in GROUPS if g not in ("norm", "readme", "repo")]
    core.header("push_swap", args.project, subject="1.2")
    if opts.quick:
        core.quick_skip(rep, "no valgrind, p100 3 runs, p500 1 run, "
                        "24 of the 120 five-number permutations")
    with core.Workspace(args.project, keep=args.keep,
                        prefix="42next-ps-") as ws:
        ctx = Ctx(rep, ws, opts)
        if sel("norm"):
            g_norm(ctx)
        if sel("readme"):
            g_readme(ctx)
        if sel("repo"):
            g_repo(ctx)
        if not any(sel(g) for g in needs_bin):
            return rep.finish()
        if not build(ctx, sel("makefile")) or not os.path.exists(ctx.binary):
            if not os.path.exists(ctx.binary):
                run(["make"], cwd=ws.dir, timeout=300)
            if not os.path.exists(ctx.binary):
                rep.section("build")
                rep.check("push_swap binary available for the tests", False,
                          detail="nothing else can be tested")
                return rep.finish()
        detect_checker(ctx)
        if sel("functions"):
            bins = [ctx.binary] + ([bonus_checker_path(ctx)]
                                   if bonus_checker_path(ctx) else [])
            g_functions(ctx, bins)
        if sel("errors"):
            g_errors(ctx)
        if sel("identity"):
            g_identity(ctx)
        if sel("small"):
            g_small(ctx)
        if sel("p100"):
            g_p(ctx, 100, 10)
        if sel("p500"):
            g_p(ctx, 500, 10)
        if sel("disorder"):
            g_disorder(ctx)
        if sel("strategies"):
            g_strategies(ctx)
        if sel("bench"):
            g_bench(ctx)
        if sel("checker"):
            g_checker(ctx)
        if sel("leaks"):
            g_leaks(ctx)
        if sel("perf"):
            g_perf(ctx)
        return rep.finish()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # ctrl-c: the temporary copies are removed on the way out
        print("\nstopped (ctrl-c)")
        sys.exit(130)
