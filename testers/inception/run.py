#!/usr/bin/env python3
"""42next - Inception tester (subject v1.1).

Static checks of the repository (Makefile, srcs/docker-compose.yml, the
Dockerfiles, the NGINX configuration, the entrypoint scripts, credentials,
README / USER_DOC / DEV_DOC) and, when the stack is already running, live
checks through docker inspect / docker exec and a TLS handshake.

    python3 -B run.py --project DIR [--only g1,g2] [--list] [--quick]
                      [--verbose] [--keep] [--no-color]

The project is only read. The live group never starts, stops, builds or
changes a container: start the stack with make first.
--quick: no online Debian / Alpine version lookup, no live group.

Internal overrides, for the tests of this tester only:
  NEXT42_INCEPTION_HOST, NEXT42_INCEPTION_PORT   TLS target (default: the
                                                  domain, port 443)
  NEXT42_INCEPTION_HTTP_PORT   port that must be closed (default 80)
  NEXT42_INCEPTION_DNS         address used as the resolution of the domain
  NEXT42_INCEPTION_OFFLINE=1   no online version lookup
  NEXT42_INCEPTION_RELEASES    JSON file used instead of the online lookup:
      {"debian": {"stable": [13, "trixie"], "oldstable": [12, "bookworm"]},
       "alpine": ["3.24", "3.23"]}
"""

import fnmatch
import ipaddress
import json
import os
import re
import shlex
import shutil
import socket
import ssl
import sys
import tempfile
import urllib.request
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))

import core  # noqa: E402

GROUPS = [
    ("files", "Makefile, srcs folder, .env"),
    ("compose", "docker-compose.yml: images, restart, network, ports, "
                "volumes"),
    ("dockerfiles", "Dockerfiles: base image, latest tag, passwords, "
                    "packages"),
    ("nginx", "NGINX: TLSv1.2 / TLSv1.3, port 443, domain name"),
    ("loops", "no infinite-loop hacks (tail -f, sleep infinity, "
              "while true, bash)"),
    ("credentials", "credentials in git, admin user name, Docker secrets"),
    ("readme", "README.md, USER_DOC.md, DEV_DOC.md"),
    ("live", "running stack: containers, TLS, ports, volumes, users"),
]

# Sentences of the subject (v1.1) behind every KO.
Q = {
    "makefile": "A Makefile is also required and must be located at the "
                "root of your directory.",
    "makefile_compose": "it has to build the Docker images using "
                        "docker-compose.yml",
    "srcs": "All the files required for the configuration of your project "
            "must be placed in a srcs folder.",
    "env": "it is mandatory to use a .env file to store environment "
           "variables.",
    "envvars": "It is mandatory to use environment variables.",
    "compose": "You have to use docker compose.",
    "dockerfile": "You also have to write your own Dockerfiles, one per "
                  "service.",
    "called": "The Dockerfiles must be called in your docker-compose.yml "
              "by your Makefile.",
    "pull": "It is then forbidden to pull ready-made Docker images",
    "image_name": "Each Docker image must have the same name as its "
                  "corresponding service.",
    "dedicated": "Each service has to run in a dedicated container.",
    "base": "the containers must be built either from the penultimate "
            "stable version of Alpine or Debian.",
    "latest": "The latest tag is prohibited.",
    "nginx": "A Docker container that contains NGINX with TLSv1.2 or "
             "TLSv1.3 only.",
    "wp": "A Docker container that contains WordPress + php-fpm (it must "
          "be installed and configured) only, without nginx.",
    "mariadb": "A Docker container that contains MariaDB only, without "
               "nginx.",
    "vol_db": "A volume that contains your WordPress database.",
    "vol_wp": "A second volume that contains your WordPress website files.",
    "network": "A docker-network that establishes the connection between "
               "your containers.",
    "restart": "Your containers have to restart in case of a crash.",
    "host": "using network: host or --link or links: is forbidden.",
    "netline": "The network line must be present in your "
               "docker-compose.yml file.",
    "loop": "Your containers mustn't be started with a command running an "
            "infinite loop.",
    "hacks": "The following are a few prohibited hacky patches: tail -f, "
             "bash, sleep infinity, while true.",
    "users": "In your WordPress database, there must be two users, one of "
             "them being the administrator.",
    "admin": "The administrator's username can't contain admin/Admin or "
             "administrator/Administrator",
    "data": "Your volumes will be available in the /home/login/data folder "
            "of the host machine",
    "domain": "This domain name must be login.42.fr.",
    "domain_ip": "you have to configure your domain name so it points to "
                 "your local IP address.",
    "own_login": "Again, you have to use your own login.",
    "nopass": "No password must be present in your Dockerfiles.",
    "secrets": "It is strongly recommended that you use Docker secrets to "
               "store any confidential information.",
    "git": "Any credentials, API keys, or passwords found in your Git "
           "repository (outside of properly configured secrets) will "
           "result in project failure.",
    "gitignore": "any credentials, API keys, passwords, etc., must be saved "
                 "locally in various ways/files and ignored by git.",
    "entry": "Your NGINX container must be the only entrypoint into your "
             "infrastructure via the port 443 only",
    "readme_cmp": "A Project description section must also explain the use "
                  "of Docker [...] as well as a comparison between",
    "docs": "the following documentation files must be present at the "
            "root of your repository.",
}


def ko(rep, name, ok, quote, where=None, expected=None, got=None, hint=None):
    """rep.check with the location and the subject sentence as detail."""
    detail = [x for x in (where, hint) if x]
    detail.append(f'subject: "{Q[quote]}"')
    return rep.check(name, ok, expected=expected, got=got,
                     detail="\n".join(detail))


def loc(rel, line=None):
    return f"{rel}:{line}" if line else rel


def read_text(path, limit=1 << 20):
    try:
        with open(path, "rb") as fh:
            data = fh.read(limit)
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", "replace").replace("\r\n", "\n")


def line_at(text, pos):
    return text.count("\n", 0, pos) + 1


# --------------------------------------------------------------------------
# YAML subset (what docker-compose.yml files use), with line numbers
# --------------------------------------------------------------------------

class YamlError(Exception):
    def __init__(self, line, msg):
        super().__init__(f"line {line}: {msg}")
        self.line = line
        self.msg = msg


class YStr(str):
    """A scalar, with its line, its text before interpolation and the
    variables interpolation could not resolve."""
    line = 0
    quoted = False
    raw = ""
    missing: tuple = ()
    defaults: tuple = ()


def ystr(value, line, quoted=False):
    s = YStr(value)
    s.line = line
    s.quoted = quoted
    s.raw = value
    return s


class YMap(dict):
    def __init__(self, line=0):
        super().__init__()
        self.line = line
        self.lines = {}
        self.merged = set()


class YList(list):
    def __init__(self, line=0):
        super().__init__()
        self.line = line


KEY_RE = re.compile(
    r"""(?:"(?P<dq>(?:[^"\\]|\\.)*)"|'(?P<sq>(?:[^']|'')*)'"""
    r"""|(?P<pl>[^\s"'#&*!|>%@`\[\]{},?-][^#]*?|-[^\s#][^#]*?))"""
    r"""\s*:(?=\s|$)""")


def is_item(content):
    return content == "-" or content.startswith("- ") \
        or content.startswith("-\t")


def strip_comment(s):
    """Text before a # comment, quotes respected."""
    quote = None
    prev = " "
    i = 0
    while i < len(s):
        c = s[i]
        if quote:
            if quote == '"' and c == "\\":
                i += 2
                prev = "x"
                continue
            if c == quote:
                if quote == "'" and s[i + 1:i + 2] == "'":
                    i += 2
                    continue
                quote = None
        elif c in "\"'" and prev in " \t[{,":
            quote = c
        elif c == "#" and prev in " \t":
            return s[:i].rstrip()
        prev = c
        i += 1
    return s.rstrip()


def closing_quote(text, q):
    i = 1
    while i < len(text):
        c = text[i]
        if q == '"' and c == "\\":
            i += 2
            continue
        if c == q:
            if q == "'" and text[i + 1:i + 2] == "'":
                i += 2
                continue
            return i
        i += 1
    return None


_ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\",
        "/": "/", " ": " ", "e": "\x1b", "a": "\a", "b": "\b"}


def unquote(text):
    q, body = text[0], text[1:-1]
    if q == "'":
        return body.replace("''", "'")
    out = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            n = body[i + 1]
            if n in "xuU":
                size = {"x": 2, "u": 4, "U": 8}[n]
                try:
                    out.append(chr(int(body[i + 2:i + 2 + size], 16)))
                    i += 2 + size
                    continue
                except ValueError:
                    pass
            out.append(_ESC.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


NULLS = ("null", "Null", "NULL", "~")


class _Flow:
    """[a, b] and {a: b} values (joined on one line)."""

    def __init__(self, text, line, anchors):
        self.t = text
        self.p = 0
        self.line = line
        self.anchors = anchors

    def peek(self):
        return self.t[self.p:self.p + 1]

    def ws(self):
        while self.p < len(self.t) and self.t[self.p] in " \t":
            self.p += 1

    def fail(self, msg):
        raise YamlError(self.line, msg)

    def parse(self):
        v = self.value()
        self.ws()
        if self.p < len(self.t):
            self.fail(f"unexpected text after a flow value: "
                      f"{self.t[self.p:self.p + 30]!r}")
        return v

    def value(self):
        self.ws()
        c = self.peek()
        if c == "[":
            return self.seq()
        if c == "{":
            return self.map()
        if c in ("'", '"'):
            end = closing_quote(self.t[self.p:], c)
            if end is None:
                self.fail("unterminated quoted string")
            text = self.t[self.p:self.p + end + 1]
            self.p += end + 1
            return ystr(unquote(text), self.line, True)
        if c == "*":
            m = re.match(r"\*([^\s,\[\]{}]+)", self.t[self.p:])
            if not m or m.group(1) not in self.anchors:
                self.fail("unknown alias")
            self.p += m.end()
            return self.anchors[m.group(1)]
        if c == "&":
            m = re.match(r"&([^\s,\[\]{}]+)\s*", self.t[self.p:])
            if not m:
                self.fail("bad anchor")
            self.p += m.end()
            v = self.value()
            self.anchors[m.group(1)] = v
            return v
        return self.plain()

    def plain(self):
        start = self.p
        while self.p < len(self.t):
            c = self.t[self.p]
            if c in ",[]{}":
                break
            if c == ":" and (self.p + 1 >= len(self.t)
                             or self.t[self.p + 1] in " \t,]}"):
                break
            self.p += 1
        text = self.t[start:self.p].strip()
        if text == "" or text in NULLS:
            return None
        return ystr(text, self.line)

    def seq(self):
        out = YList(self.line)
        self.p += 1
        while True:
            self.ws()
            if self.peek() == "]":
                self.p += 1
                return out
            if not self.peek():
                self.fail("unterminated [ list")
            item = self.value()
            self.ws()
            if self.peek() == ":":
                self.p += 1
                pair = YMap(self.line)
                pair[str(item)] = self.value()
                item = pair
                self.ws()
            out.append(item)
            if self.peek() == ",":
                self.p += 1
            elif self.peek() != "]":
                self.fail("expected ',' or ']' in a [ ] list")

    def map(self):
        out = YMap(self.line)
        self.p += 1
        while True:
            self.ws()
            if self.peek() == "}":
                self.p += 1
                return out
            if not self.peek():
                self.fail("unterminated { map")
            key = self.value()
            self.ws()
            val = None
            if self.peek() == ":":
                self.p += 1
                val = self.value()
            k = str(key) if key is not None else ""
            out[k] = val
            out.lines[k] = self.line
            self.ws()
            if self.peek() == ",":
                self.p += 1
            elif self.peek() != "}":
                self.fail("expected ',' or '}' in a { } map")


def flow_balanced(text):
    depth = 0
    quote = None
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if quote == '"' and c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        i += 1
    return depth <= 0 and quote is None


class _Yaml:
    def __init__(self, text):
        self.lines = text.replace("\r\n", "\n").replace("\r", "\n") \
            .split("\n")
        self.i = 0
        self.anchors = {}

    def fail(self, msg, line=None):
        raise YamlError(line or self.i + 1, msg)

    def peek(self):
        """(indent, content without comment) of the next meaningful line."""
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            body = raw.lstrip(" ")
            if not body.strip() or body.startswith("#"):
                self.i += 1
                continue
            if body[0] == "\t":
                self.fail("tab character used for indentation")
            content = strip_comment(body)
            if raw == body and (content in ("---", "...")
                                or body.startswith("%")):
                self.i += 1
                continue
            return len(raw) - len(body), content
        return None

    def parse(self):
        nxt = self.peek()
        if nxt is None:
            return None
        node = self.block(nxt[0])
        if self.peek() is not None:
            self.fail("unexpected content (indentation?)")
        return node

    def block(self, indent):
        ind, content = self.peek()
        if is_item(content):
            return self.seq(ind)
        if KEY_RE.match(content):
            return self.mapping(ind)
        line = self.i + 1
        self.i += 1
        return self.value(content, line, ind - 1, False)

    def mapping(self, indent):
        m = YMap(self.i + 1)
        while True:
            nxt = self.peek()
            if nxt is None or nxt[0] < indent:
                return m
            ind, content = nxt
            if ind > indent:
                self.fail("unexpected indentation")
            if is_item(content):
                self.fail("a '- ' list item where a 'key:' was expected")
            km = KEY_RE.match(content)
            if not km:
                self.fail(f"expected 'key: value', got {content[:40]!r}")
            if km.group("dq") is not None:
                key = unquote('"' + km.group("dq") + '"')
            elif km.group("sq") is not None:
                key = km.group("sq").replace("''", "'")
            else:
                key = km.group("pl").strip()
            line = self.i + 1
            self.i += 1
            value = self.value(content[km.end():], line, indent, True)
            if key == "<<":
                self.merge(m, value, line)
                continue
            if key in m and key not in m.merged:
                self.fail(f"duplicate key '{key}'", line)
            m.merged.discard(key)
            m[key] = value
            m.lines[key] = line

    def merge(self, m, value, line):
        sources = value if isinstance(value, YList) else [value]
        for src in sources:
            if not isinstance(src, YMap):
                self.fail("'<<' needs a mapping alias", line)
            for k, v in src.items():
                if k not in m:
                    m[k] = v
                    m.lines[k] = src.lines.get(k, line)
                    m.merged.add(k)

    def seq(self, indent):
        s = YList(self.i + 1)
        while True:
            nxt = self.peek()
            if nxt is None or nxt[0] < indent:
                return s
            ind, content = nxt
            if ind > indent:
                self.fail("unexpected indentation")
            if not is_item(content):
                return s
            line = self.i + 1
            rest = content[1:]
            stripped = rest.lstrip(" \t")
            col = ind + 1 + len(rest) - len(stripped)
            anchor = None
            am = re.match(r"&(\S+)\s+", stripped)
            if am and KEY_RE.match(stripped[am.end():]):
                anchor = am.group(1)
                col += am.end()
                stripped = stripped[am.end():]
            if not stripped:
                self.i += 1
                nxt2 = self.peek()
                if nxt2 and nxt2[0] > indent:
                    s.append(self.block(nxt2[0]))
                else:
                    s.append(None)
                continue
            if is_item(stripped) or KEY_RE.match(stripped):
                self.lines[self.i] = " " * col + stripped
                node = self.block(col)
                if anchor:
                    self.anchors[anchor] = node
                s.append(node)
                continue
            self.i += 1
            s.append(self.value(stripped, line, indent, False))

    def value(self, rest, line, indent, allow_seq):
        rest = rest.strip()
        anchor = None
        while rest[:1] in ("&", "!"):
            m = re.match(r"([&!])(\S*)\s*", rest)
            if m.group(1) == "&":
                anchor = m.group(2)
            rest = rest[m.end():]
        if rest.startswith("*"):
            name = rest[1:].strip()
            if name not in self.anchors:
                self.fail(f"unknown alias '*{name}'", line)
            node = self.anchors[name]
        elif not rest:
            nxt = self.peek()
            if nxt and (nxt[0] > indent or (allow_seq and nxt[0] == indent
                                            and is_item(nxt[1]))):
                node = self.block(nxt[0])
            else:
                node = None
        elif rest[0] in "|>":
            node = self.block_scalar(rest, line, indent)
        elif rest[0] in "[{":
            text = rest
            while not flow_balanced(text):
                if self.i >= len(self.lines):
                    self.fail("unterminated [ or {", line)
                text += " " + strip_comment(self.lines[self.i].strip())
                self.i += 1
            node = _Flow(text, line, self.anchors).parse()
        else:
            node = self.scalar(rest, line, indent)
        if anchor:
            self.anchors[anchor] = node
        return node

    def block_scalar(self, header, line, indent):
        if not re.fullmatch(r"[|>][0-9+-]*", header):
            self.fail(f"bad block scalar header {header!r}", line)
        parts = []
        bind = None
        while self.i < len(self.lines):
            raw = self.lines[self.i]
            if not raw.strip():
                parts.append("")
                self.i += 1
                continue
            ind = len(raw) - len(raw.lstrip(" "))
            if ind <= indent or (bind is not None and ind < bind):
                break
            if bind is None:
                bind = ind
            parts.append(raw[bind:])
            self.i += 1
        while parts and parts[-1] == "":
            parts.pop()
        if header[0] == "|":
            text = "\n".join(parts)
        else:
            out = []
            for p in parts:
                if p == "":
                    out.append("\n")
                elif out and not out[-1].endswith("\n"):
                    out.append(" " + p)
                else:
                    out.append(p)
            text = "".join(out)
        if "-" not in header:
            text += "\n"
        return ystr(text, line, True)

    def scalar(self, rest, line, indent):
        if rest[0] in "\"'":
            q = rest[0]
            text = rest
            while True:
                end = closing_quote(text, q)
                if end is not None:
                    break
                if self.i >= len(self.lines):
                    self.fail("unterminated quoted string", line)
                text += " " + self.lines[self.i].strip()
                self.i += 1
            tail = strip_comment(text[end + 1:]).strip()
            if tail:
                self.fail(f"unexpected text after a quoted string: "
                          f"{tail[:30]!r}", line)
            return ystr(unquote(text[:end + 1]), line, True)
        parts = [rest]
        while True:
            nxt = self.peek()
            if nxt is None or nxt[0] <= indent:
                break
            if KEY_RE.match(nxt[1]) or is_item(nxt[1]):
                self.fail("unexpected indentation (a key or a list item "
                          "inside a value)")
            parts.append(nxt[1])
            self.i += 1
        text = " ".join(parts)
        if text in NULLS:
            return None
        return ystr(text, line)


def parse_yaml(text):
    return _Yaml(text).parse()


def from_json(obj):
    """docker compose config --format json output -> the same node types."""
    if isinstance(obj, dict):
        m = YMap()
        for k, v in obj.items():
            m[str(k)] = from_json(v)
        return m
    if isinstance(obj, list):
        out = YList()
        out.extend(from_json(v) for v in obj)
        return out
    if obj is None:
        return None
    if isinstance(obj, bool):
        return ystr("true" if obj else "false", 0)
    return ystr(str(obj), 0)


# --------------------------------------------------------------------------
# .env and ${VAR} interpolation
# --------------------------------------------------------------------------

def parse_env(text):
    env, lines = {}, {}
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].lstrip()
        m = re.match(r"([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.*)$", s)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if val[:1] in ("'", '"'):
            end = closing_quote(val, val[0])
            val = unquote(val[:end + 1]) if end is not None else val[1:]
        else:
            val = re.split(r"\s+#", val, maxsplit=1)[0].strip()
        env[key] = val
        lines[key] = n
    return env, lines


def interpolate(s, env):
    """Compose interpolation: ${A}, ${A:-d}, ${A-d}, ${A:?e}, ${A:+x}, $A,
    $$. Returns (text, missing names, ((name, default), ...))."""
    out, missing, defaults = [], [], []
    i = 0
    while i < len(s):
        c = s[i]
        if c != "$":
            out.append(c)
            i += 1
            continue
        nxt = s[i + 1:i + 2]
        if nxt == "$":
            out.append("$")
            i += 2
            continue
        if nxt == "{":
            depth, j = 0, i + 1
            while j < len(s):
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= len(s):
                out.append(s[i:])
                break
            inner = s[i + 2:j]
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?+])(.*))?$",
                         inner, re.S)
            if not m:
                out.append(s[i:j + 1])
                i = j + 1
                continue
            name, op, arg = m.group(1), m.group(2), m.group(3) or ""
            val = env.get(name)
            if op in (":-", "-"):
                if val is None or (op == ":-" and val == ""):
                    sub, mi, de = interpolate(arg, env)
                    missing += mi
                    defaults.append((name, sub))
                    val = sub
            elif op in (":+", "+"):
                use = val is not None and (op == "+" or val != "")
                val = interpolate(arg, env)[0] if use else ""
            elif val is None or (op == ":?" and val == ""):
                missing.append(name)
                val = ""
            out.append(val)
            i = j + 1
            continue
        m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", s[i + 1:])
        if m:
            name = m.group(0)
            val = env.get(name)
            if val is None:
                missing.append(name)
                val = ""
            out.append(val)
            i += 1 + len(name)
            continue
        out.append("$")
        i += 1
    return "".join(out), missing, tuple(defaults)


def resolve(node, env):
    """A copy of the tree with every scalar interpolated."""
    if isinstance(node, YMap):
        new = YMap(node.line)
        new.lines = dict(node.lines)
        for k, v in node.items():
            new[k] = resolve(v, env)
        return new
    if isinstance(node, YList):
        lst = YList(node.line)
        lst.extend(resolve(v, env) for v in node)
        return lst
    if isinstance(node, YStr):
        val, missing, defaults = interpolate(str(node), env)
        s = ystr(val, node.line, node.quoted)
        s.raw = str(node)
        s.missing = tuple(missing)
        s.defaults = defaults
        return s
    return node


def as_list(node):
    if node is None:
        return []
    if isinstance(node, list):
        return list(node)
    return [node]


def names_of(node):
    """networks / secrets of a service: a list or a map."""
    if isinstance(node, dict):
        return [str(k) for k in node]
    return [str(x) for x in as_list(node) if x is not None and
            not isinstance(x, dict)] + \
        [str(x.get("source")) for x in as_list(node)
         if isinstance(x, dict) and x.get("source")]


def env_items(node):
    """environment: as [(key, value or None, line)]."""
    out = []
    if isinstance(node, YMap):
        for k, v in node.items():
            out.append((k, v, node.lines.get(k, node.line)))
    else:
        for item in as_list(node):
            if item is None:
                continue
            k, eq, v = str(item).partition("=")
            val = None
            if eq:
                val = ystr(v, getattr(item, "line", 0))
                val.raw = getattr(item, "raw", str(item)).partition("=")[2]
            out.append((k.strip(), val, getattr(item, "line", 0)))
    return out


# --------------------------------------------------------------------------
# Dockerfiles
# --------------------------------------------------------------------------

class Instr:
    def __init__(self, line, cmd, args):
        self.line = line
        self.cmd = cmd
        self.args = args

    def argv(self):
        """Exec form as a list, or None for the shell form."""
        a = self.args.strip()
        if a.startswith("["):
            try:
                v = json.loads(a)
            except ValueError:
                return None
            if isinstance(v, list) and all(isinstance(x, str) for x in v):
                return v
        return None


def parse_dockerfile(text):
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    escape = "\\"
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s or s.startswith("#"):
            m = re.match(r"#\s*escape\s*=\s*(\S)", s)
            if m and not out:
                escape = m.group(1)
            i += 1
            continue
        start = i + 1
        buf = lines[i]
        while buf.rstrip().endswith(escape) and i + 1 < len(lines):
            buf = buf.rstrip()[:-1]
            i += 1
            while i + 1 < len(lines) and (lines[i].strip().startswith("#")
                                          or not lines[i].strip()):
                i += 1
            buf += " " + lines[i]
        i += 1
        m = re.match(r"(\S+)\s*(.*)", buf.strip(), re.S)
        if not m:
            continue
        cmd, args = m.group(1).upper(), m.group(2)
        hd = re.search(r"<<-?\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?", args)
        if hd and cmd in ("RUN", "COPY", "ADD"):
            word = hd.group(1)
            body = []
            while i < len(lines) and lines[i].strip() != word:
                body.append(lines[i])
                i += 1
            i += 1
            args += "\n" + "\n".join(body)
        out.append(Instr(start, cmd, args))
    return out


def sub_args(text, values):
    """${A} / $A / ${A:-d} in a FROM line, with the ARG values."""
    unresolved = []

    def rep_(m):
        name = m.group(1) or m.group(3)
        op, dflt = m.group(2), None
        if op and op.startswith(":-"):
            dflt = op[2:]
        if name in values and values[name] != "":
            return values[name]
        if dflt is not None:
            return dflt
        unresolved.append(name)
        return ""
    res = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-[^}]*)?\}"
                 r"|\$([A-Za-z_][A-Za-z0-9_]*)", rep_, text)
    return res, unresolved


def arg_pairs(args):
    """ARG / ENV arguments as [(name, value or None)]."""
    pairs = []
    try:
        toks = shlex.split(args, posix=True)
    except ValueError:
        toks = args.split()
    if toks and "=" not in toks[0] and len(toks) >= 2:
        # legacy ENV form: ENV KEY value with spaces
        return [(toks[0], args.strip()[len(toks[0]):].strip())]
    for t in toks:
        k, eq, v = t.partition("=")
        pairs.append((k, v if eq else None))
    return pairs


class Stage:
    def __init__(self, ins, image, tag, digest, alias, unresolved, raw):
        self.line = ins.line
        self.image = image
        self.tag = tag
        self.digest = digest
        self.alias = alias
        self.unresolved = unresolved
        self.raw = raw
        self.parent = None      # an earlier Stage when FROM <stage name>
        self.instrs = []


class DockerInfo:
    def __init__(self, text, build_args):
        self.instrs = parse_dockerfile(text)
        self.stages = []
        gargs = {}
        for ins in self.instrs:
            if ins.cmd == "ARG" and not self.stages:
                for k, v in arg_pairs(ins.args):
                    gargs[k] = build_args.get(k, v if v is not None else "")
            elif ins.cmd == "FROM":
                self._from(ins, gargs)
            elif self.stages:
                self.stages[-1].instrs.append(ins)

    def _from(self, ins, gargs):
        toks = [t for t in ins.args.split() if not t.startswith("--")]
        raw = toks[0] if toks else ""
        alias = toks[2].lower() if len(toks) >= 3 and \
            toks[1].lower() == "as" else None
        ref, unresolved = sub_args(raw, gargs)
        image, tag, digest = ref, None, None
        if "@" in image:
            image, digest = image.split("@", 1)
        last = image.rsplit("/", 1)[-1]
        if ":" in last:
            image, tag = image.rsplit(":", 1)
        st = Stage(ins, image, tag, digest, alias, unresolved, raw)
        for prev in self.stages:
            if prev.alias and prev.alias == ref.lower():
                st.parent = prev
        self.stages.append(st)

    def final(self):
        return self.stages[-1] if self.stages else None

    def last(self, cmd):
        """Last CMD / ENTRYPOINT of the final stage (or its parents)."""
        st = self.final()
        while st is not None:
            found = [i for i in st.instrs if i.cmd == cmd]
            if found:
                return found[-1]
            st = st.parent
        return None

    def runs(self):
        return [i for i in self.instrs if i.cmd == "RUN"]


INSTALL_RE = re.compile(
    r"\b(?:apt-get|apt|aptitude)\b[^;&|\n]*?\binstall\b([^;&|\n]*)"
    r"|\bapk\b[^;&|\n]*?\badd\b([^;&|\n]*)")


def packages_in(text):
    """{package: offset} installed by apt / apk commands in text. The key
    "$" is set when a package list comes from a variable."""
    found = {}
    for m in INSTALL_RE.finditer(text.replace("\\\n", " ")):
        for tok in (m.group(1) or m.group(2) or "").split():
            tok = tok.strip("\"'")
            if tok.startswith("$"):
                found.setdefault("$", m.start())
                continue
            if not tok or tok.startswith(("-", "\\")):
                continue
            name = re.split(r"[=<>~]", tok)[0].lower()
            if re.fullmatch(r"[a-z0-9][a-z0-9.+_-]*", name):
                found.setdefault(name, m.start())
    return found


def is_nginx_pkg(p):
    return p == "nginx" or p.startswith("nginx-")


def is_phpfpm_pkg(p):
    return re.fullmatch(r"php[\d.]*-?fpm[\d.]*", p) is not None


MARIADB_PKGS = {"mariadb", "mariadb-server", "mariadb-server-core",
                "default-mysql-server"}
DBSERVER_PKGS = MARIADB_PKGS | {"mysql-server", "percona-server-server"}


def is_php_pkg(p):
    return re.match(r"php[\d.]*(-|$)", p) is not None or \
        re.fullmatch(r"php\d+(-.*)?", p) is not None


# --------------------------------------------------------------------------
# shell scripts: forbidden keep-alive patterns
# --------------------------------------------------------------------------

SHELLS = {"sh", "bash", "dash", "ash", "zsh", "ksh"}

TAIL_RE = re.compile(r"\btail\b[^\n;&|]*?\s(?:-[a-zA-Z0-9]*[fF][a-zA-Z0-9]*"
                     r"|--follow\b)")
SLEEP_INF_RE = re.compile(r"\bsleep\s+(?:infinity|inf)\b")
INF_LOOP_RE = re.compile(
    r"\bwhile\s+(?:true|:|\[\s*(?:1|true)\s*\]|\[\[\s*(?:1|true)\s*\]\]"
    r"|\(\(\s*1\s*\)\)|sleep\s+[^;\n]*?)(?=\s*(?:;|\n|\bdo\b|$))"
    r"|\buntil\s+false(?=\s*(?:;|\n|\bdo\b|$))"
    r"|\bfor\s*\(\(\s*;\s*;\s*\)\)")
BARE_SHELL_RE = re.compile(r"^[ \t]*(?:exec[ \t]+)?(?:/usr)?(?:/bin/)?"
                           r"(?:ba|da|z|k|a)?sh(?:[ \t]+-[a-z]+)*"
                           r"[ \t]*;?[ \t]*$", re.M)


def strip_shell_comments(text):
    out = []
    for line in text.split("\n"):
        quote = None
        cut = len(line)
        prev = " "
        for i, c in enumerate(line):
            if quote:
                if c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == "#" and prev in " \t;":
                cut = i
                break
            prev = c
        out.append(line[:cut])
    return "\n".join(out)


def loop_body(code, pos):
    """Text between the `do` after pos and its `done`, or None."""
    m = re.compile(r"\bdo\b").search(code, pos)
    if not m:
        return None
    depth = 1
    for t in re.compile(r"\b(do|done)\b").finditer(code, m.end()):
        depth += 1 if t.group(1) == "do" else -1
        if depth == 0:
            return code[m.end():t.start()]
    return code[m.end():]


def shell_hacks(text):
    """[(line, description)] of the keep-alive hacks in shell text."""
    code = strip_shell_comments(text)    # same lines, comments removed
    found = []
    for m in TAIL_RE.finditer(code):
        found.append((m.start(), "tail -f"))
    for m in SLEEP_INF_RE.finditer(code):
        found.append((m.start(), "sleep infinity"))
    for m in INF_LOOP_RE.finditer(code):
        body = loop_body(code, m.end())
        if body is None or not re.search(r"\b(break|exit|return|exec)\b",
                                         body):
            found.append((m.start(), f"'{m.group(0).strip()}' loop that "
                                     "never ends (no break / exit)"))
    for m in BARE_SHELL_RE.finditer(code):
        found.append((m.start(), f"'{m.group(0).strip()}' (an idle shell "
                                 "keeps the container alive)"))
    return [(line_at(code, pos), what) for pos, what in sorted(found)]


def argv_hacks(argv):
    """Hacks in an exec form command (ENTRYPOINT + CMD argv)."""
    if not argv:
        return []
    prog = os.path.basename(argv[0])
    rest = argv[1:]
    if prog in SHELLS:
        if "-c" in rest:
            i = rest.index("-c")
            return [w for _, w in shell_hacks(" ".join(rest[i + 1:i + 2]))]
        if not [a for a in rest if not a.startswith("-")]:
            return [f"'{' '.join(argv)}' (an idle shell keeps the "
                    "container alive)"]
        return []
    return [w for _, w in shell_hacks(" ".join(argv))]


# --------------------------------------------------------------------------
# the project
# --------------------------------------------------------------------------

PASS_KEY_RE = re.compile(r"PASS|PWD|SECRET|TOKEN|(^|_)KEY$|API_?KEY",
                         re.I)


def is_pass_key(key):
    k = key.upper()
    return bool(PASS_KEY_RE.search(k)) and not k.endswith("_FILE") \
        and not k.endswith("_PATH")


def literal_secret(value):
    """A value written as is (no $VAR, not a /run/secrets path)."""
    if value is None:
        return False
    v = str(value).strip().strip("\"'")
    if not v or "$" in v or v.startswith("/run/secrets"):
        return False
    if v.lower() in ("yes", "no", "true", "false", "on", "off", "0", "1",
                     "password_here"):
        return False        # MYSQL_ALLOW_EMPTY_PASSWORD=yes, wp samples
    if re.fullmatch(r"[A-Z][A-Z0-9_]*_[A-Z0-9_]*", v):
        return False        # a placeholder replaced by sed / envsubst
    return True


SCRIPT_PASS_RES = [
    re.compile(r"IDENTIFIED\s+BY\s+(['\"])(.*?)\1", re.I),
    re.compile(r"--(?:admin_password|user_pass|dbpass|password)[= ]"
               r"(['\"]?)([^\s'\";]+)\1"),
    re.compile(r"define\(\s*['\"]DB_PASSWORD['\"]\s*,\s*(['\"])(.*?)\1"),
]
SH_ASSIGN_RE = re.compile(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)"
                          r"=(\"[^\"]*\"|'[^']*'|[^\s;]*)", re.M)


def script_passwords(text, shell=True):
    """[(line, what)] literal passwords written in a script / conf file."""
    out = []
    code = strip_shell_comments(text) if shell else text
    for rx in SCRIPT_PASS_RES:
        for m in rx.finditer(code):
            if literal_secret(m.group(2)):
                out.append((line_at(code, m.start()), m.group(0)[:60]))
    if shell:
        for m in SH_ASSIGN_RE.finditer(code):
            if is_pass_key(m.group(1)) and literal_secret(m.group(2)):
                out.append((line_at(code, m.start()),
                            f"{m.group(1)}={m.group(2)[:30]}"))
    return out


class Service:
    def __init__(self, name, node=None, line=0):
        self.name = name
        self.node = node if isinstance(node, YMap) else YMap()
        self.line = line
        self.role = None
        self.has_build = False
        self.remote = False
        self.context = None
        self.dockerfile = None
        self.inline = None
        self.build_args = {}
        self.docker = None          # DockerInfo
        self.df_text = None
        self.packages = {}          # from the Dockerfile
        self.script_packages = {}   # from the scripts of the context
        self.files = []             # files of the build folder

    def get(self, key):
        return self.node.get(key)

    def kline(self, key):
        return self.node.lines.get(key, self.line)


class Project:
    def __init__(self, root):
        self.root = root
        self.srcs = os.path.join(root, "srcs")
        self.compose_path = None
        for name in ("docker-compose.yml", "docker-compose.yaml",
                     "compose.yml", "compose.yaml"):
            p = os.path.join(self.srcs, name)
            if os.path.isfile(p):
                self.compose_path = p
                break
        self.compose_dir = os.path.dirname(self.compose_path) \
            if self.compose_path else self.srcs
        self.env_path = None
        for p in (os.path.join(self.srcs, ".env"),
                  os.path.join(root, ".env")):
            if os.path.isfile(p):
                self.env_path = p
                break
        self.env, self.env_lines = {}, {}
        if self.env_path:
            self.env, self.env_lines = parse_env(
                read_text(self.env_path) or "")
        self.compose = None         # resolved tree
        self.compose_raw = None     # tree before interpolation
        self.compose_error = None
        self.compose_text = None
        self.from_docker = False
        self.services = []
        self.roles = {}
        self.git = self._git_info()
        self._load_compose()
        self._load_services()

    # ---- paths
    def rel(self, path):
        return os.path.relpath(path, self.root).replace(os.sep, "/")

    @property
    def compose_rel(self):
        return self.rel(self.compose_path) if self.compose_path \
            else "srcs/docker-compose.yml"

    # ---- git
    def _git_info(self):
        """('git', tracked set) / ('export', every file) / ('none', None)."""
        res = core.run(["git", "-c", "core.quotepath=off", "ls-files", "-z"],
                       cwd=self.root, timeout=30)
        inside = core.run(["git", "rev-parse", "--is-inside-work-tree"],
                          cwd=self.root, timeout=30)
        if res.rc == 0 and inside.out.strip() == b"true":
            files = {f for f in res.out.decode("utf-8", "replace")
                     .split("\0") if f}
            return "git", files
        if os.environ.get("NEXT42_PROJECT_LABEL"):
            return "export", set(self.all_files())
        return "none", None

    def all_files(self):
        out = []
        for top, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d != ".git"]
            for f in files:
                out.append(self.rel(os.path.join(top, f)))
        return out

    def tracked(self, rel):
        """True / False, or None when there is no git information."""
        mode, files = self.git
        if mode == "none":
            return None
        return rel in files

    def ignored(self, rel):
        """Is rel ignored by git (git check-ignore, else the .gitignore
        files read by hand)?"""
        if self.git[0] == "git":
            res = core.run(["git", "check-ignore", "-q", "--", rel],
                           cwd=self.root, timeout=30)
            return res.rc == 0
        parts = rel.split("/")
        for depth in range(len(parts)):
            text = read_text(os.path.join(self.root, *parts[:depth],
                                          ".gitignore"))
            if text is None:
                continue
            sub = parts[depth:]
            for pat in text.splitlines():
                pat = pat.strip()
                if not pat or pat.startswith(("#", "!")):
                    continue
                anchored = "/" in pat.rstrip("/")
                p = pat.strip("/")
                if p.startswith("**/"):
                    p, anchored = p[3:], False
                # a pattern matches the path or one of its parent folders
                for n in range(1, len(sub) + 1):
                    if anchored:
                        cands = ["/".join(sub[:n])]
                    else:
                        cands = [sub[n - 1]]
                    if any(fnmatch.fnmatch(c, p) for c in cands):
                        return True
        return False

    # ---- compose
    def _load_compose(self):
        if not self.compose_path:
            return
        self.compose_text = read_text(self.compose_path)
        if self.compose_text is None:
            self.compose_error = YamlError(0, "cannot read the file")
            return
        try:
            raw = parse_yaml(self.compose_text)
        except YamlError as exc:
            self.compose_error = exc
            return
        except RecursionError:
            self.compose_error = YamlError(0, "nested too deeply")
            return
        if not isinstance(raw, YMap):
            self.compose_error = YamlError(1, "the file is not a mapping")
            return
        self.compose_raw = raw
        self.compose = resolve(raw, self.env)

    def use_docker_config(self, data, tmp_dir):
        """The parser failed but docker compose config worked: use it."""
        text = json.dumps(data).replace(tmp_dir, self.root)
        self.compose = from_json(json.loads(text))
        self.compose_raw = self.compose
        self.from_docker = True
        self.services = []
        self.roles = {}
        self._load_services()

    def _load_services(self):
        node = self.compose.get("services") if self.compose else None
        if isinstance(node, YMap):
            for name, s in node.items():
                svc = Service(name, s, node.lines.get(name, 0))
                self._build_info(svc)
                self.services.append(svc)
        else:
            # no usable docker-compose.yml: the Dockerfiles of the usual
            # layout are still checked
            req = os.path.join(self.srcs, "requirements")
            for top in (req, os.path.join(req, "bonus")):
                if not os.path.isdir(top):
                    continue
                for d in sorted(os.listdir(top)):
                    p = os.path.join(top, d)
                    if os.path.isfile(os.path.join(p, "Dockerfile")):
                        svc = Service(d)
                        svc.has_build = True
                        svc.context = p
                        svc.dockerfile = os.path.join(p, "Dockerfile")
                        self.services.append(svc)
        for svc in self.services:
            self._docker_info(svc)
        self._assign_roles()

    def _build_info(self, svc):
        b = svc.get("build")
        if b is None and "build" not in svc.node:
            return
        svc.has_build = True
        ctx, df = ".", None
        if isinstance(b, YMap):
            ctx = str(b.get("context") or ".")
            df = b.get("dockerfile")
            svc.inline = b.get("dockerfile_inline")
            args = b.get("args")
            if isinstance(args, YMap):
                svc.build_args = {k: str(v) if v is not None else ""
                                  for k, v in args.items()}
            else:
                for a in as_list(args):
                    k, _, v = str(a).partition("=")
                    svc.build_args[k] = v
        elif b is not None:
            ctx = str(b)
        if re.match(r"(https?://|git@|ssh://|github\.com)", ctx):
            svc.remote = True
            return
        svc.context = os.path.normpath(os.path.join(self.compose_dir, ctx))
        if svc.inline is None:
            svc.dockerfile = os.path.normpath(
                os.path.join(svc.context, str(df) if df else "Dockerfile"))

    def _docker_info(self, svc):
        if svc.inline is not None:
            svc.df_text = str(svc.inline)
        elif svc.dockerfile and os.path.isfile(svc.dockerfile):
            svc.df_text = read_text(svc.dockerfile)
        if svc.df_text is not None:
            svc.docker = DockerInfo(svc.df_text, svc.build_args)
            for ins in svc.docker.runs():
                for p in packages_in(ins.args):
                    svc.packages.setdefault(p, ins.line)
        base = self.service_dir(svc)
        if base and os.path.isdir(base):
            count = 0
            for top, dirs, files in os.walk(base):
                dirs[:] = sorted(d for d in dirs if d != ".git")
                for f in sorted(files):
                    count += 1
                    if count > 400:
                        break
                    svc.files.append(os.path.join(top, f))
            for f in svc.files:
                if f == svc.dockerfile:
                    continue
                text = read_text(f, 256 * 1024)
                if text and is_script(f, text):
                    for p in packages_in(text):
                        svc.script_packages.setdefault(p, self.rel(f))

    def service_dir(self, svc):
        """The folder of the Dockerfile (its context when it is the same
        or the Dockerfile is elsewhere)."""
        if svc.dockerfile:
            d = os.path.dirname(svc.dockerfile)
            if svc.context and not os.path.normpath(d).startswith(
                    os.path.normpath(svc.context)):
                return svc.context
            return d
        return svc.context

    def installs(self, svc, pred):
        """(where, package) of the first package matching pred."""
        for p, line in svc.packages.items():
            if pred(p):
                return loc(self.rel(svc.dockerfile) if svc.dockerfile
                           else self.compose_rel, line), p
        for p, where in svc.script_packages.items():
            if pred(p):
                return where, p
        return None

    def _assign_roles(self):
        table = (
            ("nginx", {"nginx"}, ("nginx",), is_nginx_pkg),
            ("wordpress", {"wordpress", "wp", "php", "php-fpm", "wp-php"},
             ("wordpress",), is_phpfpm_pkg),
            ("mariadb", {"mariadb", "mysql", "db", "database"},
             ("mariadb", "mysql"), lambda p: p in MARIADB_PKGS),
        )
        for role, exact, part, pred in table:
            free = [s for s in self.services if s.role is None]
            cand = [s for s in free if s.name.lower() in exact] or \
                [s for s in free if any(x in s.name.lower() for x in part)] \
                or [s for s in free if any(pred(p) for p in s.packages)]
            if cand:
                cand[0].role = role
                self.roles[role] = cand[0]

    # ---- misc
    def domain(self):
        """(domain, where) from DOMAIN_NAME, else a *.42.fr server_name."""
        if "DOMAIN_NAME" in self.env:
            return self.env["DOMAIN_NAME"], \
                loc(self.rel(self.env_path),
                    self.env_lines.get("DOMAIN_NAME"))
        for k, v in self.env.items():
            if "DOMAIN" in k.upper() and v:
                return v, loc(self.rel(self.env_path), self.env_lines.get(k))
        for k, v in self.env.items():
            m = re.search(r"\b([a-z0-9][a-z0-9-]*\.42\.fr)\b", v or "")
            if m:
                return m.group(1), loc(self.rel(self.env_path),
                                       self.env_lines.get(k))
        nginx = self.roles.get("nginx")
        if nginx:
            for path, text in nginx_texts(self, nginx):
                m = re.search(r"\bserver_name\s+[^;]*?\b([a-z0-9-]+\.42\.fr)",
                              text)
                if m:
                    return m.group(1), loc(self.rel(path),
                                           line_at(text, m.start()))
        return None, None

    def login(self):
        dom, _ = self.domain()
        m = re.fullmatch(r"([^.]+)\.42\.fr", dom or "")
        return m.group(1) if m else None

    def secret_files(self):
        """Files of the top-level secrets: (and the secrets/ folder)."""
        out = set()
        if self.compose:
            sec = self.compose.get("secrets")
            if isinstance(sec, YMap):
                for v in sec.values():
                    if isinstance(v, YMap) and v.get("file"):
                        p = os.path.normpath(os.path.join(
                            self.compose_dir, str(v.get("file"))))
                        out.add(self.rel(p))
        for top in (os.path.join(self.root, "secrets"),
                    os.path.join(self.srcs, "secrets")):
            if os.path.isdir(top):
                for d, _, files in os.walk(top):
                    for f in files:
                        if re.search(r"example|sample|template|readme|"
                                     r"\.md$|^\.git", f, re.I):
                            continue
                        out.add(self.rel(os.path.join(d, f)))
        return sorted(out)


def is_script(path, text):
    return path.endswith((".sh", ".bash")) or text.startswith("#!")


# --------------------------------------------------------------------------
# releases: which Debian / Alpine is the penultimate stable
# --------------------------------------------------------------------------

DEBIAN_CODENAMES = {"wheezy": 7, "jessie": 8, "stretch": 9, "buster": 10,
                    "bullseye": 11, "bookworm": 12, "trixie": 13,
                    "forky": 14, "duke": 15}
# built-in knowledge, used when the online lookup is not possible
BUILTIN_DATE = "2026-11"
BUILTIN_DEBIAN = ((13, "trixie"), (12, "bookworm"))


class Releases:
    def __init__(self):
        self.debian = None      # ((stable n, name), (oldstable n, name))
        self.alpine = None      # ["3.24", "3.23", ...] newest first
        self.source = ""
        self.why_offline = ""


def fetch(url, limit=1 << 20, timeout=4):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "42next"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(limit).decode("utf-8", "replace")
    except Exception:
        return None


def _debian_release(suite):
    text = fetch(f"https://deb.debian.org/debian/dists/{suite}/Release",
                 limit=2048)
    if not text:
        return None
    v = re.search(r"^Version:\s*(\d+)", text, re.M)
    c = re.search(r"^Codename:\s*(\w+)", text, re.M)
    if not v or not c:
        return None
    return int(v.group(1)), c.group(1).lower()


def alpine_branches(data):
    out = []
    for b in data.get("release_branches", []):
        name = str(b.get("rel_branch", ""))
        m = re.fullmatch(r"v(\d+)\.(\d+)", name)
        if m and b.get("releases"):
            out.append((int(m.group(1)), int(m.group(2))))
    out.sort(reverse=True)
    return [f"{a}.{b}" for a, b in out]


def load_releases(want_debian, want_alpine, quick):
    rel = Releases()
    path = os.environ.get("NEXT42_INCEPTION_RELEASES")
    if path:
        try:
            with open(path) as fh:
                data = json.load(fh)
            d = data.get("debian")
            if d:
                rel.debian = ((int(d["stable"][0]), d["stable"][1]),
                              (int(d["oldstable"][0]), d["oldstable"][1]))
            if data.get("alpine"):
                rel.alpine = [str(x) for x in data["alpine"]]
            rel.source = "release list " + path
        except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
            rel.why_offline = f"bad {path}: {e}"
        return rel
    if quick:
        rel.why_offline = "quick mode: no online lookup"
        return rel
    if os.environ.get("NEXT42_INCEPTION_OFFLINE"):
        rel.why_offline = "online lookup disabled"
        return rel
    if want_debian:
        st, old = _debian_release("stable"), _debian_release("oldstable")
        if st and old:
            rel.debian = (st, old)
    if want_alpine:
        text = fetch("https://alpinelinux.org/releases.json")
        try:
            br = alpine_branches(json.loads(text)) if text else []
        except ValueError:
            br = []
        if len(br) >= 2:
            rel.alpine = br
    rel.source = "online (deb.debian.org, alpinelinux.org)"
    if (want_debian and not rel.debian) or (want_alpine and not rel.alpine):
        rel.why_offline = "online lookup failed (no network?)"
    return rel


def debian_verdict(tag, rel):
    """('ok' | 'ko' | 'warn', message)."""
    t = tag.lower()
    m = re.match(r"(\d+)(?:\.\d+)*|([a-z]+)", t)
    word = m.group(0) if m else t
    if word == "stable":
        return "ko", ("'stable' is the newest stable release, the subject "
                      "asks for the penultimate (oldstable)")
    if word == "oldstable":
        return "ok", "oldstable"
    if word in ("testing", "sid", "unstable", "experimental", "rc"):
        return "ko", f"'{word}' is not a stable release"
    if m and m.group(1):
        major = int(m.group(1))
    elif word in DEBIAN_CODENAMES:
        major = DEBIAN_CODENAMES[word]
    else:
        return "warn", f"unknown Debian tag '{tag}': check it by hand"
    if rel.debian:
        (sn, sname), (on, oname) = rel.debian
        want = f"{on} ({oname})"
        if major == on:
            return "ok", f"Debian {want}"
        if major == sn:
            return "ko", (f"Debian {sn} ({sname}) is the newest stable; "
                          f"the penultimate stable is {want}")
        if major > sn:
            return "ko", f"Debian {major} is not released as stable yet"
        return "ko", (f"Debian {major} is older than the penultimate stable "
                      f"{want}")
    (sn, sname), (on, oname) = BUILTIN_DEBIAN
    if major == on:
        return "ok", f"Debian {on} ({oname}), built-in list ({BUILTIN_DATE})"
    return "warn", (f"could not check online ({rel.why_offline}); the "
                    f"built-in list ({BUILTIN_DATE}) says the penultimate "
                    f"stable is Debian {on} ({oname})")


def alpine_verdict(tag, rel):
    t = tag.lower()
    if t.startswith("edge"):
        return "ko", "'edge' is not a stable release"
    m = re.match(r"(\d+)\.(\d+)", t)
    if not rel.alpine:
        return "warn", (f"could not check online ({rel.why_offline}): "
                        "check that it is the penultimate stable Alpine")
    stable, penult = rel.alpine[0], rel.alpine[1]
    if not m:
        if re.fullmatch(r"\d+", t) and stable.startswith(t + "."):
            return "ko", (f"alpine:{t} follows the newest {stable}; the "
                          f"penultimate stable is {penult}")
        return "warn", f"unknown Alpine tag '{tag}': check it by hand"
    branch = f"{m.group(1)}.{m.group(2)}"
    if branch == penult:
        return "ok", f"Alpine {penult}"
    if branch == stable:
        return "ko", (f"Alpine {stable} is the newest stable; the "
                      f"penultimate stable is {penult}")
    return "ko", f"Alpine {branch}: the penultimate stable is {penult}"


# --------------------------------------------------------------------------
# helpers shared by groups
# --------------------------------------------------------------------------

def parse_port(item):
    """(host ip, published, target) of a ports: entry."""
    if isinstance(item, YMap):
        return (str(item.get("host_ip") or ""),
                str(item.get("published") or ""),
                str(item.get("target") or ""))
    s = str(item).split("/")[0].strip()
    ip = ""
    if s.startswith("["):
        ip, _, s = s[1:].partition("]:")
    parts = s.split(":")
    if len(parts) == 1:
        return ip, "", parts[0]
    if len(parts) == 2:
        return ip, parts[0], parts[1]
    return parts[0], parts[-2], parts[-1]


class Mount:
    def __init__(self, kind, source, target, line, missing=()):
        self.kind = kind            # volume | bind | anon | tmpfs
        self.source = source
        self.target = target
        self.line = line
        self.missing = missing


def service_mounts(svc):
    out = []
    for item in as_list(svc.get("volumes")):
        line = getattr(item, "line", 0) or svc.kline("volumes")
        if isinstance(item, YMap):
            src = item.get("source")
            tgt = str(item.get("target") or "")
            kind = str(item.get("type") or "")
            missing = getattr(src, "missing", ())
            src = str(src) if src is not None else ""
            if not kind:
                kind = "bind" if src[:1] in ("/", ".", "~") else \
                    ("volume" if src else "anon")
            out.append(Mount(kind, src, tgt, line or item.line, missing))
            continue
        if item is None:
            continue
        parts = str(item).split(":")
        missing = getattr(item, "missing", ())
        if len(parts) == 1:
            out.append(Mount("anon", "", parts[0], line, missing))
            continue
        src, tgt = parts[0], parts[1]
        kind = "bind" if src[:1] in ("/", ".", "~") or not src else "volume"
        out.append(Mount(kind, src, tgt, line, missing))
    return out


DATA_RE = re.compile(r"^/home/([^/]+)/data(?:/|$)")


def data_mount(svc, role):
    mounts = [m for m in service_mounts(svc) if m.kind != "tmpfs"
              and not m.target.startswith("/run/secrets")
              and not re.search(r"\.(conf|cnf|sh|ini|php|pem|crt|key)$",
                                m.target)]
    want = "/var/lib/mysql" if role == "mariadb" else "/var/www"
    for m in mounts:
        if m.target.startswith(want):
            return m
    return mounts[0] if mounts else None


def nginx_texts(proj, svc):
    """(path, text) of the files of the NGINX service that may hold its
    configuration: build folder and bind mounted sources."""
    paths = []
    for f in svc.files:
        if f.endswith((".pem", ".crt", ".key", ".csr", ".png", ".jpg",
                       ".ico", ".gz")):
            continue
        paths.append(f)
    for m in service_mounts(svc):
        if m.kind == "bind" and m.source.startswith("."):
            p = os.path.normpath(os.path.join(proj.compose_dir, m.source))
            if os.path.isfile(p):
                paths.append(p)
            elif os.path.isdir(p):
                for top, _, files in os.walk(p):
                    for f in files:
                        paths.append(os.path.join(top, f))
    out = []
    seen = set()
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        text = read_text(p, 256 * 1024)
        if text is not None:
            out.append((p, text))
    return out


def nginx_conf_code(text):
    """nginx comments removed (shell comments too: same # syntax)."""
    return "\n".join(re.sub(r"(^|\s)#.*$", r"\1", ln)
                     for ln in text.split("\n"))


def runtime_commands(svc):
    """[(where, kind, value)] what starts the container:
    kind 'argv' (exec form list) or 'shell' (string)."""
    out = []
    for key in ("entrypoint", "command"):
        v = svc.get(key)
        if v is None:
            continue
        where = ("compose", svc.kline(key))
        if isinstance(v, YList):
            out.append((where, "argv", [str(x) for x in v if x is not None]))
        else:
            out.append((where, "shell", str(v)))
    if svc.docker:
        for cmd in ("ENTRYPOINT", "CMD"):
            ins = svc.docker.last(cmd)
            if ins is None:
                continue
            where = ("dockerfile", ins.line)
            argv = ins.argv()
            if argv is not None:
                out.append((where, "argv", argv))
            else:
                out.append((where, "shell", ins.args))
    return out


def copy_map(proj, svc):
    """{basename inside the image: source file} from COPY / ADD."""
    out = {}
    if not svc.docker or not svc.context:
        return out
    for ins in svc.docker.instrs:
        if ins.cmd not in ("COPY", "ADD"):
            continue
        argv = ins.argv()
        toks = argv if argv is not None else \
            [t for t in ins.args.split() if not t.startswith("--")]
        if len(toks) < 2:
            continue
        srcs, dest = toks[:-1], toks[-1]
        for s in srcs:
            p = os.path.normpath(os.path.join(svc.context, s))
            if os.path.isfile(p):
                name = os.path.basename(p) if dest.endswith("/") or \
                    len(srcs) > 1 else os.path.basename(dest.rstrip("/")) \
                    or os.path.basename(p)
                out[name] = p
                out.setdefault(os.path.basename(p), p)
            elif os.path.isdir(p):
                for top, _, files in os.walk(p):
                    for f in files:
                        out.setdefault(f, os.path.join(top, f))
    return out


def entry_scripts(proj, svc):
    """Scripts of the build folder run when the container starts:
    referenced by ENTRYPOINT / CMD / compose command, and the scripts
    they call."""
    scripts = {}
    for f in svc.files:
        text = read_text(f, 256 * 1024)
        if text and is_script(f, text):
            scripts[f] = text
    if not scripts:
        return []
    names = copy_map(proj, svc)
    for f in scripts:
        names.setdefault(os.path.basename(f), f)

    def refs(text):
        found = []
        for tok in re.findall(r"[\w./-]+", text):
            p = names.get(os.path.basename(tok))
            if p in scripts:
                found.append(p)
        return found
    start = []
    for _, kind, val in runtime_commands(svc):
        start += refs(" ".join(val) if kind == "argv" else val)
    todo = list(dict.fromkeys(start))
    seen = []
    while todo:
        p = todo.pop(0)
        if p in seen:
            continue
        seen.append(p)
        todo += [x for x in refs(strip_shell_comments(scripts[p]))
                 if x not in seen]
    return [(p, scripts[p]) for p in seen]


# --------------------------------------------------------------------------
# groups
# --------------------------------------------------------------------------

def group_files(rep, proj):
    rep.section("files", gid="files")
    text = core.makefile_text(proj.root)
    ko(rep, "Makefile at the root", text is not None, "makefile")
    if text is not None:
        code = "\n".join(ln.split("#")[0] for ln in text.splitlines())
        uses = re.search(r"docker[ -]compose", code)
        ko(rep, "Makefile runs docker compose", bool(uses),
           "makefile_compose", where="Makefile",
           hint="no 'docker compose' / 'docker-compose' command found")
        if uses and not re.search(r"docker-compose\.ya?ml|compose\.ya?ml|"
                                  r"\bsrcs\b", code):
            rep.warn("Makefile never names srcs/docker-compose.yml",
                     "docker compose must find srcs/docker-compose.yml "
                     "(-f srcs/docker-compose.yml or cd srcs)")
        pull = re.search(r"\bdocker\s+pull\b", code)
        ko(rep, "Makefile pulls no image", not pull, "pull",
           where=loc("Makefile", line_at(code, pull.start()))
           if pull else None)
    srcs = os.path.isdir(proj.srcs)
    ko(rep, "srcs folder", srcs, "srcs")
    # a Dockerfile / compose file outside srcs is used by the project when
    # it is a compose file or the Dockerfile of a service
    used = {proj.rel(s.dockerfile) for s in proj.services if s.dockerfile}
    outside, stray = [], []
    for f in proj.all_files():
        base = os.path.basename(f)
        if f.startswith("srcs/"):
            continue
        if re.fullmatch(r"(docker-)?compose(\.[\w-]+)?\.ya?ml", base) or \
                f in used:
            outside.append(f)
        elif base == "Dockerfile" or base.startswith("Dockerfile."):
            stray.append(f)
    ko(rep, "Dockerfiles and docker-compose.yml inside srcs", not outside,
       "srcs", got=", ".join(outside[:6]) if outside else None)
    if stray and not outside:
        rep.warn("Dockerfile outside srcs: " + ", ".join(stray[:4]),
                 f'not used by the compose file; subject: "{Q["srcs"]}"')
    # .env
    env_rel = "srcs/.env"
    if proj.env_path and proj.rel(proj.env_path) == ".env":
        rep.warn(".env is at the root, not in srcs",
                 "docker compose reads srcs/.env (next to "
                 "docker-compose.yml); the subject's example has srcs/.env")
    if proj.env_path:
        ko(rep, ".env file", True, "env")
    elif proj.ignored(env_rel):
        rep.skip(".env file", "srcs/.env is ignored by git and missing "
                 "here: create it locally before running the stack")
    else:
        ko(rep, ".env file", False, "env", expected="srcs/.env",
           got="no .env in srcs/ (nor at the root)")


def group_compose(rep, proj, docker_cfg):
    rep.section("compose", gid="compose")
    if not proj.compose_path:
        ko(rep, "srcs/docker-compose.yml", False, "called",
           got="not found in srcs/")
        return
    rep.check(f"{proj.compose_rel} found", True)
    name = os.path.basename(proj.compose_path)
    if not name.startswith("docker-compose."):
        rep.warn(f"the file is named {name}",
                 "the subject names it docker-compose.yml")
    if docker_cfg is not None:
        ok, msg = docker_cfg
        ko(rep, "docker compose config accepts the file", ok, "compose",
           where=proj.compose_rel, got=None if ok else msg)
    if proj.compose_error is not None and not proj.from_docker:
        e = proj.compose_error
        rep.warn(f"{proj.compose_rel}:{e.line}: cannot read the YAML "
                 f"({e.msg})", "the compose checks are skipped; with docker "
                 "installed, `docker compose config` decides")
        rep.skip("services, images, restart, network, ports, volumes",
                 "YAML not understood")
        return
    if proj.compose is None:
        return
    rel = proj.compose_rel
    services = proj.services
    if not rep.check("services: defined", bool(services),
                     detail=f"{rel}: no services: mapping"):
        return
    for role, quote in (("nginx", "nginx"), ("wordpress", "wp"),
                        ("mariadb", "mariadb")):
        svc = proj.roles.get(role)
        ko(rep, f"{role} service", svc is not None, quote,
           where=rel, hint=None if svc else
           f"no service named {role} or installing it")
    if proj.from_docker:
        rep.info("read from `docker compose config` (no line numbers)")

    # build + image
    seen_df = {}
    for svc in services:
        where = loc(rel, svc.line)
        img = svc.get("image")
        if not svc.has_build:
            ko(rep, f"{svc.name}: built from a Dockerfile (build:)", False,
               "pull", where=where, got=f"image: {img}" if img else
               "no build:", hint="every service needs build: with your "
               "own Dockerfile")
        else:
            rep.check(f"{svc.name}: built from a Dockerfile (build:)", True)
            if svc.remote:
                rep.warn(f"{svc.name}: build context is a remote URL",
                         "the Dockerfile cannot be checked")
            elif svc.inline is not None:
                rep.warn(f"{svc.name}: dockerfile_inline",
                         "the subject asks for Dockerfiles, one per service")
            else:
                exists = svc.dockerfile and os.path.isfile(svc.dockerfile)
                ko(rep, f"{svc.name}: Dockerfile exists", bool(exists),
                   "dockerfile", where=loc(rel, svc.kline("build")),
                   got=proj.rel(svc.dockerfile) if svc.dockerfile else None)
                if exists:
                    seen_df.setdefault(svc.dockerfile, []).append(svc.name)
        want = svc.name
        if img is None:
            ko(rep, f"{svc.name}: image name", False, "image_name",
               where=where, expected=f"image: {want}",
               got=f"no image: (compose names it <project>-{want})")
        else:
            name = str(img)
            repo = name.split("@")[0]
            last = repo.rsplit("/", 1)[-1]
            iname = last.split(":")[0]
            itag = last.split(":")[1] if ":" in last else None
            where_i = loc(rel, svc.kline("image"))
            ok = iname == want
            ko(rep, f"{svc.name}: image name", ok, "image_name",
               where=where_i, expected=want, got=name)
            if ok and "/" in repo:
                rep.warn(f"{svc.name}: image {name} has a prefix",
                         f"the subject asks for the service name ({want})")
            ko(rep, f"{svc.name}: image tag is not latest", itag != "latest",
               "latest", where=where_i, got=name)
    shared = {df: n for df, n in seen_df.items() if len(n) > 1}
    ko(rep, "one Dockerfile per service", not shared, "dockerfile",
       got="; ".join(f"{proj.rel(df)} used by {', '.join(n)}"
                     for df, n in shared.items()) or None)

    # restart
    for svc in services:
        r = svc.get("restart")
        dep = svc.get("deploy")
        cond = None
        if isinstance(dep, YMap) and isinstance(dep.get("restart_policy"),
                                                YMap):
            cond = dep.get("restart_policy").get("condition")
        val = str(r) if r is not None else None
        ok = (val is not None and re.fullmatch(
            r"always|unless-stopped|on-failure(:\d+)?", val) is not None) \
            or (val is None and str(cond) in ("any", "on-failure"))
        ko(rep, f"{svc.name}: restart policy", ok, "restart",
           where=loc(rel, svc.kline("restart") if r is not None
                     else svc.line),
           expected="restart: always / unless-stopped / on-failure",
           got=f"restart: {val}" if val is not None else "no restart:")

    # network
    top_net = proj.compose.get("networks")
    ko(rep, "networks: section present", "networks" in proj.compose,
       "netline", where=rel)
    declared = list(top_net.keys()) if isinstance(top_net, YMap) else []
    for nname in declared:
        n = top_net.get(nname)
        if isinstance(n, YMap) and str(n.get("driver") or "") == "host" \
                or nname == "host" or isinstance(n, YMap) and \
                str(n.get("name") or "") == "host":
            ko(rep, f"network {nname} is not the host network", False,
               "host", where=loc(rel, top_net.lines.get(nname)))
    for svc in services:
        nm = svc.get("network_mode")
        if nm is not None:
            ko(rep, f"{svc.name}: no network_mode: {nm}", str(nm) != "host",
               "host", where=loc(rel, svc.kline("network_mode")))
            if str(nm) != "host":
                rep.warn(f"{svc.name}: network_mode: {nm}",
                         "the subject asks for a docker-network between "
                         "the containers")
        for key in ("links", "external_links"):
            if key in svc.node:
                ko(rep, f"{svc.name}: no {key}:", False, "host",
                   where=loc(rel, svc.kline(key)))
        b = svc.get("build")
        if isinstance(b, YMap) and str(b.get("network") or "") == "host":
            ko(rep, f"{svc.name}: no build network: host", False, "host",
               where=loc(rel, b.lines.get("network")))
        if declared and nm is None and not names_of(svc.get("networks")):
            rep.warn(f"{svc.name}: joins none of the declared networks",
                     f"declared: {', '.join(declared)} "
                     "(it is only on the default network)")
    hits = []
    texts = []
    mk = core.makefile_text(proj.root)
    if mk:
        texts.append(("Makefile", mk))
    for f in proj.all_files():
        if f.startswith("srcs/") and f.endswith((".sh", ".bash")):
            texts.append((f, read_text(os.path.join(proj.root, f)) or ""))
    for f, t in texts:
        code = strip_shell_comments(t)
        for m in re.finditer(r"--link\b|--net(?:work)?[= ]host\b", code):
            hits.append(loc(f, line_at(code, m.start())) + ": "
                        + m.group(0))
    ko(rep, "no --link / --network host in Makefile and scripts", not hits,
       "host", got=", ".join(hits[:5]) or None)

    # ports
    for svc in services:
        ports = as_list(svc.get("ports"))
        where = loc(rel, svc.kline("ports"))
        if svc.role == "nginx":
            bad_lines = [getattr(p, "line", 0) for p in ports
                         if parse_port(p)[1] != "443"]
            if bad_lines and bad_lines[0]:
                where = loc(rel, bad_lines[0])
        if svc.role != "nginx":
            ko(rep, f"{svc.name}: publishes no port", not ports, "entry",
               where=where, got=", ".join(str(p) for p in ports) or None,
               hint="only NGINX may be reached from outside")
            continue
        bad = []
        for p in ports:
            ip, pub, tgt = parse_port(p)
            if pub != "443":
                bad.append(str(p) if not isinstance(p, YMap) else
                           f"published {pub or '(random)'} -> {tgt}")
            elif tgt != "443":
                rep.warn(f"{svc.name}: port 443 goes to container port "
                         f"{tgt}", "NGINX usually listens on 443")
        ko(rep, f"{svc.name}: publishes port 443", bool(ports), "entry",
           where=where, expected='ports: ["443:443"]', got="no ports:")
        ko(rep, f"{svc.name}: publishes port 443 only", not bad, "entry",
           where=where, expected="443:443", got=", ".join(bad) or None)

    # .env used
    uses_env = "$" in (proj.compose_text or "") or any(
        svc.get("env_file") is not None for svc in services)
    ko(rep, "docker-compose.yml uses the .env (env_file: or ${VAR})",
       uses_env, "envvars", where=rel)
    check_volumes(rep, proj)


def check_volumes(rep, proj):
    rel = proj.compose_rel
    top = proj.compose.get("volumes")
    top = top if isinstance(top, YMap) else YMap()
    logins = {}
    dom_login = proj.login()
    checked = set()

    def where_of(name):
        """Location of the volume data on the host, or (None, why)."""
        if name not in top:
            return None, f"volume '{name}' is not declared under volumes:"
        v = top.get(name)
        if isinstance(v, YMap) and str(v.get("external") or "") == "true":
            return "external", None
        opts = v.get("driver_opts") if isinstance(v, YMap) else None
        dev = opts.get("device") if isinstance(opts, YMap) else None
        if dev is None:
            return None, (f"volume '{name}' has no driver_opts device: "
                          "docker keeps it in /var/lib/docker/volumes")
        if getattr(dev, "missing", ()):
            return "unresolved", ", ".join(dev.missing)
        return str(dev), None

    def check_path(label, path, line, missing=()):
        if missing:
            rep.warn(f"{label}: path uses ${{{missing[0]}}}, not set in "
                     ".env", "cannot check that it is in /home/<login>/data")
            return
        path = os.path.normpath(path) if path.startswith("/") else path
        m = DATA_RE.match(path)
        ko(rep, f"{label}: stored in /home/<login>/data", bool(m), "data",
           where=loc(rel, line), got=path)
        if m:
            logins.setdefault(m.group(1), loc(rel, line))

    for role, quote in (("mariadb", "vol_db"), ("wordpress", "vol_wp")):
        svc = proj.roles.get(role)
        if svc is None:
            continue
        mnt = data_mount(svc, role)
        if not ko(rep, f"{svc.name}: has a volume", mnt is not None, quote,
                  where=loc(rel, svc.line)):
            continue
        label = f"{svc.name} volume {mnt.source or mnt.target}"
        if mnt.kind == "anon":
            ko(rep, f"{label}: named volume", False, "data",
               where=loc(rel, mnt.line), hint="an anonymous volume lives "
               "in /var/lib/docker/volumes")
        elif mnt.kind == "bind":
            rep.warn(f"{label}: bind mount, not a Docker volume",
                     f"{loc(rel, mnt.line)}\nthe subject asks for volumes "
                     "(and the README compares Docker Volumes vs Bind "
                     "Mounts)")
            src = mnt.source
            if src.startswith("."):
                src = os.path.normpath(os.path.join(proj.compose_dir, src))
            check_path(label, src, mnt.line, mnt.missing)
        else:
            checked.add(mnt.source)
            path, why = where_of(mnt.source)
            line = top.lines.get(mnt.source, mnt.line)
            if path == "external":
                rep.warn(f"{label}: external volume", "cannot check where "
                         "it is stored")
            elif path == "unresolved":
                check_path(label, "", line, (why,))
            elif path is None:
                ko(rep, f"{label}: stored in /home/<login>/data", False,
                   "data", where=loc(rel, line), hint=why)
            else:
                check_path(label, path, line)
    db = proj.roles.get("mariadb")
    wp = proj.roles.get("wordpress")
    if db and wp:
        a, b = data_mount(db, "mariadb"), data_mount(wp, "wordpress")
        if a and b and a.source and b.source:
            ko(rep, "database and website files in two volumes",
               a.source != b.source, "vol_wp", where=loc(rel, b.line),
               got=f"both use {a.source}")
    # every other named volume used by a service
    for svc in proj.services:
        for mnt in service_mounts(svc):
            if mnt.kind != "volume" or mnt.source in checked:
                continue
            checked.add(mnt.source)
            path, why = where_of(mnt.source)
            line = top.lines.get(mnt.source, mnt.line)
            label = f"volume {mnt.source} ({svc.name})"
            if path == "external":
                continue
            if path == "unresolved":
                check_path(label, "", line, (why,))
            elif path is None:
                ko(rep, f"{label}: stored in /home/<login>/data", False,
                   "data", where=loc(rel, line), hint=why)
            else:
                check_path(label, path, line)
    if len(logins) > 1:
        ko(rep, "every volume under the same /home/<login>/data", False,
           "own_login", got=", ".join(f"{k} ({v})"
                                      for k, v in logins.items()))
    if dom_login and logins:
        bad = {k: v for k, v in logins.items() if k != dom_login}
        dom, dwhere = proj.domain()
        ko(rep, "volume path login matches the domain login", not bad,
           "own_login", where=dwhere, expected=f"/home/{dom_login}/data "
           f"(domain {dom})", got=", ".join(f"/home/{k}/data ({v})"
                                            for k, v in bad.items()) or None)


def group_dockerfiles(rep, proj, rel_info):
    rep.section("dockerfiles", gid="dockerfiles")
    svcs = [s for s in proj.services if s.docker is not None]
    if not svcs:
        rep.skip("Dockerfiles", "no Dockerfile found (see compose)")
        return
    for svc in svcs:
        dfrel = proj.rel(svc.dockerfile) if svc.dockerfile else \
            f"{proj.compose_rel} (dockerfile_inline)"
        d = svc.docker
        if not d.stages:
            ko(rep, f"{svc.name}: FROM line", False, "base", where=dfrel)
            continue
        for st in d.stages:
            where = loc(dfrel, st.line)
            if st.parent is not None:
                continue
            if st.unresolved:
                rep.warn(f"{svc.name}: FROM {st.raw}",
                         f"{where}: ${st.unresolved[0]} has no value: "
                         "cannot check the base image")
                continue
            name = re.sub(r"^(docker\.io/|index\.docker\.io/)?(library/)?",
                          "", st.image.lower())
            if not ko(rep, f"{svc.name}: FROM {st.raw} is Alpine or Debian",
                      name in ("debian", "alpine"), "base", where=where,
                      hint="any other image is a ready-made image "
                      f'("{Q["pull"]}")'):
                continue
            if st.tag is None and st.digest:
                rep.warn(f"{svc.name}: FROM {st.raw} pinned by digest",
                         "cannot tell the version: use a version tag")
                continue
            tag = st.tag or ""
            ko(rep, f"{svc.name}: FROM {st.raw} has a version tag "
               "(not latest)", bool(tag) and tag.lower() != "latest",
               "latest", where=where, hint=None if tag else
               "no tag means latest")
            if not tag or tag.lower() == "latest":
                continue
            verdict, msg = (debian_verdict if name == "debian"
                            else alpine_verdict)(tag, rel_info)
            label = f"{svc.name}: {name}:{tag} is the penultimate stable"
            if verdict == "warn":
                rep.warn(label, f"{where}: {msg}")
            else:
                ko(rep, label, verdict == "ok", "base", where=where,
                   got=msg if verdict == "ko" else None)
                if verdict == "ok" and rep.verbose:
                    rep.info(msg)
        # passwords
        bad = []
        for ins in d.instrs:
            if ins.cmd in ("ENV", "ARG"):
                for k, v in arg_pairs(ins.args):
                    if is_pass_key(k) and literal_secret(v):
                        bad.append(f"{loc(dfrel, ins.line)}: {ins.cmd} {k}=")
            elif ins.cmd == "RUN":
                for _, what in script_passwords(ins.args):
                    bad.append(f"{loc(dfrel, ins.line)}: {what}")
        ko(rep, f"{svc.name}: no password in the Dockerfile", not bad,
           "nopass", got="\n".join(bad[:6]) or None)
        check_packages(rep, proj, svc)


def check_packages(rep, proj, svc):
    role = svc.role
    if role is None:
        return
    has = proj.installs
    if "$" in svc.packages or "$" in svc.script_packages:
        # part of the packages come from a variable: a missing package is
        # only a warning
        def need(name, ok, quote, where=None):
            if not ok:
                rep.warn(name, f"{where}: not found, but some packages come "
                         "from a variable ($...): check by hand")
            return True
    else:
        def need(name, ok, quote, where=None):
            return ko(rep, name, ok, quote, where=where)
    if role == "nginx":
        need(f"{svc.name}: installs nginx", bool(has(svc, is_nginx_pkg)),
             "nginx", where=proj.rel(svc.dockerfile or proj.compose_path))
        for pred, what in ((is_phpfpm_pkg, "php-fpm"),
                           (lambda p: p in DBSERVER_PKGS, "a database")):
            hit = has(svc, pred)
            ko(rep, f"{svc.name}: does not install {what}", hit is None,
               "dedicated", where=hit[0] if hit else None,
               got=hit[1] if hit else None)
    elif role == "wordpress":
        need(f"{svc.name}: installs php-fpm",
             bool(has(svc, is_phpfpm_pkg)), "wp",
             where=proj.rel(svc.dockerfile or proj.compose_path))
        hit = has(svc, is_nginx_pkg)
        ko(rep, f"{svc.name}: does not install nginx", hit is None, "wp",
           where=hit[0] if hit else None, got=hit[1] if hit else None)
        hit = has(svc, lambda p: p in DBSERVER_PKGS)
        ko(rep, f"{svc.name}: does not install a database server",
           hit is None, "dedicated", where=hit[0] if hit else None,
           got=hit[1] if hit else None)
        texts = [svc.df_text or ""] + [read_text(f, 256 * 1024) or ""
                                       for f in svc.files]
        if not any(re.search(r"wp-cli|wordpress\.org|wp core download|"
                             r"wordpress.*\.tar\.gz|latest\.tar\.gz", t)
                   for t in texts):
            rep.warn(f"{svc.name}: no WordPress download found",
                     "no wp-cli / wordpress.org download in the Dockerfile "
                     "or the scripts")
    elif role == "mariadb":
        need(f"{svc.name}: installs MariaDB",
             bool(has(svc, lambda p: p in MARIADB_PKGS)), "mariadb",
             where=proj.rel(svc.dockerfile or proj.compose_path))
        hit = has(svc, is_nginx_pkg)
        ko(rep, f"{svc.name}: does not install nginx", hit is None,
           "mariadb", where=hit[0] if hit else None,
           got=hit[1] if hit else None)
        hit = has(svc, is_php_pkg)
        ko(rep, f"{svc.name}: does not install php", hit is None,
           "dedicated", where=hit[0] if hit else None,
           got=hit[1] if hit else None)


def group_nginx(rep, proj):
    rep.section("nginx", gid="nginx")
    svc = proj.roles.get("nginx")
    dom, dwhere = proj.domain()
    check_domain(rep, proj, dom, dwhere)
    if svc is None:
        rep.skip("NGINX configuration", "no nginx service found")
        return
    texts = nginx_texts(proj, svc)
    protos, listens, names = [], [], []
    certs = ssl_on = False
    for path, text in texts:
        code = nginx_conf_code(text)
        r = proj.rel(path)
        for m in re.finditer(r"\bssl_protocols\s+([^;\"'\n]+)", code):
            protos.append((loc(r, line_at(code, m.start())),
                           m.group(1).split()))
        for m in re.finditer(r"\blisten\s+([^;=\s\"'][^;\n\"']*);", code):
            listens.append((loc(r, line_at(code, m.start())),
                            m.group(1).split()))
        for m in re.finditer(r"\bserver_name\s+([^;\n]+);", code):
            names.append((loc(r, line_at(code, m.start())),
                          m.group(1).split()))
        if re.search(r"\bssl_certificate\s", code) and \
                re.search(r"\bssl_certificate_key\s", code):
            certs = True
        if re.search(r"\bssl\s+on\s*;", code):
            ssl_on = True
    # TLS versions
    if not protos:
        rep.warn("no ssl_protocols directive found",
                 "the NGINX default may allow TLSv1 / TLSv1.1 (Debian's "
                 "nginx.conf does): set ssl_protocols TLSv1.2 TLSv1.3;")
    for where, toks in protos:
        bad = [t for t in toks if t not in ("TLSv1.2", "TLSv1.3")
               and "$" not in t]
        var = [t for t in toks if "$" in t]
        if var:
            rep.warn(f"ssl_protocols uses {var[0]}",
                     f"{where}: cannot check the value")
        ko(rep, f"ssl_protocols {' '.join(toks)}", not bad, "nginx",
           where=where, expected="TLSv1.2 and/or TLSv1.3 only",
           got=", ".join(bad) or None)
    # ports
    ssl443 = False
    others = []
    for where, toks in listens:
        addr = toks[0] if toks else ""
        port = addr.rsplit(":", 1)[-1] if ":" in addr else addr
        if "$" in port:
            continue
        if not port.isdigit():
            port = "80"
        if port == "443":
            if "ssl" in toks or ssl_on:
                ssl443 = True
        else:
            others.append(f"{where}: listen {' '.join(toks)}")
    ko(rep, "listen 443 ssl", ssl443, "entry",
       where=proj.rel(proj.service_dir(svc) or proj.root),
       hint="no 'listen 443 ssl;' found in the NGINX configuration")
    if others:
        rep.warn("NGINX listens on other ports than 443",
                 "\n".join(others[:4]) + "\nonly port 443 may be reachable "
                 "from outside (see the ports: of the compose file)")
    if not certs:
        rep.warn("no ssl_certificate / ssl_certificate_key found",
                 "TLS needs a certificate (it may be written by a script)")
    # server_name
    if names and dom:
        ok = any(dom in toks or any("DOMAIN" in t.upper() or
                                    t.endswith(".42.fr") for t in toks)
                 for _, toks in names)
        if not ok:
            rep.warn("server_name does not use the domain name",
                     "\n".join(f"{w}: server_name {' '.join(t)}"
                               for w, t in names[:3])
                     + f"\nexpected {dom} or ${{DOMAIN_NAME}}")


def check_domain(rep, proj, dom, dwhere):
    if dom is None:
        if proj.env_path is None and proj.ignored("srcs/.env"):
            rep.skip("domain name login.42.fr",
                     "srcs/.env is missing (ignored by git)")
            return
        ko(rep, "domain name login.42.fr", False, "domain",
           hint="no DOMAIN_NAME in .env and no *.42.fr server_name")
        return
    m = re.fullmatch(r"([a-z0-9][a-z0-9-]*)\.42\.fr", dom)
    ko(rep, f"domain name {dom} is <login>.42.fr", bool(m), "domain",
       where=dwhere, got=dom)
    if not m:
        return
    login = m.group(1)
    readme = read_text(os.path.join(proj.root, "README.md")) or ""
    first = next((ln for ln in readme.splitlines() if ln.strip()), "")
    if first and "curriculum by" in first.lower() and \
            login not in first.lower():
        rep.warn(f"login {login} (domain) not in the README first line",
                 f"README.md:1: {first.strip()[:100]}")


def group_loops(rep, proj):
    rep.section("loops", gid="loops")
    if not proj.services:
        rep.skip("entrypoints", "no service found")
        return
    rel = proj.compose_rel
    for svc in proj.services:
        dfrel = proj.rel(svc.dockerfile) if svc.dockerfile else rel
        cmds = runtime_commands(svc)
        hits = []
        for (kind_where, line), kind, val in cmds:
            where = loc(dfrel if kind_where == "dockerfile" else rel, line)
            if kind == "argv":
                hits += [f"{where}: {h}" for h in argv_hacks(val)]
            else:
                hits += [f"{where}: {h}" for _, h in shell_hacks(val)]
        if svc.docker is not None and not cmds:
            base = svc.docker.final()
            hits.append(f"{dfrel}: no CMD / ENTRYPOINT: the container runs "
                        f"the default command of {base.raw if base else '?'}"
                        " (a shell)")
        scripts = entry_scripts(proj, svc)
        for path, text in scripts:
            for line, h in shell_hacks(text):
                hits.append(f"{loc(proj.rel(path), line)}: {h}")
        if svc.docker is None and not cmds:
            continue
        ko(rep, f"{svc.name}: no keep-alive hack in the start command"
           + (f" and {len(scripts)} script(s)" if scripts else ""),
           not hits, "hacks", got="\n".join(hits[:6]) or None,
           hint=f'"{Q["loop"]}"')
        for path, text in scripts:
            last = [ln.strip() for ln in strip_shell_comments(text)
                    .split("\n") if ln.strip()]
            if last and re.match(r"(nginx|php-?fpm[\d.]*|mysqld|mariadbd|"
                                 r"mysqld_safe)\b", last[-1]):
                rep.warn(f"{svc.name}: {proj.rel(path)} starts the daemon "
                         "without exec",
                         f"last line: {last[-1][:60]}\nwith exec the daemon "
                         "becomes PID 1 and gets the signals")


def group_credentials(rep, proj):
    rep.section("credentials", gid="credentials")
    mode = proj.git[0]
    if mode == "none":
        rep.skip("credentials pushed to git", "not a git repository: "
                 "cannot tell what is pushed")
    # .env
    if proj.env_path:
        env_rel = proj.rel(proj.env_path)
        pw = [k for k, v in proj.env.items()
              if is_pass_key(k) and literal_secret(v)]
        tracked = proj.tracked(env_rel)
        if tracked:
            ko(rep, f"{env_rel} with passwords not in git", not pw, "git",
               where=env_rel, got=", ".join(pw) or None,
               hint="keep the .env out of git (.gitignore)")
            if not pw:
                rep.warn(f"{env_rel} is tracked by git",
                         f'subject: "{Q["gitignore"]}"')
        elif tracked is None and pw and not proj.ignored(env_rel):
            rep.warn(f"{env_rel} holds passwords and no .gitignore "
                     "ignores it", f'subject: "{Q["gitignore"]}"')
        elif mode == "git" and pw and not proj.ignored(env_rel):
            rep.warn(f"{env_rel} holds passwords and is not ignored by git",
                     "a `git add .` would push it")
    # secrets files
    sec = proj.secret_files()
    for f in sec:
        tracked = proj.tracked(f)
        full = os.path.join(proj.root, f)
        size = os.path.getsize(full) if os.path.isfile(full) else 0
        if tracked:
            ko(rep, f"{f} not in git", size == 0, "gitignore", where=f,
               hint=f'"{Q["git"]}"')
        elif tracked is None and size and not proj.ignored(f):
            rep.warn(f"{f}: no .gitignore ignores it",
                     f'subject: "{Q["gitignore"]}"')
    # literal passwords in the files that are pushed
    found = []
    rel = proj.compose_rel
    if proj.compose_raw is not None and not proj.from_docker:
        for svc in proj.services:
            for k, v, line in env_items(svc.get("environment")):
                raw = getattr(v, "raw", None)
                if is_pass_key(k) and raw is not None and \
                        literal_secret(raw):
                    found.append((rel, f"{loc(rel, line)}: {k}"))
            for k, v in svc.build_args.items():
                if is_pass_key(k) and literal_secret(v):
                    found.append((rel, f"{rel}: build arg {k}"))
        dflt = []
        for m in re.finditer(r"\$\{([A-Za-z_]\w*):?-([^}]+)\}",
                             proj.compose_text or ""):
            if is_pass_key(m.group(1)) and literal_secret(m.group(2)):
                where = loc(rel, line_at(proj.compose_text, m.start()))
                dflt.append(f"{where}: ${{{m.group(1)}:-...}}")
        if dflt:
            rep.warn("default password written in the compose file",
                     "\n".join(dflt[:4]))
    for f in scanned_files(proj):
        if os.path.basename(f) in ("Dockerfile", ".env") or f == rel:
            continue
        full = os.path.join(proj.root, f)
        text = read_text(full, 256 * 1024)
        if not text:
            continue
        shell = is_script(f, text)
        for line, what in script_passwords(text, shell=shell):
            found.append((f, f"{loc(f, line)}: {what}"))
    pushed = [w for f, w in found if proj.tracked(f) is not False]
    ko(rep, "no password written in the compose file or the scripts",
       not pushed, "git", got="\n".join(pushed[:6]) or None,
       hint="read them from the .env or from /run/secrets")
    # admin user
    check_admin(rep, proj)
    # Docker secrets (only when the compose file could be read)
    if proj.compose is not None:
        uses = isinstance(proj.compose.get("secrets"), YMap) and any(
            svc.get("secrets") for svc in proj.services)
        if not uses:
            rep.warn("no Docker secrets", f'subject: "{Q["secrets"]}"')


def scanned_files(proj, limit=2000):
    """Files of srcs/ that may hold passwords or the admin user name: no
    WordPress sources (only a wp-config.php), no binaries."""
    out = []
    for f in proj.all_files():
        if not f.startswith("srcs/"):
            continue
        base = os.path.basename(f)
        if base.endswith(".php") and not re.fullmatch(r"wp-config\.php",
                                                      base):
            continue
        if re.search(r"\.(png|jpe?g|gif|ico|svg|css|js|map|gz|zip|tar|pem|"
                     r"crt|key|woff2?|ttf|mo|po)$", base, re.I):
            continue
        out.append(f)
        if len(out) >= limit:
            break
    return out


ADMIN_KEY_RE = re.compile(r"ADMIN.*(USER|LOGIN|NAME)|(USER|LOGIN|NAME).*ADMIN",
                          re.I)


def check_admin(rep, proj):
    cands = []      # (where, value)
    for k, v in proj.env.items():
        if ADMIN_KEY_RE.search(k) and not re.search(r"PASS|MAIL|PWD", k,
                                                    re.I) and v:
            cands.append((loc(proj.rel(proj.env_path), proj.env_lines[k]),
                          v))
    for svc in proj.services:
        for k, v, line in env_items(svc.get("environment")):
            if ADMIN_KEY_RE.search(k) and not re.search(r"PASS|MAIL|PWD", k,
                                                        re.I) and v:
                cands.append((loc(proj.compose_rel, line), str(v)))
    second = False
    envs = dict(proj.env)
    for f in proj.secret_files():
        text = read_text(os.path.join(proj.root, f)) or ""
        e, lines = parse_env(text)
        for k, v in e.items():
            envs.setdefault(k, v)
            if ADMIN_KEY_RE.search(k) and not re.search(r"PASS|MAIL|PWD", k,
                                                        re.I) and v:
                cands.append((loc(f, lines[k]), v))
    for f in scanned_files(proj):
        text = read_text(os.path.join(proj.root, f), 256 * 1024)
        if not text:
            continue
        code = strip_shell_comments(text)
        for m in re.finditer(r"--admin_user[= ](['\"]?)([^\s'\";]+)\1",
                             code):
            val = m.group(2)
            vm = re.fullmatch(r"\$\{?([A-Za-z_]\w*)\}?", val)
            if vm:
                val = envs.get(vm.group(1))
            if val:
                cands.append((loc(f, line_at(code, m.start())), val))
        if re.search(r"\bwp\b[^\n]*\buser\s+create\b", code):
            second = True
    if not cands:
        rep.skip("administrator user name", "not found (no "
                 "WP_ADMIN_USER-like variable, no --admin_user=)")
    seen = set()
    for where, val in cands:
        if val in seen:
            continue
        seen.add(val)
        ko(rep, f"administrator user name '{val}' has no 'admin'",
           "admin" not in val.lower(), "admin", where=where)
    if not second:
        rep.warn("no 'wp user create' found",
                 f'subject: "{Q["users"]}"')


README_CMP = [
    ("Virtual Machines vs Docker", r"virtual machine|\bVMs?\b",
     r"docker|container"),
    ("Secrets vs Environment Variables", r"secret",
     r"environment variable|env(ironment)?[ _-]?var|\.env\b"),
    ("Docker Network vs Host Network",
     r"host[ -]network|network[ _]?mode|--network[= ]host",
     r"docker[ -]network|bridge|user[ -]defined"),
    ("Docker Volumes vs Bind Mounts", r"bind[ -]?mounts?", r"volume"),
]

DOC_TOPICS = {
    "USER_DOC.md": [
        ("services provided by the stack",
         [r"nginx", r"wordpress", r"maria ?db"]),
        ("start and stop", [r"\bmake\b|docker[ -]compose\s+up|start",
                            r"stop|down"]),
        ("website and administration panel",
         [r"https://|\.42\.fr|website|site",
          r"wp-admin|wp-login|admin(istration)? panel|dashboard"]),
        ("locate and manage credentials", [r"credential|password|secret"]),
        ("check the services are running",
         [r"docker[ -]compose\s+ps|docker\s+ps|\blogs\b|status|running"]),
    ],
    "DEV_DOC.md": [
        ("set up from scratch",
         [r"prerequisite|requirement|install", r"\.env|secret|configur"]),
        ("build and launch with the Makefile and Docker Compose",
         [r"\bmake\b", r"docker[ -]compose"]),
        ("manage the containers and volumes",
         [r"docker(\s+compose|-compose)?\s+(ps|logs|exec|stop|start|rm|"
          r"down|restart)", r"volume"]),
        ("where the data is stored and how it persists",
         [r"/home/\S*data|\bdata\b", r"persist"]),
    ],
}


def md_sections(text):
    out, cur = [], []
    for ln in text.splitlines():
        if ln.startswith("#") and cur:
            out.append("\n".join(cur))
            cur = []
        cur.append(ln)
    if cur:
        out.append("\n".join(cur))
    return out


def group_readme(rep, proj):
    rep.section("readme", gid="readme")
    core.check_readme(rep, proj.root, extra_sections=("Project description",))
    text = read_text(os.path.join(proj.root, "README.md"))
    if text is not None:
        secs = md_sections(text)
        for label, a, b in README_CMP:
            ok = any(re.search(a, s, re.I) and re.search(b, s, re.I)
                     for s in secs)
            ko(rep, f"README compares {label}", ok, "readme_cmp",
               where="README.md")
        if not re.search(r"dockerfile|docker-compose|compose", text, re.I):
            rep.warn("README never mentions the Dockerfiles / "
                     "docker-compose.yml",
                     "the Project description explains the use of Docker "
                     "and the sources included")
    for doc, topics in DOC_TOPICS.items():
        p = os.path.join(proj.root, doc)
        if not ko(rep, f"{doc} at the root", os.path.isfile(p), "docs"):
            continue
        body = read_text(p) or ""
        for label, rxs in topics:
            if not all(re.search(rx, body, re.I) for rx in rxs):
                rep.warn(f"{doc}: nothing about '{label}'",
                         "the subject lists this topic")


# --------------------------------------------------------------------------
# live
# --------------------------------------------------------------------------

def docker(args, timeout=30):
    """Every docker call of the tester goes through here."""
    return core.run(["docker", *args], timeout=timeout)


def docker_compose_config(proj, keep):
    """(ok, message, data) of `docker compose config` run on a copy of the
    project, or None when docker compose is not there."""
    if not proj.compose_path or not core.have("docker"):
        return None
    if docker(["compose", "version"], timeout=20).rc != 0:
        return None
    tmp = tempfile.mkdtemp(prefix="42next-inception-")
    try:
        dst = os.path.join(tmp, "project")

        def ignore(folder, names):
            # same as the other testers, plus big files (a data folder
            # inside the repository): docker compose config does not read
            # them
            skip = set(core.IGNORE_COPY(folder, names))
            for n in names:
                p = os.path.join(folder, n)
                try:
                    if os.path.isfile(p) and os.path.getsize(p) > 1 << 20:
                        skip.add(n)
                except OSError:
                    skip.add(n)
            return skip
        try:
            shutil.copytree(proj.root, dst, symlinks=True, ignore=ignore)
        except (OSError, shutil.Error):
            if not os.path.isdir(dst):
                return None
        cfile = os.path.join(dst, proj.rel(proj.compose_path))
        res = docker(["compose", "-f", cfile, "config", "--format", "json"],
                     timeout=60)
        if res.rc != 0:
            msg = core.output(res).strip().splitlines()
            return False, "\n".join(msg[-4:])[:400] or res.status(), None
        try:
            data = json.loads(res.out.decode("utf-8", "replace"))
        except ValueError:
            data = None
        return True, "", (data, dst)
    finally:
        if keep:
            print(core.dim(f"docker compose config copy kept: {tmp}"))
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def own_addresses():
    addrs = set()
    try:
        addrs.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    if core.have("hostname"):
        res = core.run(["hostname", "-I"], timeout=5)
        if res.rc == 0:
            addrs.update(res.out.decode().split())
    return addrs


def tls_try(host, port, sni, version):
    """(True, version) / (False, reason) / (None, why it cannot be tried)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.minimum_version = version
            ctx.maximum_version = version
        except (ValueError, ssl.SSLError) as exc:
            return None, f"the local OpenSSL cannot offer it ({exc})"
        if version < ssl.TLSVersion.TLSv1_2:
            try:
                ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
            except ssl.SSLError:
                pass
        try:
            with socket.create_connection((host, port), timeout=5) as s:
                with ctx.wrap_socket(s, server_hostname=sni) as t:
                    return True, t.version()
        except ssl.SSLError as exc:
            text = str(exc)
            if "NO_PROTOCOLS_AVAILABLE" in text.upper() or \
                    "no protocols available" in text.lower():
                return None, "the local OpenSSL cannot offer it"
            return False, text[:120]
        except OSError as exc:
            return False, f"connection failed: {exc}"


def https_get(host, port, sni, path="/"):
    """(status, headers dict, body text) or (None, error, '')."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = (f"GET {path} HTTP/1.1\r\nHost: {sni}\r\nUser-Agent: 42next\r\n"
           "Accept: */*\r\nConnection: close\r\n\r\n").encode()
    data = b""
    try:
        with socket.create_connection((host, port), timeout=10) as s:
            with ctx.wrap_socket(s, server_hostname=sni) as t:
                t.sendall(req)
                while len(data) < 512 * 1024:
                    chunk = t.recv(65536)
                    if not chunk:
                        break
                    data += chunk
    except (OSError, ssl.SSLError) as exc:
        if not data:
            return None, str(exc)[:120], ""
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    m = re.match(r"HTTP/\S+\s+(\d+)", lines[0] if lines else "")
    if not m:
        return None, "not an HTTP answer", ""
    headers = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(":")
        headers[k.strip().lower()] = v.strip()
    return int(m.group(1)), headers, body.decode("utf-8", "replace")


def pick_containers(proj, infos):
    names = {s.name for s in proj.services}
    want_dir = os.path.realpath(proj.compose_dir)
    mine = []
    for c in infos:
        labels = (c.get("Config") or {}).get("Labels") or {}
        svc = labels.get("com.docker.compose.service")
        wd = labels.get("com.docker.compose.project.working_dir") or ""
        if svc in names and wd and os.path.realpath(wd) == want_dir:
            mine.append(c)
    if mine:
        return mine
    projects = {}
    for c in infos:
        labels = (c.get("Config") or {}).get("Labels") or {}
        if labels.get("com.docker.compose.service") in names:
            projects.setdefault(labels.get("com.docker.compose.project"),
                                []).append(c)
    if not projects:
        return []
    return max(projects.values(), key=len)


def proc_hack(argv):
    """Why this process command line is a keep-alive hack, or None."""
    if not argv:
        return None
    prog = os.path.basename(argv[0])
    if prog == "tail" and any(re.fullmatch(r"-[a-zA-Z0-9]*[fF][a-zA-Z0-9]*|"
                                           r"--follow.*", a)
                              for a in argv[1:]):
        return " ".join(argv)
    if prog == "sleep" and any(a in ("infinity", "inf") for a in argv[1:]):
        return " ".join(argv)
    return None


def group_live(rep, proj):
    rep.section("live", gid="live")
    if not core.have("docker"):
        rep.skip("running stack", "docker not installed")
        return
    if not proj.services:
        rep.skip("running stack", "no service found in docker-compose.yml")
        return
    res = docker(["ps", "-a", "-q", "--no-trunc"])
    if res.rc != 0:
        rep.skip("running stack", "docker ps failed: "
                 + core.output(res).strip()[:120])
        return
    ids = res.out.decode().split()
    infos = []
    if ids:
        res = docker(["inspect", *ids])
        try:
            infos = json.loads(res.out.decode("utf-8", "replace"))
        except ValueError:
            infos = []
    mine = pick_containers(proj, infos)
    running = {}
    for c in mine:
        labels = c["Config"].get("Labels") or {}
        if (c.get("State") or {}).get("Running"):
            running[labels.get("com.docker.compose.service")] = c
    if not running:
        rep.skip("running stack", "start your stack with make, then run "
                 "42next again")
        return
    rep.info(f"{len(running)} container(s) running: "
             + ", ".join(sorted(running)))
    for svc in proj.services:
        ko(rep, f"{svc.name}: container running", svc.name in running,
           "dedicated", got=None if svc.name in running else
           "not running (crashed? see docker compose logs)")
    nets = []
    for name, c in sorted(running.items()):
        cfg, host = c.get("Config") or {}, c.get("HostConfig") or {}
        cname = (c.get("Name") or name).lstrip("/")
        image = str(cfg.get("Image") or "")
        iname = image.split("@")[0].rsplit("/", 1)[-1].split(":")[0]
        ko(rep, f"{cname}: image {image}", iname == name, "image_name",
           expected=name, got=image)
        pol = (host.get("RestartPolicy") or {}).get("Name") or "no"
        ko(rep, f"{cname}: restart policy {pol}",
           pol in ("always", "unless-stopped", "on-failure"), "restart",
           got=pol)
        mode = str(host.get("NetworkMode") or "")
        ko(rep, f"{cname}: not on the host network", mode != "host", "host",
           got=mode)
        ko(rep, f"{cname}: no links", not host.get("Links"), "host",
           got=", ".join(host.get("Links") or []) or None)
        user_nets = {n for n in ((c.get("NetworkSettings") or {})
                                 .get("Networks") or {})
                     if n not in ("bridge", "host", "none")}
        nets.append((cname, user_nets))
        ports = (c.get("NetworkSettings") or {}).get("Ports") or {}
        pub = sorted({f"{b.get('HostPort')}->{p}" for p, binds in
                      ports.items() for b in (binds or [])})
        if name == (proj.roles.get("nginx").name
                    if proj.roles.get("nginx") else None):
            ok = bool(pub) and all(x == "443->443/tcp" for x in pub)
            ko(rep, f"{cname}: publishes 443 only", ok, "entry",
               expected="443->443/tcp", got=", ".join(pub) or "nothing")
        else:
            ko(rep, f"{cname}: publishes no port", not pub, "entry",
               got=", ".join(pub) or None)
        live_pid1(rep, cname, c.get("Id") or cname)
    common = set.intersection(*[n for _, n in nets]) if nets else set()
    ko(rep, "containers share a docker network", bool(common), "network",
       got="; ".join(f"{c}: {', '.join(sorted(n)) or 'none'}"
                     for c, n in nets) if not common else None)
    live_volumes(rep, proj, running)
    live_tls(rep, proj)
    live_users(rep, proj, running)


def live_pid1(rep, cname, cid):
    res = docker(["exec", cid, "cat", "/proc/1/cmdline"], timeout=20)
    if res.rc != 0:
        rep.skip(f"{cname}: PID 1", "docker exec failed: "
                 + core.output(res).strip()[:100])
        return
    argv = [a.decode("utf-8", "replace") for a in res.out.split(b"\0") if a]
    why = proc_hack(argv)
    if why is None and argv and os.path.basename(argv[0]) in SHELLS:
        rest = argv[1:]
        if "-c" in rest:
            i = rest.index("-c")
            h = shell_hacks(" ".join(rest[i + 1:i + 2]))
            why = h[0][1] if h else None
        elif not [a for a in rest if not a.startswith("-")]:
            why = " ".join(argv) + " (idle shell)"
        else:
            rep.warn(f"{cname}: PID 1 is a shell ({' '.join(argv)[:60]})",
                     "the daemon is not PID 1: use exec in the script")
    ko(rep, f"{cname}: PID 1 is not a keep-alive hack", why is None,
       "hacks", got=why or None)
    script = ("for f in /proc/[0-9]*/cmdline; do tr '\\000' ' ' < \"$f\";"
              " echo; done 2>/dev/null")
    res = docker(["exec", cid, "sh", "-c", script], timeout=20)
    if res.rc == 0:
        bad = []
        for line in res.out.decode("utf-8", "replace").splitlines():
            h = proc_hack(line.split())
            if h:
                bad.append(h)
        ko(rep, f"{cname}: no tail -f / sleep infinity process", not bad,
           "hacks", got=", ".join(bad[:3]) or None)


def live_volumes(rep, proj, running):
    login = proj.login()
    for role, quote, want in (("mariadb", "vol_db", "/var/lib/mysql"),
                              ("wordpress", "vol_wp", "/var/www")):
        svc = proj.roles.get(role)
        if svc is None or svc.name not in running:
            continue
        c = running[svc.name]
        mounts = [m for m in c.get("Mounts") or []
                  if not str(m.get("Destination", "")).startswith(
                      "/run/secrets")]
        m = next((m for m in mounts if str(m.get("Destination", ""))
                  .startswith(want)), mounts[0] if mounts else None)
        if not ko(rep, f"{svc.name}: volume mounted", m is not None, quote,
                  expected=f"a volume on {want}", got="no mount"):
            continue
        src = m.get("Source") or ""
        if m.get("Type") == "volume" and m.get("Name"):
            res = docker(["volume", "inspect", m["Name"]])
            try:
                vol = json.loads(res.out.decode())[0]
                dev = (vol.get("Options") or {}).get("device")
            except (ValueError, IndexError, KeyError, TypeError):
                dev = None
            if dev:
                src = dev
        dm = DATA_RE.match(os.path.normpath(src) if src else "")
        ko(rep, f"{svc.name}: {m.get('Destination')} data in "
           "/home/<login>/data", bool(dm), "data", got=src)
        if dm and login:
            ko(rep, f"{svc.name}: volume under /home/{login}/data",
               dm.group(1) == login, "own_login", got=src)


def live_tls(rep, proj):
    dom = proj.domain()[0]
    sni = dom or "localhost"
    target = os.environ.get("NEXT42_INCEPTION_HOST")
    port = int(os.environ.get("NEXT42_INCEPTION_PORT") or 443)
    http_port = int(os.environ.get("NEXT42_INCEPTION_HTTP_PORT") or 80)
    if dom:
        addr = os.environ.get("NEXT42_INCEPTION_DNS")
        err = None
        if not addr:
            try:
                addr = socket.gethostbyname(dom)
            except OSError as exc:
                err = str(exc)
        local = False
        if addr:
            try:
                local = ipaddress.ip_address(addr).is_loopback or \
                    addr in own_addresses()
            except ValueError:
                local = False
        ko(rep, f"{dom} points to a local address", local, "domain_ip",
           got=addr or err, hint="add it to /etc/hosts: "
           f"127.0.0.1 {dom}")
        if not target:
            target = addr if local else "127.0.0.1"
    target = target or "127.0.0.1"
    ok12, v12 = tls_try(target, port, sni, ssl.TLSVersion.TLSv1_2)
    ok13, v13 = tls_try(target, port, sni, ssl.TLSVersion.TLSv1_3)
    if ok12 is None and ok13 is None:
        rep.skip("TLS", "the local OpenSSL cannot offer TLS 1.2 / 1.3")
        return
    ko(rep, f"TLSv1.2 or TLSv1.3 handshake on {target}:{port}",
       bool(ok12 or ok13), "nginx", got=f"TLSv1.2: {v12}; TLSv1.3: {v13}")
    if rep.verbose:
        rep.info(f"TLSv1.2: {'yes' if ok12 else v12}, "
                 f"TLSv1.3: {'yes' if ok13 else v13}")
    if not (ok12 or ok13):
        rep.skip("TLSv1 / TLSv1.1 refused", f"port {port} not reachable")
    else:
        for name, ver in (("TLSv1.1", "TLSv1_1"), ("TLSv1", "TLSv1")):
            okx, vx = tls_try(target, port, sni,
                              getattr(ssl.TLSVersion, ver))
            if okx is None:
                rep.skip(f"{name} refused", vx)
                continue
            ko(rep, f"{name} refused", not okx, "nginx",
               got=f"handshake accepted ({vx})" if okx else None)
    try:
        with socket.create_connection((target, http_port), timeout=3):
            opened = True
    except OSError:
        opened = False
    ko(rep, f"port {http_port} closed", not opened, "entry",
       got=f"{target}:{http_port} accepts connections" if opened else None)
    if not (ok12 or ok13):
        return
    status, headers, body = https_get(target, port, sni)
    for _ in range(3):
        if status not in (301, 302, 303, 307, 308) or \
                not isinstance(headers, dict):
            break
        where = headers.get("location", "")
        if "install.php" in where:
            break
        m = re.match(r"https?://[^/]+(/.*)?$", where)
        path = (m.group(1) or "/") if m else where or "/"
        status, headers, body = https_get(target, port, sni, path)
    if isinstance(headers, dict) and "install.php" in \
            headers.get("location", ""):
        ko(rep, "WordPress installed and configured", False, "wp",
           got="redirects to wp-admin/install.php")
        return
    wp = status == 200 and re.search(r"wp-content|wp-includes|WordPress",
                                     body or "") is not None
    ko(rep, f"https://{sni}/ serves WordPress", wp, "wp",
       got=f"HTTP {status}" if status else str(headers))


def live_users(rep, proj, running):
    svc = proj.roles.get("wordpress")
    if svc is None or svc.name not in running:
        rep.skip("WordPress users", "no wordpress container")
        return
    cid = running[svc.name].get("Id") or svc.name
    base = ["exec", cid, "wp", "user", "list", "--allow-root",
            "--format=json", "--fields=user_login,roles"]
    res = docker(base, timeout=60)
    if res.rc != 0 and re.search(r"executable file not found|not found|"
                                 r"no such file", core.output(res), re.I) \
            and "wordpress" not in core.output(res).lower():
        rep.skip("WordPress users", "wp-cli not found in the container")
        return
    for path in ("/var/www/html", "/var/www/wordpress", "/var/www"):
        if res.rc == 0:
            break
        res = docker(base + [f"--path={path}"], timeout=60)
    try:
        users = json.loads(res.out.decode("utf-8", "replace")) \
            if res.rc == 0 else None
    except ValueError:
        users = None
    if not isinstance(users, list):
        rep.skip("WordPress users", "wp user list failed: "
                 + core.output(res).strip()[:100])
        return
    logins = [str(u.get("user_login")) for u in users]
    admins = [str(u.get("user_login")) for u in users
              if "administrator" in str(u.get("roles", ""))]
    others = [x for x in logins if x not in admins]
    ko(rep, f"two WordPress users ({len(users)} found)", len(users) >= 2,
       "users", got=", ".join(logins) or "none")
    ko(rep, "one of them is an administrator", bool(admins), "users",
       got=", ".join(logins) or None)
    ko(rep, "and one is not an administrator", bool(others), "users",
       got=f"all administrators: {', '.join(admins)}" if not others
       else None)
    bad = [a for a in admins if "admin" in a.lower()]
    ko(rep, "administrator login has no 'admin'", not bad, "admin",
       got=", ".join(bad) or None)


# --------------------------------------------------------------------------

def main():
    args = core.parse_args("Inception tester (42next)", GROUPS)
    if not os.path.isdir(args.project):
        print(core.red(f"project directory not found: {args.project}"))
        return 2
    core.header("Inception", args.project, subject="1.1")
    proj = Project(args.project)
    rep = core.Report(verbose=args.verbose)
    want = core.selected
    docker_cfg = None
    if want(args, "compose") or proj.compose_error is not None:
        if proj.env_path or not proj.ignored("srcs/.env"):
            res = docker_compose_config(proj, args.keep)
            if res is not None:
                ok, msg, data = res
                docker_cfg = (ok, msg)
                if ok and data and data[0] and proj.compose_error:
                    proj.use_docker_config(data[0], data[1])
    if want(args, "files"):
        group_files(rep, proj)
    if want(args, "compose"):
        group_compose(rep, proj, docker_cfg)
    if want(args, "dockerfiles"):
        froms = [st for s in proj.services if s.docker
                 for st in s.docker.stages]
        imgs = {re.sub(r"^.*/", "", st.image.lower()) for st in froms}
        rel = load_releases("debian" in imgs, "alpine" in imgs, args.quick)
        group_dockerfiles(rep, proj, rel)
    if want(args, "nginx"):
        group_nginx(rep, proj)
    if want(args, "loops"):
        group_loops(rep, proj)
    if want(args, "credentials"):
        group_credentials(rep, proj)
    if want(args, "readme"):
        group_readme(rep, proj)
    if want(args, "live"):
        if args.quick:
            rep.section("live", gid="live")
            core.quick_skip(rep, "no live checks of the running stack")
        else:
            group_live(rep, proj)
    return rep.finish()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped (ctrl-c)")
        sys.exit(130)
