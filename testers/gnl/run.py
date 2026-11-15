#!/usr/bin/env python3
"""42next tester for get_next_line (Common Core).

Every scenario is a separate process (a crash or an infinite loop only fails
that scenario). The runner is linked with -Wl,--wrap=read so the tests can
count the bytes read and make read() fail on purpose. Apple ld64 has no
--wrap: on macOS those checks (and the per-call malloc tracking) are skipped.
"""

import itertools
import os
import random
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import core  # noqa: E402

MAND = ["get_next_line.c", "get_next_line_utils.c", "get_next_line.h"]
BONUS = ["get_next_line_bonus.c", "get_next_line_utils_bonus.c",
         "get_next_line_bonus.h"]
ALLOWED = {"read", "malloc", "free"}
NOT_CALLS = {"GLOBAL_OFFSET_TABLE_", "_GLOBAL_OFFSET_TABLE_"}
SIZES = [1, 2, 3, 5, 10, 42, 100, 1000, 9999, 10000, 1000000, 10000000, None]
QUICK_SIZES = [1, 42, 1000, None]
MAX_KO = 15
RUN_ENV = {"ASAN_OPTIONS": "detect_leaks=0:exitcode=86:abort_on_error=0"}
MEM_SIZES = (1, 42, 10000000)
TIMEOUT = 15        # seconds per run (slowest correct run seen: 2.5s)
VG_TIMEOUT = 120    # seconds per valgrind run
MAX_TIMEOUTS = 2    # a group stops after this many runs timed out
NOT_RUN = object()  # ctx.run() result when the group was stopped
WRAP_OK = sys.platform != "darwin"
NO_WRAP_WHY = "needs GNU ld --wrap, not available on macOS"

GROUPS = [("files", "required files and prototype"),
          ("norm", "norminette"),
          ("functions", "allowed functions, no global variable, no libft"),
          ("readme", "README.md"),
          ("repo", "repository hygiene"),
          ("basic", "basic reading (BUFFER_SIZE=42)"),
          ("buffer_sizes", "BUFFER_SIZE 1 .. 10000000 and default"),
          ("stdin", "standard input, pipes"),
          ("edge", "empty files, invalid fd, read errors, read as little "
                   "as possible"),
          ("leaks", "memory: valgrind (or ASan) with BUFFER_SIZE 1, 42, "
                    "10000000"),
          ("bonus", "bonus files: several fd at the same time (if present)")]


def bs_name(bs):
    return "no -D BUFFER_SIZE" if bs is None else f"BUFFER_SIZE={bs}"


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def make_fixtures():
    rng = random.Random(42)
    fx = {}
    fx["empty"] = b""
    fx["one_newline"] = b"\n"
    fx["only_newlines"] = b"\n" * 10
    fx["one_char"] = b"a"
    fx["one_line_no_nl"] = b"Hello, world!"
    fx["one_line_nl"] = b"Hello, world!\n"
    fx["three_lines"] = b"line 1\nline 2\nline 3\n"
    fx["three_lines_no_nl"] = b"line 1\nline 2\nline 3"
    fx["empty_lines_mixed"] = b"\n\nabc\n\n\ndef\n\n"
    fx["text"] = ("First line\n\tindented with a tab\nspaces   inside  \n"
                  "utf-8: héllo wörld €\ncarriage\r return\n"
                  "% and \\ and \"quotes\"\nlast line without newline"
                  ).encode()
    boundary = []
    for n in list(range(1, 71)) + [99, 100, 101, 999, 1000, 1001, 9998, 9999,
                                   10000, 10001]:
        boundary.append(bytes(48 + (n % 75) for _ in range(1)) * n + b"\n")
    fx["lengths_1_to_10001"] = b"".join(boundary)
    fx["long_line_2000"] = b"z" * 2000 + b"\nend"
    fx["long_line_12000"] = b"x" * 12000 + b"\nshort\n" + b"y" * 12000
    fx["long_line_30000_nl"] = (b"".join(bytes([97 + i % 26])
                                         for i in range(30000)) + b"\n")
    lines = []
    for _ in range(2000):
        n = rng.choice([0, 1, 2, 5, 10, 41, 42, 43, 80, 200])
        lines.append(bytes(rng.randint(33, 126) for _ in range(n)) + b"\n")
    fx["2000_random_lines"] = b"".join(lines)
    return fx


BASIC = ["empty", "one_newline", "only_newlines", "one_char",
         "one_line_no_nl", "one_line_nl", "three_lines", "three_lines_no_nl",
         "empty_lines_mixed", "text"]
LONG = ["lengths_1_to_10001", "long_line_12000", "long_line_30000_nl",
        "2000_random_lines"]


def split_lines(data):
    out = []
    i = 0
    while i < len(data):
        j = data.find(b"\n", i)
        if j < 0:
            out.append(data[i:])
            break
        out.append(data[i:j + 1])
        i = j + 1
    return out


def describe(data):
    n = len(split_lines(data))
    end = "ends with \\n" if data.endswith(b"\n") else (
        "last line without \\n" if data else "")
    return f"{len(data)} bytes, {n} line(s)" + (f", {end}" if end else "")


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------

def parse_out(path):
    items, info = [], {}
    if not os.path.isfile(path):
        return items, info
    with open(path, "rb") as fh:
        data = fh.read()
    i = 0
    while i < len(data):
        c = data[i:i + 1]
        if c == b"N":
            items.append(None)
            i += 2
        elif c == b"L":
            colon = data.index(b":", i)
            n = int(data[i + 1:colon])
            items.append(data[colon + 1:colon + 1 + n])
            i = colon + 1 + n + 1
        elif c in (b"F", b"I"):
            end = data.index(b"\n", i)
            text = data[i + 1:end].decode()
            if c == b"I":
                k, v = text.split("=", 1)
                info[k] = v
            else:
                items.append(("F", int(text)))
            i = end + 1
        else:
            break
    return items, info


class Ctx:
    def __init__(self, ws, rep, args):
        self.ws = ws
        self.rep = rep
        self.args = args
        self.bins = {}
        self.errors = {}
        self.counter = itertools.count(1)   # thread-safe
        self.timeouts = 0       # runs timed out in the current group
        self.lock = threading.Lock()
        self.wrap_noted = False
        self.fx = make_fixtures()
        self.fxdir = ws.tmp("fixtures")
        os.makedirs(self.fxdir, exist_ok=True)
        for name, data in self.fx.items():
            with open(os.path.join(self.fxdir, name), "wb") as fh:
                fh.write(data)

    def fpath(self, name):
        return os.path.join(self.fxdir, name)

    def binary(self, bs, bonus=False, asan=False):
        key = (bs, bonus, asan)
        if key in self.bins or key in self.errors:
            return self.bins.get(key)
        files = BONUS if bonus else MAND
        srcs = [self.ws.path(f) for f in files if f.endswith(".c")]
        out = self.ws.tmp(f"runner_{bs}_{'b' if bonus else 'm'}"
                          f"{'_asan' if asan else ''}")
        flags = ["-g", "-Wl,--wrap=read,--wrap=malloc,--wrap=free"
                 if WRAP_OK else "-DNEXT42_NO_WRAP"]
        if asan:
            flags += ["-fsanitize=address", "-fno-omit-frame-pointer"]
        if bs is not None:
            flags = ["-D", f"BUFFER_SIZE={bs}"] + flags
        ok, text = core.compile_c([os.path.join(HERE, "runner.c"), *srcs],
                                  out, includes=[self.ws.dir], flags=flags)
        if ok:
            self.bins[key] = out
        else:
            self.errors[key] = core.short_errors(
                text.replace(self.ws.dir + "/", ""))
        return self.bins.get(key)

    def prebuild(self, keys):
        """Compile several runners at the same time: (bs, bonus, asan)."""
        todo = [k for k in dict.fromkeys(keys)
                if k not in self.bins and k not in self.errors]
        parallel(lambda k: self.binary(*k), todo)

    def run(self, bs, mode, *margs, stdin=b"", bonus=False, vg=False,
            asan=False):
        """-> (Run, items, info, leaks) ; Run is None if it did not build,
        NOT_RUN if the group already had MAX_TIMEOUTS timeouts."""
        b = self.binary(bs, bonus, asan)
        if not b:
            return None, [], {}, None
        if self.timeouts >= MAX_TIMEOUTS:
            return NOT_RUN, [], {}, None
        out = self.ws.tmp(f"out_{next(self.counter)}")
        cmd = [b, mode, out, *map(str, margs)]
        leaks = None
        if vg:
            res, leaks = core.valgrind(cmd, cwd=self.ws.root, stdin=stdin,
                                       timeout=VG_TIMEOUT)
        else:
            res = core.run(cmd, cwd=self.ws.root, stdin=stdin,
                           timeout=TIMEOUT, env=RUN_ENV)
        if res is not None and res.timed_out:
            with self.lock:
                self.timeouts += 1
        items, info = parse_out(out)
        return res, items, info, leaks

    def note_no_wrap(self):
        """macOS: say once that the --wrap checks are skipped."""
        if not WRAP_OK and not self.wrap_noted:
            self.wrap_noted = True
            self.rep.skip("per-call leak tracking (malloc wrapper)",
                          NO_WRAP_WHY)


# --------------------------------------------------------------------------
# comparing
# --------------------------------------------------------------------------

def compare_lines(expected, items, after_eof=2):
    """expected: list of bytes. items: what the runner recorded (read_all).

    -> list of problem strings (empty = OK).
    """
    want = list(expected) + [None] * (1 + after_eof)
    probs = []
    for i, w in enumerate(want):
        g = items[i] if i < len(items) else "missing"
        if g == w:
            continue
        if g == "missing":
            probs.append(f"call {i + 1}: no result (the run stopped here)")
            break
        if i < len(expected):
            probs.append(f"line {i + 1}/{len(expected)}: expected "
                         f"{core.crepr(w)}, got {core.crepr(g)}"
                         + _byte_hint(w, g))
        elif i == len(expected):
            probs.append(f"call {i + 1} (end of file): expected NULL, got "
                         f"{core.crepr(g)}")
        else:
            probs.append(f"call {i + 1} (again after end of file): expected "
                         f"NULL, got {core.crepr(g)}")
        if len(probs) >= 3:
            break
    return probs


def _byte_hint(w, g):
    if w is None or g is None:
        return ""
    idx = core.first_diff(w, g)
    if idx is None:
        return ""
    return f" (first difference at byte {idx}, lengths {len(w)} vs {len(g)})"


def run_problem(res, bs):
    if res is None:
        return None
    if res.timed_out:
        return (f"timeout after {res.elapsed:.0f}s (infinite loop or "
                "blocking read?)")
    if res.crashed:
        hint = ""
        if bs and bs >= 1000000:
            hint = " (a buffer of BUFFER_SIZE bytes on the stack?)"
        return f"{res.status()}{hint}"
    if res.rc == 86:
        return ("memory error found by AddressSanitizer\n"
                + (asan_report(res.err.decode("utf-8", "replace")) or ""))
    if res.rc != 0:
        return f"runner {res.status()}: " + res.err.decode(
            "utf-8", "replace").strip()[:200]
    return None


class Shown:
    """Limits the number of detailed KO per section, and stops the section
    after MAX_TIMEOUTS timeouts (a looping get_next_line would otherwise
    cost TIMEOUT seconds per test)."""

    def __init__(self, ctx):
        self.rep = ctx.rep
        self.n = 0
        self.hidden = 0
        self.hung = []
        self.not_run = 0
        ctx.timeouts = 0
        ctx.note_no_wrap()

    def skipped(self, label, res):
        """True if the section was stopped before this test ran."""
        if res is NOT_RUN:
            self.not_run += 1
            return True
        if res is not None and res.timed_out:
            self.hung.append(label)
        return False

    def ko(self, name, detail):
        if self.n >= MAX_KO and not self.rep.verbose:
            self.hidden += 1
            return
        self.n += 1
        self.rep.check(name, False, detail=detail)

    def close(self):
        if self.hidden:
            if self.rep.current is not None:
                self.rep.current[2] += self.hidden
            print(core.red(f"  ... {self.hidden} more failing case(s) in "
                           "this group (use --verbose to see all)"))
        if self.not_run:
            more = len(self.hung) - 8
            self.rep.check(
                f"group stopped after {len(self.hung)} timeouts: "
                f"{self.not_run} more test(s) not run", False,
                detail="tests that hung:\n"
                + "\n".join("  " + h for h in self.hung[:8])
                + (f"\n  ... and {more} more" if more > 0 else ""))
        self.n = self.hidden = self.not_run = 0
        self.hung = []


def check_build(ctx, bs, bonus=False, asan=False):
    if ctx.binary(bs, bonus, asan):
        return True
    ctx.rep.check(f"compile with {bs_name(bs)}" + (" (bonus)" if bonus
                                                   else "")
                  + (" -fsanitize=address" if asan else ""), False,
                  detail=ctx.errors.get((bs, bonus, asan)))
    return False


def leak_problem(info):
    """Per-call malloc tracking: blocks from get_next_line never freed."""
    blocks, _, nbytes = info.get("leak", "0:0").partition(":")
    if int(blocks or 0) == 0:
        return None
    return (f"memory leak: {nbytes} byte(s) in {blocks} block(s) malloc'ed "
            "by get_next_line are still not freed after the last call "
            "(end of file reached)")


def asan_report(err):
    lines = err.splitlines()
    head = next((ln for ln in lines if "ERROR: AddressSanitizer" in ln), None)
    if head is None:
        return None
    head = re.sub(r"^==\d+==\s*", "", head)
    frames = [re.sub(r" 0x[0-9a-f]+ in ", " ", ln.strip()) for ln in lines
              if re.match(r"\s*#\d+ ", ln)]
    frames = [f for f in frames if "__wrap_" not in f][:5]
    text = "\n".join([head] + ["  " + f for f in frames])
    return re.sub(r"/\S*/42next-gnl-[^/]+/(project/)?", "", text)


def parallel(func, jobs):
    """func(job) for every job, several at a time, results in order."""
    if not jobs:
        return []
    workers = max(1, min(len(jobs), os.cpu_count() or 2))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(func, jobs))


def file_run(ctx, bs, name, bonus=False):
    exp = split_lines(ctx.fx[name])
    return ctx.run(bs, "read", ctx.fpath(name), len(exp) + 5, bonus=bonus)


def file_case(ctx, shown, bs, name, bonus=False, result=None):
    """result: an earlier file_run() of the same case, if already run."""
    data = ctx.fx[name]
    exp = split_lines(data)
    res, items, info, _ = result or file_run(ctx, bs, name, bonus)
    label = f"{bs_name(bs)}  {name}  ({describe(data)})"
    if shown.skipped(label, res):
        return
    prob = run_problem(res, bs)
    probs = compare_lines(exp, items)
    leak = leak_problem(info)
    if leak and not prob:
        probs.append(leak)
    if prob:
        good = sum(1 for a, b in zip(exp, items) if a == b)
        shown.ko(label, f"{prob} after {good} correct line(s)"
                 + ("\n" + "\n".join(probs[:2]) if probs else ""))
    elif probs:
        shown.ko(label, "\n".join(probs))
    else:
        ctx.rep.check(label, True)


# --------------------------------------------------------------------------
# groups
# --------------------------------------------------------------------------

def group_norm(rep, ws):
    rep.section("norminette")
    files = [f for f in MAND + BONUS if os.path.isfile(ws.path(f))]
    if not files:
        rep.check("files to check", False, detail="no get_next_line file")
        return
    core.check_norm(rep, ws.dir, files)


def compile_objects(ws, files, tag):
    objs, errors = [], []
    for f in files:
        if not f.endswith(".c") or not os.path.isfile(ws.path(f)):
            continue
        out = ws.tmp(f"{tag}_{f[:-2]}.o")
        ok, text = core.compile_c([ws.path(f)], out, includes=[ws.dir],
                                  flags=["-c", "-D", "BUFFER_SIZE=42"])
        if ok:
            objs.append(out)
        else:
            errors.append(f"{f}: {core.short_errors(text, 4)}")
    return objs, errors


# nm kinds of variables: bss, common, data, small data, read-only data
# (const), small bss, weak object. Upper case = global, lower case = static.
DATA_KINDS = set("BCDGRSV")


def nm_data_symbols(objs):
    """-> (globals, file statics, function statics) from nm.

    "Global variables are forbidden": that is every variable at file scope,
    static or not, const or not. A static variable inside a function is
    allowed (get_next_line needs one); gcc and clang name it with a dot
    (buf.0, get_next_line.buf), which no file-scope C name can have.
    """
    glob, fstat, local = [], [], []
    for o in objs:
        res = core.run(["nm", o], timeout=30)
        for line in res.out.decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) != 3:
                continue
            kind, name = parts[1], parts[2]
            if kind.upper() not in DATA_KINDS:
                continue
            if "." in name:
                local.append(name)
            elif kind.isupper():
                glob.append(name)
            else:
                fstat.append(name)
    return glob, fstat, local


def group_functions(rep, ws):
    rep.section("functions and global variables")
    for tag, files in (("mandatory", MAND), ("bonus", BONUS)):
        present = [f for f in files if os.path.isfile(ws.path(f))]
        if tag == "bonus" and not present:
            continue
        objs, errors = compile_objects(ws, files, tag)
        if errors:
            rep.check(f"{tag} files compile with -c", False,
                      detail="\n".join(errors))
        if not objs:
            continue
        syms = core.external_symbols(objs)
        if syms is None:
            rep.skip("external functions", "nm not installed")
        else:
            bad = [s for s in syms if s not in ALLOWED and s not in NOT_CALLS
                   and s not in core.COMPILER_ALIASES]
            bad += core.written_aliases(
                syms, ALLOWED, ws.dir,
                stems={f[:-2] for f in present if f.endswith(".c")})
            rep.check(f"{tag}: external functions only read, malloc, free",
                      not bad, detail=("forbidden: " + ", ".join(bad))
                      if bad else None)
        glob, fstat, _ = nm_data_symbols(objs)
        rep.check(f"{tag}: no global variable", not glob and not fstat,
                  detail=("global: " + ", ".join(
                      glob + [f"{x} (static at file scope)" for x in fstat]))
                  if glob or fstat else None)
        libft = []
        for f in present:
            with open(ws.path(f), encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if re.search(r'#\s*include\s*[<"][^>"]*libft', text):
                libft.append(f)
        if os.path.isdir(ws.path("libft")):
            libft.append("libft/ folder")
        rep.check(f"{tag}: libft not used", not libft,
                  detail=("libft used in: " + ", ".join(libft))
                  if libft else None)


def group_files(rep, ws):
    rep.section("files")
    for f in MAND:
        rep.check(f"{f} at the root", os.path.isfile(ws.path(f)))
    hdr = ws.path("get_next_line.h")
    if os.path.isfile(hdr):
        with open(hdr, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        proto = re.search(r"char\s*\*\s*get_next_line\s*\(\s*int(\s+\w+)?\s*"
                          r"\)\s*;", text)
        rep.check("get_next_line.h contains char *get_next_line(int fd);",
                  bool(proto))
    present = [f for f in BONUS if os.path.isfile(ws.path(f))]
    if present:
        rep.info("bonus files: " + ", ".join(present))


def group_basic(ctx):
    rep = ctx.rep
    rep.section("basic reading (BUFFER_SIZE=42)")
    if not check_build(ctx, 42):
        return
    shown = Shown(ctx)
    for name in BASIC + ["2000_random_lines"]:
        file_case(ctx, shown, 42, name)
    shown.close()


def group_sizes(ctx, sizes):
    rep = ctx.rep
    rep.section("BUFFER_SIZE values")
    shown = Shown(ctx)
    ctx.prebuild([(bs, False, False) for bs in sizes])
    jobs = []
    for bs in sizes:
        if not ctx.binary(bs):
            continue
        names = BASIC + LONG
        if bs is not None and bs >= 1000000:
            names = ["one_line_no_nl", "three_lines", "three_lines_no_nl",
                     "lengths_1_to_10001", "long_line_30000_nl"]
        jobs += [(bs, name) for name in names]
    results = dict(zip(jobs, parallel(lambda j: file_run(ctx, *j), jobs)))
    # report in the same order as before
    for bs in sizes:
        if not check_build(ctx, bs):
            continue
        rep.check(f"compiles with {bs_name(bs)}", True)
        for job in [j for j in jobs if j[0] == bs]:
            file_case(ctx, shown, bs, job[1], result=results[job])
    shown.close()


def group_stdin(ctx, sizes):
    rep = ctx.rep
    rep.section("standard input and pipes")
    shown = Shown(ctx)
    for bs in sizes:
        if not check_build(ctx, bs):
            continue
        for name in ("three_lines", "three_lines_no_nl", "empty",
                     "long_line_12000"):
            data = ctx.fx[name]
            exp = split_lines(data)
            for mode, how in (("fd0", "fd 0 = pipe"),
                              ("stdinfile", "fd 0 = file"),
                              ("slowpipe", "pipe written 7 bytes at a time")):
                if mode == "slowpipe" and name == "long_line_12000":
                    continue
                if mode == "fd0":
                    res, items, info, _ = ctx.run(bs, "fd0", len(exp) + 5,
                                                  stdin=data)
                else:
                    res, items, info, _ = ctx.run(bs, mode, ctx.fpath(name),
                                                  len(exp) + 5)
                label = f"{bs_name(bs)}  {how}  {name} ({describe(data)})"
                if shown.skipped(label, res):
                    continue
                prob = run_problem(res, bs)
                probs = compare_lines(exp, items)
                if not prob and leak_problem(info):
                    probs.append(leak_problem(info))
                if prob or probs:
                    shown.ko(label, "\n".join(([prob] if prob else [])
                                              + probs))
                else:
                    rep.check(label, True)
    shown.close()


def check_readerr(ctx, shown, bs, k, after=0, name="2000_random_lines"):
    """read k lines, read() gives `after` more bytes then fails, then a new
    fd is read from the start.

    after > 0 (with a long first line): the error comes while get_next_line
    holds part of a line without its newline. The subject: NULL when "an
    error occurred", so that partial line must not be returned.
    """
    exp = split_lines(ctx.fx[name])
    margs = [ctx.fpath(name), k, len(exp) + 5] + ([after] if after else [])
    res, items, info, _ = ctx.run(bs, "readerr", *margs)
    if after:
        label = (f"{bs_name(bs)}  {name}: read() gives {after} byte(s) of "
                 "the first line, then returns -1: NULL (not the partial "
                 "line), then another fd is read from its first line")
    else:
        label = (f"{bs_name(bs)}  {k} line(s) read, then read() returns -1: "
                 "NULL, then another fd is read from its first line")
    if shown.skipped(label, res):
        return
    prob = run_problem(res, bs)
    if prob:
        shown.ko(label, prob)
        return
    probs = []
    for i in range(k):
        if i >= len(items) or items[i] != exp[i]:
            probs.append(f"line {i + 1} before the error: expected "
                         f"{core.crepr(exp[i])}, got "
                         f"{core.crepr(items[i]) if i < len(items) else '-'}")
            break
    # during the error, a line already in memory may still be returned
    # (no read() needed); anything else must be NULL
    nxt = k
    for j in (k, k + 1):
        g = items[j] if j < len(items) else "missing"
        if g is None:
            continue
        if nxt < len(exp) and g == exp[nxt]:
            nxt += 1
            continue
        probs.append(f"call {j + 1} (read() fails): expected NULL, got "
                     f"{core.crepr(g) if g != 'missing' else 'nothing'}")
    probs += [p.replace("line ", "new fd, line ", 1)
              for p in compare_lines(exp, items[k + 2:])]
    if leak_problem(info):
        probs.append(leak_problem(info) + " (the data of the fd that "
                     "failed was not freed?)")
    if probs:
        shown.ko(label, "\n".join(probs[:4]))
    else:
        ctx.rep.check(label, True)


def check_badfd(ctx, shown, bs):
    res, items, _, _ = ctx.run(bs, "badfd", ctx.fpath("three_lines"))
    want = [None] * 5 + [b"line 1\n"]
    names = ["fd -1", "fd 42 (closed)", "fd just closed",
             "fd opened write-only", "fd of a directory",
             "then a valid fd, first line"]
    label = f"{bs_name(bs)}  invalid fd -> NULL (" + ", ".join(names) + ")"
    if shown.skipped(label, res):
        return
    prob = run_problem(res, bs)
    if prob:
        shown.ko(label, f"{prob} after {len(items)} call(s)"
                 + (f" ({names[len(items)]})" if len(items) < 6 else ""))
        return
    bad = [f"{names[i]}: expected {core.crepr(w)}, got "
           f"{core.crepr(items[i]) if i < len(items) else 'nothing'}"
           for i, w in enumerate(want)
           if i >= len(items) or items[i] != w]
    if bad:
        shown.ko(label, "\n".join(bad))
    else:
        ctx.rep.check(label, True)


def group_edge(ctx):
    rep = ctx.rep
    rep.section("edge cases")
    shown = Shown(ctx)
    for bs in (1, 42):
        if not check_build(ctx, bs):
            continue
        for name in ("empty", "one_newline", "only_newlines"):
            file_case(ctx, shown, bs, name)
        check_badfd(ctx, shown, bs)
        if not WRAP_OK:
            continue
        for k in (0, 1, 2):
            check_readerr(ctx, shown, bs, k)
        # read() fails while the stash holds a line without its newline
        check_readerr(ctx, shown, bs, 0, after=2 * bs, name="long_line_2000")
    if not WRAP_OK:
        rep.skip("read() errors and bytes read by the first call",
                 NO_WRAP_WHY)
        shown.close()
        return
    for bs in (1, 5, 42):
        if not check_build(ctx, bs):
            continue
        check_minread(ctx, shown, bs)
    shown.close()


def check_minread(ctx, shown, bs):
    """Subject: "Try to read as little as possible each time get_next_line()
    is called. [...] Don't read the whole file". Reading the whole file
    before the first line is a KO; more than one extra buffer is a WARN."""
    name = "2000_random_lines"
    data = ctx.fx[name]
    first = split_lines(data)[0]
    res, items, info, _ = ctx.run(bs, "minread", ctx.fpath(name))
    label = (f"{bs_name(bs)}  first call reads at most the first line + "
             f"one buffer ({len(first)} + {bs} bytes of {len(data)})")
    if shown.skipped(label, res):
        return
    prob = run_problem(res, bs)
    got = int(info.get("bytes", "-1"))
    quote = ("subject: \"Try to read as little as possible each time "
             "get_next_line() is called. [...] Don't read the whole file\"")
    read = (f"read {got} bytes ({info.get('reads')} read() calls) before "
            "returning the first line")
    if prob:
        shown.ko(label, prob)
    elif not items or items[0] != first:
        shown.ko(label, f"first line: expected {core.crepr(first)}, got "
                 f"{core.crepr(items[0]) if items else 'nothing'}")
    elif got >= len(data):
        shown.ko(label, f"{read}: the whole file\n{quote}")
    elif got > len(first) + bs:
        ctx.rep.warn(label, f"{read}, more than needed (not the whole "
                     f"file)\n{quote}")
    else:
        ctx.rep.check(label, True)


def mem_scenarios(ctx, bs):
    """Scenarios run under valgrind (or ASan): like Tripouille / francinette,
    the whole test set for BUFFER_SIZE 1, 42 and 10000000."""
    runs = []
    names = ["three_lines", "three_lines_no_nl", "empty", "only_newlines",
             "one_char", "text"]
    # keep valgrind under a few seconds per run
    if bs == 1:
        names += ["long_line_2000", "2000_random_lines"]
    elif bs < 1000000:
        names += ["long_line_12000", "2000_random_lines",
                  "long_line_30000_nl"]
    else:
        names += ["long_line_12000", "long_line_30000_nl"]
    for name in names:
        n = len(split_lines(ctx.fx[name])) + 5
        runs.append((f"read {name} to the end",
                     ("read", ctx.fpath(name), n), b""))
    runs.append(("invalid fds then a valid one",
                 ("badfd", ctx.fpath("three_lines")), b""))
    runs.append(("stdin pipe", ("fd0", 10), ctx.fx["three_lines"]))
    if not WRAP_OK:
        return runs
    n = len(split_lines(ctx.fx["text"])) + 5
    for k in (0, 1):
        runs.append((f"read() fails after {k} line(s), then a new fd",
                     ("readerr", ctx.fpath("text"), k, n), b""))
    if bs < 1000:
        n = len(split_lines(ctx.fx["long_line_2000"])) + 5
        runs.append(("read() fails in the middle of a line, then a new fd",
                     ("readerr", ctx.fpath("long_line_2000"), 0, n, 2 * bs),
                     b""))
    return runs


def still_reachable(leaks):
    """(bytes, blocks) still allocated when the runner ends: the runner
    frees everything it allocates, so this is memory of get_next_line
    (a stash never freed). The per-call tracking counts it as a leak too."""
    m = re.search(r"still reachable: ([\d,]+) bytes in ([\d,]+) blocks",
                  leaks.log)
    if not m:
        return 0, 0
    return (int(m.group(1).replace(",", "")),
            int(m.group(2).replace(",", "")))


def group_leaks(ctx):
    rep = ctx.rep
    rep.section("memory: valgrind / AddressSanitizer")
    if ctx.args.quick:
        return
    use_vg = core.have("valgrind") and not os.environ.get(
        "NEXT42_NO_VALGRIND")
    if not use_vg:
        rep.info("valgrind not found: AddressSanitizer build "
                 "(-fsanitize=address) instead")
    targets = [(False, None)]
    if all(os.path.isfile(ctx.ws.path(f)) for f in BONUS):
        targets.append((True, None))
    shown = Shown(ctx)
    # build first, then run every case in parallel
    ctx.prebuild([(bs, bonus, not use_vg) for bonus, _ in targets
                  for bs in MEM_SIZES])
    jobs = []
    for bonus, _ in targets:
        for bs in MEM_SIZES:
            if not check_build(ctx, bs, bonus, asan=not use_vg):
                continue
            runs = mem_scenarios(ctx, bs)
            if bonus:
                multi = ("three_lines", "text", "only_newlines")
                files = [ctx.fpath(n) for n in multi]
                want = interleave([ctx.fx[n] for n in multi], 1)
                runs.append(("several fd in turn",
                             ("multi", len(want) + 5, 1, *files), b""))
            tag = (" (bonus)" if bonus else "")
            for what, margs, stdin in runs:
                label = f"{bs_name(bs)}{tag}  {what}: no leak, no memory error"
                jobs.append((label, bs, bonus, margs, stdin))

    def one(job):
        _, bs, bonus, margs, stdin = job
        if not use_vg:
            return ctx.run(bs, *margs, stdin=stdin, bonus=bonus, asan=True)
        # same binary without valgrind first: a hang then costs TIMEOUT,
        # not VG_TIMEOUT
        first = ctx.run(bs, *margs, stdin=stdin, bonus=bonus)
        if first[0] is NOT_RUN or (first[0] is not None
                                   and first[0].timed_out):
            return first
        return ctx.run(bs, *margs, stdin=stdin, bonus=bonus, vg=True)

    results = parallel(one, jobs)
    # report in the same order as before
    for (label, bs, _, margs, _), (res, _, info, leaks) in zip(jobs,
                                                               results):
        if not use_vg and not WRAP_OK:
            # ASan without leak detection and no malloc tracking
            label = label.replace("no leak, ", "")
        if shown.skipped(label, res):
            continue
        # badfd stops after one line of the valid fd: the rest of the file
        # may stay in the stash. Memory still held counts only when every
        # fd was read to the end (the per-call tracking rule).
        to_eof = margs[0] != "badfd"
        if use_vg:
            if res is not None and (res.timed_out or res.crashed):
                shown.ko(label, res.status())
                continue
            if res is None or leaks is None:
                continue
            nbytes, nblocks = still_reachable(leaks) if to_eof else (0, 0)
            if not leaks.clean:
                shown.ko(label, leaks.describe() + "\n"
                         + leaks.first_error())
            elif nbytes:
                shown.ko(label, f"{nbytes} byte(s) in {nblocks} block(s) "
                         "still reachable at exit: memory of get_next_line "
                         "never freed (a static stash kept after the end of "
                         "file or after an error?)")
            else:
                rep.check(label, True)
        else:
            prob = run_problem(res, bs) or (to_eof and leak_problem(info))
            if prob:
                shown.ko(label, prob)
            else:
                rep.check(label, True)
    shown.close()


def interleave(contents, badcalls):
    lines = [split_lines(c) for c in contents]
    pos = [0] * len(lines)
    done = [False] * len(lines)
    out = []
    while not all(done):
        for i in range(len(lines)):
            if done[i]:
                continue
            out.append(("F", i))
            if pos[i] < len(lines[i]):
                out.append(lines[i][pos[i]])
                pos[i] += 1
            else:
                out.append(None)
                done[i] = True
        if badcalls:
            out += [("F", -1), None]
    return out


def first_multi_diff(want, items):
    fd, calls = "?", 0
    for i, w in enumerate(want):
        g = items[i] if i < len(items) else "missing"
        if isinstance(w, tuple):
            fd = w[1]
            if g != w:
                return f"call {calls + 1}: the run stopped here"
            continue
        calls += 1
        if g != w:
            where = "fd -1" if fd == -1 else f"file #{fd + 1}"
            got = "nothing (run stopped)" if g == "missing" else core.crepr(g)
            return (f"call {calls} ({where}): expected {core.crepr(w)}, "
                    f"got {got}")
    return None


def group_bonus(ctx):
    rep = ctx.rep
    rep.section("bonus: several fd at the same time")
    ws = ctx.ws
    present = [f for f in BONUS if os.path.isfile(ws.path(f))]
    if not present:
        rep.skip("bonus", "no *_bonus file")
        return
    missing = [f for f in BONUS if f not in present]
    if missing:
        rep.check("bonus files", False,
                  detail="missing: " + ", ".join(missing))
        return
    shown = Shown(ctx)
    sets = [("three_lines", "text", "only_newlines"),
            ("2000_random_lines", "one_char", "empty", "three_lines_no_nl"),
            ("long_line_12000", "lengths_1_to_10001")]
    for bs in (1, 42, 10000):
        if not check_build(ctx, bs, bonus=True):
            continue
        for names in sets:
            for badcalls in (0, 1):
                contents = [ctx.fx[n] for n in names]
                want = interleave(contents, badcalls)
                res, items, info, _ = ctx.run(
                    bs, "multi", len(want) + 5, badcalls,
                    *[ctx.fpath(n) for n in names], bonus=True)
                label = (f"{bs_name(bs)}  one line from each fd in turn: "
                         + ", ".join(names)
                         + (" (+ get_next_line(-1) between)" if badcalls
                            else ""))
                if shown.skipped(label, res):
                    continue
                prob = run_problem(res, bs)
                bad = first_multi_diff(want, items)
                if not bad and not prob:
                    bad = leak_problem(info)
                if prob or bad:
                    shown.ko(label, "\n".join(x for x in (prob, bad) if x))
                else:
                    rep.check(label, True)
    shown.close()


# --------------------------------------------------------------------------

# section title -> group id ("tests", the build failure, has none)
SECTION_GROUPS = {"norminette": "norm",
                  "functions and global variables": "functions",
                  "files": "files",
                  "basic reading (BUFFER_SIZE=42)": "basic",
                  "BUFFER_SIZE values": "buffer_sizes",
                  "standard input and pipes": "stdin",
                  "edge cases": "edge",
                  "memory: valgrind / AddressSanitizer": "leaks",
                  "bonus: several fd at the same time": "bonus",
                  "README.md": "readme",
                  "repository": "repo"}


def main():
    args = core.parse_args("get_next_line tester (42next)", GROUPS)
    project = args.project
    if not os.path.isdir(project):
        print(core.red(f"project directory not found: {project}"))
        return 2
    if not core.have("cc"):
        print(core.red("cc is required"))
        return 2
    quick = getattr(args, "quick", False)
    args.quick = quick
    core.header("get_next_line tester", project)
    rep = core.Report(verbose=args.verbose)
    rep.alias.update(SECTION_GROUPS)
    sizes = QUICK_SIZES if quick else SIZES
    if quick:
        core.quick_skip(rep, "no valgrind / AddressSanitizer runs, "
                        "BUFFER_SIZE " + ", ".join(str(x) for x in sizes
                                                   if x) + " and default")
    with core.Workspace(project, keep=args.keep, prefix="42next-gnl-") as ws:
        ctx = Ctx(ws, rep, args)
        if core.selected(args, "files"):
            group_files(rep, ws)
        if core.selected(args, "norm"):
            group_norm(rep, ws)
        if core.selected(args, "functions"):
            group_functions(rep, ws)
        if core.selected(args, "readme"):
            rep.section("README.md")
            core.check_readme(rep, project)
            check_readme_algo(rep, project)
        if core.selected(args, "repo"):
            rep.section("repository")
            core.check_repo(rep, project)
        need = any(core.selected(args, g) for g in
                   ("basic", "buffer_sizes", "stdin", "edge", "leaks",
                    "bonus"))
        if need and not all(os.path.isfile(ws.path(f))
                            for f in MAND if f.endswith(".c")):
            rep.section("tests")
            rep.check("get_next_line.c and get_next_line_utils.c present",
                      False, detail="cannot run the reading tests")
            return rep.finish()
        if core.selected(args, "basic"):
            group_basic(ctx)
        if core.selected(args, "buffer_sizes"):
            group_sizes(ctx, sizes)
        if core.selected(args, "stdin"):
            group_stdin(ctx, [1, 42, None])
        if core.selected(args, "edge"):
            group_edge(ctx)
        if core.selected(args, "leaks"):
            group_leaks(ctx)
        if core.selected(args, "bonus"):
            group_bonus(ctx)
        return rep.finish()


def check_readme_algo(rep, project):
    path = os.path.join(project, "README.md")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    ok = re.search(r"algorithm|algorithme", text, re.I)
    rep.check("README explains and justifies the algorithm", bool(ok),
              detail=None if ok else
              'the subject asks for "a detailed explanation and justification '
              'of the algorithm selected for this project"')


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # ctrl-c: the temporary copies are removed on the way out
        print("\nstopped (ctrl-c)")
        sys.exit(130)
