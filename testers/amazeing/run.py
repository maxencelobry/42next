#!/usr/bin/env python3
"""42next - A-Maze-ing tester (subject v1.5).

    python3 -B run.py --project DIR [--only g1,g2] [--list] [--quick]
                      [--verbose] [--keep] [--no-color]

The program is run as the subject says (python3 a_maze_ing.py config.txt)
in a pseudo-terminal, inside a temporary copy of the project, with many
configuration files written by the tester. Every output file is parsed and
the maze is validated here: dimensions, hex digits, wall coherence between
neighbours, closed outer border, connectivity, isolated cells, 3x3 open
areas, the "42" made of fully closed cells, the PERFECT=True tree, the
PERFECT=False board rules (loops, corners, centre, dead-ends) and the
shortest path (replayed, and compared with a BFS done here).

The reusable package (mazegen-*) is installed in a temporary venv without
network, imported and used; the build is replayed from the sources when the
build backend is installed on this machine.
--quick: no 50x50 / 60x40 / 100x100 mazes, no rebuild of the package.

No window is opened: DISPLAY / WAYLAND_DISPLAY are removed for the program.
"""

import ast
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import zipfile
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import core  # noqa: E402

try:
    import fcntl
    import struct
    import termios
    HAVE_PTY = hasattr(os, "openpty")
except ImportError:     # Windows
    HAVE_PTY = False

GROUPS = [
    ("files", "a_maze_ing.py, Makefile, default config, LICENSE.md, "
              ".gitignore"),
    ("makefile", "Makefile rules: install, run, debug, clean, lint (flags)"),
    ("lint", "flake8 . and mypy with the subject's flags, docstrings"),
    ("readme", "README.md requirements (+ config, algorithm, reuse, team)"),
    ("repo", "repository hygiene"),
    ("errors", "invalid configurations: clear error, no traceback"),
    ("output", "subject example config: output file format + maze"),
    ("perfect", "PERFECT=True mazes: valid, perfect, shortest path"),
    ("board", "PERFECT=False boards: loops, corners, centre, dead-ends"),
    ("pattern", "the \"42\" of fully closed cells, small mazes"),
    ("seed", "same seed = same maze, other seed = other maze"),
    ("visual", "display and keys in a pseudo-terminal (no crash)"),
    ("package", "mazegen-* file: install, import, use the class"),
    ("build", "rebuild the package from the sources (offline)"),
]

# Sentences of the subject (v1.5) behind every KO.
Q = {
    "main": "a_maze_ing.py is your main program file. You must use this "
            "name.",
    "usage": "Your program must be run with the following command: "
             "python3 a_maze_ing.py config.txt",
    "errors": "Your program must handle all errors gracefully: invalid "
              "configuration, file not found, bad syntax, impossible maze "
              "parameters, etc.",
    "crash": "It must never crash unexpectedly, and must always provide a "
             "clear error message to the user.",
    "review": "If your program crashes due to unhandled exceptions during "
              "the review, it will be considered non-functional.",
    "kv": "The configuration file must contain one 'KEY=VALUE' pair per "
          "line.",
    "comments": "Lines starting with # are comments and must be ignored.",
    "mandatory": "The following keys are mandatory: WIDTH, HEIGHT, ENTRY, "
                 "EXIT, OUTPUT_FILE, PERFECT",
    "default_cfg": "A default configuration file must be available in your "
                   "Git repository.",
    "seed": "The maze must be randomly generated, but reproducibility via "
            "a seed is required.",
    "entry": "Entry and exit exist and are different, inside the maze "
             "bounds.",
    "connect": "The structure ensures full connectivity and no isolated "
               "cells (except the '42' pattern, see below).",
    "border": "As entry and exit are specific cells, there must be walls at "
              "the external borders.",
    "coherent": "Your generated data must be coherent: each neighboring cell "
                "must have the same wall if any.",
    "open": "Corridors can't be wider than 2 cells. [...] you can have 2x3 "
            "or 3x2 open area, but never a 3x3 open area.",
    "42": "the maze must contain a visible \"42\" drawn by several fully "
          "closed cells.",
    "42small": "The \"42\" pattern may be omitted in case the maze size does "
               "not allow it (i.e. too small). Print an error message on "
               "the console in that case.",
    "perfect": "If the PERFECT flag is activated, the maze must contain "
               "exactly one path between the entry and the exit (i.e., it "
               "must be a perfect maze: no loops at all).",
    "board": "If the PERFECT flag is not activated (the default), the maze "
             "must instead be a board directly usable by a Pac-Man-like "
             "game.",
    "board_conn": "every corridor is reachable (full connectivity)",
    "corners": "the four corners and the centre are open corridors",
    "loops": "it offers at least two independent routes (loops) [...] a "
             "perfect maze with merely one wall removed (a single loop), is "
             "therefore not acceptable in this mode",
    "deadends": "dead-ends should stay rare (a couple are tolerated)",
    "write": "generates a maze [...] and writes it to a file using a "
             "hexadecimal wall representation.",
    "hex": "The maze must be written in the output file using one "
           "hexadecimal digit per cell, where each digit encodes which "
           "walls are closed",
    "rows": "Cells are stored row by row, one row per line.",
    "after": "After an empty line, the following 3 elements are inserted in "
             "the output file on 3 lines: the entry coordinates, the exit "
             "coordinates, and the shortest valid path from entry to exit, "
             "using the four letters N, E, S, W.",
    "newline": "All lines end with a \\n.",
    "visual": "Your program must provide a way to display the maze visually",
    "interact": "User interactions must be available while the program is "
                "running",
    "regen": "Re-generate a new maze and display it.",
    "class": "You must implement the maze generation as a unique class "
             "(e.g., 'MazeGenerator') inside a standalone module that can be "
             "imported in a future project.",
    "pip": "This entire reusable module (code and documentation) must be "
           "available in a single file suitable for a later installation "
           "by pip.",
    "pkgname": "This package must be called mazegen-* and the file must be "
               "located at the root of your git repository.",
    "pkgext": "Both .tar.gz and .whl extensions are allowed, as generated by "
              "the standard build of a Python package.",
    "build": "You must provide in your Git repository all needed elements "
             "to build the package.",
    "license": "you must include a LICENSE.md file at the root of your "
               "repository",
    "license_reuse": "The license you pick must explicitly allow the reuse "
                     "and distribution of your maze generator",
    "readme_doc": "The main README.md file (not part of the reusable module) "
                  "must also contain this short documentation.",
    "doc_example": "Instantiate and use your generator, with at least a "
                   "basic example.",
    "doc_params": "Pass custom parameters (e.g., size, seed).",
    "doc_access": "Access the generated structure, and access at least a "
                  "solution.",
    "readme": "A README.md file must be provided at the root of your Git "
              "repository.",
    "readme_cfg": "The complete structure and format of your config file.",
    "readme_algo": "The maze generation algorithm you chose.",
    "readme_why": "Why you chose this algorithm.",
    "readme_reuse": "What part of your code is reusable, and how.",
    "readme_team": "Your team and project management with: [...]",
    "flake8": "Your activity must adhere to the flake8 coding standard.",
    "mypy": "Use mypy for static type checking. All functions must pass "
            "mypy without errors.",
    "docstring": "Include docstrings in functions and classes following "
                 "PEP 257",
    "makefile": "Include a Makefile in your activity to automate common "
                "tasks. It must contain the following rules",
    "lint": "lint: flake8 . and mypy . --warn-return-any "
            "--warn-unused-ignores --ignore-missing-imports "
            "--disallow-untyped-defs --check-untyped-defs",
    "run": "run: Execute the main script of your activity",
    "debug": "debug: Run the main script in debug mode using Python's "
             "built-in debugger (e.g., pdb).",
    "clean": "clean: Remove temporary files or caches (e.g., __pycache__, "
             ".mypy_cache)",
    "install": "install: Install activity dependencies using pip, uv, pipx, "
               "or any other package manager of your choice.",
    "gitignore": "Include a .gitignore file to exclude Python artifacts.",
}

MAIN = "a_maze_ing.py"
MANDATORY = ("WIDTH", "HEIGHT", "ENTRY", "EXIT", "OUTPUT_FILE", "PERFECT")
LINT_FLAGS = ("--warn-return-any", "--warn-unused-ignores",
              "--ignore-missing-imports", "--disallow-untyped-defs",
              "--check-untyped-defs")
SKIP_DIRS = {".git", "__pycache__", ".mypy_cache", ".venv", "venv", "env",
             "build", "dist", "node_modules", ".tox", ".pytest_cache"}

N, E, S, W = 1, 2, 4, 8
# letter: (dx, dy, wall bit of the cell, wall bit of the neighbour)
MOVES = {"N": (0, -1, N, S), "E": (1, 0, E, W), "S": (0, 1, S, N),
         "W": (-1, 0, W, E)}
WALL_NAME = {"N": "north", "E": "east", "S": "south", "W": "west"}

TRACEBACK = "Traceback (most recent call last)"
ERRORISH = re.compile(
    r"error|invalid|must|missing|cannot|can't|could not|couldn't|not found|"
    r"unknown|expected|wrong|\bbad\b|out of|outside|should|impossible|fail|"
    r"no such|usage|not a |not allowed|too (small|big|large)|incorrect|"
    r"illegal|refus|unable|denied|exist|required|need|empty|malformed|"
    r"erreur|line \d", re.I)
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*"
                     r"(?:\x07|\x1b\\)|\x1b[()*+][0-9A-Za-z]|"
                     r"\x1b[=>78DEHMNOc]")

PY = shutil.which("python3") or sys.executable


def ko(rep, name, ok, quote, expected=None, got=None, detail=None):
    """rep.check with the subject sentence as the last detail line."""
    lines = [detail] if detail else []
    lines.append(f'subject: "{Q[quote]}"')
    return rep.check(name, ok, expected=expected, got=got,
                     detail="\n".join(lines))


def warn(rep, name, detail=None, quote=None):
    lines = [detail] if detail else []
    if quote:
        lines.append(f'subject: "{Q[quote]}"')
    rep.warn(name, "\n".join(lines) or None)


def read_text(path, limit=4 << 20):
    try:
        with open(path, "rb") as fh:
            data = fh.read(limit)
    except OSError:
        return None
    return data.decode("utf-8", "replace")


def child_env(extra=None):
    env = dict(os.environ)
    for k in ("DISPLAY", "WAYLAND_DISPLAY", "PYTHONPATH", "PYTHONSTARTUP",
              "PYTHONHOME"):
        env.pop(k, None)
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1", "TERM": "xterm-256color",
                "COLUMNS": "160", "LINES": "50", "PIP_NO_INPUT": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    if extra:
        env.update(extra)
    return env


def screen_text(data):
    """Terminal bytes as plain text: no escape sequences, no \\r."""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) \
        else data
    text = ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in text if c in "\n\t" or ord(c) >= 32)


def tail(text, n=8, width=150):
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out = [ln[:width] for ln in lines[-n:]]
    return "\n".join(out) if out else "(nothing printed)"


def traceback_tail(text, n=6):
    i = text.rfind(TRACEBACK)
    if i < 0:
        return tail(text, n)
    lines = [ln for ln in text[i:].splitlines() if ln.strip()]
    return "\n".join(lines[:1] + lines[1:][-(n - 1):])


# --------------------------------------------------------------------------
# a program running in a pseudo-terminal
# --------------------------------------------------------------------------

def _ctty():
    """Child side: own session, the pty as its controlling terminal."""
    os.setsid()
    try:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    except OSError:
        pass


class Live:
    """A program started in a pseudo-terminal (a pipe without pty).

    The output is read continuously, so a program that redraws forever
    never blocks. stop() kills the whole process group.
    """

    KEEP = 2 << 20

    def __init__(self, cmd, cwd, env):
        self.buf = bytearray()
        self.total = 0
        self.fd = None
        self.proc = None
        self.error = None
        self.eof = False
        self.cmd = cmd
        try:
            if HAVE_PTY:
                master, slave = os.openpty()
                try:
                    fcntl.ioctl(slave, termios.TIOCSWINSZ,
                                struct.pack("HHHH", 50, 160, 0, 0))
                except OSError:
                    pass
                try:
                    self.proc = subprocess.Popen(
                        cmd, cwd=cwd, env=env, stdin=slave, stdout=slave,
                        stderr=slave, preexec_fn=_ctty, close_fds=True)
                finally:
                    os.close(slave)
                self.fd = master
            else:
                self.proc = subprocess.Popen(
                    cmd, cwd=cwd, env=env, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.fd = self.proc.stdout.fileno()
        except OSError as exc:
            self.error = str(exc)
            if self.fd is not None and HAVE_PTY:
                os.close(self.fd)
            self.fd = None

    def _add(self, data):
        self.buf += data
        self.total += len(data)
        if len(self.buf) > self.KEEP:
            del self.buf[:len(self.buf) - self.KEEP]

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    @property
    def rc(self):
        return None if self.proc is None else self.proc.poll()

    def pump(self, seconds):
        """Read the output for up to `seconds` (less if the program ends)."""
        end = time.monotonic() + seconds
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return
            if self.fd is None or self.eof:
                if not self.alive():
                    return
                time.sleep(min(left, 0.05))
                continue
            try:
                ready, _, _ = select.select([self.fd], [], [],
                                            min(left, 0.05))
            except (OSError, ValueError):
                self.eof = True
                continue
            if ready:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    data = b""
                if not data:
                    self.eof = True
                    continue
                self._add(data)
            elif not self.alive():
                return

    def wait_for(self, cond, timeout, step=0.1):
        """Pump until cond() is true or the program ended. Returns cond()."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            if not self.alive():
                self.pump(0.2)
                return cond()
            self.pump(step)
        return cond()

    def send(self, data):
        if self.fd is None:
            return
        try:
            if HAVE_PTY:
                os.write(self.fd, data)
            elif self.proc and self.proc.stdin:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
        except OSError:
            pass

    def canonical(self):
        """True when the terminal is in line mode (input()), False for
        curses / getch programs, None when unknown."""
        if not HAVE_PTY or self.fd is None:
            return None
        try:
            return bool(termios.tcgetattr(self.fd)[3] & termios.ICANON)
        except (termios.error, OSError):
            return None

    def interrupt(self):
        """What Ctrl-C does: SIGINT to the foreground process group."""
        if not self.alive():
            return
        try:
            os.killpg(self.proc.pid, signal.SIGINT)
        except OSError:
            self.proc.send_signal(signal.SIGINT)

    def stop(self):
        if self.proc is None:
            return
        if self.alive():
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self.proc.pid, sig)
                except OSError:
                    try:
                        self.proc.send_signal(sig)
                    except OSError:
                        pass
                try:
                    self.proc.wait(timeout=1.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
        else:
            # what is left of a process group (make -> python)
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except OSError:
                pass
        self.pump(0.1)
        if self.fd is not None and HAVE_PTY:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.fd = None
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    @property
    def text(self):
        return screen_text(bytes(self.buf))


# --------------------------------------------------------------------------
# configurations
# --------------------------------------------------------------------------

class Case:
    """A configuration the tester writes, and what to expect."""

    def __init__(self, name, w, h, entry, exit_, perfect, extra=None,
                 comments=False, timeout=15, big=False):
        self.name = name
        self.w, self.h = w, h
        self.entry, self.exit = entry, exit_
        self.perfect = perfect
        self.extra = extra or {}
        self.comments = comments
        self.timeout = timeout
        self.big = big
        self.out = f"amz_{name}.txt"
        self.cfg_name = f"cfg_{name}.txt"

    @property
    def label(self):
        mode = "perfect" if self.perfect else "board"
        return f"{self.w}x{self.h} {mode} {fmt_xy(self.entry)}->" \
               f"{fmt_xy(self.exit)}"

    def pairs(self, extras):
        p = [("WIDTH", str(self.w)), ("HEIGHT", str(self.h)),
             ("ENTRY", f"{self.entry[0]},{self.entry[1]}"),
             ("EXIT", f"{self.exit[0]},{self.exit[1]}"),
             ("OUTPUT_FILE", self.out),
             ("PERFECT", "True" if self.perfect else "False")]
        ex = dict(extras)
        for k, v in self.extra.items():
            for old in [o for o in ex if o.upper() == k.upper()]:
                del ex[old]
            ex[k] = v
        p += [(k, v) for k, v in ex.items() if v is not None]
        return p

    def text(self, extras):
        pairs = self.pairs(extras)
        if not self.comments:
            return "".join(f"{k}={v}\n" for k, v in pairs)
        out = ["# A-Maze-ing configuration written by 42next",
               "#WIDTH=3", "#   indented text after a hash"]
        for k, v in reversed(pairs):
            out.append(f"{k}={v}")
            out.append(f"# {k} set above")
        return "\n".join(out) + "\n"

    def oneline(self, extras):
        return " ".join(f"{k}={v}" for k, v in self.pairs(extras))


def fmt_xy(p):
    return f"({p[0]},{p[1]})"


def parse_config(text):
    """{KEY (upper): value} of a KEY=VALUE file."""
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def looks_like_config(text):
    return bool(re.search(r"^\s*WIDTH\s*=", text, re.M | re.I)
                and re.search(r"^\s*HEIGHT\s*=", text, re.M | re.I))


def find_default_config(project):
    """(relative path, text) of the default configuration file."""
    found = []
    for root, dirs, files in os.walk(project):
        rel_root = os.path.relpath(root, project)
        depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                         and not d.startswith(".") and depth < 1)
        for f in sorted(files):
            if f.endswith((".py", ".whl", ".gz", ".md", ".toml")):
                continue
            p = os.path.join(root, f)
            try:
                if os.path.getsize(p) > 65536:
                    continue
            except OSError:
                continue
            text = read_text(p)
            if text and looks_like_config(text):
                rel = os.path.relpath(p, project).replace(os.sep, "/")
                found.append((rel, text))

    def rank(item):
        rel = item[0]
        base = os.path.basename(rel).lower()
        return ("/" in rel, base != "config.txt",
                "default" not in base and "config" not in base, rel)
    found.sort(key=rank)
    return found[0] if found else (None, None)


def py_files(top):
    out = []
    for root, dirs, files in os.walk(top):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                         and not d.startswith(".")
                         and not d.endswith(".egg-info"))
        for f in sorted(files):
            if f.endswith(".py"):
                out.append(os.path.join(root, f))
    return out


# --------------------------------------------------------------------------
# output file and maze validation
# --------------------------------------------------------------------------

class Parsed:
    """The output file split as the subject defines it."""

    def __init__(self):
        self.cells = None       # [[int]] h rows of w values, when readable
        self.entry_line = None
        self.exit_line = None
        self.path = None
        self.problems = []      # (check name, quote, expected, got, detail)
        self.lower = False
        self.extra_lines = []


def parse_xy_line(line):
    m = re.fullmatch(r"\s*\(?\s*(\d+)\s*,\s*(\d+)\s*\)?\s*", line or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_output(text, w, h):
    p = Parsed()
    if not text.endswith("\n") or "\r" in text:
        why = "the file does not end with \\n" if not text.endswith("\n") \
            else "\\r\\n line endings"
        p.problems.append(("every line ends with \\n", "newline",
                           None, why, f"last bytes: {core.crepr(text[-20:])}"))
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = [ln.rstrip("\r") for ln in lines]
    blank = next((i for i, ln in enumerate(lines) if ln.strip() == ""), None)
    rows = lines[:blank] if blank is not None else lines[:h]
    lens = sorted({len(r) for r in rows})
    if len(rows) != h or lens != [w]:
        p.problems.append((f"{h} rows of {w} digits", "rows",
                           f"{h} lines of {w} hex digits, then an empty line",
                           f"{len(rows)} lines before the empty line, "
                           f"lengths {lens[:6]}", None))
        return p
    for y, r in enumerate(rows):
        for x, c in enumerate(r):
            if c not in "0123456789ABCDEFabcdef":
                p.problems.append(("one hex digit per cell", "hex",
                                   "0-9 A-F", f"{c!r} at cell ({x},{y}) "
                                   f"(line {y + 1}, column {x + 1})", None))
                return p
            if c in "abcdef":
                p.lower = True
    p.cells = [[int(c, 16) for c in r] for r in rows]
    if blank != h:
        p.problems.append(("empty line after the grid", "after",
                           "an empty line", "no empty line after the grid",
                           None))
        return p
    rest = lines[h + 1:]
    p.entry_line = rest[0] if len(rest) > 0 else None
    p.exit_line = rest[1] if len(rest) > 1 else None
    p.path = rest[2] if len(rest) > 2 else None
    p.extra_lines = rest[3:]
    return p


class Maze:
    """A grid of wall bits and everything the checks need."""

    def __init__(self, cells, w, h, entry, exit_):
        self.c = cells
        self.w, self.h = w, h
        self.entry, self.exit = entry, exit_
        self.mismatch = []
        for y in range(h):
            for x in range(w):
                v = cells[y][x]
                if x + 1 < w and bool(v & E) != bool(cells[y][x + 1] & W):
                    self.mismatch.append((x, y, "E"))
                if y + 1 < h and bool(v & S) != bool(cells[y + 1][x] & N):
                    self.mismatch.append((x, y, "S"))
        self.border = []
        for x in range(w):
            if not cells[0][x] & N:
                self.border.append((x, 0, "N"))
            if not cells[h - 1][x] & S:
                self.border.append((x, h - 1, "S"))
        for y in range(h):
            if not cells[y][0] & W:
                self.border.append((0, y, "W"))
            if not cells[y][w - 1] & E:
                self.border.append((w - 1, y, "E"))
        self.closed = {(x, y) for y in range(h) for x in range(w)
                       if cells[y][x] == 15}
        self.dist, self.prev = self.bfs(entry)
        self.reach = set(self.dist)
        self.edges = 0
        self.deg = {}
        for y in range(h):
            for x in range(w):
                d = sum(1 for m in MOVES if self.passable(x, y, m))
                self.deg[(x, y)] = d
                if (x, y) in self.reach:
                    self.edges += sum(1 for m in "ES"
                                      if self.passable(x, y, m))
        # independent loops of the part reachable from the entry
        self.cycles = self.edges - len(self.reach) + 1
        self.isolated = sorted(
            (p for p in ((x, y) for y in range(h) for x in range(w))
             if p not in self.reach and p not in self.closed),
            key=lambda p: (p[1], p[0]))
        self.dead_ends = sorted(p for p in self.reach if self.deg[p] == 1)

    def inside(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h

    def passable(self, x, y, m):
        dx, dy, bit, opp = MOVES[m]
        nx, ny = x + dx, y + dy
        return self.inside(nx, ny) and not self.c[y][x] & bit \
            and not self.c[ny][nx] & opp

    def bfs(self, start):
        dist = {start: 0}
        prev = {}
        q = deque([start])
        while q:
            x, y = q.popleft()
            for m, (dx, dy, _, _) in MOVES.items():
                n = (x + dx, y + dy)
                if n not in dist and self.passable(x, y, m):
                    dist[n] = dist[(x, y)] + 1
                    prev[n] = ((x, y), m)
                    q.append(n)
        return dist, prev

    def bfs_path(self):
        if self.exit not in self.prev and self.exit != self.entry:
            return None
        out = []
        p = self.exit
        while p != self.entry:
            p, m = self.prev[p]
            out.append(m)
        return "".join(reversed(out))

    def walk(self, path):
        """(ok, why, cells visited) of a path replayed from the entry."""
        x, y = self.entry
        seen = [(x, y)]
        for i, ch in enumerate(path):
            if ch not in MOVES:
                return False, f"move {i + 1} is {ch!r}, not N, E, S or W", \
                    seen
            if not self.passable(x, y, ch):
                dx, dy = MOVES[ch][:2]
                what = "the outer border" if not self.inside(x + dx, y + dy) \
                    else f"a closed wall ({WALL_NAME[ch]} wall of " \
                         f"({x},{y}) = {self.c[y][x]:X})"
                return False, f"move {i + 1} ({ch}) from ({x},{y}) goes " \
                              f"through {what}", seen
            x, y = x + MOVES[ch][0], y + MOVES[ch][1]
            seen.append((x, y))
        if (x, y) != self.exit:
            return False, f"the path ends at ({x},{y}), not at the exit " \
                          f"{fmt_xy(self.exit)}", seen
        return True, None, seen

    def open_areas(self):
        """Top-left corners of the 3x3 blocks with no inner wall."""
        out = []
        for y0 in range(self.h - 2):
            for x0 in range(self.w - 2):
                if all(self.passable(x, y, "E") for y in range(y0, y0 + 3)
                       for x in range(x0, x0 + 2)) and \
                        all(self.passable(x, y, "S")
                            for y in range(y0, y0 + 2)
                            for x in range(x0, x0 + 3)):
                    out.append((x0, y0))
        return out

    def draw(self, cx, cy, marks=None, rx=5, ry=3):
        """ASCII view of the cells around (cx, cy)."""
        marks = marks or {}
        x0, x1 = max(0, cx - rx), min(self.w - 1, cx + rx)
        y0, y1 = max(0, cy - ry), min(self.h - 1, cy + ry)
        lines = [f"cells x {x0}..{x1}, y {y0}..{y1} "
                 "(E entry, X exit, ## fully closed, ** checked here)"]
        for y in range(y0, y1 + 1):
            top = "".join("+" + ("--" if self.c[y][x] & N else "  ")
                          for x in range(x0, x1 + 1)) + "+"
            mid = ""
            for x in range(x0, x1 + 1):
                mid += "|" if self.c[y][x] & W else " "
                mid += self.mark(x, y, marks)
            mid += "|" if self.c[y][x1] & E else " "
            lines += [top, mid]
        lines.append("".join("+" + ("--" if self.c[y1][x] & S else "  ")
                             for x in range(x0, x1 + 1)) + "+")
        return "\n".join(lines)

    def mark(self, x, y, marks):
        if (x, y) in marks:
            return marks[(x, y)]
        if (x, y) == self.entry:
            return "E "
        if (x, y) == self.exit:
            return "X "
        if (x, y) in self.closed:
            return "##"
        return "  "

    def closed_mask(self):
        if not self.closed:
            return ""
        xs = [p[0] for p in self.closed]
        ys = [p[1] for p in self.closed]
        lines = [f"fully closed cells (x {min(xs)}..{max(xs)}, "
                 f"y {min(ys)}..{max(ys)}):"]
        for y in range(min(ys), max(ys) + 1):
            lines.append("  " + "".join(
                "#" if (x, y) in self.closed else "."
                for x in range(min(xs), max(xs) + 1)))
        return "\n".join(lines)

    def closed_groups(self):
        """4-connected groups of fully closed cells, left to right."""
        left = set(self.closed)
        groups = []
        while left:
            start = left.pop()
            group = {start}
            q = [start]
            while q:
                x, y = q.pop()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    n = (x + dx, y + dy)
                    if n in left:
                        left.discard(n)
                        group.add(n)
                        q.append(n)
            groups.append(group)
        groups.sort(key=lambda g: min(p[0] for p in g))
        return groups

    def centres(self):
        xs = sorted({(self.w - 1) // 2, self.w // 2})
        ys = sorted({(self.h - 1) // 2, self.h // 2})
        return [(x, y) for y in ys for x in xs]


def cells_text(cells, limit=6):
    s = ", ".join(fmt_xy(p) for p in cells[:limit])
    return s + (f" (+{len(cells) - limit} more)" if len(cells) > limit
                else "")


def validate(ctx, case, out, label=None, mode=True):
    """Every check of a written output file. Returns the Maze or None."""
    rep = ctx.rep
    L = label or case.label
    where = ctx.where(case)
    p = parse_output(out.file_text, case.w, case.h)
    for name, quote, exp, got, det in p.problems:
        ko(rep, f"{L}: {name}", False, quote, expected=exp, got=got,
           detail="\n".join(x for x in (det, where) if x))
    if p.cells is None:
        return None
    ko(rep, f"{L}: {case.h} rows of {case.w} hex digits", True, "hex")
    if p.lower:
        warn(rep, f"{L}: lowercase hex digits in the grid",
             "the subject writes them in uppercase (\"A (binary 1010)\")")
    if p.entry_line is None and not any(q[1] == "after" for q in p.problems):
        ko(rep, f"{L}: entry, exit and path lines after the grid", False,
           "after", got="nothing after the empty line", detail=where)
    m = Maze(p.cells, case.w, case.h, case.entry, case.exit)
    if p.entry_line is not None:
        ko(rep, f"{L}: entry line", parse_xy_line(p.entry_line) == case.entry,
           "after", expected=f"{case.entry[0]},{case.entry[1]}",
           got=core.crepr(p.entry_line), detail=where)
    if p.exit_line is not None:
        ko(rep, f"{L}: exit line", parse_xy_line(p.exit_line) == case.exit,
           "after", expected=f"{case.exit[0]},{case.exit[1]}",
           got=core.crepr(p.exit_line), detail=where)
    if p.entry_line is not None and p.exit_line is None:
        ko(rep, f"{L}: exit line", False, "after", got="missing",
           detail=where)
    if p.exit_line is not None and p.path is None:
        ko(rep, f"{L}: shortest path line", False, "after", got="missing",
           detail=where)
    if p.extra_lines and any(x.strip() for x in p.extra_lines):
        warn(rep, f"{L}: {len(p.extra_lines)} extra line(s) after the path",
             f"first: {core.crepr(p.extra_lines[0], 80)}", quote="after")
    # walls
    if m.mismatch:
        x, y, d = m.mismatch[0]
        nx, ny = (x + 1, y) if d == "E" else (x, y + 1)
        lines = []
        for (a, b, dd) in m.mismatch[:4]:
            na, nb = (a + 1, b) if dd == "E" else (a, b + 1)
            od = "W" if dd == "E" else "N"
            sa = "closed" if p.cells[b][a] & MOVES[dd][2] else "open"
            sb = "closed" if p.cells[nb][na] & MOVES[od][2] else "open"
            lines.append(f"({a},{b})={p.cells[b][a]:X} {WALL_NAME[dd]} wall "
                         f"{sa}, ({na},{nb})={p.cells[nb][na]:X} "
                         f"{WALL_NAME[od]} wall {sb}")
        ko(rep, f"{L}: walls coherent between neighbours", False, "coherent",
           expected="the same wall seen from both cells",
           got=f"{len(m.mismatch)} shared wall(s) encoded differently",
           detail="\n".join(lines) + "\n" + m.draw(
               x, y, {(x, y): "**", (nx, ny): "**"}) + "\n" + where)
    else:
        ko(rep, f"{L}: walls coherent between neighbours", True, "coherent")
    if m.border:
        x, y, d = m.border[0]
        ko(rep, f"{L}: outer border closed", False, "border",
           expected="every border wall closed (entry and exit too)",
           got=f"{len(m.border)} open border wall(s): " + ", ".join(
               f"{WALL_NAME[dd]} of {fmt_xy((a, b))}"
               for a, b, dd in m.border[:5]),
           detail=m.draw(x, y, {(x, y): "**"}) + "\n" + where)
    else:
        ko(rep, f"{L}: outer border closed", True, "border")
    # connectivity
    if case.entry in m.closed or case.exit in m.closed:
        bad = [n for n, q in (("entry", case.entry), ("exit", case.exit))
               if q in m.closed]
        ko(rep, f"{L}: entry and exit are open cells", False, "entry",
           got=" and ".join(bad) + " fully closed (inside the \"42\"?)",
           detail=where)
    ok = case.exit in m.reach
    ko(rep, f"{L}: exit reachable from the entry", ok, "connect",
       got=None if ok else f"only {len(m.reach)} cell(s) reachable",
       detail=None if ok else m.draw(*case.exit) + "\n" + where)
    if m.isolated:
        x, y = m.isolated[0]
        ko(rep, f"{L}: no isolated cells (except the \"42\")", False,
           "connect", expected="every cell reachable, except the fully "
           "closed cells of the \"42\"",
           got=f"{len(m.isolated)} unreachable cell(s) that are not fully "
               f"closed: {cells_text(m.isolated)}",
           detail=m.draw(x, y, {(x, y): "**"}) + "\n" + where)
    else:
        ko(rep, f"{L}: no isolated cells (except the \"42\")", True,
           "connect")
    areas = m.open_areas()
    if areas:
        x, y = areas[0]
        ko(rep, f"{L}: no 3x3 open area", False, "open",
           got=f"{len(areas)} fully open 3x3 block(s), top-left corners "
               f"{cells_text(areas, 4)}",
           detail=m.draw(x + 1, y + 1, {(x + i, y + j): "**"
                                        for i in range(3)
                                        for j in range(3)}) + "\n" + where)
    else:
        ko(rep, f"{L}: no 3x3 open area", True, "open")
    # path
    check_path(ctx, case, m, p.path, L, where)
    if mode:
        check_mode(ctx, case, m, L, where)
    return m


def check_path(ctx, case, m, path, L, where):
    rep = ctx.rep
    if path is None:
        return
    best = m.bfs_path()
    if not path.strip():
        ko(rep, f"{L}: shortest path line", False, "after",
           expected=f"{len(best)} moves" if best else "a path",
           got="empty line", detail=where)
        return
    ok, why, seen = m.walk(path)
    marks = {q: "**" for q in seen[-2:]}
    last = seen[-1]
    if not ok:
        ko(rep, f"{L}: path follows open passages from entry to exit",
           False, "after", got=why,
           detail=f"path: {core.crepr(path, 120)}\n"
                  + m.draw(*last, marks) + "\n" + where)
        return
    ko(rep, f"{L}: path follows open passages from entry to exit", True,
       "after")
    if best is None:
        return
    ko(rep, f"{L}: path is a shortest one", len(path) == len(best), "after",
       expected=f"{len(best)} moves (BFS)", got=f"{len(path)} moves",
       detail=None if len(path) == len(best) else
       f"file: {core.crepr(path, 100)}\nBFS:  {core.crepr(best, 100)}\n"
       + where)


def check_mode(ctx, case, m, L, where):
    rep = ctx.rep
    if case.perfect:
        ok = m.cycles <= 0
        ko(rep, f"{L}: perfect (no loop)", ok, "perfect",
           expected=f"{len(m.reach) - 1} open passages for {len(m.reach)} "
                    "reachable cells (a tree)",
           got=None if ok else f"{m.edges} open passages: "
                               f"{m.cycles} independent loop(s)",
           detail=None if ok else where)
        return
    ok = m.cycles >= 2
    ko(rep, f"{L}: at least two independent loops", ok, "loops",
       expected=">= 2 independent loops",
       got=None if ok else f"{max(m.cycles, 0)} loop(s) ({m.edges} open "
                           f"passages for {len(m.reach)} cells)"
                           + (": a perfect maze" if m.cycles == 0 else ""),
       detail=None if ok else where)
    corners = [(0, 0), (m.w - 1, 0), (0, m.h - 1), (m.w - 1, m.h - 1)]
    bad = [q for q in corners if q not in m.reach or q in m.closed]
    ko(rep, f"{L}: the four corners are open corridors", not bad, "corners",
       got=None if not bad else "not open: " + cells_text(bad),
       detail=None if not bad else m.draw(*bad[0]) + "\n" + where)
    cands = m.centres()
    ok = any(q in m.reach and q not in m.closed for q in cands)
    ko(rep, f"{L}: the centre is an open corridor", ok, "corners",
       expected="one of " + cells_text(cands) + " open and reachable",
       got=None if ok else "all closed or unreachable",
       detail=None if ok else m.draw(*cands[0], {q: "**" for q in cands})
       + "\n" + where)
    # pockets of the "42" (inside the 4, ...) are dead-ends by force: the
    # subject's analyzer accepts "no dead-ends besides the 42 pattern"
    near = {(x + dx, y + dy) for x, y in m.closed
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))}
    dead = [q for q in m.dead_ends if q not in near]
    if len(dead) > 2:
        x, y = dead[0]
        warn(rep, f"{L}: {len(dead)} dead-ends (not counting the "
                  f"{len(m.dead_ends) - len(dead)} next to the \"42\")",
             f"dead-ends: {cells_text(dead)}\n"
             + m.draw(x, y, {(x, y): "**"}), quote="deadends")


# --------------------------------------------------------------------------
# context: the workspace, the default config, the runs
# --------------------------------------------------------------------------

class Outcome:
    def __init__(self, live, file_text, timed_out, elapsed):
        self.text = live.text
        self.rc = live.rc
        self.error = live.error
        self.file_text = file_text
        self.timed_out = timed_out
        self.elapsed = elapsed
        self.kept_running = False

    @property
    def traceback(self):
        return TRACEBACK in self.text

    @property
    def crashed(self):
        return self.rc is not None and self.rc < 0 and \
            self.rc not in (-signal.SIGTERM, -signal.SIGKILL)

    def status(self):
        if self.error:
            return f"cannot start: {self.error}"
        if self.kept_running:
            return "still running (stopped by the tester)"
        if self.rc is not None and self.rc < 0:
            try:
                return f"killed by {signal.Signals(-self.rc).name}"
            except ValueError:
                return f"killed by signal {-self.rc}"
        return f"exit code {self.rc}"


def file_complete(text):
    if not text or not text.endswith("\n"):
        return False
    parts = text.split("\n\n", 1)
    if len(parts) < 2:
        return False
    return len([ln for ln in parts[1].split("\n") if ln.strip()]) >= 3


class Ctx:
    def __init__(self, args, rep, ws):
        self.args = args
        self.rep = rep
        self.ws = ws
        self.project = args.project
        self.has_main = os.path.isfile(ws.path(MAIN))
        self.cfg_dir = ws.tmp("configs")
        os.makedirs(self.cfg_dir, exist_ok=True)
        self.default_cfg, self.default_text = find_default_config(
            args.project)
        values = parse_config(self.default_text or "")
        self.extras = {k: v for k, v in values.items()
                       if k.upper() not in MANDATORY}
        self.seed_key = next((k for k in self.extras if "SEED" in k.upper()),
                             None)
        if self.seed_key is None and self.has_main:
            for f in py_files(ws.dir):
                text = read_text(f) or ""
                if re.search(r"""["']seed["']""", text, re.I):
                    self.seed_key = "SEED"
                    break
        self.cache = {}
        self.timeouts = 0

    def where(self, case):
        return f"run: python3 {MAIN} {case.cfg_name}\nconfig: " \
               f"{case.oneline(self.extras)}" + (
                   f"\nfiles: {self.cfg_dir}" if self.args.keep else "")

    def write_cfg(self, name, text):
        path = os.path.join(self.cfg_dir, name)
        with open(path, "wb") as fh:
            fh.write(text.encode() if isinstance(text, str) else text)
        return path

    def start(self, argv, cwd=None):
        return Live([PY, MAIN] + list(argv), cwd or self.ws.dir, child_env())

    def generate(self, case, keep_live=False):
        """Run a valid configuration and collect the output file."""
        key = (case.name, case.text(self.extras))
        if key in self.cache and not keep_live:
            return self.cache[key]
        cfg = self.write_cfg(case.cfg_name, case.text(self.extras))
        out_path = os.path.join(self.ws.dir, case.out)
        if os.path.exists(out_path):
            os.remove(out_path)
        live = self.start([cfg])
        start = time.monotonic()
        state = {"text": None, "since": start}

        def ready():
            text = read_text(out_path)
            now = time.monotonic()
            if text != state["text"]:
                state["text"] = text
                state["since"] = now
                return False
            if text is None:
                return False
            stable = now - state["since"]
            return (file_complete(text) and stable >= 0.3) or stable >= 1.0

        live.wait_for(ready, case.timeout)
        if state["text"] is None and live.alive():
            # maybe it writes on exit: end of input, then wait a little
            live.send(b"\x04")
            live.wait_for(ready, 2)
        text = read_text(out_path)
        timed_out = text is None and live.alive()
        out = Outcome(live, text, timed_out, time.monotonic() - start)
        out.kept_running = live.alive()
        if keep_live:
            out.live = live
            return out
        live.stop()
        out.text = live.text
        out.rc = live.rc
        self.cache[key] = out
        return out


def run_valid(ctx, case, label=None, small=False):
    """generate + the 'it ran' checks. Returns the Outcome or None.

    small: a tiny maze; refusing it with a clear message is only a WARN
    (the subject does not say how small a maze must be accepted)."""
    rep = ctx.rep
    L = label or case.label
    if ctx.timeouts >= 2:
        return None
    out = ctx.generate(case)
    where = ctx.where(case)
    if out.timed_out:
        ctx.timeouts += 1
        ko(rep, f"{L}: output file written", False, "write",
           got=f"no {case.out} after {case.timeout}s (infinite loop?)",
           detail=f"screen:\n{tail(out.text, 6)}\n{where}")
        if ctx.timeouts >= 2:
            rep.skip("rest of this group", "2 timeouts")
        return None
    if out.traceback or out.crashed:
        ko(rep, f"{L}: no crash", False, "crash", got=out.status(),
           detail=traceback_tail(out.text) + "\n" + where)
    if out.file_text is None:
        if small and not out.traceback and not out.crashed and \
                ERRORISH.search(out.text):
            warn(rep, f"{L}: refused with an error message",
                 f"screen: {tail(out.text, 2)}\n{where}\nthe subject only "
                 "allows to omit the \"42\" on a small maze", quote="42small")
            return None
        ko(rep, f"{L}: output file {case.out} written", False, "write",
           got=out.status(), detail=f"screen:\n{tail(out.text, 6)}\n{where}")
        return None
    rep.check(f"{L}: output file written ({out.elapsed:.1f}s)", True)
    return out


# --------------------------------------------------------------------------
# groups: files, makefile, lint, readme, repo
# --------------------------------------------------------------------------

def git_files(project):
    """Files tracked by git (set), or None when not a git work tree."""
    inside = core.run(["git", "rev-parse", "--is-inside-work-tree"],
                      cwd=project, timeout=30)
    if inside.rc != 0 or inside.out.strip() != b"true":
        return None
    res = core.run(["git", "-c", "core.quotepath=off", "ls-files", "-z"],
                   cwd=project, timeout=30)
    if res.rc != 0:
        return None
    return {f for f in res.out.decode("utf-8", "replace").split("\0") if f}


def root_names(project):
    try:
        return sorted(os.listdir(project))
    except OSError:
        return []


def group_files(ctx):
    rep = ctx.rep
    rep.section("files")
    names = root_names(ctx.project)
    tracked = git_files(ctx.project)

    def in_git(rel):
        return tracked is None or rel in tracked

    def present(name, quote, exact_hint=True):
        ok = name in names
        alt = [n for n in names if n.lower() == name.lower() and n != name]
        got = None
        if not ok:
            got = f"{alt[0]} (wrong case)" if alt else "missing"
        ko(rep, f"{name} at the root", ok, quote, got=got)
        if ok and not in_git(name):
            ko(rep, f"{name} committed in git", False, quote,
               got="not tracked by git (ignored or not added)")
        return ok

    present(MAIN, "main")
    present("Makefile", "makefile")
    rel = ctx.default_cfg
    ko(rep, "default configuration file", rel is not None, "default_cfg",
       expected="a KEY=VALUE file with WIDTH, HEIGHT, ... in the repository",
       got=rel or "none found (root and first-level folders)")
    if rel:
        rep.info(f"default config: {rel}")
        if not in_git(rel):
            ko(rep, f"{rel} committed in git", False, "default_cfg",
               got="not tracked by git")
        keys = {k.upper() for k in parse_config(ctx.default_text)}
        miss = [k for k in MANDATORY if k not in keys]
        ok = not miss or miss == ["PERFECT"]
        ko(rep, f"{rel}: mandatory keys", ok, "mandatory",
           got=None if ok else "missing: " + ", ".join(miss))
    if present("LICENSE.md", "license"):
        text = read_text(os.path.join(ctx.project, "LICENSE.md")) or ""
        known = re.search(r"\bMIT\b|Apache|BSD|GNU|GPL|LGPL|MPL|Mozilla|"
                          r"Unlicense|ISC|Creative Commons|CC0|zlib|WTFPL|"
                          r"Permission is hereby granted|redistribut",
                          text, re.I)
        if not known:
            warn(rep, "LICENSE.md: no known license recognised",
                 f"first line: {core.crepr(text.strip()[:80])}",
                 quote="license_reuse")
        elif re.search(r"non-?commercial|no derivatives|all rights "
                       r"reserved|proprietary", text, re.I):
            warn(rep, "LICENSE.md may forbid reuse or distribution",
                 "check that the later projects may reuse and distribute it",
                 quote="license_reuse")
    if present(".gitignore", "gitignore"):
        text = read_text(os.path.join(ctx.project, ".gitignore")) or ""
        if not re.search(r"__pycache__|\*\.py\[?c", text):
            warn(rep, ".gitignore does not ignore __pycache__ / *.pyc",
                 quote="gitignore")


def make_dry(ws_dir, target):
    res = core.run(["make", "-n", target], cwd=ws_dir, timeout=30,
                   env={"MAKEFLAGS": ""})
    return res.rc, core.output(res)


def group_makefile(ctx):
    rep = ctx.rep
    rep.section("makefile")
    if not core.have("make"):
        rep.skip("makefile", "make not installed")
        return
    with core.Workspace(ctx.project, prefix="42next-amz-mk-") as ws:
        text = core.makefile_text(ws.dir)
        if not ko(rep, "Makefile present", text is not None, "makefile"):
            return
        targets = core.makefile_targets(text)
        rules = ("install", "run", "debug", "clean", "lint")
        miss = [r for r in rules if r not in targets]
        ko(rep, "rules: " + ", ".join(rules), not miss, "makefile",
           got=None if not miss else "missing: " + ", ".join(miss))
        if "lint-strict" in targets:
            rep.info("lint-strict rule present (optional)")
        if "lint" in targets:
            rc, out = make_dry(ws.dir, "lint")
            cmds = out.lower()
            has_f = re.search(r"\bflake8\b", cmds)
            has_m = re.search(r"\bmypy\b", cmds)
            ko(rep, "make lint runs flake8 and mypy", bool(has_f and has_m),
               "lint",
               got="make -n lint: " + tail(out, 4).replace("\n", " ; "))
            mypy_lines = [ln for ln in out.splitlines()
                          if re.search(r"\bmypy\b", ln)]
            joined = " ".join(mypy_lines)
            missing = [f for f in LINT_FLAGS if f not in joined]
            ko(rep, "make lint: mypy with the subject's flags", not missing,
               "lint", got=None if not missing else "missing: "
               + " ".join(missing), detail="mypy line: "
               + (mypy_lines[0].strip()[:200] if mypy_lines else "none"))
            fl = [ln for ln in out.splitlines()
                  if re.search(r"\bflake8\b", ln)]
            if fl and not re.search(r"\bflake8\s+(\S+\s+)*\.(\s|$)", fl[0]):
                warn(rep, "make lint: flake8 is not run on '.'",
                     f"got: {fl[0].strip()[:120]}", quote="lint")
            if mypy_lines and not re.search(r"\bmypy\s+(\S+\s+)*\.(\s|$)",
                                            mypy_lines[0]):
                warn(rep, "make lint: mypy is not run on '.'",
                     f"got: {mypy_lines[0].strip()[:120]}", quote="lint")
        if "debug" in targets:
            rc, out = make_dry(ws.dir, "debug")
            ok = re.search(r"\bpdb\b", out) is not None
            ko(rep, "make debug uses pdb", ok, "debug",
               got=None if ok else "make -n debug: "
               + tail(out, 3).replace("\n", " ; "))
        if "run" in targets:
            rc, out = make_dry(ws.dir, "run")
            ok = MAIN in out
            ko(rep, f"make run executes {MAIN}", ok, "run",
               got=None if ok else "make -n run: "
               + tail(out, 3).replace("\n", " ; "))
        if "install" in targets:
            rc, out = make_dry(ws.dir, "install")
            if not re.search(r"\b(pip3?|uv|pipx|poetry|pdm|conda|hatch|"
                             r"pipenv)\b", out):
                warn(rep, "make install does not call a package manager",
                     "make -n install: " + tail(out, 3).replace("\n", " ; "),
                     quote="install")
        if "clean" in targets:
            check_clean(ctx, ws)
        if "run" in targets and ctx.has_main:
            check_make_run(ctx, ws)


def check_clean(ctx, ws):
    rep = ctx.rep
    made = ["__pycache__", ".mypy_cache"]
    for f in py_files(ws.dir):
        d = os.path.relpath(os.path.dirname(f), ws.dir)
        if d != ".":
            made.append(os.path.join(d, "__pycache__").replace(os.sep, "/"))
    made = list(dict.fromkeys(made))
    for d in made:
        os.makedirs(ws.path(d), exist_ok=True)
        with open(os.path.join(ws.path(d), "x.cpython-3.pyc"), "wb") as fh:
            fh.write(b"\0")
    res = core.run(["make", "clean"], cwd=ws.dir, timeout=60)
    left = [d for d in made if os.path.exists(ws.path(d))]
    ko(rep, "make clean removes __pycache__ and .mypy_cache",
       res.rc == 0 and not left, "clean",
       got=("still there: " + ", ".join(left)) if left else
       (None if res.rc == 0 else
        "make clean failed: " + tail(core.output(res), 3)))


def check_make_run(ctx, ws):
    """make run: the default config gives its output file."""
    rep = ctx.rep
    if not ctx.default_text:
        return
    keys = {k.upper(): v for k, v in parse_config(ctx.default_text).items()}
    out_name = keys.get("OUTPUT_FILE")
    if not out_name:
        return
    target = os.path.join(ws.dir, out_name)
    if os.path.isfile(target):
        os.remove(target)
    live = Live(["make", "run"], ws.dir, child_env())
    live.wait_for(lambda: file_complete(read_text(target) or ""), 30)
    live.pump(0.5)
    text = live.text
    written = os.path.isfile(target)
    live.stop()
    ko(rep, f"make run writes {out_name} (default config)",
       written and TRACEBACK not in text, "run",
       got=None if written and TRACEBACK not in text else
       ("traceback: " + traceback_tail(text) if TRACEBACK in text
        else "no output file after 30s; screen:\n" + tail(text, 5)))


def group_lint(ctx):
    rep = ctx.rep
    rep.section("lint")
    ws = ctx.ws
    if core.have("flake8"):
        res = core.run(["flake8", "."], cwd=ws.dir, timeout=300)
        text = core.output(res).strip()
        n = len([ln for ln in text.splitlines() if ln.strip()])
        ko(rep, "flake8 .", res.rc == 0, "flake8",
           got=None if res.rc == 0 else f"{n} problem(s)",
           detail=None if res.rc == 0 else core.short_errors(text, 10))
    else:
        rep.skip("flake8 .", "flake8 not installed")
    if core.have("mypy"):
        cache = ws.tmp("mypy-cache")
        cmd = ["mypy", "."] + list(LINT_FLAGS) + ["--cache-dir", cache]
        res = core.run(cmd, cwd=ws.dir, timeout=600)
        text = core.output(res).strip()
        errs = [ln for ln in text.splitlines() if ": error:" in ln]
        ko(rep, "mypy . " + " ".join(LINT_FLAGS), res.rc == 0, "mypy",
           got=None if res.rc == 0 else
           (f"{len(errs)} error(s)" if errs else tail(text, 2)),
           detail=None if res.rc == 0 else core.short_errors(text, 10))
    else:
        rep.skip("mypy", "mypy not installed")
    missing = []
    total = 0
    for f in py_files(ws.dir):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                tree = ast.parse(fh.read())
        except (SyntaxError, ValueError, OSError):
            continue
        rel = os.path.relpath(f, ws.dir)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                total += 1
                if ast.get_docstring(node) is None:
                    missing.append(f"{rel}:{node.lineno} {node.name}")
    if missing:
        warn(rep, f"{len(missing)}/{total} functions / classes without a "
                  "docstring", "\n".join(missing[:8])
             + (f"\n... +{len(missing) - 8} more" if len(missing) > 8
                else ""), quote="docstring")
    elif total:
        rep.check(f"docstrings: {total} functions / classes", True)


def find_readme(project):
    names = root_names(project)
    if "README.md" in names:
        return "README.md", "README.md"
    alt = [n for n in names if n.lower() == "readme.md"]
    return None, (alt[0] if alt else None)


def group_readme(ctx):
    rep = ctx.rep
    rep.section("readme")
    exact, alt = find_readme(ctx.project)
    if exact is None:
        ko(rep, "README.md at the root", False, "readme",
           got=f"{alt} (the name must be README.md)" if alt else "missing")
        if not alt:
            return
        rep.info(f"content of {alt} checked below anyway")
        tmp = ctx.ws.tmp("readme")
        os.makedirs(tmp, exist_ok=True)
        shutil.copy(os.path.join(ctx.project, alt),
                    os.path.join(tmp, "README.md"))
        core.check_readme(rep, tmp)
        path = os.path.join(tmp, "README.md")
    else:
        core.check_readme(rep, ctx.project)
        path = os.path.join(ctx.project, "README.md")
    text = read_text(path) or ""
    low = text.lower()
    miss = [k for k in MANDATORY if k not in text]
    ko(rep, "config file format: every key", not miss, "readme_cfg",
       got=None if not miss else "not mentioned: " + ", ".join(miss))
    algo = re.search(r"algorithm|backtrack|prim|kruskal|wilson|eller|"
                     r"aldous|broder|hunt.and.kill|sidewinder|binary tree|"
                     r"growing tree|depth.first|\bdfs\b", low)
    ko(rep, "maze generation algorithm", bool(algo), "readme_algo")
    if not re.search(r"\bwhy\b|because|chose|chosen|choice|reason|pourquoi",
                     low):
        warn(rep, "no explanation of why this algorithm (why / because / "
                  "chose...)", quote="readme_why")
    reuse = re.search(r"reus|mazegen|reutilis", low)
    ko(rep, "what part of the code is reusable", bool(reuse), "readme_reuse")
    imp = re.search(r"^\s*(from\s+\w+(\.\w+)*\s+import\s+\w+|import\s+\w+)",
                    text, re.M)
    gen = re.search(r"\w*(maze|gen)\w*\s*\(", text, re.I)
    ok = bool(imp and gen)
    ko(rep, "package documentation: import + instantiation example", ok,
       "readme_doc", expected="an example like: from mazegen import "
       "MazeGenerator / MazeGenerator(...)",
       got=None if ok else ("no import line" if not imp
                            else "no class call"),
       detail=f'also: "{Q["doc_example"]}"')
    if "seed" not in low:
        warn(rep, "package documentation: no 'seed' parameter shown",
             quote="doc_params")
    if not re.search(r"solution|solve|shortest|path", low):
        warn(rep, "package documentation: no access to a solution shown",
             quote="doc_access")
    for what, rx in (("roles of each team member", r"\brole"),
                     ("anticipated planning", r"plann|schedule|timeline"),
                     ("what worked well / could be improved",
                      r"worked well|went well|improve"),
                     ("specific tools used", r"\btools?\b")):
        if not re.search(rx, low):
            warn(rep, f"team and project management: {what} not found",
                 quote="readme_team")


def group_repo(ctx):
    rep = ctx.rep
    rep.section("repo")
    core.check_repo(rep, ctx.project)
    tracked = git_files(ctx.project)
    if tracked:
        junk = sorted({f.split("/")[0] + "/" for f in tracked
                       if re.match(r"(\.?venv|env|build|[^/]*\.egg-info)/",
                                   f)})
        if junk:
            warn(rep, "build or venv folders in git: " + ", ".join(junk))


# --------------------------------------------------------------------------
# group: errors
# --------------------------------------------------------------------------

def base_pairs(ctx, out):
    pairs = [("WIDTH", "20"), ("HEIGHT", "15"), ("ENTRY", "0,0"),
             ("EXIT", "19,14"), ("OUTPUT_FILE", out), ("PERFECT", "True")]
    pairs += list(ctx.extras.items())
    return pairs


def cfg_with(ctx, out, changes=None, drop=(), add=()):
    pairs = []
    for k, v in base_pairs(ctx, out):
        if k.upper() in drop:
            continue
        if changes and k.upper() in changes:
            v = changes[k.upper()]
        pairs.append(f"{k}={v}")
    seen = {k.upper() for k, _ in base_pairs(ctx, out)}
    for k, v in (changes or {}).items():
        if k not in seen:
            pairs.append(f"{k}={v}")
    pairs += list(add)
    return "".join(p + "\n" for p in pairs)


def error_cases(ctx):
    """(id, label, content or special, refuse level, quote).

    refuse: "ko" the maze must not be generated, "warn" generating it is
    questionable, None only "no crash + message" is checked.
    """
    cases = [
        ("noarg", "no argument", ("args", []), "ko", "usage"),
        ("twoargs", "two arguments", ("args2", None), None, "usage"),
        ("nofile", "config file does not exist", ("missing", None), "ko",
         "errors"),
        ("dir", "config path is a directory", ("dir", None), "ko", "errors"),
        ("empty", "empty config file", "", "ko", "mandatory"),
        ("comments", "only comments", "# WIDTH=20\n# HEIGHT=15\n", "ko",
         "mandatory"),
    ]
    for key in MANDATORY[:5]:
        cases.append((f"no_{key.lower()}", f"{key} missing",
                      ("drop", key), "ko", "mandatory"))
    cases += [
        ("no_perfect", "PERFECT missing (the default is False)",
         ("drop", "PERFECT"), None, "errors"),
        ("w_alpha", "WIDTH=abc", {"WIDTH": "abc"}, "ko", "errors"),
        ("w_float", "WIDTH=20.5", {"WIDTH": "20.5"}, "ko", "errors"),
        ("w_zero", "WIDTH=0", {"WIDTH": "0"}, "ko", "errors"),
        ("h_neg", "HEIGHT=-5", {"HEIGHT": "-5"}, "ko", "errors"),
        ("w_empty", "WIDTH= (empty value)", {"WIDTH": ""}, "ko", "errors"),
        ("one_cell", "WIDTH=1 HEIGHT=1 ENTRY=EXIT=0,0",
         {"WIDTH": "1", "HEIGHT": "1", "EXIT": "0,0"}, "ko", "entry"),
        ("entry_x", "ENTRY=20,0 (WIDTH=20)", {"ENTRY": "20,0"}, "ko",
         "entry"),
        ("exit_y", "EXIT=19,15 (HEIGHT=15)", {"EXIT": "19,15"}, "ko",
         "entry"),
        ("entry_neg", "ENTRY=-1,0", {"ENTRY": "-1,0"}, "ko", "entry"),
        ("exit_far", "EXIT=500,500", {"EXIT": "500,500"}, "ko", "entry"),
        ("same", "ENTRY=EXIT=5,5", {"ENTRY": "5,5", "EXIT": "5,5"}, "ko",
         "entry"),
        ("entry_one", "ENTRY=0 (one number)", {"ENTRY": "0"}, "ko",
         "errors"),
        ("entry_three", "ENTRY=1,2,3", {"ENTRY": "1,2,3"}, "ko", "errors"),
        ("entry_alpha", "ENTRY=a,b", {"ENTRY": "a,b"}, "ko", "errors"),
        ("perfect_bad", "PERFECT=maybe", {"PERFECT": "maybe"}, "warn",
         "errors"),
        ("no_equal", "'WIDTH 20' (no =)", ("replace", ("WIDTH", "WIDTH 20")),
         "ko", "kv"),
        ("junk_line", "a line without '='", ("add", "hello world"), "warn",
         "kv"),
        ("unknown", "unknown key FOO=bar", ("add", "FOO=bar"), None,
         "errors"),
        ("dup", "WIDTH given twice", ("add", "WIDTH=25"), None, "errors"),
        ("binary", "binary garbage", ("bytes", b"\x00\xff\xfe\x80WIDTH=\x81"
                                      b"\n\xc3\x28\n"), "ko", "errors"),
        ("out_dir", "OUTPUT_FILE in a missing folder",
         {"OUTPUT_FILE": "no/such/dir/m.txt"}, "warn", "errors"),
        ("out_empty", "OUTPUT_FILE= (empty)", {"OUTPUT_FILE": ""}, "warn",
         "errors"),
        ("out_isdir", "OUTPUT_FILE is a folder", {"OUTPUT_FILE": "."},
         "warn", "errors"),
    ]
    if ctx.seed_key:
        cases.append(("seed_bad", f"{ctx.seed_key}=abc",
                      {ctx.seed_key.upper(): "abc"}, "warn", "errors"))
    return cases


def group_errors(ctx):
    rep = ctx.rep
    rep.section("errors")
    if not ctx.has_main:
        rep.skip("errors", f"no {MAIN}")
        return
    hangs = 0
    for cid, label, spec, refuse, quote in error_cases(ctx):
        if hangs >= 2:
            rep.skip("rest of the errors group", "2 timeouts")
            break
        out = f"amz_err_{cid}.txt"
        argv, content = None, None
        if isinstance(spec, dict):
            content = cfg_with(ctx, out, changes=spec)
        elif isinstance(spec, str):
            content = spec
        else:
            kind, val = spec
            if kind == "args":
                argv = list(val)
            elif kind == "args2":
                p = ctx.write_cfg(f"err_{cid}.txt", cfg_with(ctx, out))
                argv = [p, p]
            elif kind == "missing":
                argv = [os.path.join(ctx.cfg_dir, "does_not_exist.txt")]
            elif kind == "dir":
                d = os.path.join(ctx.cfg_dir, "a_folder")
                os.makedirs(d, exist_ok=True)
                argv = [d]
            elif kind == "drop":
                content = cfg_with(ctx, out, drop=(val,))
            elif kind == "replace":
                key, line = val
                content = cfg_with(ctx, out, drop=(key,), add=(line,))
            elif kind == "add":
                content = cfg_with(ctx, out, add=(val,))
            elif kind == "bytes":
                content = val
        if argv is None:
            argv = [ctx.write_cfg(f"err_{cid}.txt", content)]
        shown = f"python3 {MAIN} " + " ".join(
            os.path.basename(a) for a in argv)
        if content is not None and not isinstance(content, bytes):
            shown += "\nconfig: " + " | ".join(
                ln for ln in content.splitlines()) if content else \
                "\nconfig: (empty file)"
        out_path = os.path.join(ctx.ws.dir, out)
        for stale in (out_path,):
            if os.path.isfile(stale):
                os.remove(stale)
        live = ctx.start(argv)
        live.wait_for(lambda: os.path.isfile(out_path), 8)
        live.pump(0.3)
        accepted = os.path.isfile(out_path) or live.alive()
        running = live.alive()
        live.stop()
        text = live.text
        rc = live.rc
        L = f"{label}"
        crashed = TRACEBACK in text or (rc is not None and rc < 0
                                        and not running)
        ko(rep, f"{L}: no traceback", not crashed, "crash",
           got=None if not crashed else traceback_tail(text),
           detail=shown)
        msg = bool(ERRORISH.search(text))
        if running and not os.path.isfile(out_path) and not msg:
            hangs += 1
        if refuse and accepted:
            what = f"{out} written" if os.path.isfile(out_path) else \
                "the program kept running (no error)"
            if running and not os.path.isfile(out_path) and msg:
                warn(rep, f"{L}: error shown but the program did not exit",
                     tail(text, 3) + "\n" + shown)
                continue
            if refuse == "ko":
                ko(rep, f"{L}: refused", False, quote,
                   expected="a clear error message, no maze", got=what,
                   detail=f"screen: {tail(text, 3)}\n{shown}")
            else:
                warn(rep, f"{L}: accepted ({what})",
                     f"screen: {tail(text, 3)}\n{shown}", quote=quote)
            continue
        if accepted:
            continue
        if not text.strip():
            ko(rep, f"{L}: error message", False, "crash",
               expected="a clear error message", got="nothing printed",
               detail=f"{shown}\nexit code {rc}")
        elif not msg:
            warn(rep, f"{L}: the message does not look like an error",
                 tail(text, 3) + "\n" + shown, quote="crash")
        else:
            rep.check(f"{L}: error message", True)
            if rep.verbose:
                rep.info(tail(text, 1)[:140])


# --------------------------------------------------------------------------
# groups: output, perfect, board, pattern, seed
# --------------------------------------------------------------------------

EXAMPLE = Case("example", 20, 15, (0, 0), (19, 14), True)
PERFECT_CASES = [
    Case("p_odd", 21, 11, (0, 10), (20, 0), True),
    Case("p_inside", 30, 20, (3, 1), (26, 18), True),
    Case("p_tall", 10, 30, (9, 0), (0, 29), True),
    Case("p_wide", 60, 12, (0, 11), (59, 0), True),
    Case("p_comments", 25, 12, (24, 11), (0, 0), True, comments=True),
    Case("p_big", 60, 40, (0, 39), (59, 0), True, timeout=40, big=True),
    Case("p_huge", 100, 100, (0, 0), (99, 99), True, timeout=90, big=True),
]
BOARD_CASES = [
    Case("b_example", 20, 15, (0, 0), (19, 14), False),
    Case("b_odd", 31, 21, (1, 1), (29, 19), False),
    Case("b_wide", 40, 25, (0, 24), (39, 0), False),
    Case("b_small", 15, 11, (0, 0), (14, 10), False),
    Case("b_big", 50, 50, (2, 3), (47, 46), False, timeout=60, big=True),
]
SMALL_CASES = [
    Case("s_5x5", 5, 5, (0, 0), (4, 4), True),
    Case("s_8x6", 8, 6, (0, 0), (7, 5), True),
    Case("s_3x3", 3, 3, (0, 0), (2, 2), True),
    Case("s_2x1", 2, 1, (0, 0), (1, 0), True),
]


def need_main(ctx, gid):
    if not ctx.has_main:
        ctx.rep.skip(gid, f"no {MAIN} at the root")
        return False
    return True


def run_cases(ctx, cases):
    ctx.timeouts = 0
    for case in cases:
        if case.big and ctx.args.quick:
            core.quick_skip(ctx.rep, f"{case.label}")
            continue
        out = run_valid(ctx, case)
        if out is None:
            if ctx.timeouts >= 2:
                return
            continue
        validate(ctx, case, out)


def group_output(ctx):
    ctx.rep.section("output")
    if not need_main(ctx, "output"):
        return
    ctx.timeouts = 0
    out = run_valid(ctx, EXAMPLE)
    if out is None:
        return
    rep = ctx.rep
    if rep.verbose:
        rep.info(f"{EXAMPLE.out}: " + core.crepr(out.file_text, 120))
    validate(ctx, EXAMPLE, out)


def group_perfect(ctx):
    ctx.rep.section("perfect")
    if need_main(ctx, "perfect"):
        run_cases(ctx, PERFECT_CASES)


def group_board(ctx):
    ctx.rep.section("board")
    if need_main(ctx, "board"):
        run_cases(ctx, BOARD_CASES)


def maze_of(case, out):
    if out is None or out.file_text is None:
        return None
    p = parse_output(out.file_text, case.w, case.h)
    if p.cells is None:
        return None
    return Maze(p.cells, case.w, case.h, case.entry, case.exit)


def group_pattern(ctx):
    rep = ctx.rep
    rep.section("pattern")
    if not need_main(ctx, "pattern"):
        return
    ctx.timeouts = 0
    for case in (EXAMPLE, BOARD_CASES[0], PERFECT_CASES[1]):
        L = case.label
        if ctx.timeouts >= 2:
            break
        out = ctx.generate(case)
        if out.timed_out:
            ctx.timeouts += 1
        m = maze_of(case, out)
        if m is None:
            rep.skip(f"{L}: \"42\"", "no readable output file (see the "
                     "output / perfect / board groups)")
            continue
        groups = m.closed_groups()
        ko(rep, f"{L}: \"42\" of fully closed cells", bool(m.closed), "42",
           expected="several cells with all 4 walls closed (digit F)",
           got=None if m.closed else "no fully closed cell",
           detail=None if m.closed else ctx.where(case))
        if not m.closed:
            continue
        if len(groups) < 2 or len(m.closed) < 8:
            warn(rep, f"{L}: the fully closed cells do not look like two "
                      f"digits ({len(m.closed)} cells, {len(groups)} "
                      "group(s))", m.closed_mask(), quote="42")
        elif rep.verbose:
            rep.info(m.closed_mask().replace("\n", "\n       "))
    for case in SMALL_CASES:
        if ctx.timeouts >= 2:
            rep.skip("rest of this group", "2 timeouts")
            break
        L = case.label
        out = run_valid(ctx, case, small=True)
        if out is None:
            continue
        validate(ctx, case, out)
        m = maze_of(case, out)
        if m is None or m.closed:
            continue
        msg = re.search(r"42|pattern|motif", out.text, re.I) or \
            ERRORISH.search(out.text)
        ko(rep, f"{L}: no \"42\": message on the console", bool(msg),
           "42small", expected="an error message saying the \"42\" was "
           "omitted", got=None if msg else "no such message",
           detail=None if msg else f"screen:\n{tail(out.text, 4)}\n"
           + ctx.where(case))
    if ctx.timeouts:
        pass


def seed_case(ctx, name, seed, perfect=True):
    extra = {}
    if ctx.seed_key:
        extra[ctx.seed_key] = seed
    return Case(name, 20, 15, (0, 0), (19, 14), perfect, extra=extra)


def grid_part(text):
    return (text or "").split("\n\n", 1)[0]


def group_seed(ctx):
    rep = ctx.rep
    rep.section("seed")
    if not need_main(ctx, "seed"):
        return
    if not ctx.seed_key:
        warn(rep, "no seed key in the default config nor in the code: "
                  "reproducibility is checked through the package only",
             quote="seed")
    else:
        rep.info(f"seed key: {ctx.seed_key}")
    ctx.timeouts = 0
    runs = {}
    plan = [("a1", "42", True), ("a2", "42", True), ("b", "4242", True),
            ("c", "7", True), ("d1", "42", False), ("d2", "42", False)]
    if not ctx.seed_key:
        plan = [("a1", None, True), ("a2", None, True)]
    for name, seed, perfect in plan:
        case = seed_case(ctx, f"seed_{name}", seed, perfect)
        out = ctx.generate(case)
        if out.timed_out:
            ctx.timeouts += 1
            if ctx.timeouts >= 2:
                rep.skip("rest of this group", "2 timeouts")
                break
        runs[name] = (case, out)
        if out.file_text is None:
            ko(rep, f"{case.label} seed {seed}: output file written", False,
               "write", got=out.status(), detail=tail(out.text, 4) + "\n"
               + ctx.where(case))

    def text_of(n):
        return runs[n][1].file_text if n in runs else None

    if not ctx.seed_key:
        a, b = text_of("a1"), text_of("a2")
        if a and b:
            if a == b:
                rep.info("same maze on two runs without a seed key (a fixed "
                         "default seed?)")
        return
    for x, y, what in (("a1", "a2", "PERFECT=True"),
                       ("d1", "d2", "PERFECT=False")):
        a, b = text_of(x), text_of(y)
        if a is None or b is None:
            continue
        ko(rep, f"same seed twice ({what}, {ctx.seed_key}=42): same file",
           a == b, "seed", expected="the same maze and path",
           got=None if a == b else "a different file",
           detail=None if a == b else core.diff_lines(a, b) + "\n"
           + ctx.where(runs[x][0]))
    base = text_of("a1")
    others = [n for n in ("b", "c") if text_of(n) is not None]
    if base and others:
        same = [n for n in others if grid_part(text_of(n)) == grid_part(base)]
        ok = len(same) < len(others)
        ko(rep, f"other seeds ({ctx.seed_key}=4242, 7): other mazes", ok,
           "seed", expected="a different maze for a different seed",
           got=None if ok else "the same maze for every seed",
           detail=None if ok else ctx.where(runs["b"][0]))
        if ok and same:
            warn(rep, "two different seeds gave the same maze")


# --------------------------------------------------------------------------
# group: visual
# --------------------------------------------------------------------------

MENU_RES = [
    re.compile(r"[\[(<]\s*([^\s\])>]{1,5})\s*[\])>]\s*[:=\-]*>?\s*"
               r"([A-Za-z][A-Za-z /'_-]{2,40})"),
    re.compile(r"(?:^|[\s|,;])['\"]?([0-9A-Za-z])['\"]?\s*(?:[).:=]|->|=>|"
               r"-|\s-\s)\s*([A-Za-z][A-Za-z /'_-]{2,40})", re.M),
    re.compile(r"press\s+['\"]?(\w)['\"]?\s+(?:to\s+)?"
               r"([A-Za-z][A-Za-z /'_-]{2,40})", re.I),
    re.compile(r"([A-Za-z][A-Za-z /'_-]{2,30}?)\s*[:=]\s*['\"]?"
               r"([0-9A-Za-z])['\"]?(?=\s|$|,|\|)", re.M),
]
ACTIONS = [("regen", re.compile(r"re-?gen|new maze|generate", re.I)),
           ("path", re.compile(r"path|solution|solve", re.I)),
           ("color", re.compile(r"colou?r", re.I)),
           ("quit", re.compile(r"quit|exit|leave|close", re.I))]


def menu_keys(text):
    """{action: key} read from the menu printed by the program."""
    keys = {}
    for i, rx in enumerate(MENU_RES):
        for m in rx.finditer(text):
            key, desc = m.group(1), m.group(2)
            if i == 3:
                key, desc = desc, key
            if len(key) > 1 and not key.isdigit():
                continue
            for act, arx in ACTIONS:
                if act not in keys and arx.search(desc):
                    keys[act] = key
                    break
    return keys


def group_visual(ctx):
    rep = ctx.rep
    rep.section("visual")
    if not need_main(ctx, "visual"):
        return
    if not HAVE_PTY:
        rep.skip("visual", "no pseudo-terminal on this system")
        return
    case = Case("visual", 20, 15, (0, 0), (19, 14), True)
    out = ctx.generate(case, keep_live=True)
    live = out.live
    try:
        visual_session(ctx, case, out, live)
    finally:
        live.stop()
    for kind in ("eof", "sigint"):
        visual_abort(ctx, kind)


def visual_session(ctx, case, out, live):
    rep = ctx.rep
    if out.file_text is None:
        rep.skip("display", "no output file: " + out.status())
        return
    live.pump(1.5)
    text = live.text
    if not live.alive():
        if TRACEBACK in text:
            ko(rep, "display: no crash", False, "review",
               got=traceback_tail(text), detail=ctx.where(case))
        elif live.rc == 0:
            ko(rep, "display waits for user interaction", False, "interact",
               got="the program ended by itself (exit code 0)",
               detail=f"screen:\n{tail(text, 5)}\n{ctx.where(case)}")
        else:
            rep.skip("display", f"the display could not start here (exit "
                     f"code {live.rc}): {tail(text, 2)}")
        return
    rep.check("display waits for user interaction", True)
    shown = text.strip()
    if not shown:
        rep.skip("keys", "nothing printed on the terminal (graphical "
                 "window?): keys not tested")
        return
    keys = menu_keys(text)
    rep.info("keys found on the screen: " + (", ".join(
        f"{a}={k!r}" for a, k in keys.items()) or "none"))
    canon = live.canonical()
    nl = b"\n" if canon or canon is None else b""
    for act, label in (("regen", "re-generate"), ("path", "show/hide path"),
                       ("color", "change wall colors")):
        if act not in keys:
            rep.skip(f"{label} key", "not found on the screen")
            continue
        before_total = live.total
        before_file = read_text(ctx.ws.path(case.out))
        live.send(keys[act].encode() + nl)
        live.pump(2.0)
        now = live.text
        if now.count(TRACEBACK) > text.count(TRACEBACK):
            ko(rep, f"{label} key {keys[act]!r}: no crash", False, "review",
               got=traceback_tail(now))
            return
        if not live.alive():
            ko(rep, f"{label} key {keys[act]!r}: the program keeps running",
               False, "interact", got=f"exit code {live.rc}",
               detail=tail(now, 4))
            return
        rep.check(f"{label} key {keys[act]!r}: no crash", True)
        if live.total == before_total:
            warn(rep, f"{label} key {keys[act]!r}: nothing redrawn",
                 quote="regen" if act == "regen" else None)
        text = now
        if act == "regen":
            after = read_text(ctx.ws.path(case.out))
            if after and after != before_file and file_complete(after):
                o = Outcome(live, after, False, 0)
                validate(ctx, case, o, label="after re-generate")
                if grid_part(after) == grid_part(before_file):
                    warn(rep, "re-generate rewrote the same maze",
                         quote="regen")
    if "quit" in keys:
        live.send(keys["quit"].encode() + nl)
        live.wait_for(lambda: not live.alive(), 3)
        text2 = live.text
        if TRACEBACK in text2 and TRACEBACK not in text:
            ko(rep, f"quit key {keys['quit']!r}: no crash", False, "review",
               got=traceback_tail(text2))
        elif live.alive():
            warn(rep, f"quit key {keys['quit']!r}: still running after 3s")
        else:
            rep.check(f"quit key {keys['quit']!r}: the program ends", True)


def visual_abort(ctx, kind):
    """End of input (Ctrl-D) / Ctrl-C at the menu: no traceback."""
    rep = ctx.rep
    case = Case(f"visual_{kind}", 20, 15, (0, 0), (19, 14), True)
    out = ctx.generate(case, keep_live=True)
    live = out.live
    try:
        if out.file_text is None or not live.alive():
            return
        live.pump(1.0)
        before = live.text
        start_total = live.total
        what = "Ctrl-D (end of input)" if kind == "eof" else "Ctrl-C"
        if kind == "eof":
            if live.canonical() is False:
                rep.skip(what, "the terminal is in raw mode (curses)")
                return
            live.send(b"\x04")
        else:
            live.interrupt()
        live.wait_for(lambda: not live.alive(), 3)
        text = live.text
        if TRACEBACK in text and TRACEBACK not in before:
            warn(rep, f"{what} at the menu shows a traceback",
                 traceback_tail(text, 4), quote="review")
        elif live.alive() and live.total - start_total > (1 << 20):
            warn(rep, f"{what} at the menu: the program loops (more than "
                      "1 MB printed in 3s)", quote="review")
        else:
            rep.check(f"{what} at the menu: no traceback", True)
    finally:
        live.stop()


# --------------------------------------------------------------------------
# groups: package, build
# --------------------------------------------------------------------------

PKG_RE = re.compile(r"^mazegen-[^/\\]+\.(whl|tar\.gz)$")


def package_files(project):
    """(files at the root, mazegen files elsewhere)."""
    root = [n for n in root_names(project) if PKG_RE.match(n)
            and os.path.isfile(os.path.join(project, n))]
    other = []
    for top, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in (".git", ".venv", "venv")]
        if os.path.abspath(top) == os.path.abspath(project):
            continue
        for f in files:
            if re.match(r"^mazegen.*\.(whl|tar\.gz)$", f, re.I):
                other.append(os.path.relpath(os.path.join(top, f), project))
    return sorted(root, key=lambda n: (not n.endswith(".whl"), n)), \
        sorted(other)


def wheel_info(path):
    """(dist name, top-level modules, has description, error)."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            meta = [n for n in names if n.endswith(".dist-info/METADATA")]
            text = z.read(meta[0]).decode("utf-8", "replace") if meta else ""
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        return None, [], False, f"not a valid wheel: {exc}"
    name = re.search(r"^Name:\s*(\S+)", text, re.M)
    tops = set()
    for n in names:
        first = n.split("/")[0]
        if first.endswith((".dist-info", ".data")):
            continue
        if "/" in n:
            tops.add(first)
        elif n.endswith(".py"):
            tops.add(n[:-3])
    body = text.split("\n\n", 1)[1].strip() if "\n\n" in text else ""
    has_doc = bool(body) or any(re.search(r"readme|\.md$|\.rst$", n, re.I)
                                and not n.endswith("dist-info/METADATA")
                                for n in names)
    return (name.group(1) if name else None), sorted(tops), has_doc, None


def sdist_info(path):
    try:
        with tarfile.open(path, "r:gz") as t:
            members = t.getnames()
            pk = [m for m in members if m.count("/") == 1
                  and m.endswith("/PKG-INFO")]
            text = ""
            if pk:
                f = t.extractfile(pk[0])
                text = f.read().decode("utf-8", "replace") if f else ""
    except (tarfile.TarError, OSError) as exc:
        return None, [], False, f"not a valid .tar.gz: {exc}"
    name = re.search(r"^Name:\s*(\S+)", text, re.M)
    body = text.split("\n\n", 1)[1].strip() if "\n\n" in text else ""
    return (name.group(1) if name else None), [], bool(body), None


def normalize(name):
    return re.sub(r"[-_.]+", "-", name or "").lower()


PROBE = r'''
import contextlib, importlib, inspect, io, json, os, pathlib, pkgutil, re
import signal, sys, traceback

W, H = 15, 11
ENTRY, EXIT = (0, 0), (W - 1, H - 1)
res = {"modules": {}, "classes": [], "chosen": None, "params": [],
       "unknown": [], "instance": None, "generate": None, "grid": None,
       "solution": None, "seed": None, "seed_same": None, "seed_diff": None}


def _alarm(signum, frame):
    raise TimeoutError("more than 20s")


if hasattr(signal, "SIGALRM"):
    signal.signal(signal.SIGALRM, _alarm)


def guard(seconds=20):
    if hasattr(signal, "alarm"):
        signal.alarm(seconds)


def tb():
    return traceback.format_exc()[-900:]


found = {}
for name in sys.argv[1:]:
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            guard()
            m = importlib.import_module(name)
            guard(0)
    except BaseException:
        guard(0)
        res["modules"][name] = {"ok": False, "error": tb()}
        continue
    res["modules"][name] = {"ok": True, "doc": bool(m.__doc__),
                            "file": str(getattr(m, "__file__", ""))}
    todo = [m]
    if hasattr(m, "__path__"):
        for info in pkgutil.walk_packages(m.__path__, name + "."):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    todo.append(importlib.import_module(info.name))
            except BaseException:
                res["modules"][info.name] = {"ok": False, "error": tb()}
    for mod in todo:
        for cname, cls in list(vars(mod).items()):
            if not inspect.isclass(cls):
                continue
            if str(getattr(cls, "__module__", "")).split(".")[0] != name:
                continue
            if issubclass(cls, BaseException):
                continue
            found[cls.__module__ + "." + cls.__qualname__] = cls
res["classes"] = sorted(found)


def score(cls):
    n = cls.__name__.lower()
    s = 0
    if "gen" in n:
        s += 4
    if "maze" in n:
        s += 2
    meths = [k for k in dir(cls) if not k.startswith("_")]
    if any(re.match(r"(generate|build|create|carve)", k) for k in meths):
        s += 2
    if any(re.search(r"solve|solution|path", k) for k in meths):
        s += 1
    try:
        import enum
        if issubclass(cls, enum.Enum):
            s -= 20
    except Exception:
        pass
    return s


RULES = [
    (r"(maze_)?(width|w|cols|columns|n_?cols|size_x|nx)", lambda a: W),
    (r"(maze_)?(height|h|rows|n_?rows|size_y|ny)", lambda a: H),
    (r"size|dimensions?|shape|dims", lambda a: (W, H)),
    (r"entry|start|entrance|entry_?(point|pos|coords?|cell)|begin|"
     r"start_?(pos|cell)", lambda a: ENTRY),
    (r"exit|end|goal|finish|exit_?(point|pos|coords?|cell)|end_?(pos|cell)",
     lambda a: EXIT),
    (r"perfect|is_perfect", lambda a: True),
    (r"(random_|rng_)?seed", lambda a: 7),
    (r"output_?file|output|outfile|out_?file|filename|file|output_?path|"
     r"path", lambda a: (pathlib.Path(os.path.abspath("probe_maze.txt"))
                         if "Path" in str(a) else
                         os.path.abspath("probe_maze.txt"))),
]


def kwargs_for(sig, seed=None):
    kw, unknown, used = {}, [], []
    for p in sig.parameters.values():
        if p.name == "self" or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        val = None
        hit = False
        for rx, fn in RULES:
            if re.fullmatch(rx, p.name.lower()):
                val = fn(p.annotation)
                hit = True
                break
        if not hit:
            if p.default is p.empty:
                unknown.append(p.name)
            continue
        if isinstance(val, tuple) and isinstance(p.default, str):
            val = "%d,%d" % val
        if "seed" in p.name.lower() and seed is not None:
            val = seed
        if p.kind == p.POSITIONAL_ONLY:
            unknown.append(p.name)
            continue
        kw[p.name] = val
        used.append(p.name)
    return kw, unknown, used


def rows_of(v):
    if hasattr(v, "tolist") and hasattr(v, "shape"):
        try:
            v = v.tolist()
        except Exception:
            return None
    if isinstance(v, (list, tuple)) and v and \
            all(isinstance(r, (list, tuple)) for r in v):
        return v
    return None


def find_grid(obj):
    items = []
    try:
        items = list(vars(obj).items())
    except TypeError:
        pass
    for name in dir(type(obj)):
        if name.startswith("_"):
            continue
        if isinstance(getattr(type(obj), name, None), property):
            try:
                items.append((name, getattr(obj, name)))
            except BaseException:
                pass
    best = None
    for name, v in items:
        rows = rows_of(v)
        if rows is not None:
            if len(rows) == H and all(len(r) == W for r in rows):
                return name, "rows", rows
            if len(rows) == W and all(len(r) == H for r in rows):
                best = best or (name, "columns", rows)
        if isinstance(v, dict) and len(v) >= W * H - 60 and v and \
                all(isinstance(k, tuple) and len(k) == 2 for k in v):
            best = best or (name, "dict", v)
    return best


def canon(v, depth=0):
    if depth > 4:
        return type(v).__name__
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [canon(x, depth + 1) for x in v]
    if isinstance(v, (set, frozenset)):
        return sorted(repr(canon(x, depth + 1)) for x in v)
    if isinstance(v, dict):
        return sorted([repr(k), canon(x, depth + 1)] for k, x in v.items())
    if hasattr(v, "tolist"):
        return canon(v.tolist(), depth)
    if hasattr(v, "__dict__"):
        return canon(vars(v), depth + 1)
    return repr(v)


GEN_RX = r"(generate|build|create|make|carve|run)\w*"


def call_noarg(fn):
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False, None
    req = [p for p in sig.parameters.values() if p.default is p.empty and
           p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
    if req:
        if len(req) == 2 and re.search(r"entry|start", req[0].name) and \
                re.search(r"exit|end|goal", req[1].name):
            return True, fn(ENTRY, EXIT)
        return False, None
    return True, fn()


def build(cls, seed=None):
    sig = inspect.signature(cls)
    kw, unknown, used = kwargs_for(sig, seed)
    if unknown:
        return None, unknown, used, None
    with contextlib.redirect_stdout(io.StringIO()):
        guard()
        obj = cls(**kw)
        gen = None
        if find_grid(obj) is None:
            for name in dir(obj):
                if re.fullmatch(GEN_RX, name):
                    attr = getattr(obj, name)
                    if callable(attr):
                        ok, _ = call_noarg(attr)
                        if ok:
                            gen = name
                            break
        guard(0)
    return obj, [], used, gen


try:
    ranked = sorted(found.values(), key=lambda c: -score(c))
    cls = ranked[0] if ranked and score(ranked[0]) > 0 else (
        ranked[0] if len(ranked) == 1 else None)
    if cls is not None:
        res["chosen"] = cls.__module__ + "." + cls.__qualname__
        res["class_doc"] = bool(cls.__doc__)
        try:
            res["signature"] = str(inspect.signature(cls))
        except (TypeError, ValueError):
            res["signature"] = "?"
        obj, unknown, used, gen = build(cls)
        res["unknown"] = unknown
        res["params"] = used
        res["generate"] = gen
        if obj is not None:
            res["instance"] = True
            g = find_grid(obj)
            if g:
                name, kind, value = g
                res["grid"] = {"attr": name, "kind": kind,
                               "sample": repr(canon(value))[:160]}
            for name in dir(obj):
                if name.startswith("_") or \
                        not re.search(r"solve|solution|shortest|path", name,
                                      re.I):
                    continue
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        guard()
                        attr = getattr(obj, name)
                        if callable(attr):
                            ok, value = call_noarg(attr)
                            if not ok:
                                guard(0)
                                continue
                        else:
                            value = attr
                        guard(0)
                except BaseException:
                    guard(0)
                    res["solution_error"] = tb()
                    continue
                if value is None:
                    continue
                res["solution"] = {"name": name, "type":
                                   type(value).__name__,
                                   "len": len(value) if hasattr(
                                       value, "__len__") else None,
                                   "sample": repr(value)[:120]}
                break
            seedp = [p for p in used if "seed" in p.lower()]
            if seedp and g:
                res["seed"] = seedp[0]
                a, _, _, _ = build(cls, 1234)
                b, _, _, _ = build(cls, 1234)
                c, _, _, _ = build(cls, 98765)
                ga, gb, gc = (find_grid(x) for x in (a, b, c))
                if ga and gb and gc:
                    ca = json.dumps(canon(ga[2]), default=str)
                    res["seed_same"] = ca == json.dumps(canon(gb[2]),
                                                        default=str)
                    res["seed_diff"] = ca != json.dumps(canon(gc[2]),
                                                        default=str)
except BaseException:
    guard(0)
    res["error"] = tb()
sys.__stdout__.write("\n@@PROBE@@" + json.dumps(res, default=str) + "\n")
'''


class Venv:
    """A temporary venv with the package installed, or None."""

    def __init__(self, ctx, pkg_path):
        self.ctx = ctx
        self.dir = ctx.ws.tmp("venv")
        self.python = None
        self.how = None
        self.error = None
        self.pkg = pkg_path

    def create(self):
        res = core.run([sys.executable, "-m", "venv", "--system-site-packages",
                        self.dir], timeout=180, env=child_env())
        py = os.path.join(self.dir, "bin", "python")
        if not os.path.isfile(py):
            py = os.path.join(self.dir, "Scripts", "python.exe")
        if res.rc != 0 or not os.path.isfile(py):
            self.error = tail(core.output(res), 3)
            return False
        check = core.run([py, "-m", "pip", "--version"], timeout=60,
                         env=child_env())
        if check.rc != 0:
            self.error = "the venv has no pip (python3-venv / ensurepip " \
                         "missing)"
            return False
        self.python = py
        return True

    def install(self):
        cmd = [self.python, "-m", "pip", "install", "--no-index", "--no-deps",
               "--no-build-isolation", "--no-cache-dir", self.pkg]
        res = core.run(cmd, cwd=self.ctx.ws.tmp(""), timeout=300,
                       env=child_env())
        return res.rc == 0, core.output(res)


def installed_tops(venv_dir):
    """Top-level modules of the mazegen distribution installed in venv."""
    tops = set()
    for root, dirs, files in os.walk(venv_dir):
        for d in dirs:
            if d.endswith(".dist-info") and \
                    normalize(d).startswith("mazegen"):
                rec = os.path.join(root, d, "RECORD")
                for line in (read_text(rec) or "").splitlines():
                    first = line.split(",")[0].split("/")[0]
                    if not first or first.startswith("..") or \
                            first.endswith((".dist-info", ".data")) or \
                            first == "__pycache__":
                        continue
                    if "/" in line.split(",")[0]:
                        tops.add(first)
                    elif first.endswith(".py"):
                        tops.add(first[:-3])
    return sorted(tops)


def group_package(ctx):
    rep = ctx.rep
    rep.section("package")
    root, other = package_files(ctx.project)
    ok = bool(root)
    ko(rep, "mazegen-* package file at the root", ok, "pkgname",
       expected="e.g. mazegen-1.0.0-py3-none-any.whl or "
                "mazegen-1.0.0.tar.gz at the root",
       got=None if ok else ("found elsewhere: " + ", ".join(other[:4])
                            if other else "none"))
    if not ok:
        other = sorted(other, key=lambda n: (not n.endswith(".whl"), n))
        if not other:
            return
        rep.info(f"the checks below use {other[0]} instead")
        root = other
    tracked = git_files(ctx.project)
    if tracked is not None:
        missing = [n for n in root if n.replace(os.sep, "/") not in tracked]
        ko(rep, "package file committed in git", len(missing) < len(root),
           "pkgname", got=None if len(missing) < len(root) else
           "not tracked: " + ", ".join(missing) + " (ignored by .gitignore?)")
    path = os.path.join(ctx.ws.dir, root[0])
    if root[0].endswith(".whl"):
        name, tops, has_doc, err = wheel_info(path)
    else:
        name, tops, has_doc, err = sdist_info(path)
    if not ko(rep, f"{root[0]}: valid archive", err is None, "pkgext",
              got=err):
        return
    ok = normalize(name).startswith("mazegen")
    ko(rep, f"{root[0]}: package name mazegen", ok, "pkgname",
       got=None if ok else f"Name: {name}")
    if not has_doc:
        warn(rep, f"{root[0]}: no documentation inside the package "
                  "(empty description, no README)", quote="pip")
    venv = Venv(ctx, path)
    if not venv.create():
        if root[0].endswith(".whl") and tops:
            rep.info(f"no venv ({venv.error}): wheel imported from the file")
            python = PY
            env = child_env({"PYTHONPATH": path})
        else:
            rep.skip("install in a venv", f"cannot create a venv: "
                     f"{venv.error}")
            return
    else:
        ok, text = venv.install()
        if not ko(rep, f"pip install {root[0]} (offline venv)", ok, "pip",
                  got=None if ok else tail(text, 6)):
            return
        tops = installed_tops(venv.dir) or tops
        python = venv.python
        env = child_env()
    if not tops:
        ko(rep, "the package contains a module", False, "class",
           got="no module found in the package")
        return
    rep.info("modules: " + ", ".join(tops))
    probe_dir = ctx.ws.tmp("probe")
    os.makedirs(probe_dir, exist_ok=True)
    probe = os.path.join(ctx.ws.root, "probe.py")
    with open(probe, "w") as fh:
        fh.write(PROBE)
    res = core.run([python, "-B", probe] + tops, cwd=probe_dir, timeout=180,
                   env=env)
    text = core.output(res)
    m = re.search(r"@@PROBE@@(\{.*\})", text)
    if not m:
        ko(rep, "the package can be imported and used", False, "class",
           got=tail(text, 6))
        return
    info = json.loads(m.group(1))
    for mod, st in info["modules"].items():
        ko(rep, f"import {mod} (installed package, outside the project)",
           st["ok"], "class", got=None if st["ok"] else
           traceback_tail(st.get("error", ""), 5))
    if not any(st["ok"] for st in info["modules"].values()):
        return
    chosen = info.get("chosen")
    ok = bool(chosen)
    ko(rep, "a maze generator class in the package", ok, "class",
       got=None if ok else "classes: " + (", ".join(info["classes"])
                                          or "none"))
    if not ok:
        return
    rep.info(f"class: {chosen}{info.get('signature', '')}")
    if info.get("error"):
        warn(rep, f"{chosen}: error while probing", tail(info["error"], 5))
    if info.get("unknown"):
        rep.skip(f"{chosen}(...)", "cannot guess the parameters: "
                 + ", ".join(info["unknown"]))
    elif info.get("instance"):
        rep.check(f"{chosen}({', '.join(info['params'])}) created"
                  + (f" + .{info['generate']}()" if info.get("generate")
                     else ""), True)
        if info.get("grid"):
            g = info["grid"]
            rep.check(f"generated structure: .{g['attr']} ({g['kind']})",
                      True)
        else:
            warn(rep, f"{chosen}: no grid attribute of 15x11 found "
                      "(structure not checked)", quote="doc_access")
        if info.get("solution"):
            s = info["solution"]
            rep.check(f"solution: .{s['name']} ({s['type']}, length "
                      f"{s['len']})", True)
        else:
            warn(rep, f"{chosen}: no solution / path found",
                 quote="doc_access")
        if info.get("seed_same") is not None:
            ko(rep, f"{chosen}({info['seed']}=1234) twice: same structure",
               info["seed_same"], "seed",
               got=None if info["seed_same"] else "different structures")
            if info.get("seed_diff") is False:
                ko(rep, f"{chosen}: another seed gives another maze", False,
                   "seed", got="the same structure for seeds 1234 and 98765")
        elif not info.get("seed"):
            warn(rep, f"{chosen}: no seed parameter found",
                 quote="doc_params")
    if not info.get("class_doc"):
        warn(rep, f"{chosen}: no class docstring", quote="docstring")
    check_doc_examples(ctx, python, env, tops, probe_dir)


def check_doc_examples(ctx, python, env, tops, cwd):
    """Run the README code blocks that import the package."""
    rep = ctx.rep
    exact, alt = find_readme(ctx.project)
    text = read_text(os.path.join(ctx.project, exact or alt or "README.md"))
    if not text:
        return
    blocks = re.findall(r"```[ \t]*([\w+-]*)[^\n]*\n(.*?)```", text, re.S)
    rx = re.compile(r"^\s*(from|import)\s+(" + "|".join(
        re.escape(t) for t in tops) + r")\b", re.M)
    n = 0
    for lang, code in blocks:
        if lang.lower() not in ("", "python", "py", "python3", "pycon") or \
                not rx.search(code):
            continue
        code = "\n".join(re.sub(r"^(>>>|\.\.\.) ?", "", ln)
                         for ln in code.splitlines())
        n += 1
        path = os.path.join(cwd, f"readme_example_{n}.py")
        with open(path, "w") as fh:
            fh.write(code)
        res = core.run([python, "-B", path], cwd=cwd, timeout=30, env=env)
        out = core.output(res)
        if res.rc == 0:
            rep.check(f"README example {n} runs with the installed package",
                      True)
        else:
            warn(rep, f"README example {n} fails with the installed package",
                 ("timeout after 30s" if res.timed_out
                  else traceback_tail(out, 4)) + "\ncode: "
                 + core.crepr(code, 120), quote="doc_example")
    if n == 0:
        rep.info("no README code block imports the package")


def find_build_dirs(project):
    out = []
    for top, dirs, files in os.walk(project):
        rel = os.path.relpath(top, project)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                         and not d.startswith(".") and depth < 2)
        if "pyproject.toml" in files or "setup.py" in files:
            out.append(rel)
    return out


def build_backend(path):
    text = read_text(os.path.join(path, "pyproject.toml")) or ""
    m = re.search(r"^\s*build-backend\s*=\s*[\"']([\w.:]+)[\"']", text, re.M)
    if m:
        return m.group(1).split(":")[0]
    return "setuptools"


def group_build(ctx):
    rep = ctx.rep
    rep.section("build")
    if ctx.args.quick:
        core.quick_skip(rep, "package rebuild")
        return
    dirs = find_build_dirs(ctx.project)
    ok = bool(dirs)
    ko(rep, "build files (pyproject.toml / setup.py)", ok, "build",
       got=None if ok else "none at the root or in a sub-folder")
    if not ok:
        return
    rel = "." if "." in dirs else dirs[0]
    backend = build_backend(os.path.join(ctx.project, rel))
    chk = core.run([sys.executable, "-c", f"import {backend}"], timeout=60,
                   env=child_env())
    if chk.rc != 0:
        rep.skip("build", f"build backend {backend} not installed here "
                 "(building needs it: no network download is done)")
        return
    if core.run([sys.executable, "-m", "pip", "--version"], timeout=60).rc:
        rep.skip("build", "pip not installed")
        return
    with core.Workspace(ctx.project, prefix="42next-amz-build-") as ws:
        outdir = ws.tmp("wheels")
        src = ws.path(rel) if rel != "." else ws.dir
        cmd = [sys.executable, "-m", "pip", "wheel", "--no-deps",
               "--no-build-isolation", "--no-index", "--no-cache-dir",
               "-w", outdir, src]
        res = core.run(cmd, cwd=ws.dir, timeout=600, env=child_env())
        built = sorted(f for f in os.listdir(outdir)) if \
            os.path.isdir(outdir) else []
        ok = res.rc == 0 and bool(built)
        where = "" if rel == "." else f" (in {rel})"
        if not ko(rep, f"pip wheel --no-build-isolation .{where}", ok,
                  "build", got=None if ok else tail(core.output(res), 6)):
            return
        ok = built[0].startswith("mazegen-")
        ko(rep, f"built package name: {built[0]}", ok, "pkgname",
           expected="mazegen-*", got=None if ok else built[0])
        root, _ = package_files(ctx.project)
        whl = [n for n in root if n.endswith(".whl")]
        if whl:
            a = wheel_py(os.path.join(ctx.project, whl[0]))
            b = wheel_py(os.path.join(outdir, built[0]))
            if a is not None and b is not None and a != b:
                diff = sorted(k for k in set(a) | set(b)
                              if a.get(k) != b.get(k))
                warn(rep, f"{whl[0]} differs from a build of the current "
                          "sources", "different: " + ", ".join(diff[:6]),
                     quote="build")
            elif a is not None and b is not None:
                rep.check(f"{whl[0]} matches the current sources", True)


def wheel_py(path):
    try:
        with zipfile.ZipFile(path) as z:
            return {n: z.read(n).replace(b"\r\n", b"\n")
                    for n in z.namelist() if n.endswith(".py")}
    except (zipfile.BadZipFile, OSError):
        return None


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

ORDER = [("files", group_files), ("makefile", group_makefile),
         ("lint", group_lint), ("readme", group_readme),
         ("repo", group_repo), ("errors", group_errors),
         ("output", group_output), ("perfect", group_perfect),
         ("board", group_board), ("pattern", group_pattern),
         ("seed", group_seed), ("visual", group_visual),
         ("package", group_package), ("build", group_build)]


def main():
    args = core.parse_args("A-Maze-ing tester", GROUPS)
    if not os.path.isdir(args.project):
        print(core.red(f"project directory not found: {args.project}"))
        return 2
    core.header("A-Maze-ing", args.project,
                note="python3 a_maze_ing.py <config> in a pseudo-terminal, "
                     "inside a temporary copy", verbose=args.verbose)
    rep = core.Report(verbose=args.verbose)
    with core.Workspace(args.project, keep=args.keep,
                        prefix="42next-amz-") as ws:
        ctx = Ctx(args, rep, ws)
        for gid, fn in ORDER:
            if core.selected(args, gid):
                fn(ctx)
    return rep.finish()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped (ctrl-c)")
        sys.exit(130)
