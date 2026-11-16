#!/usr/bin/env python3
"""42next - Pacman tester (subject v1.1).

    python3 -B run.py --project DIR [--only g1,g2] [--list] [--quick]
                      [--verbose] [--keep] [--no-color]

Static checks (files, Makefile, flake8 / mypy, source, README) and runtime
checks of `python3 pac-man.py config.json`, always in a temporary copy of
the project with its dependencies installed by `make install` in a
temporary virtualenv (never in the user's environment).

A graphical game cannot be watched, so the runtime checks only judge what
the subject states precisely: the program starts, it never ends with a
Python traceback (bad arguments, broken config files, broken highscore
file, failing maze generator, random key presses), it keeps running when a
config value is missing or invalid, and it ignores # comment lines.

Headless: pygame runs with SDL_VIDEODRIVER=dummy, and its event queue is
fed by a small driver (Enter to start, then key presses) so that the game
itself runs, not only its main menu. Another library runs under xvfb-run
when it exists, else the runtime groups are skipped. The real display is
never used.
--quick: shorter runs, fewer random games, no search of the faulty key.
"""

import ast
import copy
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import core  # noqa: E402

GROUPS = [
    ("files", "pac-man.py, Makefile, .gitignore, config, mazegen package, "
              "packaging spec, project management folder"),
    ("makefile", "install / run / debug / clean / lint rules (temporary "
                 "venv)"),
    ("lint", "flake8 . and mypy . with the subject's flags"),
    ("source", "classes, docstrings, A-Maze-ing package, context managers, "
               "game features"),
    ("readme", "README.md requirements"),
    ("usage", "command line: one argument, missing / unreadable / broken "
              "file"),
    ("config", "config file: # comments, missing keys, invalid values, "
               "unknown keys"),
    ("highscore", "highscore file: missing, corrupted, wrong content"),
    ("generator", "the A-Maze-ing generator fails"),
    ("play", "random key presses, window close, time limit, game over "
             "(pygame)"),
    ("repo", "repository content"),
]

MAIN = "pac-man.py"
MYPY_FLAGS = ["--warn-return-any", "--warn-unused-ignores",
              "--ignore-missing-imports", "--disallow-untyped-defs",
              "--check-untyped-defs"]
REQUIRED_RULES = ["install", "run", "debug", "clean", "lint"]

# Sentences of the subject (v1.1) behind every KO / WARN.
Q = {
    "usage": '"$> python3 pac-man.py config.json"',
    "one_arg": '"It must take exactly one argument: a configuration file."',
    "errors": '"Any error (missing file, invalid value, missing key) must '
              'be handled cleanly with clear messages, never with a Python '
              'traceback."',
    "filename": '"The filename file does not matter, but the file must be '
                'a json file."',
    "comments": '"Lines starting with # are comments and must be ignored."',
    "faulty": '"On missing or invalid values, clamp to safe defaults, log '
              'a clear message, and continue."',
    "unknown": '"Unknown keys must be ignored. No traceback."',
    "document": '"The exact structure is up to you, but document your keys '
                'in the README and provide robust defaults."',
    "no_crash": '"Robust error handling, no crash!" / "If your program '
                'crashes due to unhandled exceptions during the review, it '
                'will be considered non-functional."',
    "external": '"Level generation based on an external \'A-Maze-ing\' '
                'package (not yours!)."',
    "own_gen": '"You must not write your own generator."',
    "as_is": '"You must use their package as-is, without modifying it. '
             'The assigned package will be re-installed during your peer '
             'review."',
    "gen_fail": '"If the generator fails, you must handle the error '
                'cleanly."',
    "hs_robust": '"Be robust to file errors (missing file, invalid format, '
                 'etc.)."',
    "makefile": '"Include a Makefile in your activity to automate common '
                'tasks. It must contain the following rules"',
    "install": '"install: Install activity dependencies using pip, uv, '
               'pipx, or any other package manager of your choice."',
    "run": '"run: Execute the main script of your activity (e.g., via '
           'Python interpreter)."',
    "debug": '"debug: Run the main script in debug mode using Python\'s '
             'built-in debugger (e.g., pdb)."',
    "clean": '"clean: Remove temporary files or caches (e.g., __pycache__, '
             '.mypy_cache) to keep the activity environment clean."',
    "lint": '"lint: flake8 . and mypy . --warn-return-any '
            '--warn-unused-ignores --ignore-missing-imports '
            '--disallow-untyped-defs --check-untyped-defs"',
    "flake8": '"Your activity must adhere to the flake8 coding standard."',
    "mypy": '"Use mypy for static type checking. All functions must pass '
            'mypy without errors."',
    "docstrings": '"Include docstrings in functions and classes following '
                  'PEP 257"',
    "context": '"Prefer context managers for resources like files or '
               'connections to ensure automatic cleanup."',
    "oop": '"you will create a complete and playable Pac-Man game like in '
           'Python, using object-oriented programming"',
    "gitignore": '"Include a .gitignore file to exclude Python artifacts."',
    "packaging": '"Your Git repository must contain the full source and the '
                 'packaging script/spec at the root."',
    "pm_dir": '"You must produce various documents that prove your '
              'approach. In a dedicated subdirectory of your repository, '
              'include any relevant elements."',
    "deploy": '"Deployment to a public gaming platform (Steam/Itch.io or '
              'similar) for demonstration."',
    "levels": '"The game consists of multiple levels (at least 10)."',
    "time_limit": '"Each level has a time limit" / "If the time limit is '
                  'reached, you can decide what happens"',
    "readme_cfg": '"A Configuration section explaining the config file '
                  'structure and default values."',
    "readme_hs": '"A Highscore section explaining how the highscore system '
                 'works and why you decided to implement it this way."',
    "readme_maze": '"A Maze Generation section explaining how the assigned '
                   'A-Maze-ing package is used to generate mazes."',
    "readme_impl": '"an Implementation section with a technical summary of '
                   'your implementation."',
    "readme_arch": '"A General Software Architecture section, with '
                   'high-level overview of the software architecture '
                   '(modules, classes, and their relationships)."',
    "readme_pm": '"A Project Management section, with a brief overview of '
                 'how you managed the activity and a link to the dedicated '
                 'project management directory."',
    "english": '"Your README must be written in English."',
    "controls": '"Can move in 4 directions (up, down, left, right) using '
                'arrow keys or WASD"',
}

# Directories that are never project sources.
SKIP_DIRS = {".git", "__pycache__", ".mypy_cache", ".pytest_cache",
             ".ruff_cache", "node_modules", ".venv", "venv", "env", ".env",
             "build", "dist", "site-packages", ".tox", ".nox", ".idea",
             ".vscode", ".eggs"}
GRAPHICS = {"pygame", "pygame_gui", "pyglet", "arcade", "pyray", "raylib",
            "mlx", "tkinter", "PIL", "OpenGL", "moderngl", "glfw", "sdl2",
            "kivy", "ursina", "curses"}
OTHER_LIBS = {"numpy", "pytest", "typing_extensions", "pydantic", "yaml",
              "toml", "tomli", "attrs", "attr", "rich", "colorama",
              "pyinstaller", "PyInstaller", "setuptools", "pkg_resources",
              "mypy", "flake8", "noise", "requests"}
FALLBACK_STDLIB = {
    "abc", "argparse", "array", "ast", "asyncio", "atexit", "base64",
    "bisect", "builtins", "collections", "colorsys", "contextlib", "copy",
    "csv", "ctypes", "curses", "dataclasses", "datetime", "decimal",
    "enum", "errno", "fnmatch", "fractions", "functools", "gc", "getopt",
    "glob", "hashlib", "heapq", "importlib", "inspect", "io", "itertools",
    "json", "logging", "math", "operator", "os", "pathlib", "pdb",
    "pickle", "platform", "pprint", "queue", "random", "re", "shutil",
    "signal", "socket", "sqlite3", "statistics", "string", "struct",
    "subprocess", "sys", "tempfile", "textwrap", "threading", "time",
    "timeit", "tkinter", "traceback", "types", "typing", "unittest",
    "uuid", "warnings", "weakref", "zipfile", "__future__"}
STDLIB = set(getattr(sys, "stdlib_module_names", FALLBACK_STDLIB)) | {
    "__future__"}

TRACEBACK = "Traceback (most recent call last)"
DISPLAY_ERR = re.compile(r"cannot open display|could not open display|"
                         r"can't open display|no available video device|"
                         r"couldn't connect to display|"
                         r"failed to open display|xcb|wayland|"
                         r"\$DISPLAY|no display", re.I)
NETWORK_ERR = re.compile(r"Temporary failure in name resolution|"
                         r"Name or service not known|Network is unreachable|"
                         r"NewConnectionError|Failed to establish a new "
                         r"connection|ConnectTimeout|Read timed out|"
                         r"Could not fetch URL|getaddrinfo failed|"
                         r"No route to host|Connection refused", re.I)
ERRORISH = re.compile(r"error|invalid|expect|decode|pars|malformed|"
                      r"cannot|can't|could not|fail|warn|default|ignor|"
                      r"comment", re.I)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def read_text(path: str, limit: int = 1 << 20) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", "replace")
    except OSError:
        return None


def cap(names: Sequence[str], limit: int = 8) -> str:
    names = list(names)
    more = f" (+{len(names) - limit})" if len(names) > limit else ""
    return ", ".join(names[:limit]) + more


def lines_of(text: str, limit: int = 8, last: bool = True) -> str:
    rows = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if len(rows) <= limit:
        return "\n".join(rows)
    if last:
        return f"... ({len(rows) - limit} lines before)\n" + "\n".join(
            rows[-limit:])
    return "\n".join(rows[:limit]) + f"\n... ({len(rows) - limit} more)"


def traceback_tail(out: str, limit: int = 6) -> str:
    """The end of the last traceback: the student's frames + the error."""
    start = out.rfind(TRACEBACK)
    block = out[start:] if start >= 0 else out
    rows = [ln for ln in block.splitlines() if ln.strip()
            and "42next-pac-driver" not in ln and "runpy.py" not in ln]
    keep = rows[-limit:]
    return "\n".join(keep)


def strip_json_comments(text: str) -> str:
    """# and // comment lines, /* */ blocks: what a config may use."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    out = []
    for line in text.splitlines():
        s = line.lstrip()
        out.append("" if s.startswith("#") or s.startswith("//") else line)
    return "\n".join(out)


def parse_config_text(text: str) -> Optional[Any]:
    try:
        return json.loads(strip_json_comments(text))
    except ValueError:
        pass
    # inline comments after a value, outside strings (best effort)
    rows = []
    for line in strip_json_comments(text).splitlines():
        rows.append(re.sub(r'\s(#|//)[^"]*$', "", line))
    try:
        return json.loads("\n".join(rows))
    except ValueError:
        return None


Path = Tuple[Any, ...]


def leaves(node: Any, path: Path = ()) -> List[Tuple[Path, Any]]:
    out: List[Tuple[Path, Any]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += leaves(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += leaves(v, path + (i,))
    elif path:
        out.append((path, node))
    return out


def containers(node: Any, path: Path = ()) -> List[Tuple[Path, Any]]:
    out: List[Tuple[Path, Any]] = []
    if isinstance(node, (dict, list)):
        if path:
            out.append((path, node))
        items = (node.items() if isinstance(node, dict)
                 else enumerate(node))
        for k, v in items:
            out += containers(v, path + (k,))
    return out


def pattern(path: Path) -> str:
    """("levels", 3, "width") -> levels[*].width"""
    text = ""
    for p in path:
        if isinstance(p, int):
            text += "[*]"
        else:
            text += ("." if text else "") + str(p)
    return text


def set_path(data: Any, path: Path, value: Any) -> None:
    node = data
    for p in path[:-1]:
        node = node[p]
    node[path[-1]] = value


def del_path(data: Any, path: Path) -> None:
    node = data
    for p in path[:-1]:
        node = node[p]
    if isinstance(node, dict):
        node.pop(path[-1], None)


def is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def show_value(v: Any) -> str:
    try:
        text = json.dumps(v)
    except (TypeError, ValueError):
        text = repr(v)
    return text if len(text) < 60 else text[:57] + "..."


DEFAULT_CONFIG: Dict[str, Any] = {
    "highscore_filename": "highscores.json",
    "level": [{"width": 15, "height": 11} for _ in range(10)],
    "lives": 3,
    "pacgum": 42,
    "points_per_pacgum": 10,
    "points_per_super_pacgum": 50,
    "points_per_ghost": 200,
    "seed": 42,
    "level_max_time": 90,
}


# --------------------------------------------------------------------------
# the project, read once (never written)
# --------------------------------------------------------------------------

class Source:
    def __init__(self, rel: str, text: str) -> None:
        self.rel = rel
        self.text = text
        self.tree: Optional[ast.AST] = None
        self.error: Optional[str] = None
        try:
            self.tree = ast.parse(text, filename=rel)
        except (SyntaxError, ValueError) as exc:
            self.error = str(exc)

    @property
    def is_test(self) -> bool:
        base = os.path.basename(self.rel)
        parts = self.rel.replace(os.sep, "/").split("/")
        return (base.startswith("test_") or base.endswith("_test.py")
                or base == "conftest.py"
                or any(p in ("tests", "test") for p in parts[:-1]))


class ImportUse:
    def __init__(self, name: str, rel: str, line: int,
                 optional: bool) -> None:
        self.name = name
        self.rel = rel
        self.line = line
        self.optional = optional


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = []
    types = (handler.type.elts if isinstance(handler.type, ast.Tuple)
             else [handler.type])
    for t in types:
        if isinstance(t, ast.Name):
            names.append(t.id)
        elif isinstance(t, ast.Attribute):
            names.append(t.attr)
    return any(n in ("ImportError", "ModuleNotFoundError", "Exception",
                     "BaseException") for n in names)


def collect_imports(src: Source) -> List[ImportUse]:
    out: List[ImportUse] = []

    def walk(node: ast.AST, optional: bool) -> None:
        for child in ast.iter_child_nodes(node):
            opt = optional
            if isinstance(node, ast.Try) and child in node.body:
                opt = optional or any(_catches_import_error(h)
                                      for h in node.handlers)
            if isinstance(child, ast.Import):
                for a in child.names:
                    out.append(ImportUse(a.name.split(".")[0], src.rel,
                                         child.lineno, opt))
            elif isinstance(child, ast.ImportFrom):
                if child.level == 0 and child.module:
                    out.append(ImportUse(child.module.split(".")[0], src.rel,
                                         child.lineno, opt))
            walk(child, opt)

    if src.tree is not None:
        walk(src.tree, False)
    return out


class Project:
    def __init__(self, path: str) -> None:
        self.path = path
        self.has_main = os.path.isfile(os.path.join(path, MAIN))
        self.make = core.makefile_text(path)
        self.targets = core.makefile_targets(self.make) if self.make else set()
        self.sources: List[Source] = []
        self.dirs: Set[str] = set()
        self._scan()
        self.local = self._local_names()
        self.imports: List[ImportUse] = []
        for s in self.sources:
            self.imports += collect_imports(s)
        names = {u.name for u in self.imports}
        self.third_party = sorted(n for n in names if n not in STDLIB
                                  and n not in self.local)
        # the packages the game needs: imported by pac-man.py or by a local
        # module it imports (a build script may import PyInstaller)
        reach = self.reachable()
        self.required = sorted({u.name for u in self.imports
                                if u.name in self.third_party
                                and not u.optional and u.rel in reach})
        self.archives = self._archives()
        self.archive_mods = sorted({m for a in self.archives
                                    for m in archive_modules(
                                        os.path.join(path, a))})
        self.config_rel: Optional[str] = None
        self.config_raw: Optional[str] = None
        self.config_data: Optional[Any] = None
        self.config_note: Optional[str] = None
        self.readme = read_text(os.path.join(path, "README.md"))

    def reachable(self) -> Set[str]:
        """Sources reached from pac-man.py through local imports (every
        non-test source when there is no pac-man.py)."""
        by_rel = {s.rel: s for s in self.sources}
        if MAIN not in by_rel:
            return {s.rel for s in self.sources if not s.is_test}
        seen = {MAIN}
        todo = [MAIN]
        while todo:
            src = by_rel[todo.pop()]
            if src.tree is None:
                continue
            here = os.path.dirname(src.rel)
            wanted: List[str] = []
            for node in ast.walk(src.tree):
                if isinstance(node, ast.Import):
                    wanted += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        base = here
                        for _ in range(node.level - 1):
                            base = os.path.dirname(base)
                        mod = (node.module or "").replace(".", "/")
                        rel = os.path.normpath(os.path.join(base, mod)) \
                            if mod else base
                        wanted.append(rel.replace(os.sep, "/").replace(
                            "/", "."))
                        wanted += [rel.replace(os.sep, ".") + "." + a.name
                                   for a in node.names]
                    elif node.module:
                        wanted.append(node.module)
                        wanted += [node.module + "." + a.name
                                   for a in node.names]
            for name in wanted:
                parts = [p for p in name.split(".") if p and p != "."]
                for i in range(1, len(parts) + 1):
                    stem = os.path.join(*parts[:i])
                    for cand in (stem + ".py",
                                 os.path.join(stem, "__init__.py"),
                                 os.path.join(here, stem + ".py")):
                        cand = os.path.normpath(cand)
                        if cand in by_rel and cand not in seen:
                            seen.add(cand)
                            todo.append(cand)
        return seen

    def _scan(self) -> None:
        for root, dirs, files in os.walk(self.path):
            rel_root = os.path.relpath(root, self.path)
            depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
            dirs[:] = sorted(
                d for d in dirs if d not in SKIP_DIRS
                and not d.startswith(".") and depth < 6
                and not os.path.isfile(os.path.join(root, d, "pyvenv.cfg")))
            for d in dirs:
                self.dirs.add(os.path.normpath(os.path.join(rel_root, d)))
            for f in sorted(files):
                if not f.endswith(".py"):
                    continue
                full = os.path.join(root, f)
                text = read_text(full)
                if text is None:
                    continue
                rel = os.path.normpath(os.path.join(rel_root, f))
                self.sources.append(Source(rel, text))

    def _local_names(self) -> Set[str]:
        names = set()
        for s in self.sources:
            parts = s.rel.replace(os.sep, "/").split("/")
            names.add(parts[-1][:-3])
            names.update(parts[:-1])
        return names

    def _archives(self) -> List[str]:
        found = []
        for root, dirs, files in os.walk(self.path):
            rel_root = os.path.relpath(root, self.path)
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                       and not d.startswith(".") and rel_root.count(
                           os.sep) < 2]
            for f in files:
                if re.match(r"mazegen[-_].*\.(whl|tar\.gz|zip)$", f, re.I):
                    found.append(os.path.normpath(os.path.join(rel_root,
                                                               f)))
        return sorted(found)

    def game_sources(self) -> List[Source]:
        return [s for s in self.sources if not s.is_test]

    def all_text(self) -> str:
        return "\n".join(s.text for s in self.game_sources())


def archive_modules(path: str) -> List[str]:
    """Top-level modules of a wheel / sdist, read without installing."""
    mods: Set[str] = set()
    try:
        if path.endswith((".whl", ".zip")):
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                top = [n for n in names if n.endswith("top_level.txt")]
                if top:
                    text = z.read(top[0]).decode("utf-8", "replace")
                    mods.update(t.strip() for t in text.split() if t.strip())
                for n in names:
                    first = n.split("/")[0]
                    if first.endswith((".dist-info", ".data")):
                        continue
                    if "/" in n or n.endswith(".py"):
                        mods.add(first[:-3] if first.endswith(".py")
                                 else first)
        elif path.endswith(".tar.gz"):
            with tarfile.open(path) as t:
                for n in t.getnames():
                    parts = n.split("/")
                    if parts[-1] != "__init__.py" or len(parts) < 3:
                        continue
                    inner = parts[1:-1]
                    if inner and inner[0] == "src":
                        inner = inner[1:]
                    if len(inner) == 1:
                        mods.add(inner[0])
    except (OSError, zipfile.BadZipFile, tarfile.TarError, KeyError,
            EOFError):
        return []
    return sorted(m for m in mods if m and m.isidentifier()
                  and m not in ("tests", "test"))


# --------------------------------------------------------------------------
# Makefile reading (dry run in a throwaway copy)
# --------------------------------------------------------------------------

class Dry:
    """`make -n <rule>` in a copy of the project (a Makefile can run
    $(shell ...) even in dry-run mode)."""

    def __init__(self, project: str, keep: bool) -> None:
        self.project = project
        self.keep = keep
        self.ws: Optional[core.Workspace] = None
        self.cache: Dict[str, Tuple[int, str]] = {}

    def recipe(self, target: str) -> Tuple[int, str]:
        if target in self.cache:
            return self.cache[target]
        if not core.have("make"):
            self.cache[target] = (127, "")
            return self.cache[target]
        if self.ws is None:
            self.ws = core.Workspace(self.project, keep=self.keep,
                                     prefix="42next-pac-dry-")
        res = core.run(["make", "-n", target], cwd=self.ws.dir, timeout=30)
        self.cache[target] = (res.rc if res.rc is not None else 1,
                              core.output(res))
        return self.cache[target]

    def close(self) -> None:
        if self.ws is not None:
            self.ws.__exit__(None, None, None)


def find_config(proj: Project, dry: Dry) -> None:
    """The config file the project uses: from `make -n run`, else the
    most config-looking .json file at the root."""
    candidates: List[str] = []
    if "run" in proj.targets:
        rc, text = dry.recipe("run")
        for m in re.finditer(r"pac-man\.py\s+(['\"]?)([^\s'\";|&]+)\1",
                             text):
            arg = m.group(2)
            if os.path.isfile(os.path.join(proj.path, arg)):
                candidates.append(os.path.normpath(arg))
    names = []
    for d in ("", "config", "configs", "conf"):
        base = os.path.join(proj.path, d)
        if not os.path.isdir(base):
            continue
        for f in sorted(os.listdir(base)):
            low = f.lower()
            if not low.endswith((".json", ".jsonc", ".json5")):
                continue
            if any(w in low for w in ("score", "package", "tsconfig",
                                      "record", "leaderboard", "save")):
                continue
            names.append(os.path.normpath(os.path.join(d, f)))
    names.sort(key=lambda n: (os.path.basename(n).lower() != "config.json",
                              "conf" not in n.lower(), n))
    candidates += names
    for rel in dict.fromkeys(candidates):
        raw = read_text(os.path.join(proj.path, rel))
        if raw is None:
            continue
        data = parse_config_text(raw)
        if isinstance(data, dict):
            proj.config_rel, proj.config_raw, proj.config_data = \
                rel, raw, data
            return
        if proj.config_note is None:
            proj.config_note = (f"{rel} cannot be read as JSON with # "
                                "comment lines")
    proj.config_data = copy.deepcopy(DEFAULT_CONFIG)


# --------------------------------------------------------------------------
# runtime: temporary venv, headless runs
# --------------------------------------------------------------------------

DRIVER = r'''"""42next-pac-driver: runs pac-man.py and sends key presses."""
import os
import random
import runpy
import sys
import time

SCRIPT = sys.argv[1]
sys.argv = sys.argv[1:]
sys.path[0] = os.path.dirname(os.path.abspath(SCRIPT))
MODE = os.environ.get("NEXT42_PAC_MODE", "")
RNG = random.Random(int(os.environ.get("NEXT42_PAC_SEED", "0") or 0))
LOG = os.environ.get("NEXT42_PAC_LOG", "")


def _setup():
    try:
        import pygame
    except Exception:
        return
    ev = pygame.event
    orig = {n: getattr(ev, n) for n in ("get", "poll", "pump", "peek")}
    orig_flip = pygame.display.flip
    orig_update = pygame.display.update
    held = set()
    st = {"t0": None, "next": 0.0, "pos": (5, 5), "ups": [], "step": 0,
          "mouse": False}
    log = []

    def note(text):
        t = time.monotonic() - (st["t0"] or time.monotonic())
        log.append(f"{t:6.2f}s {text}")
        if LOG:
            try:
                with open(LOG, "a") as fh:
                    fh.write(log[-1] + "\n")
            except OSError:
                pass

    def post(e):
        try:
            ev.post(e)
        except Exception:
            pass

    def kname(k):
        try:
            return pygame.key.name(k) or str(k)
        except Exception:
            return str(k)

    def press(k, uni=""):
        held.add(k)
        post(ev.Event(pygame.KEYDOWN, key=k, unicode=uni, mod=0,
                      scancode=0))
        if uni and uni.isprintable():
            post(ev.Event(pygame.TEXTINPUT, text=uni))
        st["ups"].append((time.monotonic() + 0.1, k, uni))
        note("key " + (repr(uni) if uni and kname(k) != uni.lower()
                       else kname(k)))

    def mouse():
        surf = pygame.display.get_surface()
        w, h = surf.get_size() if surf is not None else (100, 100)
        pos = (RNG.randrange(max(w, 1)), RNG.randrange(max(h, 1)))
        st["pos"] = pos
        post(ev.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0),
                      buttons=(0, 0, 0), touch=False))
        post(ev.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1,
                      touch=False))
        post(ev.Event(pygame.MOUSEBUTTONUP, pos=pos, button=1, touch=False))
        note(f"mouse click at {pos}")

    k = pygame
    moves = [k.K_UP, k.K_DOWN, k.K_LEFT, k.K_RIGHT,
             k.K_w, k.K_a, k.K_s, k.K_d]
    chars = list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                 "0123456789 !?@#-_.,/") + ["\u00e9", "\u00df"]
    specials = [k.K_RETURN, k.K_ESCAPE, k.K_p, k.K_SPACE, k.K_BACKSPACE,
                k.K_TAB, k.K_DELETE, k.K_n, k.K_f, k.K_l, k.K_c, k.K_g,
                k.K_i, k.K_h, k.K_m, k.K_r, k.K_q, k.K_KP_ENTER,
                k.K_PAGEUP, k.K_HOME, k.K_END] + [getattr(k, f"K_F{i}")
                                                   for i in range(1, 13)]

    def uni_of(key):
        if key in (k.K_RETURN, k.K_KP_ENTER):
            return "\r"
        if key == k.K_SPACE:
            return " "
        if key == k.K_BACKSPACE:
            return "\b"
        if key == k.K_ESCAPE:
            return "\x1b"
        if key == k.K_TAB:
            return "\t"
        if 97 <= key <= 122:
            return chr(key)
        return ""

    def fuzz_once():
        r = RNG.random()
        if r < 0.55:
            key = RNG.choice(moves)
            press(key, uni_of(key))
        elif r < 0.62:
            press(k.K_RETURN, "\r")
        elif r < 0.80:
            c = RNG.choice(chars)
            key = ord(c.lower()) if c.isascii() else k.K_UNKNOWN
            press(key, c)
        elif r < 0.84:
            press(k.K_BACKSPACE, "\b")
        elif r < 0.95:
            key = RNG.choice(specials)
            press(key, uni_of(key))
        else:
            mouse()

    def feed():
        try:
            if not pygame.display.get_init() \
                    or pygame.display.get_surface() is None:
                return
        except Exception:
            return
        now = time.monotonic()
        if st["t0"] is None:
            st["t0"] = now
            st["next"] = now + 1.0
        for item in [u for u in st["ups"] if u[0] <= now]:
            st["ups"].remove(item)
            held.discard(item[1])
            post(ev.Event(pygame.KEYUP, key=item[1], unicode=item[2],
                          mod=0, scancode=0))
        if now < st["next"]:
            return
        step = st["step"]
        st["step"] += 1
        if MODE == "menuquit":
            post(ev.Event(pygame.QUIT))
            note("window close (QUIT event)")
            st["next"] = float("inf")
            return
        if step == 0:
            press(k.K_RETURN, "\r")
            st["next"] = now + 0.8
            return
        if MODE == "quit":
            post(ev.Event(pygame.QUIT))
            note("window close (QUIT event)")
            st["next"] = float("inf")
        elif MODE == "fuzz":
            fuzz_once()
            st["next"] = now + RNG.uniform(0.03, 0.2)
        else:
            key = RNG.choice(moves)
            press(key, uni_of(key))
            st["next"] = now + 0.25

    def get(*a, **kw):
        feed()
        return orig["get"](*a, **kw)

    def poll(*a, **kw):
        feed()
        return orig["poll"](*a, **kw)

    def pump(*a, **kw):
        feed()
        return orig["pump"](*a, **kw)

    def peek(*a, **kw):
        feed()
        return orig["peek"](*a, **kw)

    def wait(*a, **kw):
        timeout = a[0] if a else kw.get("timeout", 0)
        end = time.monotonic() + timeout / 1000.0 if timeout else None
        while True:
            feed()
            e = orig["poll"]()
            if e.type != pygame.NOEVENT:
                return e
            if end is not None and time.monotonic() >= end:
                return e
            time.sleep(0.01)

    def flip(*a, **kw):
        feed()
        return orig_flip(*a, **kw)

    def update(*a, **kw):
        feed()
        return orig_update(*a, **kw)

    class Pressed:
        def __getitem__(self, key):
            return key in held

        def __len__(self):
            return 512

        def __iter__(self):
            return iter([i in held for i in range(512)])

    ev.get, ev.poll, ev.pump, ev.peek, ev.wait = get, poll, pump, peek, wait
    pygame.display.flip, pygame.display.update = flip, update
    pygame.key.get_pressed = lambda: Pressed()
    pygame.mouse.get_pos = lambda: st["pos"]


if MODE:
    _setup()
runpy.run_path(SCRIPT, run_name="__main__")
'''

# Stand-in for the A-Maze-ing package: every use of it fails.
FAKE_MAZE = r'''"""42next: stand-in package whose generator always fails."""
import importlib.abc
import importlib.machinery
import os
import sys
import types

_MARK = os.environ.get("NEXT42_PAC_MARK", "")
_KIND = os.environ.get("NEXT42_PAC_FAIL", "RuntimeError")
if _KIND == "import":
    raise ModuleNotFoundError("No module named %r (42next test: the "
                              "package is not installed)" % __name__)
_ERR = {"ValueError": ValueError}.get(_KIND, RuntimeError)


def _touch(what):
    if _MARK:
        try:
            with open(_MARK, "a") as fh:
                fh.write(what + "\n")
        except OSError:
            pass


def _fail(*args, **kwargs):
    _touch("call")
    raise _ERR("maze generation failed (42next test)")


class _Meta(type):
    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _fail


class _Boom(metaclass=_Meta):
    def __init__(self, *args, **kwargs):
        _fail()

    def __getattr__(self, name):
        return _fail


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    _touch("attr " + name)
    return _Boom


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(__name__ + "."):
            return importlib.machinery.ModuleSpec(fullname, self,
                                                  is_package=True)
        return None

    def create_module(self, spec):
        mod = types.ModuleType(spec.name)
        mod.__path__ = []
        mod.__getattr__ = __getattr__
        return mod

    def exec_module(self, module):
        pass


sys.meta_path.insert(0, _Finder())
'''


class Outcome:
    def __init__(self, rc: Optional[int], out: str, alive: bool,
                 elapsed: float, events: List[str]) -> None:
        self.rc = rc
        self.out = out
        self.alive = alive
        self.elapsed = elapsed
        self.events = events

    @property
    def traceback(self) -> bool:
        return TRACEBACK in self.out

    @property
    def crashed(self) -> bool:
        return self.rc is not None and self.rc < 0 and not self.alive

    def lines(self) -> Set[str]:
        return {ln.strip() for ln in self.out.splitlines() if ln.strip()}

    def got(self) -> str:
        if self.traceback:
            return "a Python traceback:\n" + traceback_tail(self.out)
        if self.crashed:
            try:
                name = signal.Signals(-(self.rc or 0)).name
            except ValueError:
                name = f"signal {-(self.rc or 0)}"
            return f"crashed with {name} after {self.elapsed:.1f}s"
        if self.alive:
            return f"still running after {self.elapsed:.1f}s"
        said = lines_of(self.out, 4)
        return (f"exit code {self.rc} after {self.elapsed:.1f}s"
                + (f", output:\n{said}" if said else ", no output"))


class Case:
    """One run of the game."""

    def __init__(self, label: str, args: List[str], expect: str = "any",
                 quote: str = "", show: Sequence[str] = (),
                 mode: str = "start", seed: int = 0,
                 timeout: Optional[float] = None,
                 extra_env: Optional[Dict[str, str]] = None,
                 cwd: Optional[str] = None, message: bool = False,
                 pythonpath: Optional[str] = None,
                 shown_args: Optional[List[str]] = None) -> None:
        self.label = label
        self.args = args
        self.expect = expect        # "continue", "exit", "any"
        self.quote = quote
        self.show = list(show)
        self.mode = mode
        self.seed = seed
        self.timeout = timeout
        self.extra_env = extra_env or {}
        self.cwd = cwd
        self.message = message
        self.pythonpath = pythonpath
        self.shown_args = shown_args if shown_args is not None else args

    def cmdline(self) -> str:
        return "python3 " + " ".join(shlex.quote(a) for a in
                                     [MAIN] + self.shown_args)


class Env:
    """Temporary copy + venv with the project's dependencies."""

    def __init__(self, proj: Project, args: Any, dry: Dry) -> None:
        self.proj = proj
        self.args = args
        self.dry = dry
        self.done = False
        self.ws: Optional[core.Workspace] = None
        self.venv: Optional[str] = None
        self.user_mode = False
        self.python: Optional[str] = None
        self.install_status = "skip"     # ok / ko / skip / norule
        self.install_cmd = "make install"
        self.install_out = ""
        self.missing: List[str] = []
        self.backend: Optional[str] = None
        self.reason: Optional[str] = None
        self.alive_time = 3.5 if args.quick else 5.0
        self._baseline: Optional[Outcome] = None
        self._counter = 0
        self._lock = threading.Lock()
        self.installed_maze: List[str] = []

    # -- setup ------------------------------------------------------------

    def tmp(self, name: str) -> str:
        assert self.ws is not None
        return self.ws.tmp(name)

    def base_env(self) -> Dict[str, str]:
        e = dict(os.environ)
        for k in ("DISPLAY", "WAYLAND_DISPLAY", "PYTHONHOME", "PYTHONPATH",
                  "PYTHONSTARTUP", "PIP_USER", "PIP_REQUIRE_VIRTUALENV",
                  "VIRTUAL_ENV", "CONDA_PREFIX"):
            e.pop(k, None)
        e["PYTHONDONTWRITEBYTECODE"] = "1"
        e["PYTHONUSERBASE"] = self.tmp("userbase")
        e["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        e["PIP_NO_INPUT"] = "1"
        if self.venv and not self.user_mode:
            e["VIRTUAL_ENV"] = self.venv
            e["PATH"] = (os.path.join(self.venv, "bin") + os.pathsep
                         + e.get("PATH", ""))
        if self.user_mode:
            e["PIP_USER"] = "1"
            e["PIP_BREAK_SYSTEM_PACKAGES"] = "1"
            e["PATH"] = (os.path.join(self.tmp("userbase"), "bin")
                         + os.pathsep + e.get("PATH", ""))
        return e

    def game_env(self, extra: Optional[Dict[str, str]] = None,
                 pythonpath: Optional[str] = None) -> Dict[str, str]:
        e = self.base_env()
        home = self.tmp("home")
        os.makedirs(home, exist_ok=True)
        e.update({"HOME": home,
                  "XDG_CONFIG_HOME": os.path.join(home, ".config"),
                  "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
                  "XDG_CACHE_HOME": os.path.join(home, ".cache"),
                  "PYTHONUNBUFFERED": "1",
                  "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy",
                  "PYGAME_HIDE_SUPPORT_PROMPT": "1", "NO_AT_BRIDGE": "1",
                  "LIBGL_ALWAYS_SOFTWARE": "1"})
        if pythonpath:
            e["PYTHONPATH"] = pythonpath
        if extra:
            e.update(extra)
        return e

    def setup(self) -> None:
        if self.done:
            return
        self.done = True
        self.ws = core.Workspace(self.proj.path, keep=self.args.keep,
                                 prefix="42next-pac-")
        os.makedirs(self.tmp("userbase"), exist_ok=True)
        os.makedirs(self.tmp("cases"), exist_ok=True)
        SUBST[:] = [(self.tmp("cases") + os.sep, ""),
                    (self.ws.dir + os.sep, ""),
                    (self.tmp("venv"), "<venv>"), (self.ws.root, "<tmp>")]
        with open(self.tmp("42next-pac-driver.py"), "w") as fh:
            fh.write(DRIVER)
        venv = self.tmp("venv")
        res = core.run([sys.executable, "-m", "venv",
                        "--system-site-packages", venv], timeout=180)
        if res.rc == 0 and os.path.isfile(os.path.join(venv, "bin",
                                                       "python")):
            self.venv = venv
        else:
            self.user_mode = True
        self._install()
        self._pick_python()
        if self.python is None:
            return
        self._check_imports()
        self._pick_backend()

    def _install(self) -> None:
        assert self.ws is not None
        if "install" in self.proj.targets and core.have("make"):
            res = core.run(["make", "install"], cwd=self.ws.dir,
                           env=self.base_env(), timeout=900)
            out = core.output(res)
            if res.rc != 0 and not self.user_mode \
                    and re.search(r"--user.*(virtualenv|venv)|User "
                                  r"site-packages are not visible", out):
                # `pip install --user`: works outside a venv, so try
                # again with a temporary user base instead of the venv
                self.user_mode = True
                res = core.run(["make", "install"], cwd=self.ws.dir,
                               env=self.base_env(), timeout=900)
                out = core.output(res)
            self.install_out = out
            if res.timed_out:
                self.install_status = "ko"
                self.install_out = "timeout after 900s\n" + out
            elif res.rc == 0:
                self.install_status = "ok"
            elif NETWORK_ERR.search(out):
                self.install_status = "skip"
            else:
                self.install_status = "ko"
            return
        # no rule: install what can be found, only to run the other checks
        self.install_status = "norule"
        pip = [self._venv_python(), "-m", "pip", "install", "-q"]
        steps = []
        req = os.path.join(self.ws.dir, "requirements.txt")
        if os.path.isfile(req):
            steps.append(pip + ["-r", "requirements.txt"])
        for a in self.proj.archives:
            steps.append(pip + [a])
        if os.path.isfile(os.path.join(self.ws.dir, "pyproject.toml")) \
                and not steps:
            steps.append(pip + ["."])
        self.install_cmd = " ; ".join(" ".join(s[2:]) for s in steps) \
            or "(nothing to install)"
        outs = []
        for s in steps:
            res = core.run(s, cwd=self.ws.dir, env=self.base_env(),
                           timeout=900)
            outs.append(core.output(res))
        self.install_out = "\n".join(outs)

    def _venv_python(self) -> str:
        if self.venv and not self.user_mode:
            return os.path.join(self.venv, "bin", "python")
        return sys.executable

    def _pick_python(self) -> None:
        assert self.ws is not None
        for d in (".venv", "venv", "env", ".env"):
            p = os.path.join(self.ws.dir, d, "bin", "python")
            if os.path.isfile(p):
                self.python = p
                return
        self.python = self._venv_python()

    def _check_imports(self) -> None:
        mods = [m for m in self.proj.required if m not in self.proj.local]
        code = ("import importlib, sys\n"
                "bad = []\n"
                "for m in sys.argv[1:]:\n"
                "    try:\n"
                "        importlib.import_module(m)\n"
                "    except Exception as e:\n"
                "        bad.append(m + ': ' + type(e).__name__)\n"
                "print('\\n'.join(bad))\n"
                "import importlib.metadata as md\n"
                "out = set()\n"
                "for d in md.distributions():\n"
                "    n = (d.metadata['Name'] or '').lower()\n"
                "    if not n.replace('_', '-').startswith('mazegen'):\n"
                "        continue\n"
                "    t = d.read_text('top_level.txt')\n"
                "    if t:\n"
                "        out.update(t.split())\n"
                "    for f in d.files or []:\n"
                "        p = f.parts\n"
                "        if p and not p[0].endswith(('.dist-info', "
                "'.data')) and str(f).endswith('.py'):\n"
                "            out.add(p[0][:-3] if p[0].endswith('.py') "
                "else p[0])\n"
                "print('MAZE ' + ' '.join(sorted(out)))\n")
        assert self.python is not None
        res = core.run([self.python, "-B", "-c", code, *mods],
                       cwd=self.tmp("home"), env=self.game_env(),
                       timeout=120)
        text = core.output(res)
        self.missing = []
        for ln in text.splitlines():
            if ln.startswith("MAZE "):
                self.installed_maze = [m for m in ln[5:].split()
                                       if m.isidentifier()]
            elif ": " in ln and ln.split(":")[0] in mods:
                self.missing.append(ln.strip())
        if res.rc != 0 and not self.missing and "MAZE" not in text:
            self.missing = [f"python check failed: {lines_of(text, 3)}"]

    def _pick_backend(self) -> None:
        if not self.proj.has_main:
            self.reason = f"no {MAIN} at the root"
            return
        if self.missing:
            what = {"ok": "after make install",
                    "ko": "make install failed",
                    "skip": "make install could not download (offline?)",
                    "norule": "no install rule"}[self.install_status]
            self.reason = (f"cannot import {cap(self.missing, 4)} ({what})")
            return
        if "pygame" in {u.name for u in self.proj.imports}:
            self.backend = "pygame"
        elif shutil.which("xvfb-run"):
            self.backend = "xvfb"
        else:
            self.backend = "plain"

    def mazes(self) -> List[str]:
        """Module names of the A-Maze-ing package the project imports."""
        names = set(self.installed_maze) | set(self.proj.archive_mods)
        imported = {u.name for u in self.proj.imports}
        used = sorted(n for n in names if n in imported)
        if used:
            return used
        return sorted(n for n in self.proj.third_party if "maze" in
                      n.lower())

    # -- running ------------------------------------------------------------

    def command(self, case: Case) -> List[str]:
        assert self.python is not None
        if self.backend == "pygame" and case.mode:
            cmd = [self.python, "-B", self.tmp("42next-pac-driver.py"),
                   MAIN] + case.args
        else:
            cmd = [self.python, "-B", MAIN] + case.args
        if self.backend == "xvfb":
            cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x1024x24"] + cmd
        if shutil.which("bash"):
            # a runaway allocation (huge maze) must not take the machine
            cmd = ["bash", "-c", 'ulimit -v 4194304 2>/dev/null; exec "$@"',
                   "42next"] + cmd
        return cmd

    def launch(self, case: Case, cmd: Optional[List[str]] = None,
               stdin: bytes = b"") -> Outcome:
        assert self.ws is not None
        with self._lock:
            self._counter += 1
            n = self._counter
        out_path = self.tmp(f"out-{n}.txt")
        log_path = self.tmp(f"events-{n}.txt")
        env = self.game_env({"NEXT42_PAC_MODE": case.mode,
                             "NEXT42_PAC_SEED": str(case.seed),
                             "NEXT42_PAC_LOG": log_path,
                             **case.extra_env}, case.pythonpath)
        timeout = case.timeout or self.alive_time
        start = time.monotonic()
        alive = False
        rc: Optional[int] = None
        with open(out_path, "wb") as out:
            try:
                proc = subprocess.Popen(
                    cmd or self.command(case), cwd=case.cwd or self.ws.dir,
                    stdin=subprocess.PIPE, stdout=out,
                    stderr=subprocess.STDOUT, env=env,
                    start_new_session=True)
            except OSError as exc:
                return Outcome(127, str(exc), False, 0.0, [])
            try:
                if stdin and proc.stdin is not None:
                    proc.stdin.write(stdin)
                    proc.stdin.flush()
            except OSError:
                pass
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                alive = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                proc.wait()
            finally:
                if proc.stdin is not None:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
        elapsed = time.monotonic() - start
        text = read_text(out_path, 1 << 22) or ""
        if len(text) > 200000:
            text = text[-200000:]
        events = (read_text(log_path) or "").splitlines()
        return Outcome(None if alive else rc, text, alive, elapsed, events)

    def run_all(self, cases: List[Case]) -> List[Outcome]:
        workers = max(2, min(8, (os.cpu_count() or 4) // 2))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(self.launch, cases))

    def write_case(self, name: str, content: Any) -> str:
        path = os.path.join(self.tmp("cases"), name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = content if isinstance(content, bytes) else \
            str(content).encode("utf-8")
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def config_arg(self) -> List[str]:
        if self.proj.config_rel:
            return [self.proj.config_rel]
        return [self.write_case("default-config.json",
                                json.dumps(DEFAULT_CONFIG, indent=2))]

    def baseline(self) -> Outcome:
        if self._baseline is None:
            self._baseline = self.launch(Case("baseline", self.config_arg()))
        return self._baseline

    def runtime(self) -> Optional[str]:
        """None when the game can be run, else why not."""
        self.setup()
        if self.reason:
            return self.reason
        base = self.baseline()
        if not base.alive and (DISPLAY_ERR.search(base.out)
                               and self.backend != "pygame"):
            hint = ("install xvfb (sudo apt install xvfb)"
                    if self.backend == "plain" else "even under xvfb-run")
            self.reason = (f"the game needs a display: {hint}; it said: "
                           + lines_of(base.out, 2))
        return self.reason

    def close(self) -> None:
        if self.ws is not None:
            if self.args.keep:
                print(core.dim(f"generated files: {self.tmp('cases')}"))
            self.ws.__exit__(None, None, None)


# --------------------------------------------------------------------------
# judging one run
# --------------------------------------------------------------------------

EXPECT = {
    "continue": "no traceback, the game keeps running",
    "exit": "a clear message and an exit, no traceback",
    "any": "a clear message (exit or keep running), no traceback",
    "nocrash": "no traceback (the game may close or keep running)",
}

# temporary paths shown as the student knows them (set by Env.setup)
SUBST: List[Tuple[str, str]] = []


def unpath(text: str) -> str:
    for a, b in SUBST:
        text = text.replace(a, b)
    return text


def judge(rep: core.Report, case: Case, out: Outcome,
          base: Optional[Outcome]) -> bool:
    detail = ["command: " + case.cmdline()] + [
        "  " + s for s in case.show]
    if out.events and (out.traceback or out.crashed):
        detail.append("last key presses sent: "
                      + "; ".join(e.strip() for e in out.events[-10:]))
    if case.quote:
        detail.append("subject: " + case.quote)
    bad = out.traceback or out.crashed
    if not bad and case.expect == "continue" and base is not None \
            and base.alive and not out.alive:
        bad = True
    got = unpath(out.got()).replace("\n", "\n" + " " * 17)
    return rep.check(case.label, not bad, expected=EXPECT[case.expect]
                     if bad else None, got=got if bad else None,
                     detail="\n".join(detail) if bad else None)


def new_output(out: Outcome, base: Optional[Outcome]) -> Set[str]:
    return out.lines() - (base.lines() if base else set())


def runtime_or_skip(rep: core.Report, env: Env, what: str) -> bool:
    why = env.runtime()
    if why:
        rep.skip(what, why)
        return False
    return True


def baseline_note(rep: core.Report, env: Env) -> None:
    base = env.baseline()
    if not base.alive and not base.traceback:
        rep.info("with its own config the game stops after "
                 f"{base.elapsed:.1f}s, so the 'keeps running' checks are "
                 "replaced by 'no traceback' only")


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------

PACKAGING_NAME = re.compile(r"(\.spec$|build|packag|release|deploy|bundle|"
                            r"pyinstaller|nuitka|itch|butler|installer|"
                            r"freeze|setup\.py$|dist\.)", re.I)
PACKAGING_TOOL = re.compile(r"pyinstaller|nuitka|cx_freeze|cx-freeze|"
                            r"briefcase|pygbag|butler|py2exe|py2app|zipapp|"
                            r"pyoxidizer|\bshiv\b|\bpex\b|appimage|flatpak|"
                            r"itch\.io", re.I)
PM_NAME = re.compile(r"project|manag|pm$|^pm|gestion|planning|kanban|gantt|"
                     r"organi[sz]ation|docs?$|documentation", re.I)


def pm_directories(proj: Project) -> List[str]:
    out = []
    for d in sorted(os.listdir(proj.path)):
        full = os.path.join(proj.path, d)
        if not os.path.isdir(full) or d.startswith(".") or d in SKIP_DIRS:
            continue
        if PM_NAME.search(d) and any(files for _, _, files in
                                     os.walk(full)):
            out.append(d)
    return out


def group_files(rep: core.Report, proj: Project) -> None:
    rep.section("files", gid="files")
    rep.check(f"{MAIN} at the root", proj.has_main,
              expected=f"{MAIN} (the program is launched with "
                       f"python3 {MAIN} config.json)",
              got=None if proj.has_main else "not found",
              detail=None if proj.has_main else "subject: " + Q["usage"])
    rep.check("Makefile at the root", proj.make is not None,
              detail=None if proj.make else "subject: " + Q["makefile"])
    gi = read_text(os.path.join(proj.path, ".gitignore"))
    rep.check(".gitignore at the root", gi is not None,
              detail=None if gi is not None else "subject: "
              + Q["gitignore"])
    if gi is not None and not re.search(r"__pycache__|\*\.py[cod\[]|"
                                        r"\.pyc", gi):
        rep.warn(".gitignore does not exclude __pycache__ / *.pyc",
                 "subject: " + Q["gitignore"])
    if proj.config_rel:
        rep.check(f"config file: {proj.config_rel}", True)
    else:
        rep.warn("no config file found in the repository (a config with "
                 "the subject's suggested keys is used for the tests)",
                 proj.config_note or "the subject runs python3 pac-man.py "
                 "config.json")
    if proj.config_rel and isinstance(proj.config_data, dict):
        for path, node in containers(proj.config_data):
            name = str(path[-1]).lower()
            if len(path) == 1 and isinstance(node, list) \
                    and "level" in name and len(node) < 10:
                rep.warn(f'config "{path[0]}" lists {len(node)} level(s)',
                         "subject: " + Q["levels"])
    mention = []
    for f in ("requirements.txt", "pyproject.toml", "setup.cfg",
              "setup.py", "Makefile", "Pipfile"):
        text = read_text(os.path.join(proj.path, f))
        if text and re.search(r"mazegen", text, re.I):
            mention.append(f)
    if proj.archives:
        rep.check("A-Maze-ing package: " + cap(proj.archives, 3), True)
    elif mention:
        rep.check("A-Maze-ing package referenced in " + cap(mention), True)
    else:
        rep.warn("no mazegen-* package file (.whl / .tar.gz) and no mazegen "
                 "dependency found",
                 "subject: " + Q["external"] + "\n" + Q["as_is"]
                 + "\n(the A-Maze-ing subject names the package mazegen-*)")
    rootfiles = sorted(f for f in os.listdir(proj.path)
                       if os.path.isfile(os.path.join(proj.path, f)))
    by_name = [f for f in rootfiles if PACKAGING_NAME.search(f)
               and not f.lower().startswith("readme")]
    by_tool = []
    for f in rootfiles:
        if f.endswith((".py", ".sh", ".spec", ".toml", ".cfg", ".txt",
                       ".bat", ".ps1", ".yml", ".yaml", ".json")) \
                or f in ("Makefile", "makefile", "Dockerfile"):
            text = read_text(os.path.join(proj.path, f), 1 << 18) or ""
            if PACKAGING_TOOL.search(text):
                by_tool.append(f)
    pack_rules = sorted(t for t in proj.targets if re.fullmatch(
        r"(package|build|dist|bundle|release|exe|deploy|itch|executable|"
        r"pyinstaller|wheel)", t))
    found = by_name or by_tool
    rep.check("packaging script / spec at the root", bool(found),
              expected="a packaging script or spec (PyInstaller .spec, "
                       "build script, ...) at the root",
              got=None if found else "nothing found (no .spec, no build / "
              "package script, no packaging tool named in a root file)"
              + (f"; Makefile rule(s): {', '.join(pack_rules)}"
                 if pack_rules else ""),
              detail=None if found else "subject: " + Q["packaging"])
    if found and rep.verbose:
        rep.info("packaging: " + cap(list(dict.fromkeys(found))))
    pm = pm_directories(proj)
    linked = readme_pm_links(proj)
    ok = bool(pm or linked)
    rep.check("project management folder", ok,
              expected="a dedicated subdirectory with the project "
                       "management documents",
              got=None if ok else "no such folder (and no folder linked "
                                  "from the README)",
              detail=None if ok else "subject: " + Q["pm_dir"])


# --------------------------------------------------------------------------
# README
# --------------------------------------------------------------------------

def md_sections(text: str) -> List[Tuple[int, str, str]]:
    """(level, title, body) of every markdown heading."""
    out: List[Tuple[int, str, str]] = []
    cur: Optional[List[Any]] = None
    fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            if cur:
                out.append((cur[0], cur[1], "\n".join(cur[2])))
            cur = [len(m.group(1)), m.group(2).strip(), []]
        elif cur is not None:
            cur[2].append(line)
    if cur:
        out.append((cur[0], cur[1], "\n".join(cur[2])))
    return out


def section_body(text: str, words: Sequence[str]) -> Optional[str]:
    """Body of the first heading containing one of words, with its
    sub-sections."""
    secs = md_sections(text)
    for i, (lvl, title, body) in enumerate(secs):
        if any(w in title.lower() for w in words):
            parts = [body]
            for lvl2, title2, body2 in secs[i + 1:]:
                if lvl2 <= lvl:
                    break
                parts.append(title2 + "\n" + body2)
            return "\n".join(parts)
    return None


def readme_pm_links(proj: Project) -> List[str]:
    if not proj.readme:
        return []
    body = section_body(proj.readme, ("project management", "management"))
    if body is None:
        return []
    out = []
    for m in re.finditer(r"\]\(\s*<?([^)\s>]+)>?\s*\)|href=[\"']([^\"']+)",
                         body):
        target = (m.group(1) or m.group(2) or "").split("#")[0]
        target = re.sub(r"^(\./)+", "", target)
        if not target or re.match(r"[a-z]+://", target):
            gh = re.search(r"/(?:tree|blob)/[^/]+/([^?#]+)", target or "")
            if not gh:
                continue
            target = gh.group(1)
        if os.path.isdir(os.path.join(proj.path, target.rstrip("/"))):
            out.append(target.rstrip("/"))
    return out


README_SECTIONS = [
    ("Configuration", ("configuration", "config"), "readme_cfg"),
    ("Highscore", ("highscore", "high score", "high-score", "hiscore"),
     "readme_hs"),
    ("Maze Generation", ("maze generation", "maze gen", "maze"),
     "readme_maze"),
    ("Implementation", ("implementation",), "readme_impl"),
    ("General Software Architecture", ("architecture",), "readme_arch"),
    ("Project Management", ("project management", "management"),
     "readme_pm"),
]
FR_WORDS = re.compile(r"\b(le|la|les|des|est|et|pour|avec|une|dans|sur|"
                      r"nous|vous|pas|qui|que|du)\b", re.I)
EN_WORDS = re.compile(r"\b(the|and|is|of|to|with|for|in|on|this|that|"
                      r"are|you|it|be)\b", re.I)
PLATFORM = re.compile(r"itch\.io|steampowered|steamcommunity|gamejolt|"
                      r"game jolt|newgrounds|kongregate|crazygames|poki\.|"
                      r"armorgames|\bsteam\b|itch", re.I)


def group_readme(rep: core.Report, proj: Project, env: Env) -> None:
    rep.section("readme", gid="readme")
    core.check_readme(rep, proj.path)
    text = proj.readme
    if text is None:
        return
    for label, words, qk in README_SECTIONS:
        body = section_body(text, words)
        ok = body is not None
        rep.check(f'"{label}" section', ok,
                  detail=None if ok else "subject: " + Q[qk])
        if ok and body is not None and len(body.strip()) < 20:
            rep.warn(f'"{label}" section is (almost) empty',
                     "subject: " + Q[qk])
    cfg = section_body(text, ("configuration", "config"))
    if proj.config_rel and isinstance(proj.config_data, dict):
        top = [str(k) for k in proj.config_data]
        missing = [k for k in top if k not in text]
        rep.check("README documents every key of " + proj.config_rel,
                  not missing, expected="each key named in the README",
                  got="not documented: " + cap(missing) if missing
                  else None,
                  detail="subject: " + Q["document"] if missing else None)
        nested = sorted({str(p[-1]) for p, _ in leaves(proj.config_data)
                         if len(p) > 1 and isinstance(p[-1], str)})
        undocumented = [k for k in nested if k not in text]
        if undocumented:
            rep.warn("nested config keys not named in the README: "
                     + cap(undocumented), "subject: " + Q["document"])
    if cfg is not None and not re.search(r"default", cfg, re.I):
        rep.warn("the Configuration section never mentions the default "
                 "values", "subject: " + Q["readme_cfg"])
    hs = section_body(text, ("highscore", "high score", "high-score",
                             "hiscore"))
    if hs is not None and not re.search(r"because|why|chose|choice|choos|"
                                        r"decid|reason|so that|in order to|"
                                        r"simple|easy|advantage", hs, re.I):
        rep.warn("the Highscore section does not say why it was done this "
                 "way", "subject: " + Q["readme_hs"])
    maze = section_body(text, ("maze generation", "maze gen", "maze"))
    names = env.mazes() if env.done else (proj.archive_mods or [])
    want = names or ["mazegen"]
    if maze is not None and not any(n.lower() in maze.lower()
                                    for n in want + ["a-maze-ing"]):
        rep.warn("the Maze Generation section never names the package ("
                 + ", ".join(want) + ")", "subject: " + Q["readme_maze"])
    pm = section_body(text, ("project management", "management"))
    if pm is not None:
        links = readme_pm_links(proj)
        dirs = pm_directories(proj)
        named = [d for d in dirs if d in pm]
        ok = bool(links)
        if not ok and named:
            rep.warn(f"the Project Management section names {named[0]}/ "
                     "but does not link it", "subject: " + Q["readme_pm"])
        else:
            rep.check("Project Management links the project management "
                      "folder", ok,
                      expected="a link to an existing folder of the "
                               "repository, e.g. [docs](project_management/)",
                      got=None if ok else "no link to a folder of the "
                                          "repository",
                      detail=None if ok else "subject: " + Q["readme_pm"])
    fr = len(FR_WORDS.findall(text))
    en = len(EN_WORDS.findall(text))
    if fr > 20 and fr > en:
        rep.warn(f"the README looks French ({fr} French words, {en} "
                 "English)", "subject: " + Q["english"])
    if not PLATFORM.search(text):
        rep.warn("no link to the game on a gaming platform (itch.io, "
                 "Steam, ...)", "subject: " + Q["deploy"]
                 + "\n(the build may be shown during the defense)")


# --------------------------------------------------------------------------
# Makefile
# --------------------------------------------------------------------------

def mypy_config_flags(path: str) -> Set[str]:
    """Flags turned on in mypy.ini / setup.cfg / pyproject.toml."""
    found: Set[str] = set()
    for f in ("mypy.ini", ".mypy.ini", "setup.cfg", "pyproject.toml"):
        text = read_text(os.path.join(path, f))
        if not text:
            continue
        m = re.search(r"^\[(?:mypy|tool\.mypy)\]\s*$(.*?)(?=^\[|\Z)", text,
                      re.M | re.S)
        if not m:
            continue
        body = m.group(1)
        for flag in MYPY_FLAGS + ["--strict"]:
            key = flag[2:].replace("-", "_")
            if re.search(rf"^\s*{key}\s*=\s*(true|1|yes|on)\b", body,
                         re.M | re.I):
                found.add(flag)
    return found


STRICT_COVERS = {"--warn-return-any", "--warn-unused-ignores",
                 "--disallow-untyped-defs", "--check-untyped-defs"}


def group_makefile(rep: core.Report, proj: Project, env: Env,
                   dry: Dry) -> None:
    rep.section("makefile", gid="makefile")
    if proj.make is None:
        rep.check("Makefile present", False,
                  detail="subject: " + Q["makefile"])
        return
    if not core.have("make"):
        rep.skip("Makefile rules", "make not installed")
        return
    missing = [r for r in REQUIRED_RULES if r not in proj.targets]
    rep.check("rules: " + ", ".join(REQUIRED_RULES), not missing,
              expected=", ".join(REQUIRED_RULES),
              got="missing: " + ", ".join(missing) if missing else None,
              detail="subject: " + Q["makefile"] if missing else None)
    if "lint" in proj.targets:
        check_lint_recipe(rep, proj, dry)
    if "install" in proj.targets:
        env.setup()
        check_install(rep, env)
    else:
        env.setup()
    our = lint_results(proj, env)
    if "lint" in proj.targets:
        check_make_lint(rep, env, our)
    if "run" in proj.targets:
        check_make_run(rep, env, "run")
    if "debug" in proj.targets:
        check_make_run(rep, env, "debug")
    if "clean" in proj.targets:
        check_clean(rep, proj)


def check_lint_recipe(rep: core.Report, proj: Project, dry: Dry) -> None:
    rc, text = dry.recipe("lint")
    cmds = [ln for ln in text.splitlines() if ln.strip()]
    has_flake = any(re.search(r"\bflake8\b", c) for c in cmds)
    mypy_lines = [c for c in cmds if re.search(r"\bmypy\b", c)]
    rep.check("make lint runs flake8", has_flake,
              expected="flake8 .", got=None if has_flake else
              lines_of(text, 4) or "(empty recipe)",
              detail=None if has_flake else "subject: " + Q["lint"])
    if not mypy_lines:
        rep.check("make lint runs mypy with the subject's flags", False,
                  expected="mypy . " + " ".join(MYPY_FLAGS),
                  got=lines_of(text, 4) or "(empty recipe)",
                  detail="subject: " + Q["lint"])
        return
    line = " ".join(mypy_lines)
    have_flags = {f for f in MYPY_FLAGS if f in line}
    if "--strict" in line:
        have_flags |= STRICT_COVERS
    in_cfg = mypy_config_flags(proj.path)
    if "--strict" in in_cfg:
        in_cfg |= STRICT_COVERS
    lacking = [f for f in MYPY_FLAGS if f not in have_flags
               and f not in in_cfg]
    rep.check("make lint runs mypy with the subject's flags", not lacking,
              expected="mypy . " + " ".join(MYPY_FLAGS),
              got=None if not lacking else
              f"{mypy_lines[0].strip()}\n(missing: {' '.join(lacking)})",
              detail=None if not lacking else "subject: " + Q["lint"])
    if lacking == [] and in_cfg & set(MYPY_FLAGS) - have_flags \
            and rep.verbose:
        rep.info("some flags come from the mypy config file: "
                 + " ".join(sorted(in_cfg & set(MYPY_FLAGS))))
    targets_ok = all(re.search(r"\s\.(\s|$)", c + " ")
                     for c in [c for c in cmds
                               if re.search(r"\b(flake8|mypy)\b", c)])
    if not targets_ok:
        rep.warn("make lint does not check the whole project "
                 "(flake8 . / mypy .)", "subject: " + Q["lint"])


def check_install(rep: core.Report, env: Env) -> None:
    where = ("a temporary venv" if not env.user_mode
             else "a temporary user base (pip --user)")
    if env.install_status == "skip":
        m = NETWORK_ERR.search(env.install_out)
        why = f"no network: {m.group(0)}" if m else "no network"
        rep.skip("make install", why)
        return
    ok = env.install_status == "ok"
    rep.check(f"make install ({where})", ok,
              expected="exit code 0", got=None if ok else
              lines_of(env.install_out, 10),
              detail=None if ok else "command: make install\nsubject: "
              + Q["install"])
    if ok:
        rep.check("make install installs every imported package",
                  not env.missing,
                  expected="import " + ", ".join(
                      m for m in env.proj.required) + " works",
                  got="cannot import: " + cap(env.missing)
                  if env.missing else None,
                  detail="subject: " + Q["install"] if env.missing
                  else None)


def check_make_lint(rep: core.Report, env: Env,
                    our: Optional[Dict[str, Tuple[bool, str]]]) -> None:
    assert env.ws is not None
    res = core.run(["make", "lint"], cwd=env.ws.dir, env=env.base_env(),
                   timeout=600)
    out = core.output(res)
    if our is None:
        rep.skip("make lint", "flake8 and mypy are needed to judge it")
        return
    ours_ok = all(ok for ok, _ in our.values())
    if res.timed_out:
        rep.check("make lint", False, got="timeout after 600s")
    elif res.rc == 0 and not ours_ok:
        rep.check("make lint fails when flake8 / mypy find errors", False,
                  expected="a non-zero exit code (see the lint group)",
                  got="exit code 0", detail="command: make lint\n"
                  "subject: " + Q["lint"])
    elif res.rc != 0 and ours_ok:
        rep.check("make lint passes (flake8 . and mypy . pass)", False,
                  expected="exit code 0", got=f"exit code {res.rc}:\n"
                  + lines_of(out, 8), detail="command: make lint\n"
                  "subject: " + Q["lint"])
    else:
        rep.check("make lint agrees with flake8 . + mypy .", True)


def check_make_run(rep: core.Report, env: Env, rule: str) -> None:
    why = env.runtime()
    if why:
        rep.skip(f"make {rule}", why)
        return
    assert env.ws is not None
    base = env.baseline()
    case = Case(f"make {rule}", [], mode="", timeout=env.alive_time + 1)
    cmd = ["make", rule]
    if env.backend == "xvfb":
        cmd = ["xvfb-run", "-a", "-s", "-screen 0 1280x1024x24"] + cmd
    stdin = b"c\n" if rule == "debug" else b""
    out = env.launch(case, cmd=cmd, stdin=stdin)
    bad = out.traceback or out.crashed or (base.alive and not out.alive)
    _, recipe = env.dry.recipe(rule)
    quote = Q[rule]
    rep.check(f"make {rule} starts the game", not bad,
              expected=EXPECT["continue"], got=out.got() if bad else None,
              detail=f"command: make {rule}\nrecipe: {lines_of(recipe, 3)}"
              f"\nsubject: {quote}" if bad else None)
    if rule == "debug":
        pdb = "(Pdb)" in out.out or re.search(
            r"\bpdb\b|breakpoint|PYTHONBREAKPOINT", recipe)
        rep.check("make debug uses the Python debugger (pdb)", bool(pdb),
                  expected="python3 -m pdb pac-man.py ...",
                  got=None if pdb else lines_of(recipe, 3),
                  detail=None if pdb else "subject: " + Q["debug"])
    if rule == "run" and not out.alive and not bad:
        rep.info(f"make run ended by itself after {out.elapsed:.1f}s")


def check_clean(rep: core.Report, proj: Project) -> None:
    with core.Workspace(proj.path, prefix="42next-pac-clean-") as ws:
        made = []
        dirs = {os.path.dirname(s.rel) for s in proj.sources}
        for d in sorted(dirs):
            p = os.path.join(ws.dir, d, "__pycache__")
            os.makedirs(p, exist_ok=True)
            with open(os.path.join(p, "x.cpython-310.pyc"), "wb") as fh:
                fh.write(b"\0")
            made.append(os.path.normpath(os.path.join(d, "__pycache__")))
        mc = os.path.join(ws.dir, ".mypy_cache", "3.10")
        os.makedirs(mc, exist_ok=True)
        with open(os.path.join(mc, "x.json"), "w") as fh:
            fh.write("{}")
        keep = [s.rel for s in proj.sources] + ["Makefile"]
        if os.path.isfile(os.path.join(ws.dir, "README.md")):
            keep.append("README.md")
        if proj.config_rel:
            keep.append(proj.config_rel)
        res = core.run(["make", "clean"], cwd=ws.dir, timeout=120,
                       env={"PYTHONDONTWRITEBYTECODE": "1"})
        left = [d for d in made if os.path.isdir(os.path.join(ws.dir, d))]
        rep.check("make clean removes __pycache__", res.rc == 0 and not left,
                  expected="no __pycache__ folder left",
                  got=(f"exit code {res.rc}: {lines_of(core.output(res), 3)}"
                       if res.rc else "still there: " + cap(left))
                  if res.rc or left else None,
                  detail="command: make clean (after creating "
                         + cap(made, 3) + ")\nsubject: " + Q["clean"]
                  if res.rc or left else None)
        mleft = os.path.isdir(os.path.join(ws.dir, ".mypy_cache"))
        rep.check("make clean removes .mypy_cache", not mleft,
                  expected="no .mypy_cache folder left",
                  got="still there: .mypy_cache" if mleft else None,
                  detail="command: make clean\nsubject: " + Q["clean"]
                  if mleft else None)
        gone = [f for f in keep
                if not os.path.exists(os.path.join(ws.dir, f))]
        rep.check("make clean keeps the sources", not gone,
                  got="deleted: " + cap(gone) if gone else None,
                  detail="command: make clean" if gone else None)


# --------------------------------------------------------------------------
# lint
# --------------------------------------------------------------------------

_LINT: Dict[str, Any] = {}


def tool_cmd(name: str) -> Optional[List[str]]:
    if shutil.which(name):
        return [name]
    res = core.run([sys.executable, "-B", "-c", f"import {name}"],
                   timeout=60)
    if res.rc == 0:
        return [sys.executable, "-B", "-m", name]
    core.note_missing(name)
    return None


def lint_results(proj: Project,
                 env: Env) -> Optional[Dict[str, Tuple[bool, str]]]:
    """flake8 . and mypy . in a fresh copy; None when a tool is missing."""
    if "res" in _LINT:
        return _LINT["res"]
    flake = tool_cmd("flake8")
    mypy = tool_cmd("mypy")
    _LINT["tools"] = (flake, mypy)
    if flake is None or mypy is None:
        _LINT["res"] = None
        return None
    res: Dict[str, Tuple[bool, str]] = {}
    with core.Workspace(proj.path, prefix="42next-pac-lint-") as ws:
        env_vars = {"PYTHONDONTWRITEBYTECODE": "1"}
        r = core.run(flake + ["."], cwd=ws.dir, timeout=300, env=env_vars)
        res["flake8"] = (r.rc == 0 and not r.timed_out,
                         "timeout" if r.timed_out else core.output(r))
        cmd = mypy + ["."] + MYPY_FLAGS + ["--cache-dir",
                                           ws.tmp("mypy-cache")]
        if env.done and env.python and not env.missing:
            cmd += ["--python-executable", env.python]
        r = core.run(cmd, cwd=ws.dir, timeout=600, env=env_vars)
        res["mypy"] = (r.rc == 0 and not r.timed_out,
                       "timeout" if r.timed_out else core.output(r))
    _LINT["res"] = res
    return res


FLAKE8_RELAX = re.compile(r"^\s*(ignore|extend-ignore|extend_ignore|select|"
                          r"per-file-ignores|per_file_ignores|"
                          r"max-line-length|max_line_length)\s*=\s*(.+)$",
                          re.M | re.I)


def group_lint(rep: core.Report, proj: Project, env: Env) -> None:
    rep.section("lint", gid="lint")
    res = lint_results(proj, env)
    if res is None:
        flake, mypy = _LINT.get("tools", (None, None))
        for name, t in (("flake8 .", flake), ("mypy .", mypy)):
            if t is None:
                rep.skip(name, name.split()[0] + " not installed")
        if flake is not None or mypy is not None:
            rep.skip("lint", "both flake8 and mypy are needed")
        return
    ok, out = res["flake8"]
    rep.check("flake8 .", ok, expected="no output",
              got=None if ok else lines_of(out, 12, last=False),
              detail=None if ok else "command: flake8 .\nsubject: "
              + Q["flake8"])
    ok, out = res["mypy"]
    shown = "mypy . " + " ".join(MYPY_FLAGS)
    rep.check("mypy . with the subject's flags", ok,
              expected="Success: no issues found",
              got=None if ok else lines_of(out, 12, last=False),
              detail=None if ok else f"command: {shown}\nsubject: "
              + Q["mypy"])
    if not (env.done and env.python and not env.missing) and rep.verbose:
        rep.info("mypy ran without the project's dependencies installed")
    relax = []
    for f in (".flake8", "setup.cfg", "tox.ini"):
        text = read_text(os.path.join(proj.path, f))
        if not text:
            continue
        m = re.search(r"^\[flake8\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
        if not m:
            continue
        for k, v in FLAKE8_RELAX.findall(m.group(1)):
            if k.lower().replace("_", "-") == "max-line-length" \
                    and v.strip().isdigit() and int(v) <= 79:
                continue
            relax.append(f"{f}: {k} = {v.strip()[:50]}")
    if relax:
        rep.warn("the flake8 config relaxes the standard: " + cap(relax, 4),
                 "subject: " + Q["flake8"])
    noqa = [f"{s.rel}:{i}" for s in proj.sources
            for i, ln in enumerate(s.text.splitlines(), 1)
            if re.search(r"#\s*noqa", ln, re.I)]
    ignores = [f"{s.rel}:{i}" for s in proj.sources
               for i, ln in enumerate(s.text.splitlines(), 1)
               if re.search(r"#\s*type:\s*ignore", ln)]
    if noqa:
        rep.warn(f"{len(noqa)} '# noqa' comment(s): " + cap(noqa, 5),
                 "subject: " + Q["flake8"])
    if ignores:
        rep.warn(f"{len(ignores)} '# type: ignore' comment(s): "
                 + cap(ignores, 5), "subject: " + Q["mypy"])
    for f in ("mypy.ini", ".mypy.ini", "setup.cfg", "pyproject.toml"):
        text = read_text(os.path.join(proj.path, f)) or ""
        if re.search(r"^\s*ignore_errors\s*=\s*(true|1|yes)", text,
                     re.M | re.I):
            rep.warn(f"{f} turns mypy errors off (ignore_errors)",
                     "subject: " + Q["mypy"])


# --------------------------------------------------------------------------
# source
# --------------------------------------------------------------------------

GEN_NAMES = re.compile(r"(generat\w*maze|maze\w*generat|carve|backtrack|"
                       r"kruskal|\bprim\b|_prim|prim_|wilson|eller|aldous|"
                       r"hunt_and_kill|binary_tree|sidewinder|growing_tree|"
                       r"dfs_maze|maze_dfs|recursive_division)", re.I)
FEATURES = [
    ("cheat mode", r"cheat|god.?mode|invincib|invulner",
     '"A cheat mode for review purposes."'),
    ("pause", r"pause", '"During the game, the player can pause and resume '
                        'the game."'),
    ("highscores", r"high.?score|hi.?score|leaderboard|top.?10|best.?score",
     '"Display highscores in the main menu."'),
    ("instructions screen", r"instruction|controls|how to play|help",
     '"Instructions - Shows the game controls and rules."'),
    ("exit entry", r"\bexit\b|\bquit\b", '"Exit - Closes the game '
                                         'application."'),
    ("lives", r"li(fe|ves)", '"Remaining lives"'),
    ("level", r"level", '"Current level"'),
    ("time limit", r"time", '"Remaining time for the level"'),
    ("game over", r"game.?over|you lose|you lost|defeat",
     '"Game Over Screen: Displays the final score."'),
    ("victory", r"victory|congrat|you win|you won|winner",
     '"Victory Screen: Displays the final score and a congratulatory '
     'message."'),
    ("name entry", r"name", '"Prompts the player to enter their name to '
                            'save the score in the highscores list."'),
]


def _parents(tree: ast.AST) -> Dict[ast.AST, ast.AST]:
    par = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            par[child] = node
    return par


def group_source(rep: core.Report, proj: Project, env: Env) -> None:
    rep.section("source", gid="source")
    srcs = proj.game_sources()
    if not srcs:
        rep.check("Python sources", False, got="no .py file found")
        return
    broken = [s for s in srcs if s.tree is None]
    rep.check("every .py file parses", not broken,
              got="syntax error: " + "; ".join(
                  f"{s.rel}: {s.error}" for s in broken[:3])
              if broken else None)
    classes: List[str] = []
    no_doc: List[str] = []
    no_doc_dunder: List[str] = []
    bare_open: List[str] = []
    for s in srcs:
        if s.tree is None:
            continue
        par = _parents(s.tree)
        for node in ast.walk(s.tree):
            if isinstance(node, ast.ClassDef):
                classes.append(node.name)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                if ast.get_docstring(node) is None:
                    where = f"{s.rel}:{node.lineno} {node.name}"
                    dunder = node.name.startswith("__") \
                        and node.name.endswith("__")
                    (no_doc_dunder if dunder else no_doc).append(where)
            if isinstance(node, ast.Call):
                f = node.func
                name = (f.id if isinstance(f, ast.Name) else
                        f.attr if isinstance(f, ast.Attribute) else "")
                if name == "open":
                    up = par.get(node)
                    if not isinstance(up, ast.withitem):
                        bare_open.append(f"{s.rel}:{node.lineno}")
    rep.check("object-oriented: classes are defined", bool(classes),
              expected="at least one class", got=None if classes else
              "no class in the sources",
              detail=None if classes else "subject: " + Q["oop"])
    if classes and rep.verbose:
        rep.info("classes: " + cap(classes, 10))
    rep.check("docstrings on every class and function", not no_doc,
              expected="a docstring in each class / function",
              got=f"{len(no_doc)} without: " + cap(no_doc, 6)
              if no_doc else None,
              detail="subject: " + Q["docstrings"] if no_doc else None)
    if no_doc_dunder:
        rep.warn(f"{len(no_doc_dunder)} special method(s) without a "
                 "docstring: " + cap(no_doc_dunder, 4),
                 "subject: " + Q["docstrings"])
    if bare_open:
        rep.warn("open() outside a with statement: " + cap(bare_open, 5),
                 "subject: " + Q["context"])
    check_maze_source(rep, proj, env)
    graphics = sorted(n for n in proj.third_party if n in GRAPHICS) + sorted(
        n for n in {u.name for u in proj.imports} if n in ("tkinter",
                                                           "curses"))
    if rep.verbose:
        rep.info("graphics library: " + (", ".join(graphics) or "none "
                                         "found"))
    text = proj.all_text()
    for label, rx, quote in FEATURES:
        if not re.search(rx, text, re.I):
            rep.warn(f"no trace of the {label} in the code",
                     "subject: " + quote)
    if "pygame" in graphics:
        arrows = re.search(r"K_(UP|DOWN|LEFT|RIGHT)\b", text)
        wasd = re.search(r"K_[wasdzqWASDZQ]\b", text)
        if not arrows and not wasd:
            rep.warn("no arrow key / WASD constant (pygame.K_UP, "
                     "pygame.K_w, ...) in the code",
                     "subject: " + Q["controls"])


def check_maze_source(rep: core.Report, proj: Project, env: Env) -> None:
    known = set(proj.archive_mods) | set(env.installed_maze)
    imported = {u.name for u in proj.imports}
    used = sorted(n for n in known if n in imported)
    mazey = sorted(n for n in proj.third_party if "maze" in n.lower())
    if known:
        ok = bool(used)
        rep.check("the A-Maze-ing package is imported (" + ", ".join(
            sorted(known)) + ")", ok,
            expected="import " + " / ".join(sorted(known)),
            got=None if ok else "never imported",
            detail=None if ok else "subject: " + Q["external"])
    elif mazey:
        rep.check("an A-Maze-ing package is imported (" + ", ".join(mazey)
                  + ")", True)
    else:
        others = [n for n in proj.third_party if n not in GRAPHICS
                  and n not in OTHER_LIBS]
        if others:
            rep.warn("cannot tell which import is the A-Maze-ing package: "
                     + cap(others), "subject: " + Q["external"])
        else:
            rep.check("an external A-Maze-ing package is imported", False,
                      expected="import mazegen (or the assigned package)",
                      got="no third-party import besides "
                      + (", ".join(proj.third_party) or "nothing"),
                      detail="subject: " + Q["external"] + "\n"
                      + Q["own_gen"])
    shadow = sorted(n for n in (known | set(mazey)) if n in proj.local)
    if shadow:
        rep.warn("a local copy of " + ", ".join(shadow) + " is in the "
                 "repository: it hides the installed package",
                 "subject: " + Q["as_is"])
    gen = []
    for s in proj.game_sources():
        if s.tree is None:
            continue
        for node in ast.walk(s.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and GEN_NAMES.search(
                                     node.name):
                top = s.rel.replace(os.sep, "/").split("/")[0]
                if top in shadow or top[:-3] in shadow:
                    continue
                gen.append(f"{s.rel}:{node.lineno} {node.name}")
    if gen:
        rep.warn("looks like maze generation code in the project: "
                 + cap(gen, 4), "subject: " + Q["own_gen"])


# --------------------------------------------------------------------------
# runtime: usage
# --------------------------------------------------------------------------

def group_usage(rep: core.Report, proj: Project, env: Env) -> None:
    rep.section("usage", gid="usage")
    if not runtime_or_skip(rep, env, "command line"):
        return
    base = env.baseline()
    cfg_arg = env.config_arg()
    raw = proj.config_raw or json.dumps(proj.config_data, indent=2)
    cases: List[Case] = []
    cases.append(Case("no argument", [], expect="exit",
                      quote=Q["one_arg"], message=True))
    cases.append(Case("two arguments", cfg_arg + cfg_arg, expect="exit",
                      quote=Q["one_arg"], message=True))
    missing = os.path.join(env.tmp("cases"), "missing", "nothing.json")
    cases.append(Case("missing file", [missing], quote=Q["errors"],
                      message=True,
                      shown_args=["missing/nothing.json"]))
    d = os.path.join(env.tmp("cases"), "a-directory.json")
    os.makedirs(d, exist_ok=True)
    cases.append(Case("a directory instead of a file", [d],
                      quote=Q["errors"], message=True,
                      shown_args=["a-directory.json/"]))
    unread = env.write_case("unreadable.json", raw)
    os.chmod(unread, 0)
    root = hasattr(os, "geteuid") and os.geteuid() == 0
    if not root:
        cases.append(Case("unreadable file (chmod 000)", [unread],
                          quote=Q["errors"], message=True,
                          shown_args=["unreadable.json"]))
    broken = [
        ("empty file", "empty.json", b""),
        ("only spaces and newlines", "blank.json", b"  \n\n \t\n"),
        ("only comment lines", "comments-only.json",
         b"# nothing\n# but comments\n"),
        ("not JSON at all", "text.json", b"this is not json\n"),
        ("binary, not UTF-8", "binary.json",
         bytes(range(128, 256)) + b"\x00\xff{"),
        ("truncated JSON", "truncated.json",
         raw.encode("utf-8")[:max(1, len(raw) // 2)]),
        ("trailing comma", "trailing-comma.json", b'{"lives": 3,}\n'),
        ("unclosed object", "unclosed.json", b'{"lives": 3\n'),
        ("JSON array instead of an object", "array.json", b"[1, 2, 3]\n"),
        ("JSON number instead of an object", "number.json", b"42\n"),
        ("JSON string instead of an object", "string.json", b'"config"\n'),
        ("JSON null", "null.json", b"null\n"),
        ("duplicate keys", "duplicate.json",
         b'{"lives": 3, "lives": "three"}\n'),
    ]
    for label, name, content in broken:
        path = env.write_case(name, content)
        cases.append(Case(f"config: {label}", [path], quote=Q["errors"],
                          message=True, shown_args=[name],
                          show=["contents: " + core.crepr(content, 60)]))
    renamed = [("valid config with a .txt name", "settings.txt"),
               ("valid config with spaces in its name", "my config.json")]
    for label, name in renamed:
        path = env.write_case(name, raw)
        cases.append(Case(label, [path], expect="continue",
                          quote=Q["filename"], shown_args=[name]))
    outs = env.run_all(cases)
    silent = []
    for case, out in zip(cases, outs):
        if case.quote == Q["filename"]:
            bad = out.traceback or out.crashed
            if bad:
                judge(rep, case, out, base)
            elif base.alive and not out.alive:
                rep.warn(f"{case.label}: the game does not start",
                         f"command: {case.cmdline()}\ngot: {out.got()}\n"
                         f"subject: {Q['filename']}")
            else:
                rep.check(case.label, True)
            continue
        judge(rep, case, out, base)
        if case.expect == "exit" and out.alive and not out.traceback:
            rep.warn(f"{case.label}: the game starts anyway",
                     f"command: {case.cmdline()}\nsubject: {Q['one_arg']}")
        if case.message and not out.traceback and not new_output(out, base):
            silent.append(case.label)
    if silent:
        rep.warn("no message printed for: " + cap(silent, 6),
                 "subject: " + Q["errors"] + "\n(a message shown only in "
                 "the window is not seen by this tester)")
    if not root:
        try:
            os.chmod(unread, 0o600)
        except OSError:
            pass


# --------------------------------------------------------------------------
# runtime: config
# --------------------------------------------------------------------------

def comment_variants(raw: str) -> Tuple[str, str]:
    """(comment lines at column 0, comment lines indented)."""
    rows = raw.splitlines()
    out = ["# 42next: comment line before the JSON",
           "#", '# { "broken": [ , "not json" '] + []
    inserted = False
    for ln in rows:
        out.append(ln)
        if not inserted and ln.rstrip().endswith(("{", "[", ",")):
            out.append('# "a_key_in_a_comment": [1, 2,')
            inserted = True
    out += ["# 42next: comment line after the JSON", "#}"]
    indented = []
    for ln in out:
        if ln.startswith("#"):
            indented.append("    " + ln)
        else:
            indented.append(ln)
    return "\n".join(out) + "\n", "\n".join(indented) + "\n"


KINDS: List[Tuple[str, str, Any]] = [
    ("negative", "numbers set to -1",
     lambda v: (-1 if isinstance(v, int) else -1.5) if is_num(v) else None),
    ("zero", "numbers set to 0", lambda v: 0 if is_num(v) else None),
    ("huge", "numbers set to 1000000000",
     lambda v: 10 ** 9 if is_num(v) else None),
    ("float", "integers set to 2.5",
     lambda v: 2.5 if isinstance(v, int) and not isinstance(v, bool)
     else None),
    ("text", 'numbers / booleans set to "abc"',
     lambda v: "abc" if is_num(v) or isinstance(v, bool) else None),
    ("infinity", "numbers set to Infinity",
     lambda v: float("inf") if is_num(v) else None),
    ("nan", "numbers set to NaN",
     lambda v: float("nan") if is_num(v) else None),
    ("number", "strings set to 42",
     lambda v: 42 if isinstance(v, str) else None),
    ("empty", 'strings set to ""',
     lambda v: "" if isinstance(v, str) else None),
    ("null", "every value set to null", lambda v: "__null__"),
    ("list", "every value set to []", lambda v: []),
    ("object", "every value set to {}", lambda v: {}),
]
CONTAINER_KINDS: List[Tuple[str, str, Any]] = [
    ("emptied", "lists emptied ([]) and objects emptied ({})",
     lambda v: [] if isinstance(v, list) else {}),
    ("swapped", "lists replaced by {} and objects by []",
     lambda v: {} if isinstance(v, list) else []),
]


def mutate(data: Any, paths: List[Path], fn: Any) -> Tuple[Any, List[str]]:
    """Apply fn to the value at each path; returns (new data, changes)."""
    new = copy.deepcopy(data)
    changes = []
    for p in paths:
        node = new
        try:
            for k in p:
                node = node[k]
        except (KeyError, IndexError, TypeError):
            continue
        value = fn(node)
        if value is None:
            continue
        if value == "__null__":
            value = None
        set_path(new, p, value)
        changes.append(f'"{pattern(p)}": {show_value(value)}')
    return new, list(dict.fromkeys(changes))


def dump(data: Any) -> str:
    return json.dumps(data, indent=2)


def group_config(rep: core.Report, proj: Project, env: Env,
                 args: Any) -> None:
    rep.section("config", gid="config")
    if not runtime_or_skip(rep, env, "config file"):
        return
    base = env.baseline()
    own = proj.config_rel or "the default config"
    bcase = Case(f"the game starts with {own}", env.config_arg(),
                 expect="continue", quote=Q["usage"])
    if base.traceback or base.crashed:
        judge(rep, bcase, base, None)
        rep.info("the other config checks need a game that starts")
        return
    if base.alive:
        rep.check(f"the game starts with {own}", True)
    else:
        rep.warn(f"with {own} the game stops after {base.elapsed:.1f}s",
                 base.got())
        baseline_note(rep, env)
    if proj.config_note:
        rep.info(proj.config_note + ": a config with the subject's "
                 "suggested keys is used")
    data: Any = proj.config_data
    cases: List[Case] = []
    raw = proj.config_raw or dump(data)
    col0, indented = comment_variants(raw)
    p = env.write_case("comments.json", col0)
    comment_case = Case("comment lines (# at the start of the line) are "
                        "ignored", [p], expect="continue",
                        quote=Q["comments"], shown_args=["comments.json"],
                        show=["lines added: '# { \"broken\": [ , ...', "
                              "'#', '# \"a_key_in_a_comment\": [1, 2,', "
                              "'#}'"])
    cases.append(comment_case)
    p = env.write_case("comments-indented.json", indented)
    indent_case = Case("indented comment lines (    # ...)", [p],
                       expect="continue", quote=Q["comments"],
                       shown_args=["comments-indented.json"])
    cases.append(indent_case)
    # the same config without any comment: what the comment runs are
    # compared with (the project's own file may already have comments)
    p = env.write_case("no-comments.json", dump(data))
    plain_case = Case("config without comments", [p], expect="continue",
                      shown_args=["no-comments.json"])
    cases.append(plain_case)
    p = env.write_case("empty-object.json", "{}\n")
    cases.append(Case("every key missing ({})", [p], expect="continue",
                      quote=Q["faulty"], shown_args=["empty-object.json"],
                      message=True, show=["contents: {}"]))
    if isinstance(data, dict):
        for key in list(data):
            new = copy.deepcopy(data)
            del new[key]
            name = f"missing-{re.sub(r'[^A-Za-z0-9_]+', '_', str(key))}.json"
            p = env.write_case(name, dump(new))
            cases.append(Case(f'key "{key}" missing', [p], expect="continue",
                              quote=Q["faulty"], shown_args=[name],
                              message=True,
                              show=[f'the config without "{key}"']))
    nested: Dict[str, List[Path]] = {}
    for path, _ in leaves(data) + containers(data):
        if len(path) > 1 and isinstance(path[-1], str):
            nested.setdefault(pattern(path), []).append(path)
    for pat, paths in nested.items():
        new = copy.deepcopy(data)
        for path in paths:
            del_path(new, path)
        name = f"missing-{re.sub(r'[^A-Za-z0-9_]+', '_', pat)}.json"
        p = env.write_case(name, dump(new))
        cases.append(Case(f'key "{pat}" missing', [p], expect="continue",
                          quote=Q["faulty"], shown_args=[name],
                          message=True, show=[f'every "{pat}" removed']))
    if isinstance(data, dict):
        new = copy.deepcopy(data)
        new["next42_unknown_key"] = [1, {"x": None}]
        new["Lives"] = "unknown key with another case"
        for path, node in containers(data):
            if isinstance(node, dict):
                target = new
                for k in path:
                    target = target[k]
                target["next42_unknown_nested"] = True
        p = env.write_case("unknown-keys.json", dump(new))
        cases.append(Case("unknown keys are ignored", [p], expect="continue",
                          quote=Q["unknown"], shown_args=["unknown-keys.json"],
                          show=['added: "next42_unknown_key", "Lives" and '
                                '"next42_unknown_nested" in every object']))
    all_leaves = [p for p, _ in leaves(data)]
    all_cont = [p for p, _ in containers(data)]
    kind_cases: List[Tuple[str, str, Any, List[Path], Case]] = []
    for kid, label, fn in KINDS + CONTAINER_KINDS:
        paths = all_cont if (kid, label, fn) in CONTAINER_KINDS \
            else all_leaves
        new, changes = mutate(data, paths, fn)
        if not changes:
            continue
        name = f"invalid-{kid}.json"
        p = env.write_case(name, dump(new))
        c = Case(f"invalid values: {label}", [p], expect="continue",
                 quote=Q["faulty"], shown_args=[name], message=True,
                 show=[cap(changes, 4)])
        kind_cases.append((kid, label, fn, paths, c))
        cases.append(c)
    outs = env.run_all(cases)
    result = dict(zip([id(c) for c in cases], outs))
    # messages are looked for in what a comment-free config does not print
    ref = result[id(plain_case)]
    silent = []
    for case in cases:
        out = result[id(case)]
        if any(case is k[4] for k in kind_cases):
            continue
        if case is comment_case or case is indent_case:
            plain = result[id(plain_case)]
            judge_comments(rep, case, out, plain,
                           warn_only=case is indent_case)
            continue
        judge(rep, case, out, base)
        if case.message and not out.traceback and out.alive \
                and not new_output(out, ref):
            silent.append(case.label)
    # invalid values: find the faulty key when a combined run fails
    retry: List[Tuple[str, str, Case, List[Case]]] = []
    for kid, label, fn, paths, c in kind_cases:
        out = result[id(c)]
        failed = out.traceback or out.crashed or (base.alive
                                                  and not out.alive)
        if not failed or args.quick:
            judge(rep, c, out, base)
            if not failed and c.message and out.alive \
                    and not new_output(out, ref):
                silent.append(c.label)
            continue
        groups: Dict[str, List[Path]] = {}
        for path in paths:
            groups.setdefault(pattern(path), []).append(path)
        singles = []
        for pat, gpaths in groups.items():
            new, changes = mutate(data, gpaths, fn)
            if not changes:
                continue
            name = f"invalid-{kid}-{re.sub(r'[^A-Za-z0-9_]+', '_', pat)}.json"
            p = env.write_case(name, dump(new))
            singles.append(Case(f'invalid value: "{pat}" ({label})', [p],
                                expect="continue", quote=Q["faulty"],
                                shown_args=[name], show=[cap(changes, 3)]))
        retry.append((kid, label, c, singles))
    flat = [s for _, _, _, singles in retry for s in singles]
    outs2 = env.run_all(flat) if flat else []
    res2 = dict(zip([id(s) for s in flat], outs2))
    for kid, label, c, singles in retry:
        bad = [s for s in singles
               if res2[id(s)].traceback or res2[id(s)].crashed
               or (base.alive and not res2[id(s)].alive)]
        if not bad:
            judge(rep, c, result[id(c)], base)
            continue
        # one KO per kind of value, naming every key that breaks the game
        keys = [s.label.split('"')[1] for s in bad]
        first = bad[0]
        merged = Case(f"invalid values: {label}", first.args,
                      expect="continue", quote=Q["faulty"],
                      shown_args=first.shown_args,
                      show=first.show + [
                          f"keys that break the game ({len(keys)}): "
                          + cap([f'"{k}"' for k in keys], 6)])
        judge(rep, merged, res2[id(first)], base)
    if silent:
        rep.warn(f"no message printed for {len(silent)} missing / invalid "
                 "value case(s): " + cap(silent, 4),
                 "subject: " + Q["faulty"] + "\n(a message written to a "
                 "log file or shown in the window is not seen here)")


def judge_comments(rep: core.Report, case: Case, out: Outcome,
                   base: Outcome, warn_only: bool) -> None:
    complaint = sorted(ln for ln in new_output(out, base)
                       if ERRORISH.search(ln))
    if out.traceback or out.crashed:
        if warn_only:
            rep.warn(case.label + ": " + out.got().splitlines()[-1],
                     f"command: {case.cmdline()}\nsubject: {case.quote} "
                     "(an indented # is not at the start of the line)")
        else:
            judge(rep, case, out, base)
        return
    bad = bool(complaint) or (base.alive and not out.alive)
    got = unpath(("the program complains about the config:\n" + "\n".join(
        complaint[:4])) if complaint else out.got())
    if warn_only:
        if bad:
            rep.warn(case.label + " are not accepted",
                     f"command: {case.cmdline()}\ngot: {got}\nsubject: "
                     f"{case.quote} (an indented # is not at the start of "
                     "the line: up to the evaluator)")
        return
    rep.check(case.label, not bad,
              expected="the same start as without the comments"
              if bad else None, got=got if bad else None,
              detail=f"command: {case.cmdline()}\n  " + "\n  ".join(
                  case.show) + f"\nsubject: {case.quote}" if bad else None)


# --------------------------------------------------------------------------
# runtime: highscore file
# --------------------------------------------------------------------------

HS_KEY = re.compile(r"score|leaderboard|hall.?of.?fame|record", re.I)


def highscore_target(proj: Project) -> Tuple[Optional[List[Path]],
                                             Optional[str]]:
    """(config paths of the highscore file name, or a repo file)."""
    data = proj.config_data
    paths = [p for p, v in leaves(data) if isinstance(v, str)
             and isinstance(p[-1], str) and HS_KEY.search(p[-1])]
    if paths:
        return paths, None
    for root, dirs, files in os.walk(proj.path):
        rel_root = os.path.relpath(root, proj.path)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS
                   and not d.startswith(".") and rel_root.count(os.sep) < 2]
        for f in sorted(files):
            if HS_KEY.search(f) and not f.endswith((".py", ".md")):
                return None, os.path.normpath(os.path.join(rel_root, f))
    return None, None


HS_CASES: List[Tuple[str, Optional[bytes]]] = [
    ("missing file", None),
    ("empty file", b""),
    ("not JSON", b'{"name": "abc", "sc'),
    ("binary, not UTF-8", bytes(range(128, 256)) * 4),
    ("JSON number", b"42\n"),
    ("JSON null", b"null\n"),
    ("JSON string", b'"scores"\n'),
    ("empty JSON object", b"{}\n"),
    ("empty JSON list", b"[]\n"),
    ("wrong entries in a list",
     b'[1, "x", null, [], {"name": 5, "score": "abc"}, {"score": -5}, '
     b'{"name": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "score": 10}, '
     b'{"name": "<>!?", "score": 1e999}, {"name": "B", "score": 2.5}]\n'),
    ("wrong entries in an object",
     b'{"scores": "x", "AAA": -5, "best": [1, 2], "names": null, '
     b'"highscores": {"a": "b"}, "top": [{"name": null}]}\n'),
    ("1000 entries",
     json.dumps([{"name": f"P{i}", "score": i}
                 for i in range(1000)]).encode()),
    ("a directory", None),
    ("in a folder that does not exist", None),
    ("unreadable (chmod 000)", b"[]\n"),
]


def group_highscore(rep: core.Report, proj: Project, env: Env) -> None:
    rep.section("highscore", gid="highscore")
    if not runtime_or_skip(rep, env, "highscore file"):
        return
    paths, repo_file = highscore_target(proj)
    if not paths and not repo_file:
        rep.skip("highscore file", "no highscore file name in the config "
                 "and no highscore file in the repository")
        return
    base = env.baseline()
    if base.traceback:
        rep.skip("highscore file", "the game does not start (config group)")
        return
    assert env.ws is not None
    root = hasattr(os, "geteuid") and os.geteuid() == 0
    cases: List[Case] = []
    chmod: List[str] = []
    for i, (label, content) in enumerate(HS_CASES):
        if label.startswith("unreadable") and root:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        folder = os.path.join(env.tmp("cases"), f"hs-{i}-{slug}")
        os.makedirs(folder, exist_ok=True)
        if paths:
            target = os.path.join(folder, "highscores.json")
            if label == "in a folder that does not exist":
                target = os.path.join(folder, "no-such-folder", "x",
                                      "highscores.json")
            if label == "a directory":
                os.makedirs(target, exist_ok=True)
            elif content is not None:
                with open(target, "wb") as fh:
                    fh.write(content)
            if label.startswith("unreadable"):
                os.chmod(target, 0)
                chmod.append(target)
            new = copy.deepcopy(proj.config_data)
            for p in paths:
                set_path(new, p, target)
            name = f"hs-{i}-{slug}.json"
            cfg = env.write_case(name, json.dumps(new, indent=2))
            keys = ", ".join(f'"{pattern(p)}"' for p in paths)
            cases.append(Case(f"highscore file: {label}", [cfg],
                              expect="continue", quote=Q["hs_robust"],
                              shown_args=[name],
                              show=[f"{keys} -> {label}"] + (
                                  ["contents: " + core.crepr(content, 60)]
                                  if content is not None else [])))
        else:
            assert repo_file is not None
            if label == "in a folder that does not exist":
                continue
            copy_dir = os.path.join(folder, "project")
            shutil.copytree(env.ws.dir, copy_dir, symlinks=True,
                            ignore=shutil.ignore_patterns(
                                ".venv", "venv", "__pycache__",
                                ".mypy_cache"))
            target = os.path.join(copy_dir, repo_file)
            if os.path.isdir(target):
                shutil.rmtree(target)
            elif os.path.exists(target):
                os.remove(target)
            if label == "a directory":
                os.makedirs(target, exist_ok=True)
            elif content is not None:
                with open(target, "wb") as fh:
                    fh.write(content)
            if label.startswith("unreadable"):
                os.chmod(target, 0)
                chmod.append(target)
            cases.append(Case(f"highscore file: {label}", env.config_arg(),
                              expect="continue", quote=Q["hs_robust"],
                              cwd=copy_dir,
                              show=[f"{repo_file} -> {label}"] + (
                                  ["contents: " + core.crepr(content, 60)]
                                  if content is not None else [])))
    outs = env.run_all(cases)
    for case, out in zip(cases, outs):
        judge(rep, case, out, base)
    for fpath in chmod:
        try:
            os.chmod(fpath, 0o600)
        except OSError:
            pass


# --------------------------------------------------------------------------
# runtime: failing generator
# --------------------------------------------------------------------------

def group_generator(rep: core.Report, proj: Project, env: Env) -> None:
    rep.section("generator", gid="generator")
    if not runtime_or_skip(rep, env, "maze generator failure"):
        return
    mods = env.mazes()
    if not mods:
        rep.skip("maze generator failure", "cannot tell which module is the "
                 "A-Maze-ing package")
        return
    local = [m for m in mods if m in proj.local]
    if local:
        rep.skip("maze generator failure", f"the repository has its own "
                 f"{', '.join(local)}: it cannot be replaced")
        return
    base = env.baseline()
    if base.traceback:
        rep.skip("maze generator failure", "the game does not start "
                 "(config group)")
        return
    fake = env.tmp("fake-maze")
    for m in mods:
        os.makedirs(os.path.join(fake, m), exist_ok=True)
        with open(os.path.join(fake, m, "__init__.py"), "w") as fh:
            fh.write(FAKE_MAZE)
    kinds = [("RuntimeError", "the generator raises RuntimeError"),
             ("ValueError", "the generator raises ValueError"),
             ("import", "the package cannot be imported")]
    cases = []
    marks = []
    for kind, label in kinds:
        mark = env.tmp(f"maze-mark-{kind}.txt")
        marks.append(mark)
        cases.append(Case(label, env.config_arg(), quote=Q["gen_fail"],
                          pythonpath=fake, message=True,
                          timeout=env.alive_time + 1,
                          extra_env={"NEXT42_PAC_FAIL": kind,
                                     "NEXT42_PAC_MARK": mark},
                          show=[f"every call into {', '.join(mods)} raises "
                                f"{kind}" if kind != "import" else
                                f"import {', '.join(mods)} raises "
                                "ModuleNotFoundError"]))
    outs = env.run_all(cases)
    for (kind, label), case, out, mark in zip(kinds, cases, outs, marks):
        called = "call" in (read_text(mark) or "")
        if kind == "import":
            if out.traceback:
                rep.warn(label + ": " + out.got().splitlines()[-1],
                         f"command: {case.cmdline()} (without the "
                         f"package)\nsubject: {Q['gen_fail']}\n(a missing "
                         "package is not exactly a failing generator: up "
                         "to the evaluator)")
            else:
                rep.check(label + ": clean message", True)
            continue
        if not called and not out.traceback:
            rep.skip(label, "the generator was not called during the run "
                     f"({out.elapsed:.0f}s, Enter pressed in the menu)")
            continue
        judge(rep, case, out, base)
        if not out.traceback and not new_output(out, base) \
                and not out.alive:
            rep.warn(label + ": the game stops without a message",
                     "subject: " + Q["gen_fail"])


# --------------------------------------------------------------------------
# runtime: playing (pygame only)
# --------------------------------------------------------------------------

LIVES_KEY = re.compile(r"^(lives?|nb_?lives|lifes?|player_lives|"
                       r"start(ing)?_lives|max_lives)$", re.I)
TIME_KEY = re.compile(r"time", re.I)


def group_play(rep: core.Report, proj: Project, env: Env,
               args: Any) -> None:
    rep.section("play", gid="play")
    if not runtime_or_skip(rep, env, "playing"):
        return
    if env.backend != "pygame":
        rep.skip("random key presses", "only for pygame games (the key "
                 "presses go through the pygame event queue)")
        return
    base = env.baseline()
    if base.traceback:
        rep.skip("playing", "the game does not start (config group)")
        return
    play_time = 6.0 if args.quick else 10.0
    seeds = [1] if args.quick else [1, 2, 3]
    data = proj.config_data
    cases = []
    for s in seeds:
        cases.append(Case(f"random key presses for {play_time:.0f}s "
                          f"(seed {s})", env.config_arg(), mode="fuzz",
                          seed=s, timeout=play_time, expect="nocrash",
                          quote=Q["no_crash"],
                          show=["Enter in the main menu, then random "
                                "arrows / WASD / Enter / Escape / P / "
                                "letters / F1-F12 / mouse clicks"]))
    cases.append(Case("window closed in the main menu (QUIT event)",
                      env.config_arg(), mode="menuquit", expect="nocrash",
                      quote=Q["no_crash"],
                      timeout=4.0))
    cases.append(Case("window closed during the game (QUIT event)",
                      env.config_arg(), mode="quit", expect="nocrash",
                      quote=Q["no_crash"],
                      timeout=5.0))
    lives = [p for p, v in leaves(data) if is_num(v) and isinstance(
        p[-1], str) and LIVES_KEY.match(p[-1])]
    times = [p for p, v in leaves(data) if is_num(v) and isinstance(
        p[-1], str) and TIME_KEY.search(p[-1])]
    hs_paths, _ = highscore_target(proj)
    if times:
        new = copy.deepcopy(data)
        for p in times:
            set_path(new, p, 1)
        name = "time-limit-1.json"
        cfg = env.write_case(name, json.dumps(new, indent=2))
        cases.append(Case("time limit reached (time set to 1)", [cfg],
                          mode="start", timeout=8.0,
                          expect="nocrash",
                          quote=Q["time_limit"] + " / " + Q["no_crash"],
                          shown_args=[name], show=[cap(
                              [f'"{pattern(p)}": 1' for p in times], 3)]))
    if lives:
        new = copy.deepcopy(data)
        for p in lives:
            set_path(new, p, 1)
        show = [f'"{pattern(p)}": 1' for p in lives]
        if hs_paths:
            bad = os.path.join(env.tmp("cases"), "no-such-folder", "x",
                               "hs.json")
            for p in hs_paths:
                set_path(new, p, bad)
            show.append("highscore file in a folder that does not exist")
        name = "one-life.json"
        cfg = env.write_case(name, json.dumps(new, indent=2))
        for s in ([7] if args.quick else [7, 8]):
            cases.append(Case(f"game over with 1 life, random keys (seed "
                              f"{s})", [cfg], mode="fuzz", seed=s,
                              timeout=play_time, expect="nocrash",
                              quote=Q["no_crash"],
                              shown_args=[name], show=show))
    outs = env.run_all(cases)
    for case, out in zip(cases, outs):
        judge(rep, case, out, base)
        if case.mode in ("quit", "menuquit") and out.alive \
                and not out.traceback:
            rep.warn(case.label + ": the game keeps running",
                     "the window close button does nothing")
        if case.mode == "fuzz" and not out.alive and not out.traceback \
                and rep.verbose:
            last = out.events[-1].strip() if out.events else "no key"
            rep.info(f"{case.label}: the game closed itself after "
                     f"{out.elapsed:.1f}s ({last})")
    if rep.verbose:
        rep.info("not checked (no common interface in the subject): walls, "
                 "ghost moves, score rules, lives, levels")


# --------------------------------------------------------------------------
# repository
# --------------------------------------------------------------------------

def group_repo(rep: core.Report, proj: Project) -> None:
    rep.section("repository", gid="repo")
    core.check_repo(rep, proj.path)
    res = core.run(["git", "ls-files"], cwd=proj.path, timeout=30)
    if res.rc != 0:
        return
    files = [f for f in res.out.decode("utf-8", "replace").split("\n") if f]
    venv = sorted({f.split("/")[0] for f in files if re.match(
        r"(\.?venv|env)/", f) or "/site-packages/" in f})
    if venv:
        rep.warn("a virtualenv is committed: " + cap(venv),
                 "subject: " + Q["gitignore"])
    built = [f for f in files if re.match(r"(build|dist)/", f)
             or f.endswith((".exe", ".app", ".dmg", ".AppImage"))]
    if built:
        rep.warn("build output is committed: " + cap(built, 4),
                 "subject: " + Q["packaging"] + " (the source and the "
                 "spec, the build goes to the gaming platform)")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

RUNTIME = ("makefile", "usage", "config", "highscore", "generator", "play")


def main() -> int:
    args = core.parse_args("Pacman tester", GROUPS)
    rep = core.Report(verbose=args.verbose)
    if not os.path.isdir(args.project):
        print(core.red(f"project directory not found: {args.project}"))
        return 2
    core.header("Pacman", args.project,
                note="runtime checks: headless, in a temporary copy and "
                     "venv", verbose=args.verbose)
    proj = Project(args.project)
    dry = Dry(args.project, args.keep)
    env = Env(proj, args, dry)
    try:
        find_config(proj, dry)
        if any(core.selected(args, g) for g in RUNTIME):
            if proj.has_main or proj.targets:
                env.setup()
        if core.selected(args, "files"):
            group_files(rep, proj)
        if core.selected(args, "makefile"):
            group_makefile(rep, proj, env, dry)
        if core.selected(args, "lint"):
            group_lint(rep, proj, env)
        if core.selected(args, "source"):
            group_source(rep, proj, env)
        if core.selected(args, "readme"):
            group_readme(rep, proj, env)
        if core.selected(args, "usage"):
            group_usage(rep, proj, env)
        if core.selected(args, "config"):
            group_config(rep, proj, env, args)
        if core.selected(args, "highscore"):
            group_highscore(rep, proj, env)
        if core.selected(args, "generator"):
            group_generator(rep, proj, env)
        if core.selected(args, "play"):
            group_play(rep, proj, env, args)
        if core.selected(args, "repo"):
            group_repo(rep, proj)
    finally:
        env.close()
        dry.close()
    return rep.finish()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped (ctrl-c)")
        sys.exit(130)
