#!/usr/bin/env python3
"""42next tester for ft_printf (Common Core).

Every case calls the real printf of the libc and ft_printf with the same
arguments, in the same binary. ft_printf runs in a forked child (a crash or
an infinite loop only fails that case), its stdout is captured byte by byte
and its return value is compared too.
"""

import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import core  # noqa: E402
import cases as C  # noqa: E402

NAME = "libftprintf.a"
ALLOWED = {"malloc", "free", "write", "va_start", "va_arg", "va_copy",
           "va_end"}
HEADER_DIRS = ("", "includes", "include", "inc", "headers", "src")
MAX_KO = 15

CONV_GROUPS = [("c", "%c conversion"), ("s", "%s conversion"),
               ("p", "%p conversion"), ("d", "%d conversion"),
               ("i", "%i conversion"), ("u", "%u conversion"),
               ("x", "%x conversion"), ("X", "%X conversion"),
               ("percent", "%% and plain text"),
               ("mix", "mixed conversions"),
               ("return", "return value")]

GROUPS = [("files", "ft_printf.h header and prototype"),
          ("norm", "norminette"),
          ("makefile", "Makefile (rules, relink, ar, shared-object link)"),
          ("functions", "allowed external functions"),
          ("readme", "README.md"),
          ("repo", "repository hygiene"),
          *CONV_GROUPS,
          ("bonus", "bonus: flags -0.# + and width (if make bonus exists)")]

TEST_FLAGS = ["-g", "-Wno-format", "-Wno-format-security",
              "-Wno-format-zero-length", "-Wno-unused-function"]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def get_seed(extra):
    for i, a in enumerate(extra):
        if a == "--seed" and i + 1 < len(extra) and extra[i + 1].isdigit():
            return int(extra[i + 1])
        if a.startswith("--seed=") and a[7:].isdigit():
            return int(a[7:])
    return C.DEFAULT_SEED


def silent_ko(rep, n):
    """Count n failures without printing them (output stays readable)."""
    if n and rep.current is not None:
        rep.current[2] += n


def byte_desc(data, idx):
    if idx >= len(data):
        return "(end of output)"
    b = data[idx]
    return f"{core.crepr(bytes([b]))} (0x{b:02x})"


def check_readme_algo(rep, project, data_structure):
    path = os.path.join(project, "README.md")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    algo = re.search(r"algorithm|algorithme", text, re.I)
    what = "algorithm and data structure" if data_structure else "algorithm"
    detail = ('the subject asks for "a detailed explanation and justification '
              f'of the chosen {what}" (add e.g. an "Algorithm" section)')
    rep.check("README explains the chosen algorithm", bool(algo),
              detail=None if algo else detail)
    if data_structure:
        ds = re.search(r"data structure|structure de donn", text, re.I)
        rep.check("README explains the chosen data structure", bool(ds),
                  detail=None if ds else detail)


class Build:
    """Builds libftprintf.a once (mandatory, then bonus if the rule exists)."""

    def __init__(self, ws):
        self.ws = ws
        self.done = False
        self.mand = None
        self.bonus = None
        self.has_bonus_rule = False
        self.error = None
        self.bonus_error = None

    def get(self):
        if self.done:
            return self
        self.done = True
        d = self.ws.dir
        text = core.makefile_text(d)
        if text is None:
            self.error = "no Makefile at the root"
            return self
        self.has_bonus_rule = "bonus" in core.makefile_targets(text)
        core.run(["make", "fclean"], cwd=d, timeout=120)
        res = core.run(["make"], cwd=d, timeout=300)
        lib = os.path.join(d, NAME)
        if res.rc != 0 or not os.path.isfile(lib):
            self.error = (core.short_errors((res.out + res.err).decode(
                "utf-8", "replace")) or f"{NAME} not created by make")
            return self
        self.mand = self.ws.tmp("mandatory_" + NAME)
        shutil.copy(lib, self.mand)
        if self.has_bonus_rule:
            res = core.run(["make", "bonus"], cwd=d, timeout=300)
            if res.rc != 0 or not os.path.isfile(lib):
                self.bonus_error = core.short_errors(
                    (res.out + res.err).decode("utf-8", "replace"))
            else:
                self.bonus = self.ws.tmp("bonus_" + NAME)
                shutil.copy(lib, self.bonus)
        return self


def gen_runner(ws, cases, tag):
    body = []
    for i, case in enumerate(cases):
        body.append(f"\tcase {i}: {{ {case.c_body()} }}")
    with open(os.path.join(HERE, "runner.c.in"), encoding="utf-8") as fh:
        src = fh.read()
    src = (src.replace("@LONG_LEN@", str(C.LONG_MAX_LEN))
              .replace("@CASES@", "\n".join(body)))
    path = ws.tmp(f"runner_{tag}.c")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    return path


# Apple ld64 has no --wrap: on macOS the per-call malloc tracking is off
# (the runner is built with NEXT42_NO_WRAP), everything else still runs.
WRAP_OK = sys.platform != "darwin"
NO_WRAP_WHY = "needs GNU ld --wrap, not available on macOS"
WRAP = (["-Wl,--wrap=malloc,--wrap=free"] if WRAP_OK
        else ["-DNEXT42_NO_WRAP"])
MAX_TIMEOUTS = 2    # same value as MAX_TIMEOUTS in runner.c.in
ASAN = ["-fsanitize=address", "-fno-omit-frame-pointer"]
RUN_ENV = {"ASAN_OPTIONS": "detect_leaks=0:exitcode=86:abort_on_error=0"}


def asan_sources(ws, lib):
    """The .c files behind the objects of the archive (for an ASan build)."""
    res = core.run(["ar", "t", lib], timeout=30)
    names = [n for n in res.out.decode("utf-8", "replace").split()
             if n.endswith(".o")]
    by_stem, incdirs = {}, set()
    for root, dirs, files in os.walk(ws.dir):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in files:
            if f.endswith(".c"):
                by_stem.setdefault(f[:-2], os.path.join(root, f))
            elif f.endswith(".h"):
                incdirs.add(root)
    srcs = [by_stem.get(n[:-2]) for n in names]
    if not srcs or None in srcs:
        return None
    return srcs, sorted(incdirs)


def compile_runner(ws, cases, lib, tag, asan=None):
    """asan: (sources, include dirs) to build the project with ASan."""
    src = gen_runner(ws, cases, tag)
    out = ws.tmp(f"runner_{tag}")
    if asan:
        ok, text = core.compile_c([src, *asan[0]], out, includes=asan[1],
                                  flags=TEST_FLAGS + WRAP + ASAN)
    else:
        ok, text = core.compile_c([src], out, flags=TEST_FLAGS + WRAP,
                                  libs=[lib])
    return (out if ok else None), text


def run_cases(ws, binary, cases, ids, tag):
    out = ws.tmp(f"results_{tag}.tsv")
    work = ws.tmp(f"work_{tag}")
    os.makedirs(work, exist_ok=True)
    res = core.run([binary, "cmp", out, work, *map(str, ids)],
                   timeout=60 + 6 * len(ids), env=RUN_ENV)
    records = {}
    if os.path.isfile(out):
        with open(out, encoding="ascii", errors="replace") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 9:
                    continue
                cid, status, rr, ur, rh, uh, nblk, nbytes, eh = parts
                records[int(cid)] = (status, int(rr), int(ur), unhex(rh),
                                     unhex(uh), int(nblk), int(nbytes),
                                     unhex(eh).decode("utf-8", "replace"))
    return records, res


def unhex(text):
    return b"" if text == "-" else bytes.fromhex(text)


def asan_report(err):
    """-> (one-line summary, detail lines) from an AddressSanitizer report."""
    lines = err.splitlines()
    head = next((ln for ln in lines if "ERROR: AddressSanitizer" in ln), None)
    if head is None:
        return None, []
    head = re.sub(r"^==\d+==\s*", "", head)
    frames = [ln.strip() for ln in lines if re.match(r"\s*#\d+ ", ln)]
    frames = [re.sub(r" 0x[0-9a-f]+ in ", " ", f) for f in frames
              if "libasan" not in f and "__wrap_" not in f][:5]
    summary = next((ln for ln in lines if ln.startswith("SUMMARY:")), "")
    more = [re.sub(r"/\S*/42next-printf-[^/]+/(project/)?", "", x)
            for x in frames + ([summary] if summary else [])]
    return head, more


def status_text(status):
    if status == "timeout":
        return "timeout (infinite loop?)"
    if status.startswith("signal:"):
        import signal
        num = int(status.split(":")[1])
        try:
            return f"crashed with {signal.Signals(num).name}"
        except ValueError:
            return f"killed by signal {num}"
    if status.startswith("exit:86"):
        return "memory error found by AddressSanitizer"
    if status.startswith("exit:"):
        return f"process exited ({status[5:]}) inside ft_printf"
    return status


def judge(case, rec):
    """-> (ok, expected, got, detail)"""
    status, rr, ur, ref, got, nblk, nbytes, err = rec
    if case.fixed:
        rr, ref = case.fixed
    exp_txt = f"{core.crepr(ref)}  (return {rr})"
    if status != "ok":
        head, more = asan_report(err)
        lines = ([head] + ["  " + x for x in more]) if head else []
        if got:
            lines.append(f"output before the crash: {core.crepr(got)}")
        return (False, exp_txt, status_text(status),
                "\n".join(lines) or None)
    leak = (f"memory leak: {nbytes} byte(s) in {nblk} block(s) malloc'ed "
            "during this call and never freed") if nblk else None
    if ref == got and rr == ur:
        if leak:
            return False, None, None, leak
        return True, None, None, None
    lines = [leak] if leak else []
    if ref != got:
        idx = core.first_diff(ref, got)
        lines.append(f"first different byte at index {idx}: expected "
                     f"{byte_desc(ref, idx)}, got {byte_desc(got, idx)}")
        if idx > 60:
            lo = max(0, idx - 20)
            lines.append(f"around index {idx}: expected "
                         f"{core.crepr(ref[lo:idx + 30])}, got "
                         f"{core.crepr(got[lo:idx + 30])}")
        if len(ref) != len(got):
            lines.append(f"length: expected {len(ref)} bytes, got {len(got)}")
    else:
        lines.append("output is identical, only the return value differs")
    if rr != ur:
        lines.append(f"return value: expected {rr}, got {ur}")
    if ur != len(got) and ur >= 0:
        lines.append(f"ft_printf returned {ur} but wrote {len(got)} bytes")
    got_txt = f"{core.crepr(got)}  (return {ur})"
    return False, exp_txt, got_txt, "\n".join(lines)


def report_group(rep, cases, ids, records):
    shown = 0
    hidden = 0
    hung = [i for i in ids if records.get(i, ("",))[0] == "timeout"]
    stopped = len(hung) >= MAX_TIMEOUTS
    not_run = []
    for i in ids:
        case = cases[i]
        name = case.label + (f"  [{case.title}]" if case.title else "")
        rec = records.get(i)
        if rec is None:
            if stopped:
                not_run.append(i)
                continue
            rep.check(name, False, detail="no result: the test runner itself "
                      "died (see above)")
            continue
        ok, exp, got, detail = judge(case, rec)
        if ok:
            rep.check(name, True)
            continue
        # undefined in C / not in the subject: only a WARN, but a leak in
        # the call stays a KO
        if case.ub and not (rec[0] == "ok" and rec[5]):
            rep.warn(name + "  (not defined by the subject, compared with "
                     "this libc's printf)",
                     f"expected: {exp}\ngot:      {got}")
            continue
        if shown >= MAX_KO and not rep.verbose:
            hidden += 1
            continue
        shown += 1
        rep.check(name, False, expected=exp, got=got, detail=detail)
    if hidden:
        silent_ko(rep, hidden)
        print(core.red(f"  ... {hidden} more failing case(s) in this group "
                       "(use --verbose to see all)"))
    if not_run:
        rep.check(f"group stopped after {len(hung)} timeouts: "
                  f"{len(not_run)} more case(s) not run", False,
                  detail="calls that hung:\n"
                  + "\n".join("  " + cases[i].label for i in hung))


# --------------------------------------------------------------------------
# groups
# --------------------------------------------------------------------------

def find_header(project):
    for sub in HEADER_DIRS:
        p = os.path.join(project, sub, "ft_printf.h")
        if os.path.isfile(p):
            return p
    for root, dirs, files in os.walk(project):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        if "ft_printf.h" in files:
            return os.path.join(root, "ft_printf.h")
    return None


def header_dirs(project, hdr):
    """The folder of hdr first, then every other folder with a .h file."""
    dirs = [os.path.dirname(hdr)]
    for root, sub, files in os.walk(project):
        sub[:] = sorted(x for x in sub if not x.startswith("."))
        if root not in dirs and any(f.endswith(".h") for f in files):
            dirs.append(root)
    return dirs


def group_header(rep, ws, build):
    rep.section("files: ft_printf.h")
    hdr = find_header(ws.dir)
    if not hdr:
        others = []
        for root, dirs, files in os.walk(ws.dir):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            others += [os.path.relpath(os.path.join(root, f), ws.dir)
                       for f in files if f.endswith(".h")]
        rep.check("header named ft_printf.h", False,
                  detail="headers found: " + (", ".join(others) or "none"))
        return
    rep.check(f"header named ft_printf.h ({os.path.relpath(hdr, ws.dir)})",
              True)
    with open(hdr, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    proto = re.search(r"\bint\s+ft_printf\s*\(\s*const\s+char\s*\*\s*\w*\s*,"
                      r"\s*\.\.\.\s*\)\s*;", text)
    rep.check("contains the prototype int ft_printf(const char *, ...);",
              bool(proto))
    b = build.get()
    if not b.mand:
        rep.check("header usable from a program", False,
                  detail=f"cannot build {NAME}: {b.error}")
        return
    src = ws.tmp("header_test.c")
    with open(src, "w") as fh:
        fh.write('#include "ft_printf.h"\n\nint\tmain(void)\n{\n'
                 '\treturn (ft_printf("%d|%s", 42, "ok") != 5);\n}\n')
    out = ws.tmp("header_test")
    # every folder with a .h: ft_printf.h may include "libft.h", found
    # through the -I of the Makefile
    ok, text = core.compile_c([src], out, includes=header_dirs(ws.dir, hdr),
                              libs=[b.mand])
    if not rep.check('#include "ft_printf.h" + link with -lftprintf compiles',
                     ok, detail=None if ok else core.short_errors(text)):
        return
    res = core.run([out], timeout=10)
    rep.check('ft_printf("%d|%s", 42, "ok") from that program',
              res.rc == 0 and res.out == b"42|ok",
              expected='"42|ok" (return 5)',
              got=f"{core.crepr(res.out)} ({res.status()})")


def group_makefile(rep, ws, build):
    rep.section("Makefile")
    core.check_makefile(rep, ws.dir, NAME, bonus=True)
    text = core.makefile_text(ws.dir) or ""
    uses_ar = re.search(r"(^|\s|\$\()ar(\)|\s)", text, re.M) is not None
    rep.check("library created with ar", uses_ar or "$(AR)" in text,
              detail=None if uses_ar else "no `ar` command in the Makefile")
    rep.check("libtool not used", "libtool" not in text)
    b = build.get()
    if b.mand:
        so = ws.tmp("shared_test.so")
        if WRAP_OK:
            link = ["-Wl,--whole-archive", b.mand, "-Wl,--no-whole-archive"]
        else:
            # Apple ld64: -force_load instead of --whole-archive, and allow
            # undefined symbols like a Linux shared object does
            link = ["-Wl,-force_load," + b.mand,
                    "-Wl,-undefined,dynamic_lookup"]
        res = core.run(["cc", "-shared", "-o", so, *link], timeout=60)
        err = core.output(res).replace(ws.tmp(""), "")
        rep.check(f"{NAME} links into a shared object (-fPIC)", res.rc == 0,
                  detail=None if res.rc == 0 else
                  "the moulinette links your archive into a shared object and "
                  "this fails\n(its 'mixed conversions' test then gets 0). "
                  "Add -fPIC to your compile flags.\n"
                  + "\n".join([ln for ln in err.splitlines()
                              if "relocation" in ln][:2]
                              or [core.short_errors(err, 4)]))
        if b.has_bonus_rule and b.bonus_error:
            rep.check("make bonus (fresh build)", False, detail=b.bonus_error)
    libft = ws.path("libft")
    if os.path.isdir(libft):
        rep.check("libft/ has its own Makefile",
                  core.makefile_text(libft) is not None)
        calls = re.search(r"(make|\$\(MAKE\))\s+(-C|--directory[= ])\s*\.?/?"
                          r"libft|cd\s+\.?/?libft\s*&&\s*(make|\$\(MAKE\))",
                          text)
        rep.check("Makefile builds libft with its Makefile (make -C libft)",
                  bool(calls))
        if b.mand:
            syms = core.external_symbols(
                archive_objects(ws, b.mand, "libft")) or []
            missing = [s for s in syms if s.startswith("ft_")]
            rep.check(f"libft objects are inside {NAME}", not missing,
                      detail="undefined in the archive: " + ", ".join(missing)
                      if missing else None)


def archive_objects(ws, lib, tag):
    """Extract the .o files of an archive (nm on a .a confuses core)."""
    d = ws.tmp("objs_" + tag)
    os.makedirs(d, exist_ok=True)
    core.run(["ar", "x", os.path.abspath(lib)], cwd=d, timeout=60)
    return [os.path.join(d, f) for f in sorted(os.listdir(d))
            if f.endswith(".o")]


# names core.external_symbols may report that are not real calls
NOT_CALLS = {"GLOBAL_OFFSET_TABLE_", "_GLOBAL_OFFSET_TABLE_"}


def check_functions(rep, objs, allowed, label="external functions",
                    sources=None):
    syms = core.external_symbols(objs)
    if syms is None:
        rep.skip(label, "nm not installed")
        return
    bad = [x for x in syms if x not in allowed and x not in NOT_CALLS
           and x not in core.COMPILER_ALIASES]
    stems = {os.path.basename(o)[:-2] for o in objs}
    bad += core.written_aliases(syms, allowed, sources, stems=stems)
    rep.check(f"{label}: only {', '.join(sorted(allowed))}", not bad,
              detail="forbidden: " + ", ".join(bad) if bad else None)


def group_functions(rep, ws, build):
    rep.section("external functions")
    b = build.get()
    if not b.mand:
        rep.check(f"build {NAME}", False, detail=b.error)
        return
    objs = archive_objects(ws, b.mand, "mandatory")
    rep.check(f"{NAME} contains object files", bool(objs),
              detail=None if objs else "ar x extracted no .o file")
    check_functions(rep, objs, ALLOWED, sources=ws.dir)
    if b.bonus:
        check_functions(rep, archive_objects(ws, b.bonus, "bonus"),
                        ALLOWED, label="external functions (bonus)",
                        sources=ws.dir)


def run_test_groups(rep, ws, build, args, seed):
    conv_sel = [(g, lbl) for g, lbl in CONV_GROUPS if core.selected(args, g)]
    want_bonus = core.selected(args, "bonus")
    mand = C.mandatory_cases(seed)
    b = build.get() if (conv_sel or want_bonus) else None

    passed = set()
    quick = getattr(args, "quick", False)
    has_vg = core.have("valgrind") and not os.environ.get("NEXT42_NO_VALGRIND")
    vg_on = not quick and has_vg
    asan = None
    if not quick and not has_vg and b and b.mand:
        asan = asan_sources(ws, b.mand)
        print(core.yellow("valgrind not found: the tests run on an "
                          "AddressSanitizer build of your sources"
                          if asan else "valgrind not found and the sources "
                          "of the archive were not found: no memory check"))
    if conv_sel:
        binary, err = (None, b.error) if not b.mand else \
            compile_runner(ws, mand, b.mand, "mandatory", asan)
        first_err = True
        for g, lbl in conv_sel:
            rep.section(lbl)
            if not binary:
                rep.check("build the test program", False,
                          detail=core.short_errors(err or "") if first_err
                          else "same error as above")
                first_err = False
                continue
            if first_err and not WRAP_OK:
                rep.skip("per-call leak tracking", NO_WRAP_WHY)
            first_err = False
            # one runner per group: it stops after MAX_TIMEOUTS timeouts
            gids = [i for i, c in enumerate(mand) if c.group == g]
            records, res = run_cases(ws, binary, mand, gids, "mand_" + g)
            report_group(rep, mand, gids, records)
            early_stop(res, records, gids)
            ok_ids = [i for i in gids if i in records
                      and records[i][0] == "ok"]
            passed |= set(ok_ids)
            if vg_on:
                vg_group(rep, binary, mand, ok_ids, lbl)

    if want_bonus:
        rep.section("bonus: flags and width")
        group_bonus(rep, ws, b, seed, vg_on, asan is not None)


def early_stop(res, records, ids):
    """Say it when the runner died before the end (not after timeouts)."""
    hung = sum(1 for i in ids if records.get(i, ("",))[0] == "timeout")
    if res.rc != 0 and len(records) < len(ids) and hung < MAX_TIMEOUTS:
        print(core.red(f"test runner stopped early: {res.status()}"))


def vg_group(rep, binary, cases, ids, what, max_culprits=5):
    """One valgrind run for a whole group, then one per case if dirty."""
    ids = [i for i in ids if cases[i].direct]
    if not ids:
        return
    name = f"valgrind: {len(ids)} {what} calls, no leak, no memory error"
    res, leaks = core.valgrind([binary, "direct", "-", "-",
                                *map(str, ids)], timeout=600)
    if res is None:
        return
    if leaks.clean and not res.crashed and not res.timed_out:
        rep.check(name, True)
        return
    culprits = []
    for i in ids:
        r, lk = core.valgrind([binary, "direct", "-", "-", str(i)],
                              timeout=120)
        if r.crashed or r.timed_out or not lk.clean:
            culprits.append((i, r, lk))
            if len(culprits) >= max_culprits:
                break
    lines = []
    for i, r, lk in culprits:
        why = r.status() if (r.crashed or r.timed_out) else lk.describe()
        lines.append(f"{cases[i].label}: {why}")
    if len(culprits) >= max_culprits:
        lines.append(f"(stopped after {max_culprits} cases)")
    first = culprits[0][2] if culprits else leaks
    if not culprits:
        lines.append("no single call is dirty: the problem shows up only "
                     "when calls follow each other")
    err = first.first_error()
    if err:
        lines.append("first valgrind error"
                     + (f" ({cases[culprits[0][0]].label})" if culprits
                        else "") + ":")
        lines += ["  " + x for x in err.splitlines()]
    got = res.status() if (res.crashed or res.timed_out) else \
        leaks.describe() + " in the whole group"
    rep.check(name, False, got=got, detail="\n".join(lines))


def group_bonus(rep, ws, b, seed, vg_on=False, use_asan=False):
    if not b.mand and not b.bonus:
        rep.check(f"build {NAME}", False, detail=b.error)
        return
    if not b.has_bonus_rule:
        rep.skip("bonus tests", "no bonus rule in the Makefile")
        return
    if not b.bonus:
        rep.check("make bonus", False, detail=b.bonus_error)
        return
    cases = C.bonus_cases(seed)
    asan = asan_sources(ws, b.bonus) if use_asan else None
    binary, err = compile_runner(ws, cases, b.bonus, "bonus", asan)
    if not binary:
        rep.check("build the bonus test program", False,
                  detail=core.short_errors(err))
        return
    if not WRAP_OK:
        rep.skip("per-call leak tracking", NO_WRAP_WHY)
    ids = list(range(len(cases)))
    records, res = run_cases(ws, binary, cases, ids, "bonus")
    report_group(rep, cases, ids, records)
    early_stop(res, records, ids)
    if vg_on:
        vg_group(rep, binary, cases,
                 [i for i in ids if records.get(i, ("x",))[0] == "ok"],
                 "bonus")


# --------------------------------------------------------------------------

def main():
    args = core.parse_args("ft_printf tester (42next)", GROUPS)
    seed = get_seed(args.extra)
    project = args.project
    if not os.path.isdir(project):
        print(core.red(f"project directory not found: {project}"))
        return 2
    for tool in ("cc", "make", "ar"):
        if not core.have(tool):
            print(core.red(f"{tool} is required"))
            return 2
    core.header("ft_printf tester", project)
    print(core.dim(f"random cases use seed {seed} "
                   f"(rerun the same ones with --seed {seed})"))
    rep = core.Report(verbose=args.verbose)
    rep.alias.update({"files: ft_printf.h": "files", "norminette": "norm",
                      "Makefile": "makefile",
                      "external functions": "functions",
                      "README.md": "readme", "repository": "repo",
                      "bonus: flags and width": "bonus"})
    rep.alias.update({lbl: g for g, lbl in CONV_GROUPS})
    if getattr(args, "quick", False):
        core.quick_skip(rep, "no valgrind / AddressSanitizer runs"
                        + (" (per-call leak tracking stays on)" if WRAP_OK
                           else ""))
    with core.Workspace(project, keep=args.keep,
                        prefix="42next-printf-") as ws:
        build = Build(ws)
        if core.selected(args, "files"):
            group_header(rep, ws, build)
        if core.selected(args, "norm"):
            rep.section("norminette")
            core.check_norm(rep, ws.dir)
        if core.selected(args, "makefile"):
            group_makefile(rep, ws, build)
        if core.selected(args, "functions"):
            group_functions(rep, ws, build)
        if core.selected(args, "readme"):
            rep.section("README.md")
            core.check_readme(rep, project)
            check_readme_algo(rep, project, data_structure=True)
        if core.selected(args, "repo"):
            rep.section("repository")
            core.check_repo(rep, project, binaries=(NAME,))
        run_test_groups(rep, ws, build, args, seed)
        return rep.finish()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # ctrl-c: the temporary copies are removed on the way out
        print("\nstopped (ctrl-c)")
        sys.exit(130)
