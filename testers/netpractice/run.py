#!/usr/bin/env python3
"""42next - NetPractice tester (subject v1.4).

    python3 -B run.py --project DIR [--only files,values,readme,repo]

Checks what a learner submits: the 10 files exported with "Get my config"
(one per level, at the root of the repository) and the README.md.

An export only holds the fields the learner edited, for example
    {"routes":{"H1r1":{"gate":"10.0.0.1"}},"ifs":{"H11":{"ip":"10.0.0.2"}}}
It has no login and no network diagram (both live in the training
interface), so whether a level is solved cannot be told from the files:
that is checked during the defense. This tester checks the files, the
syntax of every exported value, and the README.

The project is only read, never written to.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "common"))
import core  # noqa: E402

GROUPS = [
    ("files", "10 exported files at the root (one per level)"),
    ("values", "exported values: IP, mask, route, gateway"),
    ("readme", "README.md requirements"),
    ("repo", "repository content"),
]

LEVELS = list(range(1, 11))
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".mypy_cache"}
MAX_SIZE = 1024 * 1024
MAX_DEPTH = 4
# "level7.json", "Level 07.json", "lvl7.json", "7.json", "level7 (1).json"
LEVEL_NAME = re.compile(r"^(?:level|lvl|lv|niveau)?[\s_-]*0*(\d{1,3})(?!\d)",
                        re.I)
DEFAULT_NAME = re.compile(r"^level([1-9]|10)\.json$")
INFO_DIAGRAM = ("the exports do not contain the network diagrams: whether "
                "each level is solved is checked during the defense")

Q_TEN = ('"you must submit 10 files in your repository (one file per '
         'level)"')
Q_ROOT = '"Place them at the root of your repository."'
Q_EXPORT = '"Export one file per level using the Get my config button."'
Q_VALUE = ('the training interface rejects it ("if a gateway is missing or '
           'an IP address is invalid"), so the level cannot be completed '
           '("You must complete 10 levels")')
Q_CONCEPTS = ('"The Resources section must explicitly mention the networking '
              'concepts studied"')
Q_INSTR = ('"The Instructions section must explain how to run the training '
           'interface (e.g., open index.html), how to export configurations, '
           'and submission requirements."')
Q_SUBMIT = ('"Submission details must state that 10 exported configuration '
            'files (one per level) must be placed at the repository root."')


# --------------------------------------------------------------------------
# finding the exports
# --------------------------------------------------------------------------

class Export:
    def __init__(self, rel, path):
        self.rel = rel                  # path shown, relative to the project
        self.name = os.path.basename(rel)
        self.path = path
        self.level = level_of(self.name)
        self.data = None                # parsed JSON when it is an export
        self.error = None               # why it is not an export


def level_of(name):
    m = LEVEL_NAME.match(name)
    return int(m.group(1)) if m else None


def natural(name):
    """Sort key: level2.json before level10.json."""
    return [int(x) if x.isdigit() else x.lower()
            for x in re.split(r"(\d+)", name)]


def load(path):
    """-> (parsed JSON or None, error or None)."""
    try:
        if os.path.getsize(path) > MAX_SIZE:
            return None, "bigger than 1 MB"
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return None, f"cannot read it ({exc.strerror})"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "not UTF-8 text"
    if not text.strip():
        return None, "empty file"
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, f"invalid JSON ({exc})"


def shape_error(data):
    """None when data looks like a "Get my config" export."""
    if not isinstance(data, dict):
        return "the JSON is not an object"
    missing = [k for k in ("routes", "ifs") if k not in data]
    if missing:
        return "no " + " / ".join(f'"{k}"' for k in missing) + " key"
    bad = [k for k in ("routes", "ifs") if not isinstance(data[k], dict)]
    if bad:
        return " / ".join(f'"{k}"' for k in bad) + " is not an object"
    return None


class Scan:
    """Exports at the root, exports in sub folders, other .json files."""

    def __init__(self, project):
        self.root = []          # Export, at the root
        self.nested = []        # relative paths of exports in sub folders
        self.others = []        # .json at the root that are not exports
        try:
            names = sorted(os.listdir(project), key=natural)
        except OSError:
            names = []
        for name in names:
            path = os.path.join(project, name)
            if name.startswith(".") or not os.path.isfile(path):
                continue
            ext = os.path.splitext(name)[1].lower()
            named = self._named(name, ext)
            # other names are accepted when the content matches
            data, err = load(path)
            if err is None:
                err = shape_error(data)
            if err is None or named:
                exp = Export(name, path)
                exp.data = data if err is None else None
                exp.error = err
                self.root.append(exp)
            elif ext == ".json":
                self.others.append(name)
        for top, dirs, files in os.walk(project):
            depth = os.path.relpath(top, project).count(os.sep)
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS
                             and not d.startswith(".")
                             and depth < MAX_DEPTH)
            if os.path.samefile(top, project):
                continue
            for name in sorted(files, key=natural):
                if not name.lower().endswith(".json"):
                    continue
                path = os.path.join(top, name)
                data, err = load(path)
                if err is None and shape_error(data) is None:
                    self.nested.append(os.path.relpath(path, project))

    @staticmethod
    def _named(name, ext):
        """levelN.json (or a name starting with a level number)."""
        if ext not in (".json", ""):
            return False
        if level_of(name) is None:
            return False
        # a bare number is only a level name with the .json extension
        return ext == ".json" or not name[:1].isdigit()

    def valid(self):
        return [e for e in self.root if e.data is not None]


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------

def _names(exports, limit=12):
    names = [e.rel for e in exports]
    more = f" (+{len(names) - limit})" if len(names) > limit else ""
    return ", ".join(names[:limit]) + more


def group_files(rep, scan):
    rep.section("files", gid="files")
    exports = scan.root
    count = len(exports)
    by_level = {}
    for e in exports:
        if e.level is not None:
            by_level.setdefault(e.level, []).append(e)
    unnamed = [e for e in exports if e.level is None]
    dups = {lv: es for lv, es in by_level.items() if len(es) > 1}
    out = sorted(lv for lv in by_level if lv not in LEVELS)
    missing = [lv for lv in LEVELS if lv not in by_level] if by_level else []

    problems = []
    if count != 10:
        problems.append(f"{count} export file(s) at the root")
    for lv in sorted(dups):
        problems.append(f"level {lv} {len(dups[lv])} times: "
                        + _names(dups[lv]))
    if out:
        problems.append("no level " + ", ".join(str(x) for x in out)
                        + " in the interface (levels 1 to 10)")
    if missing and not unnamed:
        problems.append("no file for level "
                        + ", ".join(str(x) for x in missing))
    detail = [Q_TEN, Q_ROOT]
    if scan.nested and count != 10:
        detail.append(f"{len(scan.nested)} export(s) found in a sub folder, "
                      "move them to the root: " + ", ".join(scan.nested[:10]))
    rep.check("10 export files at the root, one per level", not problems,
              expected="level1.json ... level10.json (Get my config)",
              got=("; ".join(problems) + (f" ({_names(exports)})"
                                          if exports else ""))
              if problems else None,
              detail="\n".join(detail) if problems else None)
    if scan.nested and count == 10:
        rep.warn("other exports in a sub folder: "
                 + ", ".join(scan.nested[:10]),
                 "only the 10 files at the root are graded " + Q_ROOT)

    if unnamed:
        extra = ""
        if missing:
            extra = ("\nno levelN file for level "
                     + ", ".join(str(x) for x in missing))
        rep.warn("level number cannot be read from the name: "
                 + _names(unnamed),
                 "Get my config names them level1.json ... level10.json; "
                 "the subject says: \"double-check the names of your files\""
                 + extra)
    in_dup = {id(e) for es in dups.values() for e in es}
    odd = [e for e in exports if e.level is not None and id(e) not in in_dup
           and e.level in LEVELS and not DEFAULT_NAME.match(e.name)]
    if odd:
        rep.warn("not the default name levelN.json: " + _names(odd),
                 "the subject says: \"double-check the names of your files\"")

    for e in exports:
        rep.check(f"{e.rel}: an export of Get my config", e.error is None,
                  expected='{"routes":{...},"ifs":{...}}',
                  got=e.error,
                  detail=None if e.error is None else
                  "not an export of Get my config. " + Q_EXPORT)
    for e in scan.valid():
        if not edited_fields(e.data):
            rep.warn(f"{e.rel}: nothing was configured",
                     "every route and interface is empty ({}): it was "
                     "exported before any field was edited")
    if scan.others:
        rep.warn("not an export (no routes / ifs), ignored: "
                 + ", ".join(scan.others[:10]))


def edited_fields(data):
    n = 0
    for part in ("routes", "ifs"):
        for entry in data[part].values():
            if not isinstance(entry, dict) or entry:
                n += 1
    return n


# --------------------------------------------------------------------------
# values
# --------------------------------------------------------------------------

IP_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)\.(\d+)")


def check_ip(value):
    """-> (int or None, severity or None, message)."""
    if not isinstance(value, str):
        return None, "ko", "not a text value"
    text = value.strip()
    if not text:
        return None, "warn", "empty value (the field was cleared)"
    m = IP_RE.fullmatch(text)
    if not m:
        return None, "ko", "not 4 numbers separated by dots"
    octets = [int(x) for x in m.groups()]
    big = [x for x in octets if x > 255]
    if big:
        return None, "ko", f"{big[0]} is not between 0 and 255"
    num = 0
    for x in octets:
        num = num * 256 + x
    if any(len(x) > 1 and x[0] == "0" for x in m.groups()):
        return num, "warn", "leading zero in a number"
    if text != value:
        return num, "warn", "spaces around the value"
    return num, None, ""


def prefix_mask(n):
    return (0xFFFFFFFF << (32 - n)) & 0xFFFFFFFF if n else 0


def check_prefix(text):
    """'24' -> 24, or None."""
    if not text.isdigit() or int(text) > 32:
        return None
    return int(text)


def check_mask(value):
    """-> (prefix length or None, severity, message)."""
    if not isinstance(value, str):
        return None, "ko", "not a text value"
    text = value.strip()
    if text.startswith("/"):
        n = check_prefix(text[1:])
        if n is None:
            return None, "ko", "the prefix after / must be 0 to 32"
        return n, ("warn" if text != value else None), \
            ("spaces around the value" if text != value else "")
    num, sev, msg = check_ip(value)
    if num is None:
        return None, sev, msg
    inv = ~num & 0xFFFFFFFF
    if (inv + 1) & inv:
        return None, "ko", "not a valid mask: the 1 bits must be contiguous"
    return 32 - inv.bit_length(), sev, msg


def check_route(value):
    """-> ((network, prefix) or None, severity, message)."""
    if not isinstance(value, str):
        return None, "ko", "not a text value"
    text = value.strip()
    if not text:
        return None, "warn", "empty value (the field was cleared)"
    if text.lower() == "default":
        return (0, 0), ("warn" if text != value else None), \
            ("spaces around the value" if text != value else "")
    if text.count("/") != 1:
        return None, "ko", "not a.b.c.d/n or default"
    addr, n = text.split("/")
    num, sev, msg = check_ip(addr)
    if num is None:
        return None, "ko", f"destination {addr!r}: {msg}"
    prefix = check_prefix(n)
    if prefix is None:
        return None, "ko", f"prefix /{n}: must be 0 to 32"
    if text != value:
        return (num, prefix), "warn", "spaces around the value"
    return (num, prefix), sev, msg


FIELDS = {
    "routes": {"route": (check_route, "a.b.c.d/n (0 to 32) or default"),
               "gate": (check_ip, "an IPv4 address such as 192.168.1.254")},
    "ifs": {"ip": (check_ip, "an IPv4 address such as 192.168.1.1"),
            "mask": (check_mask, "a mask such as 255.255.255.128 or /25")},
}


def ip_str(num):
    return ".".join(str((num >> s) & 255) for s in (24, 16, 8, 0))


def group_values(rep, scan):
    rep.section("values", gid="values")
    rep.info(INFO_DIAGRAM)
    if not scan.root:
        rep.skip("exported values", "no export file found")
        return
    for e in scan.root:
        if e.data is None:
            rep.skip(f"{e.rel}", "not an export, see files")
            continue
        parsed = check_file_values(rep, e)
        sanity(rep, e, parsed)


def check_file_values(rep, e):
    """Syntax of every field. Returns {(part, key, field): value}."""
    parsed = {}
    bad = 0
    for part in ("routes", "ifs"):
        for key, entry in e.data[part].items():
            where = f"{e.rel}: {part} > {key}"
            if not isinstance(entry, dict):
                bad += 1
                rep.check(where, False, expected="an object",
                          got=core.crepr(json.dumps(entry)), detail=Q_VALUE)
                continue
            for field, value in entry.items():
                spec = FIELDS[part].get(field)
                if spec is None:
                    continue
                func, expected = spec
                val, sev, msg = func(value)
                got = json.dumps(value)
                if sev == "ko":
                    bad += 1
                    rep.check(f"{where} > {field}", False, expected=expected,
                              got=f"{got}: {msg}", detail=Q_VALUE)
                elif sev == "warn":
                    rep.warn(f"{where} > {field} = {got}: {msg}")
                if val is not None:
                    parsed[(part, key, field)] = val
    if not bad:
        rep.check(f"{e.rel}: every value is valid", True)
    return parsed


ROUTE_KEY = re.compile(r"^([A-Z][A-Za-z]*\d*)r(\d+)$")


def sanity(rep, e, parsed):
    """Networking hints, WARN only: the topology is not in the file."""
    ifs = e.data["ifs"]
    addr = {}
    for key in ifs:
        ip = parsed.get(("ifs", key, "ip"))
        mask = parsed.get(("ifs", key, "mask"))
        if ip is not None:
            addr.setdefault(ip, []).append(key)
        if ip is None or mask is None or mask > 30:
            continue
        m = prefix_mask(mask)
        net = ip & m
        bcast = net | (~m & 0xFFFFFFFF)
        what = None
        if ip == net:
            what = "the network address"
        elif ip == bcast:
            what = "the broadcast address"
        if what:
            rep.warn(f"{e.rel}: {key} ip {ip_str(ip)}/{mask} is {what}",
                     "a host cannot use it (valid range "
                     f"{ip_str(net + 1)} - {ip_str(bcast - 1)})")
    for ip, keys in sorted(addr.items()):
        if len(keys) > 1:
            rep.warn(f"{e.rel}: {' and '.join(keys)} have the same ip "
                     f"{ip_str(ip)}")
    devices = {}
    for key in e.data["routes"]:
        m = ROUTE_KEY.match(key)
        if m:
            devices.setdefault(m.group(1), []).append(key)
    for dev, routes in sorted(devices.items()):
        own = [k for k in ifs if owner(k, devices) == dev]
        nets = []
        for k in own:
            ip = parsed.get(("ifs", k, "ip"))
            mask = parsed.get(("ifs", k, "mask"))
            if ip is None or mask is None:
                nets = []
                break
            nets.append((k, ip, mask))
        if not nets:
            continue    # an interface of the device is fixed: unknown
        for r in routes:
            gate = parsed.get(("routes", r, "gate"))
            if gate is None:
                continue
            if any(gate & prefix_mask(n) == ip & prefix_mask(n)
                   for _, ip, n in nets):
                continue
            have = ", ".join(f"{k} {ip_str(ip)}/{n}" for k, ip, n in nets)
            rep.warn(f"{e.rel}: {r} gate {ip_str(gate)} is outside the "
                     f"networks of {dev}",
                     f"{dev} interfaces: {have}; a gateway must be "
                     "reachable on one of them")


def owner(key, devices):
    """Device of an interface key (H11 -> H1), None when not certain."""
    found = [d for d in devices if key.startswith(d)
             and len(key) == len(d) + 1 and key[-1].isdigit()]
    return found[0] if len(found) == 1 else None


# --------------------------------------------------------------------------
# README
# --------------------------------------------------------------------------

CONCEPTS = [
    ("TCP/IP addressing",
     r"tcp\s*/\s*ip|\bip\s*(v4\s*)?address|\bipv4\b|\baddressing\b"),
    ("subnet mask", r"subnet|net\s*mask|\bmasks?\b|\bcidr\b"),
    ("default gateway", r"gateway"),
    ("routers", r"\brout(er|ers|ing)\b"),
    ("switches", r"\bswitch(es)?\b"),
    ("OSI layers", r"\bosi\b|\blayers?\s*[1-7]\b|\b(physical|data\s*link|"
                   r"network|transport)\s+layer"),
]

INSTRUCTIONS = [
    ("how to run the training interface (index.html, run.sh, "
     "python3 -m http.server)",
     r"index\.html|run\.sh|http\.server|localhost|web\s*server|browser"),
    ("how to export configurations (Get my config)",
     r"get\s*my\s*config|\bexport(s|ing)?\b|"
     r"download\w*\s+(\w+\s+){0,2}config"),
]

TEN = re.compile(r"\b(10|ten)\b|one\s+(exported\s+)?(file|config\w*|export)"
                 r"\s+(per|for\s+each)\s+level|level\s*0?1\b.{0,60}"
                 r"level\s*10\b", re.I | re.S)
# the subject allows the campus language: root, racine, raiz / raíz, radice
ROOT = re.compile(r"\b(root|racine|ra[ií]z|radice)\b", re.I)


def md_headings(lines):
    """[(index, level, lowercase title)], code blocks skipped."""
    out = []
    fence = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        m = re.match(r"^(#{1,6})\s*(.*?)\s*#*\s*$", ln)
        if m and not fence:
            out.append((i, len(m.group(1)), m.group(2).lower()))
    return out


def md_section(lines, heads, word):
    """Text of the first section whose title contains word (with its sub
    sections), or None."""
    for n, (i, level, title) in enumerate(heads):
        if word in title:
            end = len(lines)
            for j, lv, _ in heads[n + 1:]:
                if lv <= level:
                    end = j
                    break
            return "\n".join(lines[i + 1:end])
    return None


def group_readme(rep, project):
    rep.section("README.md", gid="readme")
    core.check_readme(rep, project)
    path = os.path.join(project, "README.md")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    lines = text.splitlines()
    heads = md_headings(lines)
    resources = md_section(lines, heads, "resource")
    instructions = md_section(lines, heads, "instruction")
    readme_concepts(rep, text, resources)
    readme_instructions(rep, text, instructions)


def readme_concepts(rep, text, resources):
    scope = resources if resources is not None else text
    where = "the Resources section" if resources is not None else "README.md"
    found = [c for c, rx in CONCEPTS if re.search(rx, scope, re.I)]
    anywhere = [c for c, rx in CONCEPTS if re.search(rx, text, re.I)]
    if not found:
        if anywhere:
            rep.warn("networking concepts are mentioned, but not in the "
                     "Resources section: " + ", ".join(anywhere),
                     Q_CONCEPTS)
            return
        rep.check("README mentions the networking concepts studied", False,
                  expected=", ".join(c for c, _ in CONCEPTS),
                  got="none of them", detail=Q_CONCEPTS)
        return
    rep.check("README mentions the networking concepts studied", True)
    for c, rx in CONCEPTS:
        if c in found:
            continue
        note = " (it is mentioned elsewhere)" if c in anywhere else ""
        rep.warn(f"{where} never mentions {c}{note}",
                 Q_CONCEPTS + ", such as TCP/IP addressing, subnet mask, "
                 "default gateway, routers and switches, OSI layers, etc.")


def readme_instructions(rep, text, instructions):
    scope = instructions if instructions is not None else text
    items = [(label, re.search(rx, scope, re.I) is not None,
              re.search(rx, text, re.I) is not None, Q_INSTR)
             for label, rx in INSTRUCTIONS]
    ten, root = TEN.search(scope), ROOT.search(scope)
    miss = [w for w, ok in (("10 files (one per level)", ten),
                            ("at the repository root", root)) if not ok]
    ten_all, root_all = TEN.search(text), ROOT.search(text)
    items.append(("submission: 10 exported files at the repository root",
                  not miss, bool(ten_all and root_all), Q_SUBMIT))
    for label, ok, elsewhere, quote in items:
        name = f"Instructions explain {label}"
        if ok:
            rep.check(name, True)
        elif elsewhere and instructions is not None:
            rep.warn(f"{name}: found outside the Instructions section",
                     quote)
        else:
            got = None
            if label.startswith("submission") and miss:
                got = "never states: " + ", ".join(miss)
            rep.check(name, False, got=got, detail=quote)


# --------------------------------------------------------------------------
# repository
# --------------------------------------------------------------------------

IGNORED = {".gitignore", ".gitattributes"}


def repo_files(project):
    res = core.run(["git", "ls-files"], cwd=project, timeout=30)
    if res.rc == 0 and res.out.strip():
        out = res.out.decode("utf-8", "replace")
        return [f for f in out.split("\n") if f]
    files = []
    for root, dirs, names in os.walk(project):
        dirs[:] = [d for d in dirs if d != ".git"]
        for n in names:
            files.append(os.path.relpath(os.path.join(root, n), project))
    return files


def group_repo(rep, project, scan):
    rep.section("repository", gid="repo")
    core.check_repo(rep, project)
    known = {e.rel for e in scan.root} | {"README.md"} | IGNORED
    extra = sorted(f for f in repo_files(project)
                   if f.replace(os.sep, "/") not in known)
    if extra:
        rep.warn("other files than the exports and README.md: "
                 + ", ".join(extra[:12])
                 + (f" (+{len(extra) - 12})" if len(extra) > 12 else ""),
                 "the subject asks for the 10 exported files and a "
                 "README.md")


def main():
    args = core.parse_args("NetPractice tester", GROUPS)
    rep = core.Report(verbose=args.verbose)
    if not os.path.isdir(args.project):
        print(core.red(f"project directory not found: {args.project}"))
        return 2
    core.header("NetPractice", args.project, subject="1.4")
    scan = Scan(args.project)
    if core.selected(args, "files"):
        group_files(rep, scan)
    if core.selected(args, "values"):
        group_values(rep, scan)
    if core.selected(args, "readme"):
        group_readme(rep, args.project)
    if core.selected(args, "repo"):
        group_repo(rep, args.project, scan)
    return rep.finish()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped (ctrl-c)")
        sys.exit(130)
