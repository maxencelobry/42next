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

Everything runs in a throwaway copy of the project (or an empty temporary
folder for cwd "temp"), func_stdio / module tests run in a child process
with a timeout, every interpreter gets -B: nothing is ever written into the
tested project (no fixture, no __pycache__, no .mypy_cache).

Static checks on the source (never run): authorized builtins, imports,
type hints, naming, forbidden / required constructs, plus flake8 and mypy
(when installed), which every subject of the module requires.

Exit code: 0 all passed, 1 something failed, 2 setup problem.
Stdlib only, Python 3.9+.
"""

import argparse
import ast
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

IGNORE_COPY = shutil.ignore_patterns(".git", "__pycache__", ".mypy_cache",
                                     ".pytest_cache", ".ruff_cache",
                                     "node_modules", ".venv", "venv")
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


def check_rule(rule, actual):
    """Return (ok, detail). `rule` is a match descriptor dict."""
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
               "raw": actual}
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


def run_snippet(code, env):
    """Run assert-based check code. A list of lines is joined first."""
    if isinstance(code, list):
        code = "\n".join(code)
    env.setdefault("note", NOTES.append)
    try:
        exec(code, env)
    except AssertionError as exc:
        return False, str(exc) or "check failed"
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


def run_script(test, box, track_open):
    argv = [str(a) for a in test.get("argv", [])]
    timeout = test.get("timeout", 15)
    invoke = os.path.relpath(box.file, box.cwd)
    log = box.tmp("open.json")
    if track_open:
        cmd = [sys.executable, "-B", "-c", TRACK_RUNNER, log, invoke, *argv]
    else:
        cmd = [sys.executable, "-B", invoke, *argv]
    try:
        proc = subprocess.run(cmd, cwd=box.cwd, input=test.get("stdin", ""),
                              capture_output=True, text=True,
                              timeout=timeout, env=child_env(),
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
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
            ok, detail = check_rule(rule, text)
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
        ok, detail = check_rule(out_check.get("match"), content)
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
    with open(req, "w", encoding="utf-8") as fh:
        json.dump({"test": test, "file": box.file,
                   "track_open": track_open}, fh)
    try:
        proc = subprocess.run([sys.executable, "-B",
                               os.path.abspath(__file__), "--child", req,
                               res],
                              cwd=box.cwd, capture_output=True, text=True,
                              timeout=timeout, env=child_env(),
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


def run_test(test, ex, project):
    """Returns (status, detail): status True / False / None (skipped)."""
    if test.get("nonroot") and is_root():
        return None, "running as root: permissions are not enforced"
    mode = test.get("mode", "script")
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


def check_imports(tree, allowed, file_path, project):
    bad = []
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


def check_require_imports(tree, wanted):
    """wanted: list of {"kind": import|from|relative|absolute_from,
    "module": dotted name (optional)}."""
    missing = []
    for want in wanted:
        kind, mod = want["kind"], want.get("module")
        found = False
        for node in ast.walk(tree):
            if kind == "import" and isinstance(node, ast.Import):
                found = any(a.name == mod for a in node.names)
            elif kind == "from" and isinstance(node, ast.ImportFrom):
                found = node.level == 0 and node.module == mod
            elif kind == "relative" and isinstance(node, ast.ImportFrom):
                found = node.level > 0
            elif kind == "under" and isinstance(node, ast.Import):
                found = any(a.name == mod or a.name.startswith(mod + ".")
                            for a in node.names)
            elif kind == "under" and isinstance(node, ast.ImportFrom):
                found = node.level == 0 and (
                    node.module == mod or node.module.startswith(mod + "."))
            elif kind == "absolute" and isinstance(node, (ast.Import,
                                                          ast.ImportFrom)):
                found = isinstance(node, ast.Import) or node.level == 0
            if found:
                break
        if not found:
            missing.append(want.get("label") or
                           {"import": f"import {mod}",
                            "from": f"from {mod} import ...",
                            "relative": "a relative import (from . import)",
                            "under": f"an import of {mod}",
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
               "ClassDef": "class", "SetComp": "set comprehension"}


def static_checks(ex, project):
    """List of (name, status, detail) for the source checks asked."""
    files = [ex["file"]] + [f for f in ex.get("also_check", [])
                            if os.path.isfile(os.path.join(project, f))]
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
        label = ("only imports of the project's own modules"
                 if allowed == "local" else
                 "imports: " + (", ".join(allowed) or "none"))
        each(label, lambda r, t: check_imports(
            t, allowed, os.path.join(project, r), project))
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
    files = [ex["file"]] + [f for f in ex.get("also_check", [])
                            if os.path.isfile(os.path.join(project, f))]
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
                results.append(("flake8", "skip", "flake8 not installed"))
            else:
                proc = subprocess.run(cmd + files, cwd=work,
                                      capture_output=True, text=True,
                                      env=child_env(), timeout=120)
                out = (proc.stdout + proc.stderr).strip()
                results.append(("flake8", "ok" if proc.returncode == 0
                                else "ko", _clip(out)))
        if want_mypy:
            cmd = tool("mypy")
            if cmd is None:
                results.append(("mypy", "skip", "mypy not installed"))
            else:
                cache = os.path.join(root, "mypy_cache")
                proc = subprocess.run(
                    cmd + ["--cache-dir", cache, "--no-color-output",
                           "--no-error-summary", "--show-error-codes",
                           *files],
                    cwd=work, capture_output=True, text=True,
                    env=child_env(), timeout=300)
                out = (proc.stdout + proc.stderr).strip()
                status = "ok" if proc.returncode == 0 else "ko"
                name = "mypy"
                if status == "ko" and want_mypy == "expect_errors":
                    status = "warn"
                    name = ("mypy (the subject says an error is expected "
                            "here, on purpose)")
                results.append((name, status, _clip(out)))
    except subprocess.TimeoutExpired as exc:
        results.append(("lint", "ko", f"timed out: {exc.cmd[0]}"))
    finally:
        remove_tree(root)
    return results


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
                   if rel in [ex["file"]] + ex.get("also_check", [])]
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
            for test in [] if args.lint_only else ex.get("tests", []):
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
                for note in dict.fromkeys(NOTES):
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
