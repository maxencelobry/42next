"""42next self-test: every tester on real projects, as they are and with
injected bugs, and a check of what it prints.

    python3 -I dev/battery.py PROJECTS... [--only TEXT] [--update] [-j N]

PROJECTS: folders that hold 42 projects (a clone of the owner's submitted
repo github.com/maxencelobry/42mlobry, ~/Desktop/42...). A project is found
by its folder name (ft_libft, ft_printf, ft_growing_code...) up to 3
levels down; the first folder given wins. A case whose project is not there
is skipped. The testers without a sample project still get smoke cases.

The RESULT line of each project as it is, is kept in dev/baseline.json.
--update writes it from this run (after a change that adds or removes a
check on purpose). Exit 0 when every case passes.

The projects are never modified: each case works on its own copy.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTERS = os.path.join(REPO, "testers")
SPECS = os.path.join(TESTERS, "py", "specs")
BASELINE = os.path.join(REPO, "dev", "baseline.json")
HDR = '#include "libft.h"\n'

# Python projects: folder name -> spec
PY = {
    "ft_growing_code": "growing_code", "ft_code_cultivation":
    "code_cultivation", "ft_garden_guardian": "garden_guardian",
    "ft_data_quest": "data_quest", "ft_data_archivist": "data_archivist",
    "ft_code_nexus": "code_nexus", "ft_codex": "the_codex",
    "ft_the_matrix": "the_matrix", "ft_data_deck": "datadeck",
    "ft_funcmage": "funcmage", "ft_cosmic_data": "cosmic_data",
}
# C (and non-spec) projects: folder name -> tester
C = {"ft_libft": "libft", "ft_printf": "printf", "ft_gnl": "gnl",
     "push_swap": "push_swap", "ft_a_maze_ing": "amazeing"}
# testers with no sample project: smoke cases only
SMOKE = ["born2beroot", "netpractice", "inception", "pacman"]


# ---------------------------------------------------------------- mutations

def sub(path, old, new):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if old not in text:
        raise MutationError(f"{old!r} not in {os.path.basename(path)}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(old, new, 1))


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def append(path, text):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


def remove(*paths):
    for p in paths:
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)


class MutationError(Exception):
    """The injected bug does not apply any more (the project changed)."""


# ---------------------------------------------------------------- cases

class Case:
    def __init__(self, name, project, tester, args=(), mutate=None,
                 want=(), never=(), max_s=None, env=None, rc=None,
                 base=False, cli=None):
        self.name, self.project, self.tester = name, project, tester
        self.args, self.mutate = list(args), mutate
        self.want, self.never = list(want), list(never)
        self.max_s, self.env, self.rc = max_s, env or {}, rc
        self.base = base        # compare the RESULT line with baseline.json
        self.cli = cli          # run bin/42next.js with these words instead


def cases():
    out = []
    add = out.append

    # ------------------------------------------------------------ libft
    for name, py in PY.items():
        add(Case(f"{name} as it is", name, "py", ["--spec", spec(py)],
                 base=True))
    for name, tester in C.items():
        add(Case(f"{name} as it is", name, tester, base=True))

    add(Case("libft strlen +1", "ft_libft", "libft", ["--only", "ft_strlen"],
             mutate=lambda d: sub(d + "/ft_strlen.c", "return (i);",
                                  "return (i + 1);"),
             want=[r"KO ft_strlen:"]))
    add(Case("libft memcpy calls libc", "ft_libft", "libft",
             ["--only", "functions"],
             mutate=lambda d: write(
                 d + "/ft_memcpy.c", HDR + "#include <string.h>\n"
                 "void\t*ft_memcpy(void *dst, const void *src, size_t n)\n"
                 "{\n\treturn (memcpy(dst, src, n));\n}\n"),
             want=[r"memcpy is called in ft_memcpy\.c"]))
    add(Case("libft optimizer memset is not a KO", "ft_libft", "libft",
             ["--only", "functions"],
             mutate=lambda d: sub(d + "/Makefile", "-Wall", "-O2 -Wall"),
             never=[r"memset is called", r"strlen is called",
                    r"ft_strlen\.o: strlen"]))
    add(Case("libft putnbr leak (quick)", "ft_libft", "libft",
             ["--only", "ft_putnbr_fd", "--quick"],
             mutate=lambda d: write(
                 d + "/ft_putnbr_fd.c", HDR + "void\tft_putnbr_fd(int n, int "
                 "fd)\n{\n\tft_putstr_fd(ft_itoa(n), fd);\n}\n"),
             want=[r"KO ft_putnbr_fd:"]))
    add(Case("libft isalpha exit(0)", "ft_libft", "libft",
             ["--only", "ft_isalpha"],
             mutate=lambda d: write(
                 d + "/ft_isalpha.c", HDR + "#include <stdlib.h>\n"
                 "int\tft_isalpha(int c)\n{\n\tif (c == 'z')\n\t\texit(0);\n"
                 "\treturn ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= "
                 "'Z'));\n}\n"),
             want=[r"exited the program", r"KO ft_isalpha:"]))
    add(Case("libft strlen infinite loop", "ft_libft", "libft",
             mutate=lambda d: sub(d + "/ft_strlen.c", "\t\ti++;\n",
                                  "\t\t;\n"),
             want=[r"KO ft_strlen:", r"OK ft_isalpha:", r"OK ft_atoi:"],
             max_s=300))
    add(Case("libft local static is no global", "ft_libft", "libft",
             ["--only", "functions"],
             mutate=lambda d: sub(d + "/ft_strlen.c", "\tsize_t\ti;\n",
                                  "\tstatic size_t\tcalls;\n\tsize_t\t\ti;\n"
                                  "\n\tcalls++;\n"),
             never=[r"calls"]))
    add(Case("libft file-scope global", "ft_libft", "libft",
             ["--only", "functions"],
             mutate=lambda d: append(d + "/ft_strlen.c", "\nint\tg_count;\n"),
             want=[r"g_count"]))
    add(Case("libft ${NAME} rule", "ft_libft", "libft",
             ["--only", "makefile"],
             mutate=lambda d: (sub(d + "/Makefile", "$(TARGET):", "${NAME}:"),
                               sub(d + "/Makefile", "all: $(TARGET)",
                                   "all: ${NAME}"),
                               subprocess.run(["sed", "-i", "s/TARGET/NAME/g",
                                               d + "/Makefile"])),
             never=[r"missing: \$\(NAME\)"]))
    add(Case("libft no valgrind -> ASan", "ft_libft", "libft",
             ["--only", "ft_lstmap,ft_strdup"],
             env={"NEXT42_NO_VALGRIND": "1"},
             want=[r"SKIP", r"KO ft_lstmap:"]))
    add(Case("libft quick", "ft_libft", "libft", ["--quick"],
             want=[r"^RESULT \d+/\d+"], max_s=120))
    add(Case("libft no Makefile, no libft.h: functions still tested",
             "ft_libft", "libft", ["--only", "ft_strlen,ft_strdup"],
             mutate=lambda d: remove(d + "/Makefile", d + "/libft.h"),
             want=[r"WARNING: NO MAKEFILE AND NO LIBFT\.H",
                   r"OK ft_strlen:", r"OK ft_strdup:",
                   r"^RESULT .*see the warning above"], rc=0))
    add(Case("libft no Makefile, full run is KO", "ft_libft", "libft",
             ["--quick"], mutate=lambda d: remove(d + "/Makefile"),
             want=[r"WARNING: NO MAKEFILE", r"KO .*Makefile",
                   r"OK ft_strlen:"], rc=1))
    add(Case("libft empty README", "ft_libft", "libft", ["--only", "readme"],
             mutate=lambda d: write(d + "/README.md", ""),
             want=[r"KO +README\.md has content"],
             never=[r'"Description" section']))
    add(Case("libft empty .c", "ft_libft", "libft", ["--only", "files"],
             mutate=lambda d: write(d + "/ft_strlen.c", ""),
             want=[r"not found: ft_strlen \(ft_strlen\.c is empty\)"]))

    # ------------------------------------------------------------ printf
    add(Case("printf wrong return", "ft_printf", "printf",
             ["--only", "return"],
             mutate=lambda d: sub(d + "/src/ft_printf.c", "return (l);",
                                  "return (l + 1);"),
             want=[r"KO"]))
    add(Case("printf hang on %u", "ft_printf", "printf",
             mutate=lambda d: sub(d + "/src/ft_printf.c", "\tif (c == 'c')\n",
                                  "\tif (c == 'u')\n\t\twhile (1)\n\t\t\t;\n"
                                  "\tif (c == 'c')\n"),
             want=[r"timeout", r"OK"], max_s=300))
    add(Case("printf memcpy in source", "ft_printf", "printf",
             ["--only", "functions"],
             mutate=lambda d: (
                 sub(d + "/src/ft_printf.c", '#include "printf.h"\n',
                     '#include "printf.h"\n#include <string.h>\n'),
                 sub(d + "/src/ft_printf.c", "\ti = 0;\n\tl = 0;\n",
                     "\ti = 0;\n\tl = 0;\n\tmemcpy(&s, &i, l + 1);\n")),
             want=[r"forbidden: .*memcpy"]))
    add(Case("printf test main.c with memcpy ignored", "ft_printf", "printf",
             ["--only", "functions"],
             mutate=lambda d: write(
                 d + "/main.c", "#include <string.h>\nint main(void){char "
                 "a[2]; memcpy(a, \"x\", 2); return a[0];}\n"),
             never=[r"forbidden"]))
    add(Case("printf empty ft_printf.c: one build KO", "ft_printf", "printf",
             ["--quick"], mutate=lambda d: write(d + "/src/ft_printf.c", ""),
             want=[r"KO +the test program builds",
                   r"SKIP the \d+ conversion groups"],
             never=[r"same error as above"]))

    # ------------------------------------------------------------ gnl
    add(Case("gnl global const", "ft_gnl", "gnl", ["--only", "functions"],
             mutate=lambda d: append(d + "/get_next_line_utils.c",
                                     "\nconst int\tg_answer = 42;\n"),
             want=[r"g_answer"]))
    add(Case("gnl memcpy in source", "ft_gnl", "gnl", ["--only", "functions"],
             mutate=lambda d: (
                 sub(d + "/get_next_line_utils.c",
                     '#include "get_next_line.h"\n',
                     '#include "get_next_line.h"\n#include <string.h>\n'),
                 append(d + "/get_next_line_utils.c",
                        "\nvoid\t*gnl_cp(void *d, const void *s, size_t n)\n"
                        "{\n\treturn (memcpy(d, s, n));\n}\n")),
             want=[r"forbidden: .*memcpy"]))
    add(Case("gnl hang everywhere", "ft_gnl", "gnl",
             mutate=lambda d: (
                 sub(d + "/get_next_line.c", "get_next_line(int fd)",
                     "gnl_real(int fd)"),
                 append(d + "/get_next_line.c",
                        "\nchar\t*get_next_line(int fd)\n{\n"
                        "\tvolatile int\tx = 1;\n\n\twhile (x)\n\t\t;\n"
                        "\treturn (gnl_real(fd));\n}\n")),
             want=[r"timeout", r"^RESULT"], max_s=420))
    add(Case("gnl bonus file is KO (v1.3)", "ft_gnl", "gnl",
             ["--only", "files"],
             mutate=lambda d: shutil.copy(d + "/get_next_line.c",
                                          d + "/get_next_line_bonus.c"),
             want=[r"KO +no \*_bonus file", r"older subject"]))

    # ------------------------------------------------------------ push_swap
    add(Case("push_swap make -s -C libft", "push_swap", "push_swap",
             ["--only", "makefile"],
             mutate=lambda d: sub(d + "/Makefile",
                                  "\t$(MAKE) -C $(LIBFTDIR)\n",
                                  "\tmake -s -C $(LIBFTDIR)\n"),
             never=[r"KO .*libft built"]))
    add(Case("push_swap ${MAKE} -C libft", "push_swap", "push_swap",
             ["--only", "makefile"],
             mutate=lambda d: sub(d + "/Makefile",
                                  "\t$(MAKE) -C $(LIBFTDIR)\n",
                                  "\t${MAKE} -C ${LIBFTDIR}\n"),
             never=[r"KO .*libft built"]))
    add(Case("push_swap memcpy in source", "push_swap", "push_swap",
             ["--only", "functions"],
             mutate=lambda d: (
                 sub(d + "/main.c", '#include "push_swap.h"\n',
                     '#include "push_swap.h"\n#include <string.h>\n'),
                 append(d + "/main.c",
                        "\nvoid\t*ps_cp(void *d, const void *s, size_t n)\n"
                        "{\n\treturn (memcpy(d, s, n));\n}\n")),
             want=[r"forbidden: .*memcpy"]))
    add(Case("push_swap empty Makefile", "push_swap", "push_swap",
             ["--quick"], mutate=lambda d: write(d + "/Makefile", ""),
             want=[r"KO +make builds push_swap",
                   r"nothing else can be tested"], rc=1))
    add(Case("push_swap quick: chosen skips are not 'not tested'",
             "push_swap", "push_swap", ["--quick", "--only", "leaks"],
             never=[r"not tested \(SKIP above\)"]))

    # ------------------------------------------------------------ A-Maze-ing
    add(Case("amazeing empty main file", "ft_a_maze_ing", "amazeing",
             ["--quick"], mutate=lambda d: write(d + "/a_maze_ing.py", ""),
             want=[r"KO"], rc=1))

    # ------------------------------------------------------------ python
    add(Case("py reworded message is WARN", "ft_garden_guardian", "py",
             ["--spec", spec("garden_guardian")],
             mutate=lambda d: sub(d + "/ex3/ft_custom_errors.py",
                                  "The tomato plant", "The rose plant"),
             want=[r"WARN", r"^RESULT 42/42"]))
    add(Case("py wrong value is KO", "ft_growing_code", "py",
             ["--spec", spec("growing_code")],
             mutate=lambda d: sub(d + "/ex2/ft_plot_area.py",
                                  "int(length) * int(width)",
                                  "int(length) * int(width) + 1"),
             want=[r"KO"], rc=1))
    add(Case("py required phrase is KO", "ft_growing_code", "py",
             ["--spec", spec("growing_code")],
             mutate=lambda d: sub(d + "/ex1/ft_garden_name.py",
                                  "Growing well!", "Growing fine!"),
             want=[r"KO"], rc=1))
    for name, py in PY.items():
        add(Case(f"py {name}: empty file is KO", name, "py",
                 ["--spec", spec(py), "--quick"],
                 mutate=lambda d, py=py: empty_first(d, py),
                 want=[r"KO +\S+ has code"], rc=1))
    add(Case("py cosmic data without pydantic: names still checked",
             "ft_cosmic_data", "py", ["--spec", spec("cosmic_data"),
                                      "--quick", "--only", "ex0"],
             mutate=lambda d: write(d + "/ex0/space_station.py",
                                    '"""Station."""\nx = 1\n'),
             # "defines" shows when pydantic is missing; with it, the
             # behaviour tests fail instead
             want=[r"KO +(defines SpaceStation|SpaceStation)"], rc=1))

    # ------------------------------------------------------------ smoke
    for tester in [*C.values(), "py", *SMOKE]:
        add(Case(f"{tester} --list", None, tester,
                 ["--list"] + (["--spec", spec("growing_code")]
                               if tester == "py" else []),
                 want=[r"^\w+\t"], rc=0))
    for tester in SMOKE + ["libft", "gnl"]:
        add(Case(f"{tester} on an empty folder", "", tester,
                 want=[r"\S"], never=[r"^RESULT \d+/\d+  all checks passed"]))

    # ------------------------------------------------------------ CLI
    add(Case("cli help", None, None, cli=["help"], want=[r"42next failed"],
             rc=0))
    add(Case("cli lint on a Python project", "ft_growing_code", None,
             cli=["lint"], want=[r"^RESULT \d+/\d+  all checks passed"],
             never=[r"behaviour"], rc=0))
    add(Case("cli failed before any run", "ft_libft", None, cli=["failed"],
             want=[r"No finished run of libft yet"], rc=2))
    add(Case("cli libft folder without libft.h is found", "ft_libft", None,
             cli=["ft_strlen"],
             mutate=lambda d: remove(d + "/libft.h"),
             want=[r"^libft ", r"OK ft_strlen:"]))
    return out


def spec(name):
    return os.path.join(SPECS, name + ".json")


def empty_first(d, py):
    with open(spec(py), encoding="utf-8") as fh:
        first = json.load(fh)["exercises"][0]["file"]
    if not os.path.isfile(os.path.join(d, first)):
        raise MutationError(f"{first} not in the project")
    write(os.path.join(d, first), "# nothing yet\n")


# ---------------------------------------------------------------- running

def tree_hash(d):
    h = hashlib.md5()
    for root, dirs, files in os.walk(d):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(root, f)
            h.update(p.encode())
            if os.path.islink(p):
                h.update(os.readlink(p).encode())
                continue
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def find_projects(roots):
    """{folder name: path}, up to 3 levels under each root."""
    found = {}
    wanted = set(PY) | set(C)
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        for cur, dirs, _ in os.walk(root):
            depth = cur[len(root):].count(os.sep)
            dirs[:] = [x for x in sorted(dirs) if not x.startswith(".")]
            if depth >= 3:
                dirs[:] = []
            base = os.path.basename(cur)
            if base in wanted and base not in found:
                found[base] = cur
                dirs[:] = []
    return found


def tester_cmd(tester):
    path = ("py/run_tests.py" if tester == "py" else f"{tester}/run.py")
    return [sys.executable, "-B", os.path.join(TESTERS, path)]


def run_case(case, projects, baseline, work, logs):
    """(status, line, result): status PASS / FAIL / SKIP."""
    if case.project and case.project not in projects:
        return "SKIP", f"SKIP  {case.name}  (no {case.project} folder)", None
    d = os.path.join(work, re.sub(r"\W+", "_", case.name))
    if case.project:
        shutil.copytree(projects[case.project], d, symlinks=True,
                        ignore=shutil.ignore_patterns(".git"))
    else:
        os.makedirs(d)
    try:
        if case.mutate:
            case.mutate(d)
    except MutationError as exc:
        return "FAIL", (f"FAIL  {case.name}\n      the injected bug no "
                        f"longer applies: {exc}"), None
    before = tree_hash(d)
    env = dict(os.environ, NO_COLOR="1", NEXT_NO_UPDATE="1", **case.env)
    env.pop("NEXT42_FAILED", None)
    if case.cli is not None:
        env["HOME"] = os.path.join(work, "home-" + os.path.basename(d))
        os.makedirs(env["HOME"], exist_ok=True)
        cmd = ["node", os.path.join(REPO, "bin", "42next.js"), *case.cli,
               "--no-color"]
        cwd = d
    elif case.project is None:
        cmd, cwd = [*tester_cmd(case.tester), *case.args], d
    else:
        cmd = [*tester_cmd(case.tester), "--project", d, "--no-color",
               *case.args]
        cwd = work
    t = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           env=env, timeout=900)
        out, rc = p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired as exc:
        out, rc = f"{exc.stdout or ''}{exc.stderr or ''}", None
    dt = time.time() - t
    errs = []
    if rc is None:
        errs.append("still running after 900s")
    for w in case.want:
        if not re.search(w, out, re.M):
            errs.append(f"missing /{w}/")
    for n in case.never:
        m = re.search(n, out, re.M)
        if m:
            errs.append(f"unexpected /{n}/: {m.group(0)[:100]}")
    if case.max_s and dt > case.max_s:
        errs.append(f"took {dt:.0f}s > {case.max_s}s")
    if case.rc is not None and rc != case.rc:
        errs.append(f"exit {rc}, expected {case.rc}")
    # a crash of 42next itself: a traceback at column 0 (the student's
    # tracebacks are quoted indented, as WARN / KO details)
    if re.search(r"^Traceback \(most recent call last\):", out, re.M):
        errs.append("Traceback in the output (42next crashed)")
    if tree_hash(d) != before:
        errs.append("the project copy was modified")
    res = re.findall(r"^RESULT .*$", out, re.M)
    result = res[-1] if res else None
    if case.base:
        got = short_result(result)
        want = baseline.get(case.name)
        if want is None:
            errs.append(f"no baseline yet (got {got!r}): run with --update")
        elif got != want:
            errs.append(f"expected {want!r} (baseline), got {got!r}")
    status = "FAIL" if errs else "PASS"
    line = f"{status}  {case.name}  ({dt:.0f}s) {result or ''}".rstrip()
    if errs:
        line += "\n      " + "\n      ".join(errs)
        log = os.path.join(logs, re.sub(r"\W+", "_", case.name) + ".log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(" ".join(cmd) + "\n\n" + out)
        line += f"\n      output: {log}"
    shutil.rmtree(d, ignore_errors=True)
    return status, line, short_result(result)


def short_result(line):
    """'RESULT 510/530' from the full RESULT line."""
    if not line:
        return None
    m = re.match(r"RESULT \d+/\d+", line)
    return m.group(0) if m else line


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("projects", nargs="+", help="folders with 42 projects")
    ap.add_argument("--only", default="", help="cases whose name has this")
    ap.add_argument("--update", action="store_true",
                    help="write the RESULT lines of this run as baselines")
    ap.add_argument("-j", type=int, default=max(2, (os.cpu_count() or 2) // 2))
    args = ap.parse_args()

    projects = find_projects(args.projects)
    try:
        with open(BASELINE, encoding="utf-8") as fh:
            baseline = json.load(fh)
    except (OSError, ValueError):
        baseline = {}
    todo = [c for c in cases() if args.only in c.name]
    work = tempfile.mkdtemp(prefix="42next-battery-")
    logs = tempfile.mkdtemp(prefix="42next-battery-logs-")
    print(f"{len(todo)} cases, {len(projects)} projects found, "
          f"{args.j} at a time")
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    new = dict(baseline)
    t = time.time()
    try:
        with ThreadPoolExecutor(max_workers=args.j) as pool:
            futs = {pool.submit(run_case, c, projects, baseline, work, logs):
                    c for c in todo}
            for fut in as_completed(futs):
                case = futs[fut]
                status, line, result = fut.result()
                counts[status] += 1
                print(line, flush=True)
                if case.base and result:
                    new[case.name] = result
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if args.update:
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(dict(sorted(new.items())), fh, indent=1)
            fh.write("\n")
        print(f"baselines written: {BASELINE}")
    if not counts["FAIL"]:
        shutil.rmtree(logs, ignore_errors=True)
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, "
          f"{counts['SKIP']} skipped ({time.time() - t:.0f}s)")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
