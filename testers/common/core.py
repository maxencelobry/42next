"""Shared helpers for the 42next testers (stdlib only, Python 3.9+).

Every tester follows the same contract, so the Node menu can drive any of
them the same way:

    python3 -B run.py --project DIR [--only g1,g2] [--list] [--no-color]
                      [--verbose] [--keep]

  --list     print one selectable group per line: "<id>\t<label>"
  --only     run only these group ids (comma separated)
  --verbose  print passing checks too (default: only failures + totals)
  --keep     keep the temporary build directory and print its path
  --quick    skip the slow parts (valgrind, big benchmarks): args.quick

Exit code: 0 everything passed, 1 something failed, 2 setup problem.
The project directory is never written to: everything is built in a copy.
"""

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

# --------------------------------------------------------------------------
# colours
# --------------------------------------------------------------------------

USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ

# neutral theme, same as the menu: grey text, colour only for OK / KO /
# WARN. Exact colours on truecolor terminals, nearest 256-colour otherwise
_TRUE = any(x in os.environ.get("COLORTERM", "")
            for x in ("truecolor", "24bit"))


def _rgb(hexa, c256):
    if not _TRUE:
        return f"38;5;{c256}"
    r, g, b = (int(hexa[i:i + 2], 16) for i in (1, 3, 5))
    return f"38;2;{r};{g};{b}"


THEME = {"dim": _rgb("#8a8a8a", 245),
         "ok": _rgb("#4ade80", 78), "ko": _rgb("#f87171", 203),
         "warn": _rgb("#fbbf24", 220)}


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def green(t):
    return _c(THEME["ok"], t)


def red(t):
    return _c(THEME["ko"], t)


def yellow(t):
    return _c(THEME["warn"], t)


def accent(t):
    return _c("1", t)


def dim(t):
    return _c(THEME["dim"], t)


def bold(t):
    return _c("1", t)


# --------------------------------------------------------------------------
# arguments
# --------------------------------------------------------------------------

# Group ids of the running tester (set by parse_args). A Report section whose
# name is one of them maps to that group for NEXT42_FAILED.
GROUP_IDS: set = set()


def parse_args(description, groups):
    """groups: list of (id, label). Handles --list itself and exits."""
    global USE_COLOR
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--project", default=".")
    parser.add_argument("--only", default="")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--color", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--quick", action="store_true",
                        help="skip valgrind and long benchmarks")
    args, extra = parser.parse_known_args()
    args.extra = extra
    if args.no_color:
        USE_COLOR = False
    if args.color:
        USE_COLOR = True
    if args.list:
        for gid, label in groups:
            print(f"{gid}\t{label}")
        sys.exit(0)
    known = {g for g, _ in groups}
    GROUP_IDS.clear()
    GROUP_IDS.update(known)
    args.only = [g for g in args.only.split(",") if g]
    unknown = [g for g in args.only if g not in known]
    if unknown:
        print(red(f"unknown group(s): {', '.join(unknown)}"))
        print("available: " + ", ".join(sorted(known)))
        sys.exit(2)
    args.project = os.path.abspath(args.project)
    return args


def selected(args, gid):
    return not args.only or gid in args.only


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
#
# Same meaning in every tester:
#   KO    the subject (or the moulinette) requires it and it is broken
#   WARN  up to the evaluator, or not written in the subject: shown, not fatal
#   SKIP  a tool is missing, or the part was skipped on purpose (--quick)


SUBJECT = None     # version of the subject the checks follow (header())


def header(title, project, note=None, verbose=False, subject=None):
    """First lines of every tester: what is tested, which subject, where.

    The subject version is always shown: the checks follow that PDF, and a
    student with another version knows where a difference may come from.
    """
    home = os.path.expanduser("~")
    where = "~" + project[len(home):] if project.startswith(home) else project
    # The menu sets it when it tests a temporary export of the last commit.
    where = os.environ.get("NEXT42_PROJECT_LABEL") or where
    global SUBJECT
    SUBJECT = subject
    version = f"subject v{subject}  " if subject else ""
    print(f"{bold(title)}  {dim(version + where)}")
    if note and verbose:
        print(dim(note))


def quick_skip(rep, what):
    """The one line every tester prints in --quick mode."""
    print(f"  {yellow('SKIP')} {dim('quick mode: ' + what)}")


class Report:
    """Collects checks per section and prints them as they come.

    A check is OK / KO / WARN / SKIP. Only KO counts as a failure. WARN is
    for things a grader may or may not flag (shown, never fatal).

    Each section maps to a group id (for NEXT42_FAILED): the explicit gid,
    else alias[name], else name when it is a known group id, else None.
    """

    def __init__(self, verbose=False, group_ids=None):
        self.verbose = verbose
        self.sections = []      # (name, ok, ko, warn)
        self.current = None
        self.failed_sections = []
        self.alias = {}         # section name -> group id, filled by testers
        self.group_ids = GROUP_IDS if group_ids is None else set(group_ids)
        self.current_gid = None
        self.failed_gids = []   # group id (or None) of each failed section
        self.old = []           # signs of an older subject (old_subject())
        self.skipped = 0        # checks not run (SKIP lines, with their count)
        self.banners = []       # big_warning(): repeated before RESULT

    def section(self, name, gid=None):
        self._close()
        if gid is None:
            gid = self.alias.get(name)
        if gid is None and name in self.group_ids:
            gid = name
        self.current_gid = gid
        self.current = [name, 0, 0, 0]
        print()
        print(accent(f"== {name} =="))

    def _close(self):
        if self.current:
            name, ok, ko, warn = self.current
            self.sections.append(tuple(self.current))
            total = ok + ko
            extra = f", {warn} warning(s)" if warn else ""
            if total == 0:
                # nothing could run here: "OK 0/0" would read as a pass
                print(f"   {yellow('--')} {name}: not tested{extra}")
            else:
                tag = green("OK") if ko == 0 else red("KO")
                print(f"   {tag} {name}: {ok}/{total}{extra}")
            if ko:
                self.failed_sections.append(name)
                self.failed_gids.append(self.current_gid)
            self.current = None

    def check(self, name, ok, expected=None, got=None, detail=None):
        if self.current is None:
            self.section("checks")
        if ok:
            self.current[1] += 1
            if self.verbose:
                print(f"  {green('OK  ')} {name}")
            return True
        self.current[2] += 1
        print(f"  {red('KO  ')} {name}")
        if expected is not None:
            print(f"       {dim('expected:')} {expected}")
        if got is not None:
            print(f"       {dim('got:     ')} {got}")
        if detail:
            for line in str(detail).splitlines():
                print(f"       {dim(line)}")
        return False

    def warn(self, name, detail=None):
        if self.current is None:
            self.section("checks")
        self.current[3] += 1
        print(f"  {yellow('WARN')} {name}")
        if detail:
            for line in str(detail).splitlines():
                print(f"       {dim(line)}")

    def skip(self, name, why, count=1):
        """A check not run. count: how many checks this line stands for."""
        self.skipped += count
        print(f"  {yellow('SKIP')} {name} {dim('(' + why + ')')}")

    def info(self, text):
        print(f"       {dim(text)}")

    def old_subject(self, what):
        """Note something written for an older version of the subject.

        The checks follow the current subject only. finish() lists these
        notes together, so the student sees at once that their project
        follows an older PDF. The check itself is reported as usual (KO when
        the current subject forbids it).
        """
        if what not in self.old:
            self.old.append(what)

    def big_warning(self, title, lines=()):
        """A warning that must not be missed: shown now, and again just
        before the RESULT line."""
        self.banners.append((title, list(lines)))
        self._banner(title, lines)

    @staticmethod
    def _banner(title, lines):
        bar = "!" * (len(title) + 8)
        print()
        print("  " + bold(red(bar)))
        print("  " + bold(red(f"!!  {title}  !!")))
        print("  " + bold(red(bar)))
        for line in lines:
            print(f"  {yellow(line)}")

    def finish(self):
        self._close()
        for title, lines in self.banners:
            self._banner(title, lines)
        ok = sum(s[1] for s in self.sections)
        ko = sum(s[2] for s in self.sections)
        if self.old:
            print()
            current = f"subject v{SUBJECT}" if SUBJECT else "the subject"
            print(f"  {red('older subject:')} this project follows an older "
                  f"version of the subject, 42next checks {current}:")
            for what in self.old:
                print(f"       - {what}")
            print(f"       {dim('get the current subject PDF on the intra')}")
        if MISSING:
            print()
            print(f"  {yellow('missing:')} {', '.join(sorted(MISSING))} "
                  f"{dim('(the checks that need them were skipped)')}")
            for line in install_hints(MISSING):
                print(f"       {dim(line)}")
        print()
        if ko == 0 and self.banners:
            # the checks passed, but a big warning says the project is not
            # gradable as it is: never a green line under it
            print(bold(yellow(f"RESULT {ok}/{ok + ko}  no failure in what "
                              "was tested, but see the warning above")))
        elif ko == 0 and self.skipped:
            # never "all checks passed" when part of the project was not run
            print(bold(yellow(f"RESULT {ok}/{ok + ko}  no failure, but "
                              f"{self.skipped} not tested (SKIP above)")))
        elif ko == 0:
            print(bold(green(f"RESULT {ok}/{ok + ko}  all checks passed")))
        else:
            print(bold(red(f"RESULT {ok}/{ok + ko}  {ko} failed in: "
                           + ", ".join(self.failed_sections))))
        self._write_failed()
        return 0 if ko == 0 else 1

    def _write_failed(self):
        """NEXT42_FAILED=path: failed group ids, one per line, for the menu.

        A single "*" line means a failed section has no group id, so the
        failures cannot be rerun alone. Empty file when nothing failed.
        """
        path = os.environ.get("NEXT42_FAILED")
        if not path:
            return
        if None in self.failed_gids:
            lines = ["*"]
        else:
            lines = list(dict.fromkeys(self.failed_gids))
        try:
            with open(path, "w") as f:
                f.write("".join(g + "\n" for g in lines))
        except OSError:
            pass


# --------------------------------------------------------------------------
# processes
# --------------------------------------------------------------------------

class Run:
    def __init__(self, rc, out, err, timed_out, elapsed):
        self.rc = rc
        self.out = out
        self.err = err
        self.timed_out = timed_out
        self.elapsed = elapsed

    @property
    def crashed(self):
        return self.rc is not None and self.rc < 0

    @property
    def signame(self):
        if not self.crashed:
            return None
        try:
            return signal.Signals(-self.rc).name
        except ValueError:
            return f"signal {-self.rc}"

    def status(self):
        if self.timed_out:
            return (f"timeout after {self.elapsed:.1f}s "
                    "(infinite loop or blocked read?)")
        if self.crashed:
            return f"crashed with {self.signame}"
        return f"exit code {self.rc}"


def _no_core_dump():
    """A crashing student program must not leave core files behind.

    Set once in the tester process: every child inherits the limit. This
    replaces a preexec_fn, which is not safe when processes are started
    from several threads (the child can deadlock before exec).
    """
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):
        pass


if os.name == "posix":
    _no_core_dump()


def run(cmd, cwd=None, stdin=b"", timeout=10, env=None):
    """Run cmd, capture bytes. Never raises for timeouts or crashes."""
    if isinstance(stdin, str):
        stdin = stdin.encode()
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True,
                              timeout=timeout, env=full_env)
        return Run(proc.returncode, proc.stdout, proc.stderr, False,
                   time.monotonic() - start)
    except subprocess.TimeoutExpired as exc:
        return Run(None, exc.stdout or b"", exc.stderr or b"", True,
                   time.monotonic() - start)
    except OSError as exc:
        return Run(127, b"", str(exc).encode(), False, 0.0)


def output(res):
    """stdout + stderr of a Run, as text."""
    return (res.out + res.err).decode("utf-8", "replace")


# Tools whose absence skips checks: Report.finish() says how to install them.
MISSING: set = set()
_TRACKED = {"valgrind", "norminette", "flake8", "mypy", "cc", "make", "nm",
            "git", "docker"}
_PY_TOOLS = ("norminette", "flake8", "mypy")


def have(tool):
    ok = shutil.which(tool) is not None
    if not ok and tool in _TRACKED:
        MISSING.add(tool)
    return ok


def note_missing(tool):
    """A tool found another way than have() is missing."""
    MISSING.add(tool)


_PY_LIBS: set = set()


def note_missing_lib(package):
    """A Python library (pip name) the interpreter running the tests cannot
    import: the end of the run says how to install it."""
    MISSING.add(package)
    _PY_LIBS.add(package)


def _externally_managed():
    """pip install --user is refused (PEP 668: Ubuntu 23.04+, Debian 12+)."""
    try:
        import sysconfig
        return os.path.isfile(os.path.join(sysconfig.get_paths()["stdlib"],
                                           "EXTERNALLY-MANAGED"))
    except (ImportError, KeyError, OSError):
        return False


def install_hints(tools):
    """The commands that install `tools` on this machine, one per line."""
    tools = sorted(tools)
    py = [t for t in tools if t in _PY_TOOLS]
    libs = [t for t in tools if t in _PY_LIBS]
    native = [t for t in tools if t not in _PY_TOOLS and t not in libs
              and t != "docker"]
    lines = []
    if sys.platform == "darwin":
        if "valgrind" in native:
            lines.append("valgrind does not run on recent macOS: memory is "
                         "checked with AddressSanitizer instead")
            native.remove("valgrind")
        if native:
            lines.append("xcode-select --install")
    elif native:
        apt = {"valgrind": "valgrind", "cc": "build-essential",
               "make": "make", "nm": "binutils", "git": "git"}
        other = {"valgrind": "valgrind", "cc": "gcc", "make": "make",
                 "nm": "binutils", "git": "git"}
        if have("apt-get"):
            pkgs = [apt[t] for t in native]
            lines.append("sudo apt install " + " ".join(dict.fromkeys(pkgs)))
        elif have("dnf"):
            lines.append("sudo dnf install " + " ".join(other[t]
                                                        for t in native))
        elif have("pacman"):
            lines.append("sudo pacman -S " + " ".join(other[t]
                                                      for t in native))
        else:
            lines.append("install with your package manager: "
                         + ", ".join(other[t] for t in native))
    if py:
        if have("pipx"):
            lines.append("pipx install " + " ".join(py))
        elif _externally_managed():
            lines.append(("sudo apt install pipx && " if have("apt-get")
                          else "install pipx, then ")
                         + "pipx install " + " ".join(py))
        else:
            lines.append("python3 -m pip install --user " + " ".join(py))
    if libs:
        if sys.prefix != sys.base_prefix:
            # the tests already run in a venv (the project's, or the
            # active one): the packages go there
            lines.append(f"{sys.executable} -m pip install "
                         + " ".join(libs))
        else:
            venv_py = (".venv\\Scripts\\python" if os.name == "nt"
                       else ".venv/bin/python")
            lines.append("in the project folder: python3 -m venv .venv && "
                         f"{venv_py} -m pip install " + " ".join(libs)
                         + "   (42next then uses .venv by itself)")
    if "docker" in tools:
        lines.append("docker: https://docs.docker.com/engine/install/")
    return lines


# --------------------------------------------------------------------------
# display
# --------------------------------------------------------------------------

def crepr(data, limit=160):
    """Show bytes / str like a C string literal: "a\\tb\\n\\x00"."""
    if data is None:
        return "NULL"
    if isinstance(data, str):
        data = data.encode("utf-8", "surrogateescape")
    out = []
    for b in data:
        ch = chr(b)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\0":
            out.append("\\0")
        elif 32 <= b < 127:
            out.append(ch)
        else:
            out.append(f"\\x{b:02x}")
    text = "".join(out)
    if len(text) > limit:
        text = text[:limit] + f"...(+{len(text) - limit} chars)"
    return f'"{text}"'


def first_diff(expected, got):
    """Index of the first differing byte, or None."""
    for i, (a, b) in enumerate(zip(expected, got)):
        if a != b:
            return i
    if len(expected) != len(got):
        return min(len(expected), len(got))
    return None


def diff_lines(expected, got, context=3):
    """Explain a multi-line difference: first different line + context."""
    if isinstance(expected, bytes):
        expected = expected.decode("utf-8", "replace")
    if isinstance(got, bytes):
        got = got.decode("utf-8", "replace")
    exp = expected.split("\n")
    act = got.split("\n")
    i = 0
    while i < len(exp) and i < len(act) and exp[i] == act[i]:
        i += 1
    want = repr(exp[i]) if i < len(exp) else "(end of output)"
    have_ = repr(act[i]) if i < len(act) else "(end of output)"
    lines = [f"first difference at line {i + 1}:",
             f"  expected {want}",
             f"  got      {have_}"]
    start = max(0, i - context)
    if start < i:
        lines.append("context before:")
        lines += [f"  {n + 1:>4}| {exp[n]}" for n in range(start, i)]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# workspace
# --------------------------------------------------------------------------

IGNORE_COPY = shutil.ignore_patterns(".git", "__pycache__", ".mypy_cache",
                                     ".pytest_cache", ".ruff_cache",
                                     "node_modules", ".venv", "venv")


def _inside(path, top):
    return path == top or path.startswith(top.rstrip(os.sep) + os.sep)


def _detach_links(project, copy):
    """No symlink of the copy may lead back into the real project.

    A link to a place inside the project becomes a relative link to the
    same place inside the copy. A link to something outside the project is
    replaced by a copy of what it points to (a dangling one is dropped):
    `make clean` in the copy must never delete files through a link.
    """
    real_project = os.path.realpath(project)
    for root, dirs, files in os.walk(copy):
        for name in dirs + files:
            link = os.path.join(root, name)
            if not os.path.islink(link):
                continue
            orig = os.path.join(project, os.path.relpath(link, copy))
            target = os.path.realpath(orig)
            os.remove(link)
            if _inside(target, real_project):
                dest = os.path.join(copy, os.path.relpath(target,
                                                          real_project))
                os.symlink(os.path.relpath(dest, root), link)
            elif os.path.isdir(target):
                shutil.copytree(target, link, ignore=IGNORE_COPY)
            elif os.path.isfile(target):
                shutil.copy2(target, link)


def _copy_error(exc):
    if isinstance(exc, shutil.Error) and exc.args \
            and isinstance(exc.args[0], list):
        whys = [str(e[-1]) for e in exc.args[0][:3]]
        more = len(exc.args[0]) - len(whys)
        return "; ".join(whys) + (f" (+{more} more)" if more > 0 else "")
    return str(exc)


class Workspace:
    """A throwaway copy of the project. Use as a context manager."""

    def __init__(self, project, keep=False, prefix="42next-"):
        self.project = project
        self.keep = keep
        self.root = tempfile.mkdtemp(prefix=prefix)
        self.dir = os.path.join(self.root, "project")
        try:
            shutil.copytree(project, self.dir, symlinks=True,
                            ignore=IGNORE_COPY)
            _detach_links(project, self.dir)
        except (OSError, shutil.Error) as exc:
            shutil.rmtree(self.root, ignore_errors=True)
            print(red(f"cannot copy the project to a temporary folder: "
                      f"{_copy_error(exc)}"))
            sys.exit(2)

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def tmp(self, name):
        return os.path.join(self.root, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.keep:
            print(dim(f"\nbuild directory kept: {self.root}"))
        else:
            shutil.rmtree(self.root, ignore_errors=True)


# --------------------------------------------------------------------------
# compiling
# --------------------------------------------------------------------------

CFLAGS = ["-Wall", "-Wextra", "-Werror"]


def compile_c(sources, output, includes=(), flags=(), libs=(), cc="cc",
              cwd=None):
    """Return (ok, compiler output)."""
    cmd = [cc, *CFLAGS, *flags]
    for inc in includes:
        cmd += ["-I", inc]
    cmd += [*sources, "-o", output, *libs]
    res = run(cmd, cwd=cwd, timeout=120)
    text = (res.out + res.err).decode("utf-8", "replace")
    return res.rc == 0, text


def short_errors(text, max_lines=12):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # "error" alone would match every compile line (-Werror)
    keep = [ln for ln in lines
            if re.search(r"\b(error|warning)\b|undefined reference", ln,
                         re.I)] or lines
    out = keep[:max_lines]
    if len(keep) > max_lines:
        out.append(f"... ({len(keep) - max_lines} more lines)")
    return "\n".join(out)


# --------------------------------------------------------------------------
# valgrind
# --------------------------------------------------------------------------

class Leaks:
    def __init__(self, definitely, indirectly, errors, open_fds, log):
        self.definitely = definitely
        self.indirectly = indirectly
        self.errors = errors
        self.open_fds = open_fds
        self.log = log

    @property
    def clean(self):
        return (self.definitely == 0 and self.indirectly == 0
                and self.errors == 0)

    def describe(self):
        parts = []
        if self.definitely:
            parts.append(f"{self.definitely} bytes definitely lost")
        if self.indirectly:
            parts.append(f"{self.indirectly} bytes indirectly lost")
        if self.errors:
            parts.append(f"{self.errors} memory error(s)")
        return ", ".join(parts) or "clean"

    def first_error(self, max_lines=8):
        """The first valgrind error block, to show where it happens."""
        out = []
        grab = False
        for line in self.log.splitlines():
            body = re.sub(r"^==\d+== ?", "", line)
            if not grab and re.match(r"(Invalid|Conditional|Use of|Mismatched|"
                                     r"Source and|Syscall|Argument|"
                                     r"\d[\d,]* bytes in)", body):
                grab = True
            if grab:
                if not body.strip():
                    break
                out.append(body)
                if len(out) >= max_lines:
                    break
        return "\n".join(out)


def _num(pattern, text):
    m = re.search(pattern, text)
    return int(m.group(1).replace(",", "")) if m else 0


def valgrind(cmd, cwd=None, stdin=b"", timeout=60, track_fds=False):
    """Run cmd under valgrind. Returns (Run, Leaks) or (None, None)."""
    if not have("valgrind"):
        return None, None
    fd, logfile = tempfile.mkstemp(prefix="42next-vg-")
    os.close(fd)
    vg = ["valgrind", "--leak-check=full", "--track-origins=yes",
          "--show-leak-kinds=definite,indirect",
          "--errors-for-leak-kinds=definite,indirect", f"--log-file={logfile}"]
    if track_fds:
        vg.append("--track-fds=yes")
    res = run(vg + list(cmd), cwd=cwd, stdin=stdin, timeout=timeout)
    try:
        with open(logfile, encoding="utf-8", errors="replace") as fh:
            log = fh.read()
    finally:
        os.remove(logfile)
    open_fds = 0
    m = re.search(r"FILE DESCRIPTORS: (\d+) open", log)
    if m:
        open_fds = max(0, int(m.group(1)) - 3)
    leaks = Leaks(_num(r"definitely lost: ([\d,]+) bytes", log),
                  _num(r"indirectly lost: ([\d,]+) bytes", log),
                  _num(r"ERROR SUMMARY: ([\d,]+) errors", log),
                  open_fds, log)
    return res, leaks


# --------------------------------------------------------------------------
# norminette
# --------------------------------------------------------------------------

def check_norm(rep, directory, files=None):
    """Run norminette on directory (or on the given files)."""
    if not have("norminette"):
        rep.skip("norminette", "norminette not installed")
        return
    target = files if files else ["."]
    res = run(["norminette", *target], cwd=directory, timeout=120)
    text = res.out.decode("utf-8", "replace")
    bad = {}
    current = None
    for line in text.splitlines():
        m = re.match(r"^(.+?): (OK|Error)!?", line)
        if m:
            current = m.group(1)
            if m.group(2) == "Error":
                bad[current] = []
            continue
        if current in bad and line.strip():
            bad[current].append(line.strip())
    checked = len(re.findall(r": (?:OK|Error)", text))
    if checked == 0:
        rep.check("norminette found files to check", False,
                  detail=text.strip()[:400] or "no .c / .h file found")
        return
    rep.check(f"norminette: {checked} file(s) checked, {len(bad)} with errors",
              not bad,
              detail="\n".join(f"{f}:\n  " + "\n  ".join(e[:6])
                               + (f"\n  ... +{len(e) - 6} more"
                                  if len(e) > 6 else "")
                               for f, e in bad.items()) or None)


# --------------------------------------------------------------------------
# Makefile
# --------------------------------------------------------------------------

def makefile_text(directory):
    for name in ("Makefile", "makefile"):
        p = os.path.join(directory, name)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as fh:
                return fh.read()
    return None


def _same_var(text):
    """${X} and $(X) are the same thing for make: use $(X) everywhere."""
    return re.sub(r"\$\{([^{}()]*)\}", r"$(\1)", text)


def makefile_targets(text):
    """Targets as written, with ${X} spelled $(X)."""
    targets = set()
    for line in text.splitlines():
        if line.startswith("\t") or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([^:=#\t][^:=#]*?)\s*::?(?!=)", line)
        if m:
            for t in m.group(1).split():
                targets.add(_same_var(t))
    return targets


def _rule_for_file(text, targets, name):
    """The target that builds `name` without NAME: '$(TARGET)' or 'libft.a'."""
    if name in targets:
        return name
    for m in re.finditer(r"^\s*(\w+)\s*(?::::=|::=|:=|\?=|=)\s*(\S+)\s*$",
                         text, re.M):
        var, value = m.group(1), m.group(2)
        if var != "NAME" and value == name and f"$({var})" in targets:
            return f"$({var})"
    return None


def _artifacts(directory):
    found = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            if f.endswith(".o") or f.endswith(".d"):
                found.append(os.path.relpath(os.path.join(root, f), directory))
    return sorted(found)


def _mtimes(directory):
    out = {}
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            p = os.path.join(root, f)
            try:
                out[os.path.relpath(p, directory)] = os.stat(p).st_mtime_ns
            except OSError:
                pass
    return out


def _backdate(directory, seconds=2):
    """Move every mtime back in time, all by the same amount.

    make only compares mtimes with each other, so it sees no change. Any file
    written by the next make then gets a newer mtime, even on a filesystem
    with 1 second precision, without having to sleep.
    """
    shift = int(seconds * 1e9)
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d != ".git"]
        for n in dirs + files:
            p = os.path.join(root, n)
            try:
                st = os.lstat(p)
                os.utime(p, ns=(st.st_atime_ns - shift,
                                st.st_mtime_ns - shift),
                         follow_symlinks=False)
            except (OSError, NotImplementedError):
                pass


def _builds_with_clang(ws_dir, name):
    """make failed: does it build with clang, when cc is gcc here?"""
    if not have("clang"):
        return False
    ver = run(["cc", "--version"], timeout=10)
    if b"gcc" not in ver.out.lower() and b"free software" not in \
            ver.out.lower():
        return False
    run(["make", "fclean"], cwd=ws_dir, timeout=120)
    res = run(["make", "CC=clang"], cwd=ws_dir, timeout=300)
    return res.rc == 0 and os.path.exists(os.path.join(ws_dir, name))


def check_makefile(rep, ws_dir, name, rules=("$(NAME)", "all", "clean",
                                             "fclean", "re"),
                   extra_rules=(), bonus=False):
    """Full Makefile check, run inside a workspace copy.

    Leaves the project built (`make` done) so tests can use it.
    Returns True if `make` produced `name`.
    """
    text = makefile_text(ws_dir)
    if text is None:
        rep.check("Makefile present", False, detail="no Makefile at the root")
        return False
    rep.check("Makefile present", True)

    targets = makefile_targets(text)
    missing = [r for r in list(rules) + list(extra_rules)
               if _same_var(r) not in targets]
    detail = f"missing: {', '.join(missing)}" if missing else None
    # The rule is there under another name ($(TARGET) = libft.a): say so.
    other = (_rule_for_file(text, targets, name)
             if "$(NAME)" in missing else None)
    if other and detail:
        detail += (f"\nthe {name} rule is {other}: the subject wants it "
                   f"named $(NAME), with NAME = {name}")
    rep.check(f"rules: {', '.join(list(rules) + list(extra_rules))}",
              not missing, detail=detail)

    m = re.search(r"^\s*CC\s*[:?]?=\s*(\S+)", text, re.M)
    cc = m.group(1) if m else None
    rep.check("compiler is cc", cc in (None, "cc"),
              expected="CC = cc", got=f"CC = {cc}")
    flags_ok = all(f in text for f in CFLAGS)
    rep.check("flags -Wall -Wextra -Werror", flags_ok,
              detail=None if flags_ok else
              "missing: " + ", ".join(f for f in CFLAGS if f not in text))

    res = run(["make"], cwd=ws_dir, timeout=300)
    built = os.path.exists(os.path.join(ws_dir, name))
    if res.rc != 0 and _builds_with_clang(ws_dir, name):
        # a warning only gcc emits (-Wtype-limits...) under -Werror: cc is
        # clang on many 42 machines, so this is not a sure failure
        rep.warn(f"make builds {name} with clang, not with gcc (cc here)",
                 short_errors(output(res)))
        rep.skip("no relink (second make does nothing)",
                 "built with CC=clang only")
        return True
    if not rep.check(f"make builds {name}", res.rc == 0 and built,
                     detail=None if res.rc == 0 and built else
                     (f"make exited 0 but {name} was not created"
                      if res.rc == 0 else short_errors(output(res)))):
        return False

    _backdate(ws_dir)
    before = _mtimes(ws_dir)
    res2 = run(["make"], cwd=ws_dir, timeout=300)
    after = _mtimes(ws_dir)
    touched = sorted(f for f in after if before.get(f) != after[f])
    out2 = output(res2).strip()
    said = "\n".join(out2.splitlines()[:6])
    rep.check("no relink (second make does nothing)", not touched,
              detail=None if not touched else
              "rebuilt on second make: " + ", ".join(touched[:8])
              + (f"\nmake said:\n{said}" if out2 else ""))

    if bonus and "bonus" in targets:
        res_b = run(["make", "bonus"], cwd=ws_dir, timeout=300)
        rep.check("make bonus", res_b.rc == 0,
                  detail=short_errors(output(res_b)) if res_b.rc else None)
        _backdate(ws_dir)
        before = _mtimes(ws_dir)
        run(["make", "bonus"], cwd=ws_dir, timeout=300)
        after = _mtimes(ws_dir)
        touched = sorted(f for f in after if before.get(f) != after[f])
        rep.check("no relink on second make bonus", not touched,
                  detail=("rebuilt: " + ", ".join(touched[:8])
                          if touched else None))

    res = run(["make", "clean"], cwd=ws_dir, timeout=120)
    left = _artifacts(ws_dir)
    rep.check("make clean removes every .o", res.rc == 0 and not left,
              detail=("still there: " + ", ".join(left[:8])) if left else None)
    rep.check(f"make clean keeps {name}",
              os.path.exists(os.path.join(ws_dir, name)))

    res = run(["make", "fclean"], cwd=ws_dir, timeout=120)
    rep.check(f"make fclean removes {name}",
              res.rc == 0 and not os.path.exists(os.path.join(ws_dir, name)))

    res = run(["make", "re"], cwd=ws_dir, timeout=300)
    ok = res.rc == 0 and os.path.exists(os.path.join(ws_dir, name))
    rep.check("make re rebuilds", ok,
              detail=None if ok else
              short_errors(output(res)))
    if bonus and "bonus" in targets:
        run(["make", "bonus"], cwd=ws_dir, timeout=300)
    return ok


# --------------------------------------------------------------------------
# external functions
# --------------------------------------------------------------------------

IGNORED_SYMBOLS = {"_GLOBAL_OFFSET_TABLE_", "__stack_chk_fail", "_DYNAMIC",
                   "__gmon_start__", "_ITM_deregisterTMCloneTable",
                   "_ITM_registerTMCloneTable", "__cxa_finalize",
                   "__libc_start_main", "__stack_chk_guard", "_init", "_fini",
                   "__dso_handle", "__TMC_END__", "__bss_start", "_edata",
                   "_end", "_start", "__data_start", "data_start",
                   "_IO_stdin_used", "__libc_csu_init", "__libc_csu_fini",
                   "dyld_stub_binder"}

# compilers may emit these for plain C code (an optimizer turns a byte loop
# into a memset call, a big struct copy into memcpy). source_calls() tells
# such a call from one written in the source.
COMPILER_ALIASES = {"memcpy", "memset", "memmove", "__memcpy_chk",
                    "__memset_chk", "__memmove_chk"}


def chk_name(sym):
    """__memcpy_chk -> memcpy (what _FORTIFY_SOURCE turns a call into)."""
    m = re.fullmatch(r"__(\w+)_chk", sym)
    return m.group(1) if m else sym


def source_calls(src, includes=(), workdir=None):
    """The functions the C file src calls in its source.

    It is compiled again at -O0 with -fno-builtin: no optimizer, no builtin
    expansion, so the only calls left are the ones written in the source
    (an optimizer may add memset, memcpy or strlen calls for plain loops).
    Returns the set of undefined symbols (C names, __x_chk read as x), or
    None when the file cannot be compiled on its own.
    """
    if not have("nm") or not have("cc"):
        return None
    fd, obj = tempfile.mkstemp(prefix="42next-src-", suffix=".o",
                               dir=workdir)
    os.close(fd)
    try:
        cmd = ["cc", "-O0", "-fno-builtin", "-w", "-c", src, "-o", obj]
        for inc in includes:
            cmd += ["-I", inc]
        if run(cmd, timeout=60).rc != 0:
            return None
        res = run(["nm", "-u", obj], timeout=30)
        return {chk_name(_c_name(ln.split()[-1]))
                for ln in res.out.decode("utf-8", "replace").splitlines()
                if ln.split()}
    finally:
        os.remove(obj)


def _c_name(sym):
    """C name of an nm symbol: no @version, no Mach-O leading underscore."""
    sym = sym.split("@")[0]
    if sys.platform == "darwin" and sym.startswith("_"):
        sym = sym[1:]   # macOS prefixes every C symbol with one _
    return sym


def external_symbols(files):
    """Undefined symbols that are not defined by any of the given files."""
    if not have("nm"):
        return None
    undefined, defined = set(), set()
    for f in files:
        res = run(["nm", "-u", f], timeout=30)
        for line in res.out.decode("utf-8", "replace").splitlines():
            parts = line.split()
            if parts and parts[-1] not in ("U", "w"):
                undefined.add(_c_name(parts[-1]))
        res = run(["nm", "--defined-only", "-g", f], timeout=30)
        for line in res.out.decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) >= 3:
                defined.add(_c_name(parts[-1]))
    return sorted(s for s in undefined
                  if s not in IGNORED_SYMBOLS and s not in defined)


def c_sources(top):
    """Every .c file under top, and every folder holding a .h file."""
    srcs, incs = [], set()
    for root, dirs, files in os.walk(top):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if f.endswith(".c"):
                srcs.append(os.path.join(root, f))
            elif f.endswith(".h"):
                incs.add(root)
    return srcs, sorted(incs)


def written_aliases(syms, allowed, top, stems=None, workdir=None):
    """The COMPILER_ALIASES among syms that the sources under top really
    call (not added by the optimizer). Those are forbidden like any other
    function. Only the .c files that were built count (`stems`, or the .o
    files found under top), so a test main.c lying around is ignored.
    Files that do not compile on their own are skipped."""
    alias = {chk_name(s) for s in syms
             if s in COMPILER_ALIASES and chk_name(s) not in allowed}
    if not alias or not top:
        return []
    srcs, incs = c_sources(top)
    if stems is None:
        stems = {f[:-2] for _, _, files in os.walk(top)
                 for f in files if f.endswith(".o")}
    built = [s for s in srcs if os.path.basename(s)[:-2] in stems]
    srcs = built or srcs
    found = set()
    for src in srcs:
        calls = source_calls(src, incs, workdir=workdir)
        if calls:
            found |= calls & alias
    return sorted(found)


def check_functions(rep, files, allowed, label="external functions",
                    sources=None):
    """KO for any external function outside `allowed` (a set of names).

    memcpy / memset / memmove may be added by the compiler: with `sources`
    (the project folder) they are KO only when the source calls them."""
    syms = external_symbols(files)
    if syms is None:
        rep.skip(label, "nm not installed")
        return
    bad = [s for s in syms if s not in allowed and s not in COMPILER_ALIASES]
    bad += written_aliases(syms, allowed, sources)
    used = [s for s in syms if s not in bad]
    rep.check(f"{label}: only {', '.join(sorted(allowed)) or 'none'}",
              not bad,
              detail=("forbidden: " + ", ".join(bad)) if bad else None)
    if used and rep.verbose:
        rep.info("used: " + ", ".join(used))


# --------------------------------------------------------------------------
# repository hygiene + README
# --------------------------------------------------------------------------

GARBAGE = re.compile(r"(\.o|\.a|\.d|\.out|\.gch|\.swp|\.dSYM|~)$|"
                     r"(^|/)(\.DS_Store|a\.out|__pycache__|\.mypy_cache|"
                     r"\.vscode|\.idea)(/|$)")


def check_repo(rep, project, binaries=()):
    """Files that should not be pushed. Uses git ls-files when possible."""
    res = run(["git", "ls-files"], cwd=project, timeout=30)
    if res.rc == 0 and res.out.strip():
        files = res.out.decode().split("\n")
        source = "tracked by git"
    else:
        files = []
        for root, dirs, names in os.walk(project):
            dirs[:] = [d for d in dirs if d != ".git"]
            for n in names:
                files.append(os.path.relpath(os.path.join(root, n), project))
        source = "in the folder"
    files = [f for f in files if f]
    bad = [f for f in files if GARBAGE.search(f)
           or os.path.basename(f) in binaries]
    rep.check(f"no build / editor garbage {source}", not bad,
              detail=("remove: " + ", ".join(bad[:12])
                      + (f" (+{len(bad) - 12})" if len(bad) > 12 else ""))
              if bad else None)
    check_git_state(rep, project)


SOURCE_EXT = (".c", ".h", ".py", ".md", ".sh", ".json", ".txt")


def _cap(names, limit=8):
    more = f" (+{len(names) - limit})" if len(names) > limit else ""
    return ", ".join(names[:limit]) + more


def _git_lines(args, project):
    res = run(["git", "-c", "core.quotepath=off"] + args, cwd=project,
              timeout=30)
    if res.rc != 0:
        return None
    return [x for x in res.out.decode("utf-8", "replace").split("\n") if x]


def check_git_state(rep, project):
    """WARN when sources are not committed: only what is pushed is graded."""
    inside = _git_lines(["rev-parse", "--is-inside-work-tree"], project)
    if inside != ["true"]:
        return
    others = _git_lines(["ls-files", "--others", "--exclude-standard"],
                        project) or []
    new = [f for f in others if f.endswith(SOURCE_EXT)
           or os.path.basename(f) == "Makefile"]
    if new:
        rep.warn(f"not in git: {_cap(new)} "
                 "(git add them: only what is pushed is graded)")
    status = _git_lines(["status", "--porcelain", "--untracked-files=no",
                         "--", "."], project) or []
    top = _git_lines(["rev-parse", "--show-toplevel"], project)
    changed = []
    for line in status:
        path = line[3:].split(" -> ")[-1].strip('"')
        if top:
            path = os.path.relpath(os.path.join(top[0], path), project)
        changed.append(path)
    if changed:
        rep.warn(f"changed since the last commit: {_cap(changed)} "
                 "(commit and push them)")


# README section names in the main campus languages (the subjects allow
# them): French, Spanish, Portuguese, Italian, German
SECTION_TRANSLATIONS = {
    "Description": ["descripci", "descriç", "descrizione", "beschreibung",
                    "présentation", "presentaci"],
    "Instructions": ["instruccion", "instruç", "istruzioni", "anleitung"],
    "Resources": ["ressource", "recurso", "risorse", "quellen"],
}


def check_readme(rep, project, extra_sections=()):
    """README.md rules shared by the current Common Core subjects.

    Returns False when README.md is missing or empty: the other README
    checks of a tester have nothing to read then.
    """
    path = os.path.join(project, "README.md")
    if not os.path.isfile(path):
        rep.check("README.md at the root", False)
        return False
    rep.check("README.md at the root", True)
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if not text.strip():
        # one KO, not one per missing section
        rep.check("README.md has content", False, detail=(
            "the file is empty: the subject asks for an italic first line "
            "with your login, then Description, Instructions and "
            "Resources sections"))
        return False
    lines = [ln for ln in text.splitlines()]
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    italic = re.fullmatch(r"(\*|_)(?!\1)(.+)\1", first)
    inner = italic.group(2) if italic else first
    rep.check("first line is in italics", bool(italic),
              expected="*This activity has been created as part of the 42 "
                       "curriculum by <login>.*",
              got=first[:120] if not italic else None)
    wording = re.search(r"(project|activity) has been created as part of the "
                        r"42 curriculum by \S+", inner, re.I)
    # "you may use the main language of your campus": a translated first
    # line is shown as a warning, not a failure
    translated = not wording and "42" in inner and re.search(
        r"curr[ií]cul|cursus|curricol|lehrplan", inner, re.I)
    if translated:
        rep.warn("first line translated",
                 "the subject gives the English sentence: " + inner[:120])
    else:
        rep.check("first line wording + login(s)", bool(wording),
                  got=None if wording else inner[:120])
    headings = [re.sub(r"^#+\s*", "", ln).strip().lower()
                for ln in lines if ln.startswith("#")]
    for sec in ("Description", "Instructions", "Resources", *extra_sections):
        names = [sec.lower()] + SECTION_TRANSLATIONS.get(sec, [])
        ok = any(n in h for h in headings for n in names)
        rep.check(f'"{sec}" section', ok)
    ai = re.search(r"\bAI\b|artificial intelligence|intelligence "
                   r"artificielle|inteligencia artificial|intelig[eê]ncia "
                   r"artificial|ChatGPT|Claude|Copilot|Gemini|LLM|\bIA\b",
                   text, re.I)
    rep.check("Resources explain how AI was used", bool(ai),
              detail=None if ai else
              "the subject asks which tasks / parts used AI")
    return True
