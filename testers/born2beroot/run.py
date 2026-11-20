#!/usr/bin/env python3
"""42next - Born2beRoot tester (host side, subject v1.3).

    python3 -B run.py --project DIR [--only signature,repo,readme,script]
                      [--vm PATH] [--no-hash] [--login LOGIN] [--host HOST]

The VM itself is checked by check.sh, which runs inside the VM
(the `script` group prints how to send and launch it).

  --vm PATH    folder (or disk file) holding the VM disk. Default:
               ~/VirtualBox VMs and the UTM folder on macOS.
  --no-hash    do not compute the sha1 of the disk (it reads the whole file).
  --login      your login, for the printed ssh / scp commands.
  --host       VM address for ssh / scp (default: localhost, NAT port forward).
"""

import hashlib
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "common"))
import core  # noqa: E402

GROUPS = [
    ("signature", "signature.txt + sha1 of the VM disk"),
    ("repo", "repository content (no VM disk pushed)"),
    ("readme", "README.md requirements"),
    ("script", "check.sh: how to run it inside the VM"),
]

DISK_EXT = (".vdi", ".qcow2", ".vmdk")
VM_FILE_RE = re.compile(r"\.(vdi|qcow2|vmdk|vhd|vhdx|ova|ovf|vbox|vbox-prev|"
                        r"nvram|sav|iso|img|utm|raw)$", re.I)
ALLOWED = {"signature.txt", "README.md"}
HERE = os.path.dirname(os.path.abspath(__file__))
CHECK_SH = os.path.join(HERE, "check.sh")


def extra_opts(extra):
    """Parse the tester specific options left by core.parse_args."""
    opts = {"vm": None, "no_hash": False, "login": None, "host": "localhost"}
    i = 0
    while i < len(extra):
        arg = extra[i]
        key, _, val = arg.partition("=")
        name = key.lstrip("-").replace("-", "_")
        if name == "no_hash":
            opts["no_hash"] = True
        elif name in ("vm", "login", "host"):
            if not val and i + 1 < len(extra):
                i += 1
                val = extra[i]
            opts[name] = val
        i += 1
    return opts


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def repo_files(project):
    res = core.run(["git", "ls-files"], cwd=project, timeout=30)
    if res.rc == 0 and res.out.strip():
        out = res.out.decode("utf-8", "replace")
        files = [f for f in out.split("\n") if f]
        return files, "tracked by git"
    files = []
    for root, dirs, names in os.walk(project):
        dirs[:] = [d for d in dirs if d != ".git"]
        for n in names:
            files.append(os.path.relpath(os.path.join(root, n), project))
    return files, "in the folder"


def read_signature(project):
    path = os.path.join(project, "signature.txt")
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def find_disks(opts):
    if opts["vm"]:
        roots = [os.path.expanduser(opts["vm"])]
    else:
        home = os.path.expanduser("~")
        roots = [os.path.join(home, "VirtualBox VMs"),
                 os.path.join(home, "Library", "Containers",
                              "com.utmapp.UTM", "Data", "Documents")]
    disks = []
    searched = []
    for root in roots:
        if os.path.isfile(root):
            disks.append(root)
            searched.append(root)
            continue
        if not os.path.isdir(root):
            continue
        searched.append(root)
        for dirpath, dirs, names in os.walk(root):
            for n in names:
                if n.lower().endswith(DISK_EXT):
                    disks.append(os.path.join(dirpath, n))
    return sorted(disks), searched, roots


def sha1_file(path):
    h = hashlib.sha1()
    size = os.path.getsize(path)
    done = 0
    last = time.monotonic()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(8 * 1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
            done += len(chunk)
            now = time.monotonic()
            if sys.stdout.isatty() and now - last > 1:
                pct = 100 * done / size if size else 100
                print(f"\r       hashing {pct:5.1f}%", end="", flush=True)
                last = now
    if sys.stdout.isatty():
        print("\r" + " " * 30 + "\r", end="")
    return h.hexdigest()


def is_snapshot(path):
    parts = [p.lower() for p in path.split(os.sep)]
    return "snapshots" in parts


def vm_running():
    if not core.have("VBoxManage"):
        return None
    res = core.run(["VBoxManage", "list", "runningvms"], timeout=20)
    if res.rc != 0:
        return None
    return [ln.split('"')[1] for ln in res.out.decode("utf-8", "replace")
            .splitlines() if ln.count('"') >= 2]


# --------------------------------------------------------------------------

def group_signature(rep, project, opts):
    rep.section("signature")
    data = read_signature(project)
    if data is None:
        rep.check("signature.txt at the root of the repository", False,
                  detail="the subject asks for the sha1 of the VM disk in "
                         "signature.txt")
        return
    rep.check("signature.txt at the root of the repository", True)
    text = data.decode("utf-8", "replace")
    sig = text.strip()
    # "paste the signature of your machine's virtual disk": a sha1 must be
    # there; extra text around it (certUtil / sha1sum output) is not
    # forbidden, only harder to compare by eye
    found = re.findall(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])",
                       text)
    rep.check("signature.txt contains a sha1 (40 hex characters)",
              bool(found),
              expected="6e657c4619944be17df3c31faa030c25e43e40af",
              got=None if found else core.crepr(data, 120))
    if not found:
        return
    if len(found) > 1:
        rep.warn(f"signature.txt holds {len(found)} sha1 values",
                 "the first one is compared with the VM disk")
    elif sig != found[0]:
        rep.warn("signature.txt has other text around the sha1",
                 "the subject example is the bare sha1: "
                 + core.crepr(data, 120))
    sig = found[0]
    if sig != sig.lower():
        rep.warn("signature in uppercase (certUtil output?)",
                 "sha1sum prints lowercase; graders compare by eye")
    if "\r" in text:
        rep.warn("signature.txt has Windows line endings (\\r\\n)")
    sig = sig.lower()

    disks, searched, roots = find_disks(opts)
    if not disks:
        rep.skip("compare with the VM disk",
                 "no .vdi/.qcow2/.vmdk found in "
                 + (", ".join(searched) or ", ".join(roots))
                 + "; use --vm PATH")
        return
    main = [d for d in disks if not is_snapshot(d)]
    snaps = [d for d in disks if is_snapshot(d)]
    if snaps:
        rep.warn(f"{len(snaps)} snapshot disk(s) found",
                 "no snapshot may exist at the beginning of an evaluation\n"
                 + "\n".join(snaps[:5]))
    running = vm_running()
    if running:
        rep.warn("VM running: " + ", ".join(running),
                 "starting the VM changes the disk, so its sha1 too")
    if opts["no_hash"]:
        rep.skip("compare with the VM disk", "--no-hash")
        for d in main:
            rep.info(f"{d} ({human(os.path.getsize(d))})")
        return
    total = sum(os.path.getsize(d) for d in main)
    rep.info(f"computing sha1 of {len(main)} disk(s), {human(total)}: this "
             "reads every byte and can take several minutes "
             "(skip with --no-hash)")
    hashes = []
    for d in main:
        start = time.monotonic()
        digest = sha1_file(d)
        hashes.append((d, digest))
        rep.info(f"{digest}  {d} ({time.monotonic() - start:.0f}s)")
        if digest == sig:
            break
    match = [d for d, h in hashes if h == sig]
    rep.check("signature.txt matches a VM disk", bool(match),
              expected=sig,
              got=None if match else ", ".join(h for _, h in hashes),
              detail=("matches " + match[0]) if match else
              "a VM booted after signing changes its sha1: sign a copy of "
              "the disk, or a snapshot (removed before the evaluation)")


def group_repo(rep, project):
    rep.section("repository")
    files, source = repo_files(project)
    vm = [f for f in files if VM_FILE_RE.search(f)]
    rep.check(f"no virtual machine file {source} (FORBIDDEN)", not vm,
              detail=("remove: " + ", ".join(vm[:10])) if vm else None)
    big = []
    for f in files:
        p = os.path.join(project, f)
        if os.path.isfile(p) and os.path.getsize(p) > 10 * 1024 * 1024:
            big.append(f"{f} ({human(os.path.getsize(p))})")
    if big:
        # only the VM itself is FORBIDDEN (checked above by extension)
        rep.warn("big file(s) (> 10 MB) in the repository",
                 ", ".join(big) + " (a VM disk under another name?)")
    # "You only need to submit the expected README.md file, and a
    # signature.txt file": other files are not forbidden (the VM is, above)
    extra = sorted(f for f in files if f not in ALLOWED)
    if extra:
        rep.warn("other files than signature.txt and README.md",
                 "the subject only asks for these two: "
                 + ", ".join(extra[:12])
                 + (" ..." if len(extra) > 12 else ""))
    nested = [f for f in files if os.path.basename(f) in ALLOWED
              and f not in ALLOWED]
    if nested:
        rep.warn("signature.txt / README.md found in a sub folder",
                 "they must be at the root: " + ", ".join(nested))


def group_readme(rep, project):
    rep.section("README.md")
    core.check_readme(rep, project, extra_sections=("Project description",))
    path = os.path.join(project, "README.md")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read().lower()
    for a, b in (("Debian", "Rocky"), ("AppArmor", "SELinux"),
                 ("UFW", "firewalld"), ("VirtualBox", "UTM")):
        rep.check(f"compares {a} vs {b}",
                  a.lower() in text and b.lower() in text,
                  detail="the subject asks for this comparison in the "
                         "project description")
    for topic in ("partition", "password", "sudo", "user"):
        if topic not in text:
            rep.warn(f"README never mentions '{topic}'",
                     "design choices: partitioning, security policies, "
                     "user management, services installed")


def group_script(rep, opts):
    rep.section("check.sh (inside the VM)")
    if not rep.check("check.sh present", os.path.isfile(CHECK_SH),
                     got=CHECK_SH):
        return
    if core.have("bash"):
        res = core.run(["bash", "-n", CHECK_SH], timeout=20)
        rep.check("check.sh syntax", res.rc == 0,
                  detail=res.err.decode("utf-8", "replace") or None)
    login = opts["login"] or os.environ.get("USER") or "<login>"
    host = opts["host"]
    scp = f'scp -P 4242 "{CHECK_SH}" {login}@{host}:/tmp/check.sh'
    ssh = f'ssh -t -p 4242 {login}@{host} "sudo bash /tmp/check.sh {login}"'
    print(f"""
  {core.bold('check.sh')}: {CHECK_SH}

  The VM cannot be inspected from here. Copy check.sh into the VM and run it
  there as root. It changes no configuration; it runs your monitoring.sh
  once, with wall replaced by a print to stdout:

    {core.accent(scp)}
    {core.accent(ssh)}

  -t is needed: with requiretty, sudo refuses to run without a terminal.
  With VirtualBox NAT, forward host port 4242 to guest port 4242 first
  (Settings > Network > Advanced > Port Forwarding), or use the VM IP.
  Optional 2nd argument: path to monitoring.sh if it is not in a crontab.
  Booting the VM changes its disk sha1: sign a copy of the disk first.""")


def main():
    args = core.parse_args("Born2beRoot tester (host side)", GROUPS)
    opts = extra_opts(args.extra)
    rep = core.Report(verbose=args.verbose)
    rep.alias.update({"repository": "repo", "README.md": "readme",
                      "check.sh (inside the VM)": "script"})
    if not os.path.isdir(args.project):
        print(core.red(f"project directory not found: {args.project}"))
        return 2
    core.header("Born2beRoot", args.project, subject="1.3")
    if core.selected(args, "signature"):
        group_signature(rep, args.project, opts)
    if core.selected(args, "repo"):
        group_repo(rep, args.project)
    if core.selected(args, "readme"):
        group_readme(rep, args.project)
    if core.selected(args, "script"):
        group_script(rep, opts)
    return rep.finish()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # ctrl-c: the temporary copies are removed on the way out
        print("\nstopped (ctrl-c)")
        sys.exit(130)
