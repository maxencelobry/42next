#!/usr/bin/env python3
"""Behavioural tester for the 42 Python modules (piscine Python / "oop").

C testers compile the student's code and run a bundled binary. Python
projects can't be compiled, so this harness runs each exercise the way the
subject says to run it, captures what it does and checks it against the
behaviour the subject pins down.

    python3 -B run_tests.py [--spec SPEC] [--project DIR] [--only ex0,ex2]
                            [--file PATH] [--list] [--no-color] [--verbose]
                            [--quick | --lint-only | --no-lint]
    python3 -B run_tests.py --detect PATH        (JSON, used by the menu)

--spec is a spec id (specs/<id>.json) or a path; without it the project is
recognised from its file names. --project may be the project root, one of
its exN/ folders or a folder holding it (e.g. dataquest/ with 01/ inside).
--dir and --exercise are kept as aliases of --project and --only.
--quick skips mypy (slow), --lint-only runs only flake8 + mypy, --no-lint
skips both.

A run is driven by a JSON spec. Each exercise has tests, each test runs in
one of these modes:

  * script      - `python3 -B <file> argv...` with stdin / fixture files,
                  stdout + stderr + exit code + files written are checked.
  * func_stdio  - import the file, call one function with stdin fed in,
                  check what it prints (the "only a function" modules).
  * module      - import the file and run a Python snippet against it
                  (`m` is the module, `capture(fn, *args, stdin="")` returns
                  (stdout, exception)): return values, raised exceptions,
                  class structure...

  * files       - no run of its own: a Python snippet (`code`) checks
                  project files (`file`: its content is `text`, None when
                  missing; `source` is the exercise file) and may run the
                  exercise, several times, with `run(**test keys)` ->
                  {"out", "err", "code", "timeout", "new_files", "ctx"}
                  (one run per set of keys, shared by the tests). Helpers:
                  toml(text), requirements(text), requirement_name(text),
                  norm_dist(name), env_keys(text), ignored(gitignore text,
                  name), tracked(rel) (True / False, None outside git),
                  venv_dirs(), expect_lines(out, items), root.

Everything runs in a throwaway copy of the project (or an empty temporary
folder for cwd "temp"), func_stdio / module tests run in a child process
with a timeout, every interpreter gets -B: nothing is ever written into the
tested project (no fixture, no __pycache__, no .mypy_cache). Folders that
are virtual environments (a pyvenv.cfg inside) are never copied.

Interpreter and environment of a test (script / module / func_stdio):
  * "env": {"NAME": "value" | null}: set / remove environment variables.
  * "interpreter": "global" - the Python outside any virtual environment
    (VIRTUAL_ENV & co removed), "venv" - a fresh `python -m venv
    --without-pip` made in the temp dir (named "venv_name", default venv)
    and activated the way `source bin/activate` does. Python rules then get
    `ctx`: the interpreter's executable, prefix, base_prefix, in_venv,
    site_packages, venv, venv_name, versions (of the required packages).
  * "hide_modules": ["pkg" | {"module", "pip"}]: these packages look not
    installed (import fails, importlib.util.find_spec gives None,
    importlib.metadata does not see them), installed or not.

Third-party packages (pydantic, pandas...): "requires": ["mod" | {"module",
"pip", "version": major}] on an exercise (every test) or a test (overrides
it). The tester never installs anything: when a package is missing from the
interpreter running the tests, those tests are SKIPs and the end of the run
says how to install it (core.note_missing_lib). Static checks, flake8 and
mypy still run; mypy then gets --python-executable (it sees the packages
the tests see) and its "module not found" errors on a missing required
package make it a SKIP, other errors still count.

Static checks on the source (never run): authorized builtins, imports
("imports": a list, "stdlib" in it allows the standard library;
"imports_warn": modules only shown as WARN, e.g. typing when the subject
lists no module for type hints), type hints, naming, forbidden / required
constructs (require_imports kind "any": import, from ... import or
importlib.import_module / __import__), plus flake8 and mypy (when
installed), which every subject of the module requires. "mypy_expect" /
"flake8_expect": the error codes the subject allows (WARN, not KO).

Exit code: 0 all passed, 1 something failed, 2 setup problem.
Stdlib only, Python 3.9+.
"""

import argparse
import ast
import glob
import builtins
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SPECS_DIR = os.path.join(HERE, "specs")
COMMON_DIR = os.path.normpath(os.path.join(HERE, "..", "common"))

_IGNORE_NAMES = shutil.ignore_patterns(".git", "__pycache__", ".mypy_cache",
                                       ".pytest_cache", ".ruff_cache",
                                       "node_modules", ".venv", "venv")


def IGNORE_COPY(directory, names):
    """copytree filter: caches, .git, and every virtual environment."""
    ignored = set(_IGNORE_NAMES(directory, names))
    for name in names:
        if name not in ignored and os.path.isfile(
                os.path.join(directory, name, "pyvenv.cfg")):
            ignored.add(name)
    return ignored


CHILD_ENV = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
             "PYTHONHASHSEED": "0"}
NOTES: list = []    # remarks gathered while matching (shown as WARN)


def child_env():
    env = dict(os.environ)
    env.update(CHILD_ENV)
    env.pop("PYTHONSTARTUP", None)
    return env


# --------------------------------------------------------------------------
# normalisation + matching
# --------------------------------------------------------------------------

def normalize(text):
    """Unify the typographic quotes of the PDF examples with the ASCII ones
    a program prints, ignore trailing whitespace and trailing blank lines."""
    if text is None:
        text = ""
    # colours are decoration: "\x1b[1m=== Archive ===\x1b[0m" is the line
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    text = (text
            .replace("’", "'").replace("‘", "'")
            .replace("“", '"').replace("”", '"')
            .replace("–", "-").replace("—", "-"))
    lines = [ln.rstrip() for ln in text.splitlines()]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


# Values in a line of output, for subjects whose output is only an example:
# numbers, quoted strings, CamelCase names (exception classes), True / False /
# None, and the test's own inputs (argv / stdin / args) echoed back.
_QUOTED = r"""(?<!\w)'[^'\n]*'(?!\w)|(?<!\w)"[^"\n]*"(?!\w)"""
_NUMBER = r"(?<![\w.])-?\d+(?:\.\d+)?(?!\w)"
_CAMEL = r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+\b"
_CONST = r"\b(?:True|False|None)\b"


def value_tokens(text, inputs=()):
    phrases = sorted({normalize(p).strip() for p in inputs if str(p).strip()},
                     key=len, reverse=True)
    parts = [r"(?<!\w)" + re.escape(p) + r"(?!\w)" for p in phrases]
    pattern = "|".join(parts + [_QUOTED, _NUMBER, _CAMEL, _CONST])
    out = []
    for m in re.finditer(pattern, text):
        tok = m.group(0)
        if len(tok) >= 2 and tok[0] in "'\"" and tok[-1] == tok[0]:
            tok = tok[1:-1]
        out.append(tok)
    return out


def _same_value(a, b):
    try:
        return float(a) == float(b)
    except ValueError:
        return a == b


def loose_line(expected, got, rule):
    """(ok, why): every value of the expected line is found, in order, in
    the line that was printed (the words around them may differ)."""
    if (expected.strip() == "") != (got.strip() == ""):
        return False, "blank line expected" if not expected.strip() else \
            "non-blank line expected"
    for pat in rule.get("ignore", []):
        expected = re.sub(pat, " ", expected)
    inputs = rule.get("inputs", [])
    want, have = value_tokens(expected, inputs), value_tokens(got, inputs)
    if not want:
        # no value to compare: the line must still be about the same thing
        # (one word of 4+ letters in common), or any line would match
        words = set(re.findall(r"[a-z]{4,}", expected.lower()))
        if words and not words & set(re.findall(r"[a-z]{4,}", got.lower())):
            return False, "no word in common with the subject's line"
    pos = 0
    for tok in want:
        while pos < len(have) and not _same_value(have[pos], tok):
            pos += 1
        if pos == len(have):
            return False, (f"value {tok!r} not found (values expected: "
                           f"{want}, printed: {have})")
        pos += 1
    return True, ""


def loose_equals(expected, norm, rule):
    """For `"wording": "example"`: the subject shows the output as an
    example, so another wording is accepted (a note, shown as WARN) as long
    as the same non-blank lines carry the same values, and the phrases the
    subject does require word for word (`require`) are there."""
    exp = [ln for ln in expected.split("\n") if ln.strip()]
    got = [ln for ln in norm.split("\n") if ln.strip()]
    if len(exp) != len(got):
        return False, (f"{len(got)} non-blank line(s) printed, the subject "
                       f"shows {len(exp)}")
    for e, g in zip(exp, got):
        ok, why = loose_line(e, g, rule)
        if not ok:
            return False, f"{e!r} vs {g!r}: {why}"
    pos = 0
    for phrase in rule.get("require", []):
        idx = norm.find(normalize(phrase), pos)
        if idx < 0:
            return False, (f"missing {normalize(phrase)!r} (required by the "
                           "subject, word for word)")
        pos = idx + len(normalize(phrase))
    for phrase in rule.get("forbid", []):
        if normalize(phrase) in norm:
            return False, (f"{normalize(phrase)!r} is the subject's message "
                           "for the other case")
    return True, ""


def _first_diff_line(expected, actual):
    exp, act = expected.split("\n"), actual.split("\n")
    for i in range(max(len(exp), len(act))):
        e = exp[i] if i < len(exp) else None
        a = act[i] if i < len(act) else None
        if e != a:
            return f"line {i + 1}: {a!r} (subject: {e!r})"
    return ""


def check_rule(rule, actual, ctx=None):
    """Return (ok, detail). `rule` is a match descriptor dict, `ctx` the
    facts about the interpreter of the run (python rules see it)."""
    if rule is None:
        return True, ""
    norm = normalize(actual)
    rtype = rule.get("type", "contains_all")

    if rtype == "equals":
        expected = normalize(rule["expected"])
        if norm == expected:
            return True, ""
        if rule.get("wording") == "example":
            ok, why = loose_equals(expected, norm, rule)
            if ok:
                NOTES.append("output worded differently from the subject "
                             "example (accepted, same values): "
                             + _first_diff_line(expected, norm))
                return True, ""
            return False, _diff(expected, norm) + "\n" + why
        return False, _diff(expected, norm)

    if rtype == "contains_all":
        missing = [item for item in rule["items"]
                   if normalize(item) not in norm]
        if missing:
            return False, "missing: " + ", ".join(repr(m) for m in missing)
        return True, ""

    if rtype == "not_contains":
        found = [item for item in rule["items"] if normalize(item) in norm]
        if found:
            return False, "should not contain: " + ", ".join(
                repr(f) for f in found)
        return True, ""

    if rtype == "in_order":
        pos = 0
        for item in rule["items"]:
            needle = normalize(item)
            idx = norm.find(needle, pos)
            if idx < 0:
                return False, f"not found (in order): {item!r}"
            pos = idx + len(needle)
        return True, ""

    if rtype == "regex":
        if re.search(rule["pattern"], norm, re.MULTILINE | re.DOTALL):
            return True, ""
        return False, f"no match for /{rule['pattern']}/"

    if rtype == "empty":
        if not norm.strip():
            return True, ""
        return False, "should be empty, got: " + repr(norm[:300])

    if rtype == "not_empty":
        if norm.strip():
            return True, ""
        return False, "output was empty"

    if rtype == "lines":
        return match_lines(rule, norm)

    if rtype == "python":
        env = {"out": norm, "lines": norm.split("\n"), "parse": parse_literal,
               "raw": actual, "ctx": ctx or {}}
        return run_snippet(rule["code"], env)

    return False, f"unknown match type {rtype!r}"


def match_lines(rule, norm):
    """Line by line, blank lines included. An item is a plain string (exact,
    or same values when the rule has "wording": "example") or {"re": regex,
    "subject": text}: the regex must match the whole line; if it does but
    the line differs from the subject's text, a note is left (the subject
    lets some messages be customised)."""
    got = norm.split("\n") if norm else []
    want = rule["items"]
    start = 0
    if rule.get("tail"):
        start = max(0, len(got) - len(want))
    for i, item in enumerate(want):
        n = start + i
        line = got[n] if n < len(got) else None
        extra = ""
        if isinstance(item, dict):
            ok = line is not None and re.fullmatch(item["re"], line)
            shown = item.get("subject", "/" + item["re"] + "/")
        else:
            ok = line == normalize(item)
            shown = item
            if not ok and line is not None \
                    and rule.get("wording") == "example":
                ok, why = loose_line(normalize(item), line, rule)
                if ok:
                    NOTES.append(f"line {n + 1} worded differently from the "
                                 f"subject example (accepted, same values): "
                                 f"{line!r} vs {normalize(item)!r}")
                else:
                    extra = why
        if not ok:
            context = got[max(0, n - 2):n]
            msg = (f"line {n + 1}: expected {shown!r}\n"
                   f"            got {line if line is None else repr(line)}")
            if line is None:
                msg = (f"line {n + 1}: expected {shown!r}\n"
                       f"            got (end of output)")
            if context:
                msg += "\n  after: " + "\n         ".join(
                    repr(c) for c in context)
            if extra:
                msg += "\n  " + extra
            return False, msg
        if isinstance(item, dict) and "subject" in item:
            if line != normalize(item["subject"]):
                NOTES.append(f"line {n + 1} differs from the subject example"
                             f" (accepted): {line!r} vs "
                             f"{normalize(item['subject'])!r}")
    if not rule.get("tail") and len(got) > len(want):
        return False, (f"extra output from line {len(want) + 1}: "
                       f"{got[len(want)]!r}")
    return True, ""


def parse_literal(text):
    """ast.literal_eval that also understands `set()` (empty set)."""
    text = text.strip()
    if text == "set()":
        return set()
    return ast.literal_eval(text)


def expect_lines(out, items):
    """(ok, why): each item is found as a whole line, in this order (other
    lines may come in between). An item is a line, or {"re": regex,
    "subject": the subject's text} for the lines that hold values."""
    lines = [ln.strip() for ln in normalize(out).split("\n")]
    pos = 0
    for item in items:
        if isinstance(item, dict):
            shown = item.get("subject", "/" + item["re"] + "/")
            pattern = re.compile(item["re"])
            idx = next((i for i in range(pos, len(lines))
                        if pattern.fullmatch(lines[i])), None)
        else:
            shown = normalize(item).strip()
            idx = next((i for i in range(pos, len(lines))
                        if lines[i] == shown), None)
        if idx is None:
            after = repr(lines[pos - 1]) if pos else "(start of output)"
            near = [repr(ln) for ln in lines[pos:pos + 3] if ln]
            return False, (f"subject line {shown!r} not found\n"
                           f"  after {after}, got: "
                           + (", ".join(near) or "(end of output)"))
        pos = idx + 1
    return True, ""


def run_snippet(code, env):
    """Run assert-based check code. A list of lines is joined first."""
    if isinstance(code, list):
        code = "\n".join(code)
    env.setdefault("note", NOTES.append)
    env.setdefault("expect_lines", expect_lines)
    try:
        exec(code, env)
    except AssertionError as exc:
        return False, str(exc) or "check failed"
    except SkipTest:
        raise
    except Exception as exc:  # noqa: BLE001
        tb = traceback.extract_tb(exc.__traceback__)
        where = ""
        if tb:
            last = tb[-1]
            if last.filename != "<string>":
                where = f" (in {os.path.basename(last.filename)} " \
                        f"line {last.lineno})"
        return False, f"{exc.__class__.__name__}: {exc}{where}"
    return True, ""


def _diff(expected, actual):
    exp = expected.split("\n")
    act = actual.split("\n") if actual else []
    i = 0
    while i < len(exp) and i < len(act) and exp[i] == act[i]:
        i += 1
    want = repr(exp[i]) if i < len(exp) else "(end of output)"
    got = repr(act[i]) if i < len(act) else "(end of output)"
    out = [f"first difference at line {i + 1}:",
           f"  expected {want}",
           f"  got      {got}"]
    if i > 0:
        out.append("after:")
        out += ["  " + repr(ln) for ln in exp[max(0, i - 2):i]]
    return "\n".join(out)


# --------------------------------------------------------------------------
# child side: func_stdio / module tests (run in a subprocess)
# --------------------------------------------------------------------------

class OpenTracker:
    """Wrap builtins.open to find files that are opened and never closed."""

    def __init__(self):
        self.files = []
        self.real = builtins.open

    def install(self):
        def tracked(file, *args, **kwargs):
            fh = self.real(file, *args, **kwargs)
            self.files.append((str(file), fh))
            return fh
        builtins.open = tracked

    def unclosed(self):
        return sorted({name for name, fh in self.files if not fh.closed})


def load_module(file_path):
    mod_dir = os.path.dirname(os.path.abspath(file_path))
    if mod_dir not in sys.path:
        sys.path.insert(0, mod_dir)
    name = os.path.splitext(os.path.basename(file_path))[0]
    spec = importlib.util.spec_from_file_location(name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    old_stdin = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        spec.loader.exec_module(module)
    finally:
        sys.stdin = old_stdin
    return module


def capture(func, *args, stdin="", **kwargs):
    """Call func(*args) with stdin fed in. Returns (stdout, exception)."""
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin)
    buf = io.StringIO()
    error = None
    try:
        with redirect_stdout(buf):
            func(*args, **kwargs)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001 - SystemExit too
        error = exc
    finally:
        sys.stdin = old_stdin
    return buf.getvalue(), error


def call(func, *args, stdin="", **kwargs):
    """Like capture but returns (return value, stdout, exception)."""
    box = {}

    def inner():
        box["v"] = func(*args, **kwargs)
    out, err = capture(inner, stdin=stdin)
    return box.get("v"), out, err


def _import(file_path):
    try:
        with redirect_stdout(io.StringIO()):
            return load_module(file_path), None
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001
        tb = traceback.format_exc().strip().splitlines()
        return None, (f"import failed: {exc.__class__.__name__}: {exc}"
                      + ("\n" + tb[-3].strip() if len(tb) >= 3 else ""))


def run_func_stdio(test, file_path):
    module, err = _import(file_path)
    if err:
        return False, err
    func_name = test["func"]
    if not hasattr(module, func_name):
        return False, f"function {func_name}() is not defined in the file"
    out, exc = capture(getattr(module, func_name), *test.get("args", []),
                       stdin=test.get("stdin", ""))
    if exc is not None and not test.get("expect_raise"):
        return False, f"raised {exc.__class__.__name__}: {exc}"
    if exc is None and test.get("expect_raise"):
        return False, "expected the call to raise, but it returned normally"
    return check_rule(test.get("match"), out)


def run_module(test, file_path, tracker):
    real_open = tracker.real if tracker else open

    def read_text(path):
        with real_open(path, encoding="utf-8") as fh:
            return fh.read()
    env = {"capture": capture, "call": call, "parse": parse_literal,
           "read_text": read_text,
           "unclosed": tracker.unclosed if tracker else (lambda: []),
           "is_root": hasattr(os, "geteuid") and os.geteuid() == 0}
    if test.get("load", True):
        module, err = _import(file_path)
        if err:
            return False, err
        env["m"] = module
    elif os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    return run_snippet(test["code"], env)


def child_main(req_path, res_path):
    sys.dont_write_bytecode = True
    sys.path[:] = [p for p in sys.path
                   if os.path.abspath(p or ".") not in (HERE, COMMON_DIR)]
    with open(req_path, encoding="utf-8") as fh:
        req = json.load(fh)
    test, file_path = req["test"], req["file"]
    tracker = None
    if req.get("track_open"):
        tracker = OpenTracker()
        tracker.install()
    try:
        if test.get("mode") == "func_stdio":
            ok, detail = run_func_stdio(test, file_path)
        else:
            ok, detail = run_module(test, file_path, tracker)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001
        ok, detail = False, f"tester error: {exc.__class__.__name__}: {exc}"
    with (tracker.real if tracker else open)(res_path, "w",
                                             encoding="utf-8") as fh:
        json.dump({"ok": ok, "detail": detail, "notes": NOTES}, fh)
    sys.stdout.flush()
    os._exit(0)


# --------------------------------------------------------------------------
# parent side: workspaces + runners
# --------------------------------------------------------------------------

def _make_writable(root):
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            try:
                os.chmod(os.path.join(base, name), 0o700)
            except OSError:
                pass


def remove_tree(root):
    _make_writable(root)
    shutil.rmtree(root, ignore_errors=True)


class Sandbox:
    """A throwaway place to run one test. cwd modes:
      scriptdir - copy of the whole project, cwd = folder of the file
      root      - copy of the whole project, cwd = project root
      temp      - empty folder holding only the file (+ fixtures)"""

    def __init__(self, project, rel_file, cwd_mode, fixtures):
        self.root = tempfile.mkdtemp(prefix="42next-py-")
        if cwd_mode == "temp":
            self.cwd = os.path.join(self.root, "run")
            os.mkdir(self.cwd)
            self.file = os.path.join(self.cwd, os.path.basename(rel_file))
            shutil.copy(os.path.join(project, rel_file), self.file)
        else:
            work = os.path.join(self.root, "project")
            shutil.copytree(project, work, ignore=IGNORE_COPY)
            self.file = os.path.join(work, rel_file)
            self.cwd = work if cwd_mode == "root" else \
                os.path.dirname(self.file)
        chmods = []
        for name, value in (fixtures or {}).items():
            path = os.path.join(self.cwd, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if isinstance(value, dict):
                if value.get("dir"):
                    os.makedirs(path, exist_ok=True)
                else:
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(value.get("content", ""))
                if "mode" in value:
                    chmods.append((path, int(str(value["mode"]), 8)))
            else:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(value)
        for path, mode in chmods:
            os.chmod(path, mode)
        self.before = set(self.listing())

    def listing(self):
        out = []
        for base, dirs, files in os.walk(self.cwd):
            for name in files:
                out.append(os.path.relpath(os.path.join(base, name),
                                           self.cwd))
        return out

    def new_files(self):
        return sorted(set(self.listing()) - self.before)

    def tmp(self, name):
        return os.path.join(self.root, name)

    def close(self):
        remove_tree(self.root)


TRACK_RUNNER = r"""
import atexit, builtins, json, os, runpy, sys
_files = []
_real = builtins.open
_log = sys.argv[1]
def _open(file, *args, **kwargs):
    fh = _real(file, *args, **kwargs)
    _files.append((str(file), fh))
    return fh
def _report():
    left = sorted({n for n, f in _files if not f.closed})
    with _real(_log, "w") as fh:
        json.dump(left, fh)
builtins.open = _open
atexit.register(_report)
script = sys.argv[2]
sys.argv = sys.argv[2:]
sys.path[0] = os.path.dirname(os.path.abspath(script))
runpy.run_path(script, run_name="__main__")
"""


def is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


class SkipTest(Exception):
    """The test cannot run here (a package or a tool is missing)."""


# --------------------------------------------------------------------------
# interpreters: the current one, the global one, a fresh venv
# --------------------------------------------------------------------------

PROBE = r"""
import json, os, site, sys
from importlib import metadata
names = json.loads(sys.argv[1])
try:
    sites = site.getsitepackages()
except AttributeError:
    sites = []
info = {"executable": sys.executable, "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "in_venv": sys.prefix != sys.base_prefix,
        "site_packages": sites, "user_site": site.getusersitepackages(),
        "version": "%d.%d" % sys.version_info[:2], "versions": {}}
for name in names:
    try:
        info["versions"][name] = metadata.version(name)
    except Exception:
        info["versions"][name] = None
print(json.dumps(info))
"""

HIDE_SITE = r'''"""42next: some packages look not installed (hide_modules)."""
import re as _re
import sys as _sys
from importlib.machinery import PathFinder as _PathFinder

_MODULES = set(%(modules)r)
_DISTS = set(%(dists)r)


def _norm(name):
    return _re.sub(r"[-_.]+", "-", str(name)).lower()


class _HideFinder:
    @classmethod
    def find_spec(cls, name, path=None, target=None):
        if name.partition(".")[0] in _MODULES:
            return None
        return _PathFinder.find_spec(name, path, target)

    @classmethod
    def invalidate_caches(cls):
        _PathFinder.invalidate_caches()

    @classmethod
    def find_distributions(cls, *args, **kwargs):
        for dist in _PathFinder.find_distributions(*args, **kwargs):
            try:
                name = dist.metadata["Name"]
            except Exception:
                name = None
            if name is None or _norm(name) not in _DISTS:
                yield dist


_sys.meta_path[:] = [_HideFinder if f is _PathFinder else f
                     for f in _sys.meta_path]
'''

VENV_VARS = ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "CONDA_PREFIX",
             "CONDA_DEFAULT_ENV", "CONDA_PROMPT_MODIFIER", "PYTHONHOME")


def _bin_dir(prefix):
    return os.path.join(prefix, "Scripts" if os.name == "nt" else "bin")


def _is_venv_python(path):
    here = os.path.dirname(os.path.abspath(path))
    return any(os.path.isfile(os.path.join(d, "pyvenv.cfg"))
               for d in (here, os.path.dirname(here)))


def global_python():
    """The interpreter outside the active virtual environment, or None."""
    if sys.prefix == sys.base_prefix:
        return sys.executable
    for cand in (getattr(sys, "_base_executable", ""),
                 os.path.realpath(sys.executable)):
        if cand and os.path.isfile(cand) and not _is_venv_python(cand):
            return cand
    return None


def _leave_venvs(env):
    """The environment of a shell where no venv is activated."""
    drop = set()
    active = env.get("VIRTUAL_ENV")
    if active:
        drop.add(os.path.normcase(os.path.normpath(_bin_dir(active))))
    if sys.prefix != sys.base_prefix:
        drop.add(os.path.normcase(os.path.dirname(
            os.path.abspath(sys.executable))))
    for name in VENV_VARS:
        env.pop(name, None)
    env["PATH"] = os.pathsep.join(
        p for p in env.get("PATH", "").split(os.pathsep)
        if p and os.path.normcase(os.path.normpath(p)) not in drop)
    return env


def _pkg(item):
    """{"module", "pip", "version"} from "name" or a dict."""
    if isinstance(item, str):
        return {"module": item, "pip": item}
    return {"pip": item["module"], **item}


def _probe(python, env, names=()):
    proc = subprocess.run([python, "-B", "-c", PROBE,
                           json.dumps(sorted(set(names)))],
                          capture_output=True, text=True, env=env,
                          timeout=60)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"error": (proc.stderr or proc.stdout).strip()[-300:]}


_REQUIRES: dict = {}


def missing_packages(items):
    """[(package, why)] for the required packages the interpreter running
    the tests cannot import. Nothing is ever installed."""
    out = []
    for item in items or []:
        pkg = _pkg(item)
        key = json.dumps(pkg, sort_keys=True)
        if key not in _REQUIRES:
            code = ("import importlib.util, sys\n"
                    "from importlib import metadata\n"
                    f"if importlib.util.find_spec({pkg['module']!r}) is None:"
                    "\n    sys.exit(3)\n"
                    "try:\n"
                    f"    print(metadata.version({pkg['pip']!r}))\n"
                    "except Exception:\n"
                    "    print('?')\n")
            proc = subprocess.run([sys.executable, "-B", "-c", code],
                                  capture_output=True, text=True,
                                  env=child_env(), timeout=60)
            why = None
            if proc.returncode != 0:
                why = "not installed"
            elif pkg.get("version"):
                have = proc.stdout.strip()
                major = str(pkg["version"])
                if have != "?" and have.split(".")[0] != major:
                    why = (f"{pkg['pip']} {have} is installed, the subject "
                           f"needs {major}.x")
            _REQUIRES[key] = why
        if _REQUIRES[key]:
            out.append((pkg, _REQUIRES[key]))
    return out


def _note_missing_lib(name):
    mod = sys.modules.get("core")
    if mod is not None and hasattr(mod, "note_missing_lib"):
        mod.note_missing_lib(name)


def missing_reason(missing):
    """One sentence for the output of missing_packages()."""
    names = [p["pip"] for p, why in missing if why == "not installed"]
    parts = [why for _, why in missing if why != "not installed"]
    if names:
        parts.insert(0, ", ".join(names) + " not installed for "
                     + sys.executable)
    return "; ".join(parts)


def check_requires(items):
    """Raise SkipTest when a required package is missing."""
    missing = missing_packages(items)
    if missing:
        for pkg, _ in missing:
            _note_missing_lib(pkg["pip"])
        raise SkipTest(missing_reason(missing))


def interpreter(test, box):
    """(python, env, ctx) of a test: the interpreter running the tests, the
    global one or a fresh venv, the test's env vars, hidden packages. ctx
    (facts read from that interpreter) is None for the plain case."""
    env = child_env()
    kind = test.get("interpreter")
    python = sys.executable
    venv = None
    if kind in ("global", "venv"):
        env = _leave_venvs(env)
    if kind == "global":
        python = global_python()
        if python is None:
            raise SkipTest("cannot find the Python outside the active "
                           "virtual environment")
    elif kind == "venv":
        venv = os.path.join(box.root, test.get("venv_name", "venv"))
        try:
            made = subprocess.run([sys.executable, "-m", "venv",
                                   "--without-pip", venv],
                                  capture_output=True, text=True,
                                  timeout=120, env=env)
        except subprocess.TimeoutExpired:
            raise SkipTest("python3 -m venv timed out")
        if made.returncode != 0:
            raise SkipTest("python3 -m venv failed: " + (
                (made.stderr or made.stdout).strip().splitlines() or
                ["?"])[-1])
        python = os.path.join(_bin_dir(venv), "python.exe" if os.name == "nt"
                              else "python3")
        env["VIRTUAL_ENV"] = venv
        env["VIRTUAL_ENV_PROMPT"] = os.path.basename(venv)
        env["PATH"] = _bin_dir(venv) + os.pathsep + env.get("PATH", "")
    elif kind is not None:
        raise SkipTest(f"unknown interpreter {kind!r}")
    for name, value in (test.get("env") or {}).items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = str(value)
    hide = [_pkg(h) for h in test.get("hide_modules", [])]
    if hide:
        site_dir = box.tmp("hide-site")
        os.makedirs(site_dir, exist_ok=True)
        with open(os.path.join(site_dir, "sitecustomize.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(HIDE_SITE % {
                "modules": sorted({h["module"] for h in hide}),
                "dists": sorted({re.sub(r"[-_.]+", "-", h["pip"]).lower()
                                 for h in hide})})
        env["PYTHONPATH"] = os.pathsep.join(
            [site_dir] + [p for p in [env.get("PYTHONPATH")] if p])
    ctx = None
    if kind or hide or test.get("requires"):
        names = [_pkg(r)["pip"] for r in test.get("requires") or []]
        ctx = _probe(python, env, names)
        ctx["venv"] = venv
        ctx["venv_name"] = os.path.basename(venv) if venv else None
    return python, env, ctx


def exec_script(test, box, track_open=False):
    """Run the file as a script. Returns (proc or None if timed out, ctx)."""
    argv = [str(a) for a in test.get("argv", [])]
    python, env, ctx = interpreter(test, box)
    invoke = os.path.relpath(box.file, box.cwd)
    if track_open:
        cmd = [python, "-B", "-c", TRACK_RUNNER, box.tmp("open.json"),
               invoke, *argv]
    else:
        cmd = [python, "-B", invoke, *argv]
    try:
        proc = subprocess.run(cmd, cwd=box.cwd, input=test.get("stdin", ""),
                              capture_output=True, text=True,
                              timeout=test.get("timeout", 15), env=env,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return None, ctx
    return proc, ctx


def run_script(test, box, track_open):
    timeout = test.get("timeout", 15)
    log = box.tmp("open.json")
    proc, ctx = exec_script(test, box, track_open)
    if proc is None:
        return False, (f"timed out after {timeout}s: the script probably "
                       "waits for input it never gets, or loops forever")

    match = test.get("match", {})
    details = []
    if "exit_code" in match:
        want = match["exit_code"]
        bad = proc.returncode == 0 if want == "nonzero" else \
            proc.returncode != want
        if bad:
            details.append(f"exit code: expected {want}, got "
                           f"{proc.returncode}")
    if not match.get("allow_traceback") and "Traceback" in proc.stderr:
        details.append("crashed:\n  " + "\n  ".join(
            proc.stderr.strip().splitlines()[-3:]))
    for stream, text in (("stdout", proc.stdout), ("stderr", proc.stderr),
                         ("output", proc.stdout + "\n" + proc.stderr)):
        rules = match.get(stream)
        if rules is None:
            continue
        for rule in rules if isinstance(rules, list) else [rules]:
            ok, detail = check_rule(rule, text, ctx)
            if not ok:
                details.append(f"{stream}: " + detail.replace(
                    "\n", "\n  "))
    for out_check in test.get("outputs", []):
        path = os.path.join(box.cwd, out_check["file"])
        if not os.path.isfile(path):
            details.append(f"file {out_check['file']!r} was not created")
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError as exc:
            details.append(f"cannot read {out_check['file']!r}: {exc}")
            continue
        ok, detail = check_rule(out_check.get("match"), content, ctx)
        if not ok:
            details.append(f"file {out_check['file']!r}: "
                           + detail.replace("\n", "\n  "))
    if test.get("no_new_files"):
        created = box.new_files()
        if created:
            details.append("no file should be written, found: "
                           + ", ".join(created))
    if track_open and not details:
        try:
            with open(log, encoding="utf-8") as fh:
                left = json.load(fh)
        except (OSError, ValueError):
            left = []
        if left:
            details.append("file(s) opened and never closed: "
                           + ", ".join(repr(x) for x in left))
    if details:
        return False, "\n".join(details)
    return True, ""


def run_child(test, box, track_open):
    timeout = test.get("timeout", 15)
    req = box.tmp("request.json")
    res = box.tmp("result.json")
    python, env, _ = interpreter(test, box)
    with open(req, "w", encoding="utf-8") as fh:
        json.dump({"test": test, "file": box.file,
                   "track_open": track_open}, fh)
    try:
        proc = subprocess.run([python, "-B",
                               os.path.abspath(__file__), "--child", req,
                               res],
                              cwd=box.cwd, capture_output=True, text=True,
                              timeout=timeout, env=env,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, (f"timed out after {timeout}s: waiting for input, "
                       "or an infinite loop")
    try:
        with open(res, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        tail = "\n".join((proc.stderr or proc.stdout).strip()
                         .splitlines()[-4:])
        return False, ("the code stopped the interpreter (exit() / crash, "
                       f"status {proc.returncode})" +
                       (":\n" + tail if tail else ""))
    NOTES.extend(data.get("notes", []))
    return data["ok"], data["detail"]


# --------------------------------------------------------------------------
# files mode: project files (requirements.txt, pyproject.toml, .env...)
# --------------------------------------------------------------------------

_REQ_NAME = re.compile(r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*"
                       r"(\[[^\]]*\])?\s*(.*)$")
_REQ_SPEC = re.compile(r"(?:(?:===|==|!=|~=|<=|>=|<|>)\s*[\w.*+!-]+\s*"
                       r"(?:,\s*(?:===|==|!=|~=|<=|>=|<|>)\s*[\w.*+!-]+\s*)*"
                       r"|\(\s*(?:===|==|!=|~=|<=|>=|<|>)[^)]*\)\s*"
                       r"|@\s*\S+\s*)?(;.*)?$")


def norm_dist(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(text):
    """Normalized project name of a PEP 508 requirement, None if invalid."""
    m = _REQ_NAME.match(text.strip())
    if not m or not _REQ_SPEC.fullmatch(m.group(3)):
        return None
    return norm_dist(m.group(1))


def requirements(text):
    """requirements.txt -> [(line number, name or None, line, error)]:
    options (-r, -e, --index-url...) and URLs have no name, a line that is
    not a valid requirement has an error."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        if line.startswith("-") or "://" in line or line.startswith("."):
            out.append((n, None, raw, None))
            continue
        name = requirement_name(line)
        out.append((n, name, raw, None if name else
                    "not a valid requirement (pip install -r would fail)"))
    return out


def env_keys(text):
    """Variable names of a .env file (KEY=value, `export` accepted)."""
    keys = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        m = re.match(r"([A-Za-z_][A-Za-z0-9_.]*)\s*=", line)
        if m:
            keys.append(m.group(1))
    return keys


def ignored(gitignore, name):
    """Does this .gitignore (text) ignore the file `name` (relative to the
    folder of the .gitignore)? Last matching pattern wins, ! negates."""
    import fnmatch
    result = False
    base = name.split("/")[-1]
    for raw in gitignore.splitlines():
        pat = raw.rstrip()
        if not pat or pat.startswith("#"):
            continue
        neg = pat.startswith("!")
        if neg:
            pat = pat[1:]
        if pat.endswith("/"):
            continue        # only folders
        anchored = "/" in pat.strip("/")
        pat = pat.lstrip("/")
        target = name if anchored or raw.lstrip("!").startswith("/") \
            else base
        if fnmatch.fnmatchcase(target, pat) or (
                pat.startswith("**/") and fnmatch.fnmatchcase(base, pat[3:])):
            result = not neg
    return result


def git_tracked(project, rel):
    """True / False: is `rel` committed or staged in the project's git
    repository; None when git is missing or the project is not in one."""
    if not shutil.which("git"):
        return None
    try:
        inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                                cwd=project, capture_output=True, text=True,
                                timeout=20)
        if inside.stdout.strip() != "true":
            return None
        proc = subprocess.run(["git", "ls-files", "--", rel], cwd=project,
                              capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return bool(proc.stdout.strip())


def venv_dirs(project):
    """Folders of the project that are virtual environments."""
    out = []
    for base, dirs, files in os.walk(project):
        if "pyvenv.cfg" in files and base != project:
            out.append(os.path.relpath(base, project).replace(os.sep, "/"))
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d != ".git"]
    return sorted(out)


def parse_toml(text):
    """TOML text -> dict. tomllib on 3.11+, else a small parser that knows
    what a pyproject.toml holds. ValueError when the text is not TOML."""
    try:
        import tomllib
    except ImportError:
        return _MiniToml(text).parse()
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(str(exc)) from None


class _MiniToml:
    """Tables, arrays of tables, dotted / quoted keys, strings (the four
    kinds), integers, floats, booleans, dates (kept as text), arrays and
    inline tables. Python 3.9 / 3.10 only (no tomllib there)."""

    ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r",
               '"': '"', "\\": "\\"}

    def __init__(self, text):
        self.s = text.replace("\r\n", "\n")
        self.i = 0

    def fail(self, msg):
        line = self.s.count("\n", 0, self.i) + 1
        raise ValueError(f"line {line}: {msg}")

    def peek(self, n=1):
        return self.s[self.i:self.i + n]

    def blank(self, newlines=False):
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t" or (newlines and c == "\n"):
                self.i += 1
            elif c == "#":
                while self.i < len(self.s) and self.s[self.i] != "\n":
                    self.i += 1
            else:
                break

    def end_of_line(self):
        self.blank()
        if self.i < len(self.s):
            if self.s[self.i] != "\n":
                self.fail(f"unexpected {self.s[self.i]!r}")
            self.i += 1

    def key(self):
        parts = []
        while True:
            self.blank()
            c = self.peek()
            if c in ('"', "'"):
                parts.append(self.string())
            else:
                m = re.compile(r"[A-Za-z0-9_-]+").match(self.s, self.i)
                if not m:
                    self.fail("key expected")
                parts.append(m.group(0))
                self.i = m.end()
            self.blank()
            if self.peek() != ".":
                return parts
            self.i += 1

    @staticmethod
    def table_at(root, path, fail):
        node = root
        for part in path:
            node = node.setdefault(part, {})
            if isinstance(node, list):
                node = node[-1]
            if not isinstance(node, dict):
                fail(f"{'.'.join(path)} is not a table")
        return node

    def parse(self):
        root: dict = {}
        current = root
        while True:
            self.blank(newlines=True)
            if self.i >= len(self.s):
                return root
            if self.peek(2) == "[[":
                self.i += 2
                path = self.key()
                if self.peek(2) != "]]":
                    self.fail("]] expected")
                self.i += 2
                parent = self.table_at(root, path[:-1], self.fail)
                items = parent.setdefault(path[-1], [])
                if not isinstance(items, list):
                    self.fail(f"{'.'.join(path)} is not an array of tables")
                current = {}
                items.append(current)
            elif self.peek() == "[":
                self.i += 1
                path = self.key()
                if self.peek() != "]":
                    self.fail("] expected")
                self.i += 1
                current = self.table_at(root, path, self.fail)
            else:
                path = self.key()
                if self.peek() != "=":
                    self.fail("= expected after the key")
                self.i += 1
                self.blank()
                node = self.table_at(current, path[:-1], self.fail)
                if path[-1] in node:
                    self.fail(f"duplicate key {'.'.join(path)}")
                node[path[-1]] = self.value()
            self.end_of_line()

    def string(self):
        for quote in ('"""', "'''"):
            if self.peek(3) == quote:
                self.i += 3
                if self.peek() == "\n":
                    self.i += 1
                end = self.s.find(quote, self.i)
                if end < 0:
                    self.fail("unterminated string")
                raw = self.s[self.i:end]
                self.i = end + 3
                if quote == "'''":
                    return raw
                raw = re.sub(r"\\[ \t]*\n\s*", "", raw)
                return self.unescape(raw)
        quote = self.peek()
        self.i += 1
        out = []
        while True:
            if self.i >= len(self.s) or self.s[self.i] == "\n":
                self.fail("unterminated string")
            c = self.s[self.i]
            if c == quote:
                self.i += 1
                break
            if c == "\\" and quote == '"':
                out.append(self.s[self.i:self.i + 2])
                if self.s[self.i + 1:self.i + 2] in ("u", "U"):
                    n = 4 if self.s[self.i + 1] == "u" else 8
                    out[-1] = self.s[self.i:self.i + 2 + n]
                self.i += len(out[-1])
                continue
            out.append(c)
            self.i += 1
        text = "".join(out)
        return self.unescape(text) if quote == '"' else text

    def unescape(self, raw):
        def sub(m):
            esc = m.group(1)
            if esc[0] in "uU":
                return chr(int(esc[1:], 16))
            if esc not in self.ESCAPES:
                self.fail(f"invalid escape \\{esc}")
            return self.ESCAPES[esc]
        return re.sub(r"\\(u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|.)", sub, raw)

    def value(self):
        c = self.peek()
        if c in ('"', "'"):
            return self.string()
        if c == "[":
            self.i += 1
            items = []
            while True:
                self.blank(newlines=True)
                if self.peek() == "]":
                    self.i += 1
                    return items
                items.append(self.value())
                self.blank(newlines=True)
                if self.peek() == ",":
                    self.i += 1
                elif self.peek() != "]":
                    self.fail(", or ] expected in the array")
        if c == "{":
            self.i += 1
            table: dict = {}
            self.blank()
            if self.peek() == "}":
                self.i += 1
                return table
            while True:
                path = self.key()
                if self.peek() != "=":
                    self.fail("= expected in the inline table")
                self.i += 1
                self.blank()
                self.table_at(table, path[:-1], self.fail)[path[-1]] = \
                    self.value()
                self.blank()
                if self.peek() == "}":
                    self.i += 1
                    return table
                if self.peek() != ",":
                    self.fail(", or } expected in the inline table")
                self.i += 1
        m = re.compile(r"[0-9A-Za-z+_.:-]+(?: [0-9][0-9:.+Z-]*)?").match(
            self.s, self.i)
        if not m:
            self.fail("value expected")
        word = m.group(0)
        self.i = m.end()
        if word in ("true", "false"):
            return word == "true"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*|\d{2}:\d{2}.*", word):
            return word
        clean = word.replace("_", "")
        try:
            return int(clean, 0) if re.fullmatch(
                r"[+-]?(0[xob][0-9a-fA-F]+|0|[1-9]\d*)", clean) \
                else float(clean)
        except ValueError:
            self.fail(f"invalid value {word!r}")
        return None


_RUNS: dict = {}


def run_files(test, ex, project):
    rel = test.get("file")
    path = os.path.join(project, rel) if rel else None
    text = None
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()

    def run(**over):
        """Run the exercise file once with these test keys (the same run
        is reused by the other tests of the exercise)."""
        check_requires(over.get("requires"))
        key = json.dumps([project, ex["file"], over], sort_keys=True)
        if key in _RUNS:
            return dict(_RUNS[key])
        box = Sandbox(project, ex["file"], over.get("cwd", "scriptdir"),
                      over.get("fixtures"))
        try:
            proc, ctx = exec_script(over, box)
            if proc is None:
                res = {"out": "", "raw": "", "err": "", "code": None,
                       "timeout": True, "new_files": box.new_files(),
                       "ctx": ctx or {}}
            else:
                res = {"out": normalize(proc.stdout), "raw": proc.stdout,
                       "err": proc.stderr, "code": proc.returncode,
                       "timeout": False, "new_files": box.new_files(),
                       "ctx": ctx or {}}
        finally:
            box.close()
        _RUNS[key] = res
        return dict(res)
    source = None
    try:
        with open(os.path.join(project, ex["file"]), encoding="utf-8",
                  errors="replace") as fh:
            source = fh.read()
    except OSError:
        pass
    env = {"root": project, "path": path, "text": text, "source": source,
           "toml": parse_toml, "requirements": requirements,
           "requirement_name": requirement_name, "norm_dist": norm_dist,
           "env_keys": env_keys, "ignored": ignored,
           "tracked": lambda r: git_tracked(project, r),
           "venv_dirs": lambda: venv_dirs(project), "run": run,
           "parse": parse_literal, "re": re, "os": os}
    return run_snippet(test["code"], env)


def run_test(test, ex, project):
    """Returns (status, detail): status True / False / None (skipped)."""
    if test.get("nonroot") and is_root():
        return None, "running as root: permissions are not enforced"
    mode = test.get("mode", "script")
    try:
        check_requires(test.get("requires", ex.get("requires")))
        if mode == "files":
            return run_files(test, ex, project)
        box = Sandbox(project, ex["file"], test.get("cwd", "scriptdir"),
                      test.get("fixtures"))
        try:
            track = bool(test.get("track_open", ex.get("track_open")))
            if mode == "script":
                return run_script(test, box, track)
            if mode in ("func_stdio", "module"):
                return run_child(test, box, track)
            return False, f"unknown test mode {mode!r}"
        finally:
            box.close()
    except SkipTest as exc:
        return None, str(exc)


# --------------------------------------------------------------------------
# static checks (read the source, never run it)
# --------------------------------------------------------------------------

def is_main_guard(node):
    return isinstance(node, ast.If) and "__name__" in ast.dump(node.test)


def check_authorized(tree, allowed):
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef))}
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)):
            continue
        name = node.func.id
        if name in allowed or name in defined or not hasattr(builtins, name):
            continue
        obj = getattr(builtins, name)
        if isinstance(obj, type) and issubclass(obj, BaseException):
            continue
        bad.append(f"{name}() line {node.lineno}")
    if bad:
        return False, "not authorized: " + ", ".join(bad)
    return True, ""


def check_forbidden_calls(tree, names):
    bad = [f"{n.func.id}() line {n.lineno}" for n in ast.walk(tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
           and n.func.id in names]
    if bad:
        return False, "forbidden: " + ", ".join(bad)
    return True, ""


def imported_modules(tree):
    """(top-level module, line, is_relative) for every import."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name.split(".")[0], node.lineno, False))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                out.append((node.module or ".", node.lineno, True))
            else:
                out.append((node.module.split(".")[0], node.lineno, False))
    return out


def stdlib_modules():
    """Top-level names of the standard library."""
    names = getattr(sys, "stdlib_module_names", None)
    if names:
        return set(names)
    import sysconfig
    out = set(sys.builtin_module_names)
    try:
        for entry in os.listdir(sysconfig.get_paths()["stdlib"]):
            out.add(entry[:-3] if entry.endswith(".py") else entry)
    except (OSError, KeyError):
        pass
    return out


def check_imports(tree, allowed, file_path, project, extra=()):
    """extra: modules accepted here (shown elsewhere as a warning)."""
    bad = []
    if allowed != "local":
        allowed = set(allowed) | set(extra)
        if "stdlib" in allowed:
            allowed |= stdlib_modules()
    for mod, line, relative in imported_modules(tree):
        if mod == "__future__":
            continue
        if allowed == "local":
            if relative:
                continue
            here = os.path.dirname(file_path)
            if any(os.path.exists(os.path.join(d, mod + ".py"))
                   or os.path.isdir(os.path.join(d, mod))
                   for d in (here, project)):
                continue
            bad.append(f"{mod} line {line}")
        elif relative or mod not in allowed:
            bad.append(f"{mod} line {line}")
    if bad:
        return False, "import not allowed: " + ", ".join(bad)
    return True, ""


def _check_extra_imports(tree, extra):
    found = [f"{mod} line {line}" for mod, line, relative
             in imported_modules(tree) if not relative and mod in extra]
    if found:
        return False, "imported: " + ", ".join(found)
    return True, ""


def _dynamic_imports(tree):
    """(names imported with importlib.import_module / __import__ and a
    constant, True when such a call takes a non-constant name)."""
    names, dynamic = [], False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        fname = func.id if isinstance(func, ast.Name) else \
            func.attr if isinstance(func, ast.Attribute) else ""
        if fname not in ("import_module", "__import__") or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            names.append(arg.value)
        else:
            dynamic = True
    return names, dynamic


def _mentions(tree, mod):
    return any(isinstance(n, ast.Constant) and n.value == mod
               for n in ast.walk(tree))


def check_require_imports(tree, wanted):
    """wanted: list of {"kind": import|from|relative|under|absolute|any,
    "module": dotted name (optional)}. "any": the module (or a submodule)
    is imported in any way: import, from ... import, importlib.import_module
    / __import__ with its name, or with a computed name while the name
    appears in the file (a loop over the dependencies)."""
    missing = []
    dynamic_names, dynamic = _dynamic_imports(tree)
    for want in wanted:
        kind, mod = want["kind"], want.get("module")
        found = False
        if kind == "any":
            found = any(n == mod or n.startswith(mod + ".")
                        for n in dynamic_names) or (
                dynamic and _mentions(tree, mod))
            kind_static = "under"
        else:
            kind_static = kind
        for node in [] if found else ast.walk(tree):
            if kind_static == "import" and isinstance(node, ast.Import):
                found = any(a.name == mod for a in node.names)
            elif kind_static == "from" and isinstance(node, ast.ImportFrom):
                found = node.level == 0 and node.module == mod
            elif kind_static == "relative" and isinstance(node,
                                                          ast.ImportFrom):
                found = node.level > 0
            elif kind_static == "under" and isinstance(node, ast.Import):
                found = any(a.name == mod or a.name.startswith(mod + ".")
                            for a in node.names)
            elif kind_static == "under" and isinstance(node, ast.ImportFrom):
                found = node.level == 0 and node.module is not None and (
                    node.module == mod or node.module.startswith(mod + "."))
            elif kind_static == "absolute" and isinstance(
                    node, (ast.Import, ast.ImportFrom)):
                found = isinstance(node, ast.Import) or node.level == 0
            if found:
                break
        if not found:
            missing.append(want.get("label") or
                           {"import": f"import {mod}",
                            "from": f"from {mod} import ...",
                            "relative": "a relative import (from . import)",
                            "under": f"an import of {mod}",
                            "any": f"an import of {mod}",
                            "absolute": "an absolute import"}[kind])
    if missing:
        return False, "expected: " + ", ".join(missing)
    return True, ""


def check_typed(tree):
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        if args and args[0].arg in ("self", "cls"):
            args = args[1:]
        missing = [a.arg for a in args if a.annotation is None]
        for extra in (node.args.vararg, node.args.kwarg):
            if extra is not None and extra.annotation is None:
                missing.append(extra.arg)
        if node.returns is None:
            missing.append("return")
        if missing:
            bad.append(f"{node.name}() line {node.lineno}: "
                       f"{', '.join(missing)}")
    if bad:
        return False, "missing type hints: " + "; ".join(bad)
    return True, ""


SNAKE = re.compile(r"_{0,2}[a-z][a-z0-9_]*|_")
DUNDER = re.compile(r"__[a-z0-9_]+__")
PASCAL = re.compile(r"_?[A-Z][A-Za-z0-9]*")
CONST = re.compile(r"_?[A-Z][A-Z0-9_]*")


def check_naming(tree):
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if not PASCAL.fullmatch(node.name):
                bad.append(f"class {node.name} (PascalCase) line "
                           f"{node.lineno}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not (SNAKE.fullmatch(node.name)
                    or DUNDER.fullmatch(node.name)):
                bad.append(f"def {node.name} (snake_case) line "
                           f"{node.lineno}")
            for arg in node.args.args + node.args.kwonlyargs:
                if not SNAKE.fullmatch(arg.arg):
                    bad.append(f"argument {arg.arg} line {node.lineno}")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if not (SNAKE.fullmatch(node.id) or CONST.fullmatch(node.id)):
                bad.append(f"variable {node.id} line {node.lineno}")
        elif isinstance(node, ast.Attribute) and \
                isinstance(node.ctx, ast.Store):
            if not (SNAKE.fullmatch(node.attr)
                    or CONST.fullmatch(node.attr)):
                bad.append(f"attribute {node.attr} line {node.lineno}")
    if bad:
        return False, "naming: " + ", ".join(dict.fromkeys(bad))
    return True, ""


def check_only_functions(tree):
    bad = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value,
                                                     ast.Constant):
            continue
        bad.append(f"line {node.lineno}")
    if bad:
        return False, "code outside a function: " + ", ".join(bad)
    return True, ""


def check_recursive(tree):
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef):
            continue
        for node in ast.walk(func):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == func.name):
                return True, ""
    return False, "no function calls itself"


def node_counts(tree):
    counts = {}
    for node in ast.walk(tree):
        name = node.__class__.__name__
        counts[name] = counts.get(name, 0) + 1
    return counts


def check_nodes(tree, wanted):
    counts = node_counts(tree)
    bad = [f"{name}: {counts.get(name, 0)}/{n}"
           for name, n in wanted.items() if counts.get(name, 0) < n]
    if bad:
        return False, "missing constructs: " + ", ".join(bad)
    return True, ""


def check_forbid_nodes(tree, forbidden):
    bad = []
    for node in ast.walk(tree):
        name = node.__class__.__name__
        if name in forbidden:
            bad.append(f"line {node.lineno}: {forbidden[name]}")
    if bad:
        return False, "; ".join(bad)
    return True, ""


def check_sys_path(tree):
    bad = [f"line {n.lineno}" for n in ast.walk(tree)
           if isinstance(n, ast.Attribute) and n.attr == "path"
           and isinstance(n.value, ast.Name) and n.value.id == "sys"]
    if bad:
        return False, "sys.path is used: " + ", ".join(bad)
    return True, ""


NODE_LABELS = {"With": "with statement", "ListComp": "list comprehension",
               "DictComp": "dict comprehension", "Yield": "yield",
               "ClassDef": "class", "SetComp": "set comprehension",
               "Try": "try block", "ExceptHandler": "except clause",
               "Global": "global statement"}


def also_files(ex, project):
    """The `also_check` files that exist; glob patterns (ex0/*.py) are
    expanded, so the files of a package get the same checks."""
    out = []
    for pat in ex.get("also_check", []):
        for path in sorted(glob.glob(os.path.join(project, pat))):
            rel = os.path.relpath(path, project).replace(os.sep, "/")
            if os.path.isfile(path) and rel != ex["file"] and rel not in out:
                out.append(rel)
    return out


def static_checks(ex, project):
    """List of (name, status, detail) for the source checks asked."""
    files = [ex["file"]] + also_files(ex, project)
    results = []
    trees = []
    for rel in files:
        try:
            with open(os.path.join(project, rel), encoding="utf-8") as fh:
                trees.append((rel, ast.parse(fh.read())))
        except SyntaxError as exc:
            results.append((f"{rel} parses", "ko", str(exc)))
        except (OSError, UnicodeDecodeError) as exc:
            results.append((f"{rel} readable", "ko", str(exc)))
    if not trees:
        return results

    def each(name, fn, fail="ko", why=""):
        bad = []
        for rel, tree in trees:
            ok, detail = fn(rel, tree)
            if not ok:
                bad.append(detail if len(trees) == 1 else f"{rel}: {detail}")
        if bad and why:
            bad.append(why)
        results.append((name, fail if bad else "ok", "\n".join(bad)))

    if "authorized" in ex:
        allowed = ex["authorized"]
        # a builtin outside the list is up to the evaluator: warn, not fail
        each("only authorized functions: " + ", ".join(allowed),
             lambda r, t: check_authorized(t, allowed), fail="warn",
             why="not in the subject's authorized list: some evaluators "
                 "accept it, some don't: be ready to justify it.")
    if "forbidden" in ex:
        each("no " + ", ".join(f"{f}()" for f in ex["forbidden"]),
             lambda r, t: check_forbidden_calls(t, ex["forbidden"]))
    if "imports" in ex:
        allowed = ex["imports"]
        extra = ex.get("imports_warn", [])
        label = ("only imports of the project's own modules"
                 if allowed == "local" else
                 "imports: " + (", ".join("standard library" if a == "stdlib"
                                          else a for a in allowed)
                                or "none"))
        each(label, lambda r, t: check_imports(
            t, allowed, os.path.join(project, r), project, extra))
        if extra:
            each("no import of " + ", ".join(extra)
                 + " (not in the subject's list)",
                 lambda r, t: _check_extra_imports(t, extra), fail="warn",
                 why="not in the subject's authorized list, accepted for "
                     "the type hints the subject asks for: up to the "
                     "evaluator.")
    if ex.get("no_sys_path"):
        each("sys.path is not modified", lambda r, t: check_sys_path(t))
    if ex.get("require_imports"):
        rel, tree = trees[0]
        results.append(("import style: " + ", ".join(
            w.get("label") or (w["kind"] + " " + w.get("module", ""))
            .strip() for w in ex["require_imports"]),
            *_st(check_require_imports(tree, ex["require_imports"]))))
    if ex.get("typed"):
        each("type hints on every function / method",
             lambda r, t: check_typed(t))
    if ex.get("naming"):
        each("naming: PascalCase classes, snake_case functions / variables",
             lambda r, t: check_naming(t))
    if ex.get("only_functions"):
        each("only functions, no main / top-level code",
             lambda r, t: check_only_functions(t))
    if ex.get("main_guard"):
        tree = trees[0][1]
        ok = any(is_main_guard(n) for n in tree.body)
        results.append(('has an if __name__ == "__main__": block',
                        "ok" if ok else "ko",
                        "" if ok else "no main guard found"))
    if ex.get("recursive"):
        results.append(("really recursive",
                        *_st(check_recursive(trees[0][1]))))
    if ex.get("require_nodes"):
        wanted = ex["require_nodes"]
        results.append(("uses " + ", ".join(
            f"{NODE_LABELS.get(k, k)} x{v}" for k, v in wanted.items()),
            *_st(check_nodes(trees[0][1], wanted))))
    if ex.get("forbid_nodes"):
        forbidden = ex["forbid_nodes"]
        each("not used: " + ", ".join(NODE_LABELS.get(k, k)
                                      for k in forbidden),
             lambda r, t: check_forbid_nodes(t, forbidden))
    return results


def _st(pair):
    ok, detail = pair
    return ("ok" if ok else "ko"), detail


# --------------------------------------------------------------------------
# flake8 / mypy (on a copy, caches in /tmp)
# --------------------------------------------------------------------------

def tool_cmd(name):
    if shutil.which(name):
        return [name]
    probe = subprocess.run([sys.executable, "-B", "-c", f"import {name}"],
                           capture_output=True, env=child_env())
    if probe.returncode == 0:
        return [sys.executable, "-B", "-m", name]
    return None


_TOOLS: dict = {}


def tool(name):
    if name not in _TOOLS:
        _TOOLS[name] = tool_cmd(name)
    return _TOOLS[name]


def lint_checks(ex, project, quick=False):
    files = [ex["file"]] + also_files(ex, project)
    results = []
    want_flake8 = ex.get("flake8", True)
    want_mypy = False if quick else ex.get("mypy", True)
    if not want_flake8 and not want_mypy:
        return results
    root = tempfile.mkdtemp(prefix="42next-py-lint-")
    try:
        work = os.path.join(root, "project")
        shutil.copytree(project, work, ignore=IGNORE_COPY)
        if want_flake8:
            cmd = tool("flake8")
            if cmd is None:
                _note_missing("flake8")
                results.append(("flake8", "skip", "flake8 not installed"))
            else:
                proc = subprocess.run(cmd + files, cwd=work,
                                      capture_output=True, text=True,
                                      env=child_env(), timeout=120)
                out = (proc.stdout + proc.stderr).strip()
                status = "ok" if proc.returncode == 0 else "ko"
                name = "flake8"
                allowed = ex.get("flake8_expect")
                if status == "ko" and allowed:
                    codes = re.findall(r":\d+:\d+: ([A-Z]+\d+) ", out)
                    if codes and all(c in allowed for c in codes):
                        status = "warn"
                        name = ("flake8 (the subject allows these errors "
                                "here: " + ", ".join(allowed) + ")")
                    else:
                        name = ("flake8 (only " + ", ".join(allowed)
                                + " is allowed by the subject)")
                results.append((name, status, _clip(out)))
        if want_mypy:
            cmd = tool("mypy")
            if cmd is None:
                _note_missing("mypy")
                results.append(("mypy", "skip", "mypy not installed"))
            else:
                cache = os.path.join(root, "mypy_cache")
                needs = _all_requires(ex)
                # the packages the tests see (mypy may live in a pipx venv)
                pyexe = ["--python-executable", sys.executable] \
                    if needs else []
                proc = subprocess.run(
                    cmd + ["--cache-dir", cache, "--no-color-output",
                           "--no-error-summary", "--show-error-codes",
                           *pyexe, *files],
                    cwd=work, capture_output=True, text=True,
                    env=child_env(), timeout=300)
                out = (proc.stdout + proc.stderr).strip()
                status = "ok" if proc.returncode == 0 else "ko"
                name = "mypy"
                absent = [pkg for pkg, _ in missing_packages(needs)]
                if status == "ko" and absent:
                    out, dropped = _drop_missing(out, absent)
                    if dropped and not re.search(r": error: ", out):
                        status, out = "skip", (
                            missing_reason(missing_packages(needs))
                            + ": mypy cannot check the code that uses it")
                        for pkg in absent:
                            _note_missing_lib(pkg["pip"])
                expected = ex.get("mypy_expect")
                if status == "ko" and expected:
                    # only the error codes the subject causes on purpose
                    codes = re.findall(r"error: .*\[([\w-]+)\]\s*$", out,
                                       re.M)
                    other = [c for c in codes if c not in expected]
                    if codes and not other:
                        status = "warn"
                        name = ("mypy (the subject says this error is "
                                "expected, on purpose)")
                    else:
                        name = ("mypy (only " + ", ".join(expected)
                                + " is expected by the subject)")
                elif status == "ko" and want_mypy == "expect_errors":
                    status = "warn"
                    name = ("mypy (the subject says an error is expected "
                            "here, on purpose)")
                results.append((name, status, _clip(out)))
    except subprocess.TimeoutExpired as exc:
        results.append(("lint", "ko", f"timed out: {exc.cmd[0]}"))
    finally:
        remove_tree(root)
    return results


def _all_requires(ex):
    out = list(ex.get("requires") or [])
    for test in ex.get("tests", []):
        out += test.get("requires") or []
    return list({json.dumps(_pkg(r), sort_keys=True): r
                 for r in out}.values())


def _drop_missing(out, absent):
    """mypy output without the 'module not found' errors (and their notes)
    of the packages that are not installed. Returns (text, dropped)."""
    mods = {p["module"] for p in absent}
    kept, dropped = [], False
    for line in out.splitlines():
        m = re.search(r'module named "([\w.]+)"', line)
        if m and m.group(1).split(".")[0] in mods:
            dropped = True
            continue
        kept.append(line)
    if dropped and not any(": error: " in ln for ln in kept):
        kept = []
    return "\n".join(kept), dropped


def _clip(text, n=12):
    lines = text.splitlines()
    if len(lines) > n:
        lines = lines[:n] + [f"... ({len(lines) - n} more)"]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# specs + detection
# --------------------------------------------------------------------------

def spec_ids():
    return sorted(f[:-5] for f in os.listdir(SPECS_DIR) if f.endswith(".json"))


def load_spec(name):
    path = name if os.path.isfile(name) else \
        os.path.join(SPECS_DIR, name + ".json")
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    spec.setdefault("project", os.path.splitext(os.path.basename(path))[0])
    spec["_path"] = os.path.abspath(path)
    defaults = spec.get("defaults", {})
    spec["exercises"] = [{**defaults, **ex} for ex in spec["exercises"]]
    for ex in spec["exercises"]:
        for test in ex.get("tests", []):
            _spread_wording(test, test.get("wording", ex.get("wording")))
    return spec


def _test_rules(test):
    match = test.get("match")
    rules = []
    if isinstance(match, dict) and "type" in match:
        rules.append(match)
    elif isinstance(match, dict):
        for stream in ("stdout", "stderr", "output"):
            r = match.get(stream)
            rules += (r if isinstance(r, list) else [r]) if r else []
    rules += [o["match"] for o in test.get("outputs", []) if o.get("match")]
    return rules


def _spread_wording(test, wording):
    """"wording": "example" (spec defaults, exercise or test): the subject
    only shows the output as an example. Copied onto each equals / lines
    rule, with the test's inputs (echoed values count as values)."""
    inputs = [str(a) for a in test.get("argv", [])]
    inputs += [a for a in test.get("args", []) if isinstance(a, str)]
    inputs += test.get("stdin", "").splitlines()
    for rule in _test_rules(test):
        if rule.get("type") not in ("equals", "lines"):
            continue
        if wording:
            rule.setdefault("wording", wording)
        rule.setdefault("inputs", inputs)


def candidate_roots(path):
    start = path if os.path.isdir(path) else os.path.dirname(path)
    start = os.path.abspath(start)
    out = []
    d = start
    for _ in range(3):
        out.append(d)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    try:
        subs = sorted(e.path for e in os.scandir(start)
                      if e.is_dir() and not e.name.startswith("."))
    except OSError:
        subs = []
    return out + subs[:50]


def score(spec, root):
    files = {ex["file"] for ex in spec["exercises"]}
    return sum(os.path.isfile(os.path.join(root, f)) for f in files), \
        len(files)


def detect(path, only_spec=None):
    """Best (spec, root) for path, plus the exercise(s) path points at."""
    path = os.path.abspath(path)
    specs = [load_spec(only_spec)] if only_spec else \
        [load_spec(s) for s in spec_ids()]
    found = []
    for rank, root in enumerate(candidate_roots(path)):
        for spec in specs:
            got, total = score(spec, root)
            if got:
                found.append((got, got / total, -rank, spec, root, total))
    result = {"spec": None, "spec_file": None, "title": None,
              "project": None, "score": 0, "total": 0, "exercise": None,
              "exercises": [], "file": None, "alternatives": []}
    if not found:
        return result
    found.sort(key=lambda f: f[:3], reverse=True)
    got, _, _, spec, root, total = found[0]
    result.update(spec=spec["project"], spec_file=spec["_path"],
                  title=spec.get("title"), project=root, score=got,
                  total=total)
    seen = set()
    for f in found[1:]:
        key = (f[3]["project"], f[4])
        if key not in seen and len(result["alternatives"]) < 4:
            seen.add(key)
            result["alternatives"].append({"spec": f[3]["project"],
                                           "project": f[4], "score": f[0],
                                           "total": f[5]})
    rel = os.path.relpath(path, root)
    if not rel.startswith(".."):
        if os.path.isfile(path):
            result["file"] = path
            ids = [ex["id"] for ex in spec["exercises"]
                   if rel in [ex["file"]] + also_files(ex, root)]
        else:
            ids = [ex["id"] for ex in spec["exercises"]
                   if rel != "." and os.path.dirname(ex["file"]) == rel]
        result["exercises"] = ids
        result["exercise"] = ids[0] if ids else None
    return result


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def label(ex):
    return f"{ex['id']} · {ex['title']} · {ex['file']}"


def _note_missing(tool):
    """Tell core a lint tool is missing (it prints how to install it)."""
    mod = sys.modules.get("core")
    if mod is not None:
        mod.note_missing(tool)


def setup_core(color):
    sys.path.insert(0, COMMON_DIR)
    import core
    if color is not None:
        core.USE_COLOR = color
    return core


def parse_cli(argv):
    p = argparse.ArgumentParser(
        description="Tester for the 42 Python modules.")
    p.add_argument("--spec", help="spec id (" + ", ".join(spec_ids())
                   + ") or path; auto-detected when omitted")
    p.add_argument("--project", "--dir", dest="project", default=None,
                   help="project folder (root, an exN/ folder, or a parent)")
    p.add_argument("--only", "--exercise", dest="only", action="append",
                   default=[], help="exercise ids, comma separated")
    p.add_argument("--file", help="only test the exercise of this file")
    p.add_argument("--list", action="store_true",
                   help="print 'id<TAB>label' per exercise and exit")
    p.add_argument("--detect", metavar="PATH",
                   help="print the detected project as JSON and exit")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--color", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="also show passing checks")
    p.add_argument("--no-lint", action="store_true",
                   help="skip flake8 / mypy")
    p.add_argument("--lint-only", action="store_true",
                   help="only flake8 + mypy, no behaviour / source checks")
    p.add_argument("--quick", action="store_true",
                   help="skip mypy (slow), keep everything else")
    return p.parse_args(argv)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--child"]:
        child_main(argv[1], argv[2])
        return 0
    args = parse_cli(argv)
    color = False if args.no_color else (True if args.color else None)
    core = setup_core(color)

    if args.detect:
        print(json.dumps(detect(args.detect, args.spec), indent=2))
        return 0

    target = args.file or args.project or os.getcwd()
    if args.file and not os.path.isfile(args.file):
        print(core.red(f"no such file: {args.file}"))
        return 2
    try:
        found = detect(target, args.spec)
    except (OSError, ValueError) as exc:
        print(core.red(f"cannot load spec: {exc}"))
        return 2
    if found["spec"] is None:
        if args.spec and args.list:
            found["spec_file"] = load_spec(args.spec)["_path"]
        else:
            print(core.red(f"no known Python project found from {target}"))
            print("known projects: " + ", ".join(spec_ids()))
            return 2
    spec = load_spec(found["spec_file"])
    exercises = spec["exercises"]

    if args.list:
        for ex in exercises:
            print(f"{ex['id']}\t{label(ex)}")
        return 0

    project = found["project"]
    only = [x for chunk in args.only for x in chunk.split(",") if x]
    known = {ex["id"] for ex in exercises}
    unknown = [x for x in only if x not in known]
    if unknown:
        print(core.red(f"unknown exercise(s): {', '.join(unknown)}"))
        print("available: " + ", ".join(ex["id"] for ex in exercises))
        return 2
    auto_note = None
    if args.file:
        if not found["exercises"]:
            print(core.red(f"{args.file} is not an exercise file of "
                           f"{spec.get('title')}"))
            return 2
        only = only or found["exercises"]
    elif not only and found["exercises"]:
        only = found["exercises"]
        auto_note = (f"started inside {os.path.relpath(target, project)}/: "
                     f"testing {', '.join(only)} only "
                     f"(--project {project} for everything)")
    if only:
        exercises = [ex for ex in exercises if ex["id"] in only]

    rep = core.Report(verbose=args.verbose,
                      group_ids={ex["id"] for ex in exercises})
    core.header(spec.get("title", spec["project"]), project,
                spec.get("note"), args.verbose)
    if getattr(args, "quick", False):
        core.quick_skip(rep, "no mypy")
    if auto_note:
        print(core.dim(auto_note))

    def lint(ex):
        if args.lint_only:
            return lint_checks({**ex, "flake8": True,
                                "mypy": ex.get("mypy") or True}, project)
        if args.no_lint:
            return []
        return lint_checks(ex, project, args.quick)

    # flake8 + mypy take about 2s per exercise: start them all now, in the
    # background, while the exercises run
    present = [n for n, ex in enumerate(exercises)
               if os.path.isfile(os.path.join(project, ex["file"]))]
    pool = ThreadPoolExecutor(
        max_workers=max(1, min(len(present), (os.cpu_count() or 2) // 2)))
    lints = {n: pool.submit(lint, exercises[n]) for n in present}
    ran = 0
    try:
        for n, ex in enumerate(exercises):
            path = os.path.join(project, ex["file"])
            if not os.path.isfile(path):
                rep._close()
                print()
                rep.skip(label(ex), ex["file"] + " not found")
                continue
            ran += 1
            rep.section(label(ex), gid=ex["id"])
            tests = [] if args.lint_only else ex.get("tests", [])
            # tests that need a missing package: one SKIP line per reason,
            # the other tests still run
            blocked: dict = {}
            runnable = []
            for test in tests:
                absent = missing_packages(test.get("requires",
                                                   ex.get("requires")))
                if not absent:
                    runnable.append(test)
                    continue
                for pkg, _ in absent:
                    _note_missing_lib(pkg["pip"])
                blocked.setdefault(missing_reason(absent), []).append(test)
            for why, skipped in blocked.items():
                rep.skip(skipped[0].get("name", "test") if len(skipped) == 1
                         else f"{len(skipped)} behaviour tests", why)
            tests = runnable
            for test in tests:
                NOTES.clear()
                name = test.get("name", "test")
                try:
                    status, detail = run_test(test, ex, project)
                except Exception as exc:  # noqa: BLE001 - keep going
                    status, detail = False, \
                        f"tester error: {exc.__class__.__name__}: {exc}"
                if status is None:
                    rep.skip(name, detail)
                elif not status and test.get("severity") == "warn":
                    rep.warn(name + " (advice, not fatal)", detail)
                else:
                    rep.check(name, status, detail=detail or None)
                # a note says why a passing check is only a warning; on a
                # failed check it would contradict the KO
                for note in dict.fromkeys(NOTES if status else []):
                    rep.warn(note)
            results = [] if args.lint_only else static_checks(ex, project)
            results += lints[n].result() if n in lints else []
            for name, status, detail in results:
                if status == "skip":
                    rep.skip(name, detail)
                elif status == "warn":
                    rep.warn(name, detail)
                else:
                    rep.check(name, status == "ok", detail=detail or None)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    if ran == 0:
        rep._close()
        print()
        print(core.bold(core.red(
            f"RESULT 0/0  no exercise file found in {project}")))
        return 2
    return rep.finish()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # ctrl-c: the temporary copies are removed on the way out
        print("\nstopped (ctrl-c)")
        sys.exit(130)
