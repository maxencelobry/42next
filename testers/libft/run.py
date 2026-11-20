#!/usr/bin/env python3
"""42next - libft tester (subject v1.3).

Static checks (files, libft.h prototypes, norminette, Makefile, external
functions, README, repository) and a C test harness that runs every case
in its own process: crashes and infinite loops are reported per case, the
N-th malloc can be made to fail, leaks are tracked per case and the whole
binary is also run under valgrind, one function at a time (an
AddressSanitizer build of the sources when valgrind is not installed).

    python3 -B run.py --project DIR [--only g1,g2] [--list] [--no-color]
                      [--verbose] [--keep] [--quick]
"""

import os
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import core  # noqa: E402

C_DIR = os.path.join(HERE, "c")

PART1 = ["isalpha", "isdigit", "isalnum", "isascii", "isprint", "strlen",
         "memset", "bzero", "memcpy", "memmove", "strlcpy", "strlcat",
         "toupper", "tolower", "strchr", "strrchr", "strncmp", "memchr",
         "memcmp", "strnstr", "atoi", "calloc", "strdup"]
PART2 = ["substr", "strjoin", "strtrim", "split", "itoa", "strmapi",
         "striteri", "putchar_fd", "putstr_fd", "putendl_fd", "putnbr_fd"]
PART3 = ["lstnew", "lstadd_front", "lstsize", "lstlast", "lstadd_back",
         "lstdelone", "lstclear", "lstiter", "lstmap"]
FUNCS = ["ft_" + f for f in PART1 + PART2 + PART3]

# external functions allowed by the subject, per function
ALLOWED: dict[str, set[str]] = {f: set() for f in FUNCS}
for _f in ("calloc", "strdup", "substr", "strjoin", "strtrim", "itoa",
           "strmapi", "lstnew"):
    ALLOWED["ft_" + _f] = {"malloc"}
for _f in ("split", "lstmap"):
    ALLOWED["ft_" + _f] = {"malloc", "free"}
for _f in ("lstdelone", "lstclear"):
    ALLOWED["ft_" + _f] = {"free"}
for _f in ("putchar_fd", "putstr_fd", "putendl_fd", "putnbr_fd"):
    ALLOWED["ft_" + _f] = {"write"}

PROTOTYPES = {
    "ft_isalpha": "int ft_isalpha(int c)",
    "ft_isdigit": "int ft_isdigit(int c)",
    "ft_isalnum": "int ft_isalnum(int c)",
    "ft_isascii": "int ft_isascii(int c)",
    "ft_isprint": "int ft_isprint(int c)",
    "ft_strlen": "size_t ft_strlen(const char *s)",
    "ft_memset": "void *ft_memset(void *s, int c, size_t n)",
    "ft_bzero": "void ft_bzero(void *s, size_t n)",
    "ft_memcpy": "void *ft_memcpy(void *dest, const void *src, size_t n)",
    "ft_memmove": "void *ft_memmove(void *dest, const void *src, size_t n)",
    "ft_strlcpy": "size_t ft_strlcpy(char *dst, const char *src, size_t size)",
    "ft_strlcat": "size_t ft_strlcat(char *dst, const char *src, size_t size)",
    "ft_toupper": "int ft_toupper(int c)",
    "ft_tolower": "int ft_tolower(int c)",
    "ft_strchr": "char *ft_strchr(const char *s, int c)",
    "ft_strrchr": "char *ft_strrchr(const char *s, int c)",
    "ft_strncmp": "int ft_strncmp(const char *s1, const char *s2, size_t n)",
    "ft_memchr": "void *ft_memchr(const void *s, int c, size_t n)",
    "ft_memcmp": "int ft_memcmp(const void *s1, const void *s2, size_t n)",
    "ft_strnstr": "char *ft_strnstr(const char *big, const char *little, "
                  "size_t len)",
    "ft_atoi": "int ft_atoi(const char *nptr)",
    "ft_calloc": "void *ft_calloc(size_t nmemb, size_t size)",
    "ft_strdup": "char *ft_strdup(const char *s)",
    "ft_substr": "char *ft_substr(char const *s, unsigned int start, "
                 "size_t len)",
    "ft_strjoin": "char *ft_strjoin(char const *s1, char const *s2)",
    "ft_strtrim": "char *ft_strtrim(char const *s1, char const *set)",
    "ft_split": "char **ft_split(char const *s, char c)",
    "ft_itoa": "char *ft_itoa(int n)",
    "ft_strmapi": "char *ft_strmapi(char const *s, "
                  "char (*f)(unsigned int, char))",
    "ft_striteri": "void ft_striteri(char *s, "
                   "void (*f)(unsigned int, char *))",
    "ft_putchar_fd": "void ft_putchar_fd(char c, int fd)",
    "ft_putstr_fd": "void ft_putstr_fd(char *s, int fd)",
    "ft_putendl_fd": "void ft_putendl_fd(char *s, int fd)",
    "ft_putnbr_fd": "void ft_putnbr_fd(int n, int fd)",
    "ft_lstnew": "t_list *ft_lstnew(void *content)",
    "ft_lstadd_front": "void ft_lstadd_front(t_list **lst, t_list *new)",
    "ft_lstsize": "unsigned int ft_lstsize(t_list *lst)",
    "ft_lstlast": "t_list *ft_lstlast(t_list *lst)",
    "ft_lstadd_back": "void ft_lstadd_back(t_list **lst, t_list *new)",
    "ft_lstdelone": "void ft_lstdelone(t_list *lst, void (*del)(void *))",
    "ft_lstclear": "void ft_lstclear(t_list **lst, void (*del)(void *))",
    "ft_lstiter": "void ft_lstiter(t_list *lst, void (*f)(void *))",
    "ft_lstmap": "t_list *ft_lstmap(t_list *lst, void *(*f)(void *), "
                 "void (*del)(void *))",
}

STATIC_GROUPS = [
    ("files", "expected files at the root, libft.h prototypes and t_list"),
    ("norm", "norminette"),
    ("makefile", "Makefile rules, no relink, libft.a built with ar"),
    ("functions", "external functions per function, no global variable, "
                  "static helpers"),
    ("readme", "README.md requirements"),
    ("repo", "no build garbage in the repository"),
]
SHORTCUTS = {
    "part1": ("Part 1 - libc functions (23)", ["ft_" + f for f in PART1]),
    "part2": ("Part 2 - additional functions (11)",
              ["ft_" + f for f in PART2]),
    "lists": ("Part 3 - linked lists (9)", ["ft_" + f for f in PART3]),
    "mandatory": ("all 43 functions", FUNCS),
}


def groups():
    out = list(STATIC_GROUPS)
    out += [(k, v[0]) for k, v in SHORTCUTS.items()]
    out += [(f, f"tests for {f}") for f in FUNCS]
    return out


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

TOKEN = re.compile(r"\{\{(NULL|[0-9a-f]*)\}\}")


def render(text):
    """Turn {{hex}} tokens from the C harness into C string literals."""
    def sub(m):
        if m.group(1) == "NULL":
            return "NULL"
        return core.crepr(bytes.fromhex(m.group(1)), limit=200)
    return TOKEN.sub(sub, text)


def text_of(res):
    return (res.out + res.err).decode("utf-8", "replace")


def root_files(project):
    try:
        return sorted(os.listdir(project))
    except OSError:
        return []


def source_for(func, names):
    for cand in (f"{func}.c", f"{func}_bonus.c"):
        if cand in names:
            return cand
    return None


def defined_in(func, project, names):
    """True if a root .c file defines func. The Norm puts the return type
    at the start of the line, so `type<TAB>func(` at column 0 is the
    definition, never a call (calls are indented)."""
    pat = re.compile(r"(?m)^[A-Za-z_][\w \t\*]*\b" + re.escape(func)
                     + r"\s*\([^;]*$")
    for n in names:
        if not n.endswith(".c"):
            continue
        try:
            with open(os.path.join(project, n), encoding="utf-8",
                      errors="replace") as fh:
                if pat.search(fh.read()):
                    return True
        except OSError:
            pass
    return False


def make_if_needed(rep, ws_dir, state):
    """Build libft.a in the workspace (once). Returns its path or None."""
    lib = os.path.join(ws_dir, "libft.a")
    if state.get("built"):
        return lib if os.path.isfile(lib) else None
    state["built"] = True
    text = core.makefile_text(ws_dir)
    if text is None:
        rep.check("Makefile present (needed to build libft.a)", False)
        return None
    # v1.3 has no bonus part: the linked lists are Part 3 of the mandatory
    # part, so `make` alone must put all 43 functions in libft.a
    res = core.run(["make"], cwd=ws_dir, timeout=300)
    if not os.path.isfile(lib):
        rep.check("make builds libft.a at the root", False,
                  detail=core.short_errors(text_of(res)) or
                  "libft.a not created")
        return None
    return lib


def make_commands(ws_dir):
    """Commands `make` and `make bonus` would run (make -n -B), in a scratch
    copy of the workspace. None when make cannot tell."""
    text = core.makefile_text(ws_dir)
    if text is None or not core.have("make"):
        return None
    dry = os.path.join(os.path.dirname(ws_dir), "make_dry_run")
    shutil.rmtree(dry, ignore_errors=True)
    try:
        shutil.copytree(ws_dir, dry, symlinks=True)
    except (OSError, shutil.Error):
        return None
    goals = [[]] + ([["bonus"]] if "bonus" in core.makefile_targets(text)
                    else [])
    out = []
    try:
        for goal in goals:
            res = core.run(["make", "-n", "-B", *goal], cwd=dry, timeout=60)
            if res.rc == 0:
                out.append(res.out.decode("utf-8", "replace"))
    finally:
        shutil.rmtree(dry, ignore_errors=True)
    return "\n".join(out) if out else None


def project_sources(ws_dir):
    """{stem: path} of the .c files of the copy, and the folders with a .h"""
    by_stem, incdirs = {}, set()
    for root, dirs, files in os.walk(ws_dir):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for f in sorted(files):
            if f.endswith(".c"):
                by_stem.setdefault(f[:-2], os.path.join(root, f))
            elif f.endswith(".h"):
                incdirs.add(root)
    return by_stem, sorted(incdirs)


def archive_members(lib, workdir):
    """Extract libft.a into workdir. Returns list of member paths."""
    os.makedirs(workdir, exist_ok=True)
    res = core.run(["ar", "t", lib], timeout=30)
    names = [n for n in res.out.decode("utf-8", "replace").split("\n") if n]
    core.run(["ar", "x", lib], cwd=workdir, timeout=30)
    return [os.path.join(workdir, n) for n in names
            if os.path.isfile(os.path.join(workdir, n))]


def nm_lines(path, *flags):
    res = core.run(["nm", *flags, path], timeout=30)
    out = []
    for line in res.out.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            out.append((parts[-2], parts[-1]))
    return out


# --------------------------------------------------------------------------
# static groups
# --------------------------------------------------------------------------

def check_files(rep, project, ws_dir):
    rep.section("files")
    names = root_files(project)
    for need in ("Makefile", "libft.h"):
        rep.check(f"{need} at the root", need in names)
    # "Files to Submit: Makefile, libft.h, ft_*.c": every .c is an ft_*.c,
    # but nothing says one file per function (ft_lst_utils.c is fine)
    other = [f for f in names if f.endswith(".c") and not f.startswith("ft_")]
    rep.check("every .c file is named ft_*.c", not other,
              detail=("not ft_*.c: " + ", ".join(other)) if other else None)
    missing = [f for f in FUNCS if not source_for(f, names)
               and not defined_in(f, project, names)]
    rep.check(f"the {len(FUNCS)} functions are defined in the .c files",
              not missing,
              detail=("not found: " + ", ".join(missing)) if missing else None)
    bonus_files = [f for f in names if f.endswith("_bonus.c")]
    if bonus_files:
        rep.old_subject("*_bonus.c files (" + ", ".join(bonus_files[:3])
                        + (", ..." if len(bonus_files) > 3 else "")
                        + "): v1.3 has no bonus part")
    if "bonus" in core.makefile_targets(core.makefile_text(project) or ""):
        rep.old_subject("a bonus rule in the Makefile: v1.3 has no bonus "
                        "part")

    # "Place all your files at the root": no source in sub folders
    nested = []
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if os.path.abspath(root) == os.path.abspath(project):
            continue
        nested += [os.path.relpath(os.path.join(root, f), project)
                   for f in files if f.endswith((".c", ".h"))]
    rep.check("all source files at the root (no sub folder)", not nested,
              detail=("found: " + ", ".join(nested[:10])) if nested else None)

    # "Turning in unused files is forbidden": every .c must be compiled.
    # What make would run (make -n -B), so $(addprefix), $(wildcard) and
    # friends are expanded. Text search of the Makefile only as a fallback.
    commands = make_commands(ws_dir)
    ext = r"\.[co]"
    how = "not compiled by make / make bonus"
    if commands is None:
        commands = core.makefile_text(project) or ""
        ext = r"(\.c|\.o)?"
        how = "not in the Makefile"
    unused = [f for f in names if f.endswith(".c")
              and not re.search(r"(?<![\w.])" + re.escape(f[:-2])
                                + ext + r"(?![\w])", commands)]
    rep.check("no unused .c file (each one is compiled by the Makefile)",
              not unused,
              detail=(f"{how}: " + ", ".join(unused)) if unused else None)

    header = os.path.join(project, "libft.h")
    if not os.path.isfile(header):
        return
    with open(header, encoding="utf-8", errors="replace") as fh:
        htext = fh.read()
    check_prototypes(rep, htext, ws_dir)


def signed_only(conflicts, ws_dir, probe):
    """Conflicts that go away when 'unsigned int' is read as 'int'."""
    cand = [f for f in conflicts
            if PROTOTYPES.get(f, "").startswith("unsigned int ")]
    if not cand:
        return []
    with open(probe, "w") as fh:
        fh.write('#include "libft.h"\n' + "\n".join(
            PROTOTYPES[f][len("unsigned "):] + ";" for f in cand) + "\n")
    res = core.run(["cc", "-fsyntax-only", "-I", ws_dir, probe], timeout=60)
    still = set(re.findall(r"conflicting types for [‘'\"](\w+)",
                           text_of(res)))
    return [f for f in cand if f not in still]


def check_prototypes(rep, htext, ws_dir):
    clean = re.sub(r"/\*.*?\*/", "", htext, flags=re.S)
    clean = re.sub(r"//[^\n]*", "", clean)
    rep.check("no 'restrict' in libft.h (forbidden by the subject)",
              not re.search(r"\brestrict\b", clean))
    undeclared = [f for f in FUNCS
                  if not re.search(r"\b" + f + r"\s*\(", clean)]
    rep.check("libft.h declares the 43 functions", not undeclared,
              detail=("not declared: " + ", ".join(undeclared))
              if undeclared else None)

    probe = os.path.join(os.path.dirname(ws_dir), "proto_probe.c")
    decls = "\n".join(PROTOTYPES[f] + ";" for f in FUNCS
                      if f not in undeclared)
    with open(probe, "w") as fh:
        fh.write('#include "libft.h"\n' + decls + "\n"
                 "int nx_probe(void)\n{\n\tt_list l;\n\n"
                 "\tl.content = (void *)0;\n\tl.next = &l;\n"
                 "\treturn (l.next != 0);\n}\n")
    res = core.run(["cc", "-fsyntax-only", "-I", ws_dir, probe], timeout=60)
    out = text_of(res)
    conflicts = sorted(set(re.findall(
        r"conflicting types for [‘'\"](\w+)", out)))
    struct_bad = bool(re.search(r"unknown type name [‘'\"]t_list|"
                                r"has no member named|no member named", out))
    other = res.rc != 0 and not conflicts and not struct_bad
    # int ft_lstsize: the prototype of the older subject (lists in the bonus
    # part). Still a KO, and listed with the other signs of an old subject.
    for f in signed_only(conflicts, ws_dir, probe):
        rep.old_subject(f"{f} returns int (v1.3: unsigned int)")
    if conflicts:
        lines = []
        for f in conflicts:
            m = re.search(r"^[^\n;]*\b" + f + r"\s*\([^;]*;", clean, re.M)
            got = " ".join(m.group(0).split()) if m else "?"
            lines.append(f"{f}:\n  subject: {PROTOTYPES.get(f, '?')};"
                         f"\n  libft.h: {got}")
        rep.check("prototypes match the subject", False,
                  detail="\n".join(lines))
    else:
        rep.check("prototypes match the subject", not other,
                  detail=core.short_errors(out) if other else None)
    rep.check("t_list declared with content and next", not struct_bad,
              detail=core.short_errors(out) if struct_bad else None)


def check_makefile_group(rep, ws_dir, state):
    rep.section("makefile")
    ok = core.check_makefile(rep, ws_dir, "libft.a")
    state["built"] = True
    text = core.makefile_text(ws_dir)
    if text is None:
        return
    c99 = re.search(r"-std\s*=\s*c99\b", text)
    rep.check("no -std=c99 flag", not c99,
              detail='subject: "forbidden [...] to compile your code with '
                     'the -std=c99 flag"' if c99 else None)
    rep.check("libtool not used (forbidden, use ar)",
              not re.search(r"\blibtool\b", text))
    rep.check("Makefile uses ar",
              bool(re.search(r"\bar\b|\$[({]AR[)}]", text)))
    lib = os.path.join(ws_dir, "libft.a")
    if ok and os.path.isfile(lib):
        with open(lib, "rb") as fh:
            magic = fh.read(8)
        rep.check("libft.a is an ar archive", magic == b"!<arch>\n",
                  got=None if magic == b"!<arch>\n" else core.crepr(magic))


def check_functions_group(rep, ws_dir, state):
    rep.section("functions")
    lib = make_if_needed(rep, ws_dir, state)
    if not lib:
        rep.skip("external functions", "libft.a not built")
        return
    if not core.have("nm"):
        rep.skip("external functions", "nm not installed")
        return
    members = archive_members(lib, os.path.join(os.path.dirname(ws_dir),
                                                "ar_x"))
    defined = set()
    for m in members:
        defined |= {s for t, s in nm_lines(m, "--defined-only")}
    by_stem, incdirs = project_sources(ws_dir)
    bad = []
    unsure = []
    globals_ = []
    helpers = []
    for m in members:
        base = os.path.basename(m)
        func = re.sub(r"(_bonus)?\.o$", "", base)
        allowed = ALLOWED.get(func, {"malloc", "free", "write"})
        und = {s for t, s in nm_lines(m, "-u")}
        ext = sorted(s for s in und if s not in defined
                     and s not in core.IGNORED_SYMBOLS)
        wrong = [s for s in ext if s not in allowed
                 and s not in core.COMPILER_ALIASES]
        alias = [s for s in ext if s in core.COMPILER_ALIASES
                 and s not in allowed]
        note = ""
        if wrong or alias:
            # The optimizer turns plain loops into memset / memcpy / strlen
            # calls. What counts is what the source calls (at -O0): that
            # also catches ft_memcpy returning the libc memcpy.
            src = by_stem.get(base[:-2])
            found = core.source_calls(src, incdirs,
                                      workdir=os.path.dirname(ws_dir)) \
                if src else None
            if found is not None:
                wrong = sorted(s for s in found if s not in defined
                               and s not in core.IGNORED_SYMBOLS
                               and s not in allowed)
                called = [s for s in wrong if s in core.COMPILER_ALIASES]
                if called:
                    note = (f"; {', '.join(called)} is called in "
                            f"{os.path.basename(src)} itself, not added "
                            "by the compiler")
            elif alias:
                unsure.append(f"{base}: {', '.join(alias)}")
        if wrong:
            bad.append(f"{base}: {', '.join(sorted(wrong))} (allowed: "
                       f"{', '.join(sorted(allowed)) or 'none'}{note})")
        for t, s in nm_lines(m, "--defined-only"):
            # a static inside a function (nm: calls.0, f.calls) is not a
            # global variable; a static at file scope is one
            if t in "DBCGSVv" or (t in "dbgs" and "." not in s):
                globals_.append(f"{s} ({base})")
            elif t == "T" and s not in FUNCS:
                helpers.append(f"{s} ({base})")
    rep.check("external functions: only the ones the subject allows per "
              "function", not bad, detail="\n".join(bad) or None)
    if unsure:
        rep.warn("memcpy / memset / memmove used: could not tell if the "
                 "compiler added the call or the source makes it",
                 "\n".join(unsure) + "\n(the source file was not found or "
                 "does not compile on its own; calling the libc function "
                 "is forbidden, a call added by the optimizer is not)")
    rep.check("no global variable", not globals_,
              detail=("found: " + ", ".join(globals_)) if globals_ else None)
    rep.check("helper functions are static", not helpers,
              detail=("not static: " + ", ".join(helpers)
                      + "\n(subject: define helper functions as static)")
              if helpers else None)


# --------------------------------------------------------------------------
# function tests
# --------------------------------------------------------------------------

class Case:
    def __init__(self, status, call):
        self.status = status
        self.call = call
        self.exp = []
        self.got = []
        self.detail = []
        self.info = []


def parse_output(text):
    cases = {}
    missing = set()
    cur = None
    case = None
    for line in text.splitlines():
        tag, _, rest = line.partition("\t")
        if tag == "@F":
            cur = rest
            cases.setdefault(cur, [])
        elif tag == "@N":
            missing.add(rest)
        elif tag == "@C" and cur:
            status, _, call = rest.partition("\t")
            case = Case(status, call)
            cases[cur].append(case)
        elif case and tag == "@E":
            case.exp.append(rest)
        elif case and tag == "@G":
            case.got.append(rest)
        elif case and tag == "@D":
            case.detail.append(rest)
        elif case and tag == "@I":
            case.info.append(rest)
    return cases, missing, cur


def build_tester(rep, ws, lib):
    """Compile the harness and link it with libft.a. None on failure."""
    objdir = ws.tmp("objs")
    os.makedirs(objdir, exist_ok=True)
    objs = []
    for src in sorted(os.listdir(C_DIR)):
        if not src.endswith(".c"):
            continue
        obj = os.path.join(objdir, src[:-2] + ".o")
        ok, out = core.compile_c(["-c", os.path.join(C_DIR, src)], obj,
                                 includes=[C_DIR],
                                 flags=["-g", "-fno-builtin"])
        if not ok:
            print(core.red(f"tester does not compile ({src}):"))
            print(core.short_errors(out))
            sys.exit(2)
        objs.append(obj)
    syms = {s for t, s in nm_lines(lib, "--defined-only")
            if t in "TtWw" and s in FUNCS}
    force = [f"-Wl,-u,{s}" for s in sorted(syms)]
    exe = ws.tmp("libft_tester")
    res = core.run(["cc", *objs, "-o", exe, "-Wl,--wrap=malloc",
                    "-Wl,--wrap=free", *force, lib], timeout=120)
    if res.rc != 0:
        rep.section("link")
        rep.check("test program links with libft.a", False,
                  detail=core.short_errors(text_of(res))
                  + "\n(undefined reference: a function of libft.a calls "
                  "something that is not in it)")
        return None
    return exe


VG_ERROR = re.compile(r"^==\d+== (Invalid|Conditional|Use of|Mismatched|"
                      r"Source and|Syscall|Argument)", re.M)


def vg_describe(leaks):
    """Like Leaks.describe, but without counting leak records as errors."""
    parts = []
    if leaks.definitely:
        parts.append(f"{leaks.definitely} bytes definitely lost")
    if leaks.indirectly:
        parts.append(f"{leaks.indirectly} bytes indirectly lost")
    errors = len(VG_ERROR.findall(leaks.log))
    if errors:
        parts.append(f"{errors} memory error(s)")
    return ", ".join(parts) or "no leak, no memory error"


def run_valgrind(exe, func):
    run, leaks = core.valgrind([exe, "--nofork", func], timeout=300)
    return func, run, leaks


# --------------------------------------------------------------------------
# AddressSanitizer, when valgrind is not installed
# --------------------------------------------------------------------------

ASAN = ["-fsanitize=address", "-fno-omit-frame-pointer"]
# allocator_may_return_null: the calloc overflow cases ask for huge sizes
ASAN_ENV = "exitcode=86:abort_on_error=0:allocator_may_return_null=1"


def build_asan_tester(ws, lib):
    """Harness + the sources behind libft.a, built with ASan.

    -> (exe, None) or (None, why)
    """
    res = core.run(["ar", "t", lib], timeout=30)
    names = [n for n in res.out.decode("utf-8", "replace").split()
             if n.endswith(".o")]
    by_stem, incdirs = project_sources(ws.dir)
    missing = [n for n in names if n[:-2] not in by_stem]
    if not names or missing:
        return None, ("source of " + ", ".join(missing[:4])
                      + " not found" if missing else "libft.a is empty")
    objdir = ws.tmp("asan_objs")
    os.makedirs(objdir, exist_ok=True)
    jobs = []
    for src in sorted(os.listdir(C_DIR)):
        if src.endswith(".c"):
            jobs.append((os.path.join(C_DIR, src),
                         os.path.join(objdir, "h_" + src[:-2] + ".o"),
                         [C_DIR], ["-fno-builtin"]))
    for n in names:
        jobs.append((by_stem[n[:-2]], os.path.join(objdir, "p_" + n),
                     incdirs, ["-w"]))

    def compile_one(job):
        src, obj, incs, extra = job
        cmd = ["cc", "-g", *ASAN, *extra, "-c", src, "-o", obj]
        for inc in incs:
            cmd += ["-I", inc]
        return src, core.run(cmd, timeout=120)

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
        for src, r in pool.map(compile_one, jobs):
            if r.rc != 0:
                return None, (f"{os.path.basename(src)} does not compile "
                              "with -fsanitize=address:\n"
                              + core.short_errors(text_of(r), 4))
    exe = ws.tmp("libft_tester_asan")
    r = core.run(["cc", *ASAN, *[j[1] for j in jobs], "-o", exe,
                  "-Wl,--wrap=malloc", "-Wl,--wrap=free"], timeout=120)
    if r.rc != 0:
        return None, "link failed:\n" + core.short_errors(text_of(r), 4)
    return exe, None


def _asan_once(exe, func, logdir, leaks):
    """One ASan run. The report goes to a file: some cases close fd 2."""
    log = os.path.join(logdir, f"asan_{func}_{int(leaks)}")
    res = core.run([exe, "--nofork", func], timeout=300,
                   env={"ASAN_OPTIONS": f"{ASAN_ENV}:detect_leaks="
                        f"{int(leaks)}:log_path={log}"})
    text = []
    for name in sorted(os.listdir(logdir)):
        if name.startswith(os.path.basename(log) + "."):
            path = os.path.join(logdir, name)
            with open(path, encoding="utf-8", errors="replace") as fh:
                text.append(fh.read())
            os.remove(path)
    res.err += "".join(text).encode()
    return res


def run_asan(exe, func):
    """-> (func, Run, leaks checked?)"""
    logdir = os.path.dirname(exe)
    res = _asan_once(exe, func, logdir, True)
    if b"LeakSanitizer has encountered a fatal error" in res.err:
        # LeakSanitizer does not work here (ptrace denied, ...)
        return func, _asan_once(exe, func, logdir, False), False
    return func, res, True


def asan_result(res):
    """-> (clean, summary, detail) from an ASan / LeakSanitizer run."""
    err = res.err.decode("utf-8", "replace")
    lines = err.splitlines()
    summary = next((ln for ln in lines if ln.startswith("SUMMARY:")), "")
    frames = [re.sub(r" 0x[0-9a-f]+ in ", " ", ln.strip()) for ln in lines
              if re.match(r"\s*#\d+ ", ln)]
    frames = [re.sub(r"/\S*/42next-libft-[^/]+/(project/|asan_objs/)?", "",
                     f).replace(C_DIR + os.sep, "") for f in frames
              if not re.search(r"libasan|libsanitizer|__wrap_|"
                               r"nx_raw_malloc", f)][:6]
    summary = re.sub(r"/\S*/42next-libft-[^/]+/(project/)?", "",
                     summary).replace(C_DIR + os.sep, "")
    head = next((ln for ln in lines if "ERROR: AddressSanitizer" in ln),
                None)
    if head:
        head = re.sub(r"^==\d+==\s*ERROR: AddressSanitizer: ", "", head)
        head = re.sub(r" on address 0x[0-9a-f]+.*", "", head)
        return False, f"memory error ({head})", "\n".join(frames + [summary])
    if "ERROR: LeakSanitizer" in err:
        m = re.search(r"(\d+) byte\(s\) leaked in (\d+) allocation", summary)
        what = (f"{m.group(1)} bytes leaked in {m.group(2)} block(s)"
                if m else "memory leak")
        return False, what, "\n".join(frames)
    if res.timed_out or res.crashed:
        return False, res.status(), None
    if res.rc not in (0, None):
        return False, f"exit code {res.rc}", core.short_errors(err, 6) or None
    return True, "no leak, no memory error", None


def show_case(rep, c):
    name = render(c.call)
    infos = [render(i) for i in c.info]
    if c.status == "OK":
        rep.check(name, True)
        for i in infos:
            rep.warn(f"{name}: {i}",
                     "the subject does not fix the allocation size: "
                     "up to the evaluator")
    elif c.status == "WARN":
        rep.warn(name, "\n".join(render(d) for d in c.detail + c.info))
    else:
        exp = render(c.exp[0]) if c.exp else None
        got = render(c.got[0]) if c.got else None
        more = []
        for e, g in list(zip(c.exp, c.got))[1:]:
            more += [f"expected: {render(e)}", f"got:      {render(g)}"]
        more += [render(d) for d in c.detail]
        more += infos
        if c.status == "TIMEOUT":
            more.insert(0, "TIMEOUT")
        rep.check(name, False, expected=exp, got=got,
                  detail="\n".join(more) or None)


def test_functions(rep, ws, funcs, state, args):
    lib = make_if_needed(rep, ws.dir, state)
    if not lib:
        rep.section("function tests")
        rep.check("libft.a available for the tests", False,
                  detail="the Makefile did not produce libft.a at the root")
        return
    exe = build_tester(rep, ws, lib)
    if not exe:
        return
    # 5 s per case, and the harness stops a function after 2 timeouts:
    # one looping function costs about 10 s, it cannot sink the others
    res = core.run([exe, "--timeout", "5", *funcs], cwd=ws.root,
                   timeout=120 + 20 * len(funcs))
    cases, missing, last = parse_output(res.out.decode("utf-8", "replace"))
    died = None
    if res.timed_out or res.rc != 0:
        died = res.status()

    todo = [f for f in funcs if f in cases and cases[f]
            and not any(c.status in ("CRASH", "TIMEOUT", "SKIP")
                        for c in cases[f])]
    workers = max(1, min(len(todo), os.cpu_count() or 2))
    vg = {}
    asan = {}
    use_vg = core.have("valgrind") and not os.environ.get("NEXT42_NO_VALGRIND")
    if args.quick or not todo:
        pass
    elif use_vg:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = pool.map(lambda f: run_valgrind(exe, f), todo)
            for func, run, leaks in results:
                vg[func] = (run, leaks)
    else:
        asan_exe, why = build_asan_tester(ws, lib)
        if asan_exe:
            rep.skip("valgrind", "not installed: memory checked with "
                     "AddressSanitizer + LeakSanitizer instead")
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for func, run, leaks_on in pool.map(
                        lambda f: run_asan(asan_exe, f), todo):
                    asan[func] = (run, leaks_on)
        else:
            rep.skip("valgrind", "not installed, and no AddressSanitizer "
                     f"build ({why.splitlines()[0]}): only the per-case "
                     "leak tracking checks memory")

    for func in funcs:
        rep.section(func)
        if func in missing:
            why = ("symbol not defined in the archive (missing file in the "
                   "Makefile?)")
            if func[3:] in PART3:
                why += ("\nsubject v1.3: the linked lists are Part 3 of the "
                        "mandatory part, `make` must build them (no bonus "
                        "rule)")
                rep.old_subject("linked lists not built by `make` (v1.3: "
                                "Part 3 of the mandatory part)")
            rep.check(f"{func} found in libft.a", False, detail=why)
            continue
        if func not in cases:
            rep.check(f"{func} tests ran", False,
                      detail=f"the test program stopped before ({died})"
                      if died else None)
            continue
        skipped = [c for c in cases[func] if c.status == "SKIP"]
        for c in cases[func]:
            if c.status != "SKIP":
                show_case(rep, c)
        if skipped:
            rep.skip(f"{len(skipped)} more case(s) of {func}",
                     "not run: 2 cases already timed out")
        if died and func == last:
            rep.check("test program finished", False, detail=died)
        if func in asan:
            run, leaks_on = asan[func]
            clean, what, detail = asan_result(run)
            if not clean and any(c.status != "OK" for c in cases[func]):
                detail = (detail or "") + (
                    "\n(some cases already failed above: part of this may "
                    "be memory the tester could not free after them)")
            rep.check(f"AddressSanitizer ({func}): {what}", clean,
                      detail=detail.strip() if detail else None)
            if not leaks_on:
                rep.skip("LeakSanitizer", "does not work on this system: "
                         "leaks only checked per case")
        elif func in vg:
            run, leaks = vg[func]
            if run is None or leaks is None:
                continue
            if run.timed_out or run.crashed:
                rep.check("valgrind run", False, detail=run.status())
            else:
                ko = any(c.status != "OK" for c in cases[func])
                detail = None
                if not leaks.clean:
                    detail = leaks.first_error() or ""
                    if ko:
                        detail += ("\n(some cases already failed above: part "
                                   "of this may be memory the tester could "
                                   "not free after them)")
                rep.check(f"valgrind ({func}): {vg_describe(leaks)}",
                          leaks.clean, detail=detail)
        elif not args.quick and func not in todo and (use_vg or asan):
            rep.skip("valgrind" if use_vg else "AddressSanitizer",
                     "skipped: a case crashed or timed out")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    args = core.parse_args("42next libft tester (subject v1.3)", groups())
    if not hasattr(args, "quick"):
        args.quick = "--quick" in args.extra
    if not os.path.isdir(args.project):
        print(core.red(f"project directory not found: {args.project}"))
        return 2
    if not core.have("cc"):
        print(core.red("cc not found"))
        return 2
    rep = core.Report(verbose=args.verbose)
    core.header("libft", args.project, subject="1.3")
    if args.quick:
        core.quick_skip(rep, "no valgrind")

    funcs = []
    for gid in args.only:
        if gid in SHORTCUTS:
            funcs += SHORTCUTS[gid][1]
        elif gid in FUNCS:
            funcs.append(gid)
    if not args.only:
        funcs = list(FUNCS)
    funcs = [f for f in FUNCS if f in set(funcs)]

    state = {}
    with core.Workspace(args.project, keep=args.keep,
                        prefix="42next-libft-") as ws:
        if core.selected(args, "files"):
            check_files(rep, args.project, ws.dir)
        if core.selected(args, "norm"):
            rep.section("norm")
            core.check_norm(rep, ws.dir)
        if core.selected(args, "makefile"):
            check_makefile_group(rep, ws.dir, state)
        if core.selected(args, "functions"):
            check_functions_group(rep, ws.dir, state)
        if core.selected(args, "readme"):
            rep.section("readme")
            core.check_readme(rep, args.project)
        if core.selected(args, "repo"):
            rep.section("repo")
            core.check_repo(rep, args.project, binaries=("libft.a",))
        if funcs:
            test_functions(rep, ws, funcs, state, args)
        return rep.finish()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # ctrl-c: the temporary copies are removed on the way out
        print("\nstopped (ctrl-c)")
        sys.exit(130)
