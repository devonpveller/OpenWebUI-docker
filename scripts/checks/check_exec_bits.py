#!/usr/bin/env python3
"""check_exec_bits.py - which tracked files are EXECUTED DIRECTLY, and are they 100755?

WHY THIS EXISTS (ac-exec-bits, 2026-09-25). On Linux and macOS a file runs by path
(`/scripts/backup.sh`, `./x.sh`, an image's `CMD ["/scripts/watch.sh"]`) only if its
executable bit is set, and a clone takes that bit from the MODE git recorded: 100755 or
100644. This repo is developed on Windows with core.fileMode=false, where git never sees
a mode change, and Docker Desktop shows every bind-mounted file as -rwxrwxrwx. So a
script committed 100644 works here and fails with "Permission denied" on the first real
Linux host - silently, for a sidecar in a sleep loop.

WHAT IT DERIVES. The set is read from the INDEX (staged content, so pre-commit sees what
is about to be committed), never from a hand list. A tracked file is EXECUTED DIRECTLY
when one of these runs it by path with no interpreter in front of it:

  compose    a service's entrypoint + command (the service's own keys, else the YAML
             anchors it merges, else - for a service with `build:` - its Dockerfile's
             ENTRYPOINT/CMD) or its healthcheck `test`, executes a container path that
             one of THAT service's bind mounts maps to a tracked file. `sh -c` scripts
             are read line by line, so `while ...; do /scripts/backup.sh` counts, and so
             does a crontab rendered inline (`printf '%s /scripts/x.sh check'`).
  dockerfile ENTRYPOINT/CMD/RUN executes a path that a COPY/ADD put in the image. The
             entry says whether the image makes it executable itself (COPY --chmod, or a
             RUN chmod naming it). If it does, the IMAGE is fine whatever git says - but
             a bind mount of the same file is not, and an edit that drops the chmod would
             fail silently, so it is inventoried either way.
  cron       a tracked crontab line's command, when it is a repo path.
  docs       a fenced shell code block in a tracked *.md, or the `Usage` block of a
             tracked script's header comment, starts a command with a tracked file's path
             (`scripts/stack/rehearse-fresh-clone.sh --ref x`, `./x.sh`).
  ci         a `run:` line in .github/workflows/*.yml.

"Command position": the first word of a line or of a `sh -c` string, or the word after
`;` `&&` `||` `|` `$(` `(` `do` `then` `else` `exec` `nohup` `env` `sudo` `tini --`, or
after a rendered crontab's `%s` schedule. The SAME path after `sh`, `bash`, `python`,
`deno run`, `node`, `pwsh`, `.` ... is INTERPRETED and needs no bit; `--explain` lists
those uses too, and they are not inventoried.

BLIND SPOTS, stated because a check that hides them is worse than none: a path built in
a variable (`"$DIR/x.sh"`, an `os.path.join(...)` handed to subprocess as argv[0]) is not
followed; a long-syntax bind mount whose source is not relative is ignored, as is any
absolute host path; OB1 is a submodule with its own modes and is not scanned;
scripts/archive/ and documentation/archive/ are out of scope (nothing live runs them - add
a rule here if that changes); PowerShell's `& ./x.ps1` needs no bit and is ignored.

USAGE (from anywhere in the repo; Python 3.8+, stdlib only, any OS):
  python3 scripts/checks/check_exec_bits.py            # the derived set, and why
  python3 scripts/checks/check_exec_bits.py --explain  # also every INTERPRETED use seen
  python3 scripts/checks/check_exec_bits.py --write    # regenerate the inventory file
  python3 scripts/checks/check_exec_bits.py --check    # pre-commit: exit 1 on inventory
                                                       # drift or a file not 100755
The inventory, scripts/checks/exec-bits.inventory, is GENERATED: one path per line, a
tab, then why. pre-commit also reads it with plain sh, so the mode rule holds on a host
with no Python at all; only the drift half (did the tree gain a new one?) needs Python.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import shlex
import subprocess
import sys

INVENTORY = "scripts/checks/exec-bits.inventory"
EXCLUDED_PREFIXES = ("scripts/archive/", "documentation/archive/", "OB1/")

# The word before a path that makes the path a COMMAND.
CMD_STARTERS = {
    "", ";", "&&", "||", "|", "(", "$(", "`", "{", "!", "do", "then", "else", "exec",
    "nohup", "env", "time", "sudo", "tini", "--", "dumb-init", "-c", "%s", "gosu",
    "su-exec", "xargs",
}
# The word before a path that makes the path an interpreter's ARGUMENT.
INTERPRETERS = {
    "sh", "bash", "ash", "dash", "zsh", "python", "python3", "pythonw", "pythonw.exe",
    "python.exe", "node", "deno", "run", "pwsh", "powershell", "powershell.exe",
    "source", ".", "uv", "ts-node", "tsx", "perl", "ruby", "php", "busybox",
}
SHELLS = {"sh", "bash", "ash", "dash", "zsh"}


# ---------------------------------------------------------------------------- index
def git(*args: str, inp: bytes | None = None) -> bytes:
    r = subprocess.run(["git", *args], input=inp, capture_output=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr.decode("utf-8", "replace"))
        raise SystemExit(f"check_exec_bits: git {' '.join(args)} failed ({r.returncode})")
    return r.stdout


class Index:
    """Tracked files as the INDEX has them - mode and content - whatever the worktree says."""

    def __init__(self) -> None:
        self.mode: dict[str, str] = {}
        self.sha: dict[str, str] = {}
        for rec in git("ls-files", "-s", "-z").split(b"\0"):
            if not rec:
                continue
            meta, path = rec.split(b"\t", 1)
            mode, sha, _stage = meta.decode().split()
            p = path.decode("utf-8", "surrogateescape")
            self.mode[p] = mode
            self.sha[p] = sha
        self.dirs: set[str] = set()
        for p in self.mode:
            d = posixpath.dirname(p)
            while d and d not in self.dirs:
                self.dirs.add(d)
                d = posixpath.dirname(d)
        self._text: dict[str, str] = {}

    def is_file(self, p: str) -> bool:
        return self.mode.get(p, "").startswith("100")

    def preload(self, paths: list[str]) -> None:
        want = [p for p in paths if p not in self._text and self.is_file(p)]
        if not want:
            return
        out = git("cat-file", "--batch", inp=("\n".join(self.sha[p] for p in want) + "\n").encode())
        pos = 0
        for p in want:
            nl = out.index(b"\n", pos)
            size = int(out[pos:nl].split()[2])
            body = out[nl + 1: nl + 1 + size]
            pos = nl + 1 + size + 1
            self._text[p] = body.decode("utf-8", "replace").replace("\r\n", "\n")

    def text(self, p: str) -> str:
        if p not in self._text:
            self.preload([p])
        return self._text.get(p, "")

    def has_shebang(self, p: str) -> bool:
        return self.text(p).startswith("#!")


def in_scope(p: str) -> bool:
    return not p.startswith(EXCLUDED_PREFIXES)


def norm(base_dir: str, rel: str) -> str | None:
    """Repo-relative POSIX path for `rel` taken from `base_dir`; None if it leaves the repo."""
    p = posixpath.normpath(posixpath.join(base_dir, rel) if base_dir else rel)
    if p == ".." or p.startswith("../") or p.startswith("/"):
        return None
    return p


def indent(ln: str) -> int:
    return len(ln) - len(ln.lstrip(" "))


def is_blank(ln: str) -> bool:
    return not ln.strip() or ln.lstrip().startswith("#")


# ------------------------------------------------------------------ classification
_TOKEN = re.compile(r"""[^\s"'\[\],;&|()`=<>]+""")


def classify(prefix: str) -> str:
    """'direct', 'interp' or 'other' for a path preceded, on its line, by `prefix`."""
    s = prefix.rstrip().rstrip("\"'").rstrip()
    if not s:
        return "direct"
    for sep in ("&&", "||", "$(", ";", "|", "(", "`", "{", "!"):
        if s.endswith(sep):
            return "direct"
    words = s.split()
    w = words[-1].strip("\"'")
    if w == "-c":
        # `sh -c "<path> ..."`: the path starts the shell's script. After anything else
        # (`tinyproxy -c /etc/x.conf`) -c is just an option taking a file.
        before = words[-2].strip("\"'") if len(words) > 1 else ""
        return "direct" if posixpath.basename(before) in SHELLS else "other"
    if w in CMD_STARTERS:
        return "direct"
    base = w.rsplit("/", 1)[-1]
    if base in CMD_STARTERS - {"", "-c"}:
        return "direct"
    if base in INTERPRETERS or re.fullmatch(r"python3(\.\d+)?", base):
        return "interp"
    return "other"


class Found:
    def __init__(self) -> None:
        self.direct: dict[str, set[str]] = {}
        self.interp: dict[str, set[str]] = {}

    def add(self, kind: str, path: str, why: str) -> None:
        if not in_scope(path):
            return
        if kind == "direct":
            self.direct.setdefault(path, set()).add(why)
        elif kind == "interp":
            self.interp.setdefault(path, set()).add(why)


def scan_line(line: str, resolve, found: Found, why: str) -> None:
    """Classify every token in a shell-shaped line that `resolve` maps to a repo file."""
    for m in _TOKEN.finditer(line):
        repo = resolve(m.group(0))
        if repo:
            found.add(classify(line[: m.start()]), repo, why)


def argv_lines(argv: list[str]) -> list[str]:
    """Shell-shaped lines whose command-position words are exactly what `argv` executes.

    A wrapper (tini, dumb-init) is peeled; `<shell> -c <script>` yields the script's own
    lines; anything else is argv joined, so `sh /x.sh` reads as interpreted and
    `/x.sh arg` as direct.
    """
    a = list(argv)
    while a and posixpath.basename(a[0]) in ("tini", "dumb-init"):
        a = a[1:]
        if "--" in a[:3]:
            a = a[a.index("--") + 1:]
        else:
            while a and a[0].startswith("-"):
                a = a[1:]
    if not a:
        return []
    if posixpath.basename(a[0]) in SHELLS and len(a) >= 3 and a[1] == "-c":
        return a[2].split("\n")
    return [" ".join(a)]


def to_argv(v) -> list[str] | None:
    """A compose/Docker value -> argv. A string is split the way the ENGINE splits it."""
    if v is None:
        return None
    if isinstance(v, list):
        return v
    try:
        return shlex.split(v)
    except ValueError:
        return v.split()


# --------------------------------------------------------------------- dockerfiles
DOCKERFILE_RE = re.compile(r"(^|/)(Dockerfile[^/]*|dockerfile[^/]*|[^/]+\.Dockerfile)$")


def exec_form(arg: str) -> list[str] | None:
    arg = arg.strip()
    if arg.startswith("["):
        try:
            v = json.loads(arg)
        except ValueError:
            return None
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            return v
    return None


def dockerfile_instructions(text: str) -> list[tuple[str, str]]:
    out, cur = [], ""
    for raw in text.split("\n"):
        line = raw.rstrip()
        if line.lstrip().startswith("#") or (not cur and not line.strip()):
            continue
        if line.endswith("\\"):
            cur += line[:-1] + " "
            continue
        cur += line
        parts = cur.strip().split(None, 1)
        if parts:
            out.append((parts[0].upper(), parts[1] if len(parts) > 1 else ""))
        cur = ""
    return out


class DockerImage:
    """What one Dockerfile, built from one context, puts in the image and runs."""

    def __init__(self, idx: Index, dockerfile: str, context: str) -> None:
        self.dockerfile, self.context = dockerfile, context
        self.copies: dict[str, str] = {}      # container file -> repo file
        self.copy_dirs: dict[str, str] = {}   # container dir -> repo dir ("" = context root)
        self.chmodded: set[str] = set()       # container paths the image makes executable
        self.entrypoint = None                # list (exec form) or str (shell form)
        self.cmd = None
        self.runs: list[str] = []
        workdir = "/"
        for ins, arg in dockerfile_instructions(idx.text(dockerfile)):
            if ins == "WORKDIR":
                workdir = posixpath.join(workdir, arg.strip())
            elif ins in ("COPY", "ADD"):
                toks = arg.split()
                flags = [t for t in toks if t.startswith("--")]
                if any(f.startswith("--from") for f in flags):
                    continue
                plain = " ".join(t for t in toks if not t.startswith("--"))
                rest = exec_form(plain) or plain.split()
                if len(rest) < 2:
                    continue
                srcs, dest = rest[:-1], rest[-1]
                if not dest.startswith("/"):
                    dest = posixpath.join(workdir, dest)
                chmod_x = any(f.startswith("--chmod=") and re.search(r"[1357]", f.split("=", 1)[1])
                              for f in flags)
                for s in srcs:
                    rp = norm(context, s)
                    if rp is None:
                        continue
                    if idx.is_file(rp):
                        target = posixpath.normpath(
                            posixpath.join(dest, posixpath.basename(rp))
                            if dest.endswith("/") or len(srcs) > 1 else dest)
                        self.copies[target] = rp
                        if chmod_x:
                            self.chmodded.add(target)
                    elif rp in idx.dirs or rp == ".":
                        self.copy_dirs[posixpath.normpath(dest)] = "" if rp == "." else rp
            elif ins == "RUN":
                self.runs.append(arg)
                for m in re.finditer(r"chmod\s+(?:-\S+\s+)*(\S+)\s+([^;&|]+)", arg):
                    if "x" in m.group(1) or re.fullmatch(r"0?[0-7]*[1357][0-7]*", m.group(1)):
                        for t in m.group(2).split():
                            self.chmodded.add(posixpath.normpath(posixpath.join(workdir, t)))
            elif ins == "ENTRYPOINT":
                self.entrypoint = exec_form(arg) or arg
                self.cmd = None  # ENTRYPOINT resets an inherited CMD
            elif ins == "CMD":
                self.cmd = exec_form(arg) or arg

    def resolve(self, cpath: str) -> str | None:
        if not cpath.startswith("/"):
            return None
        p = posixpath.normpath(cpath)
        if p in self.copies:
            return self.copies[p]
        for d, rd in self.copy_dirs.items():
            pre = d.rstrip("/") + "/"
            if p.startswith(pre):
                return posixpath.join(rd, p[len(pre):]) if rd else p[len(pre):]
        return None


def image_argv(v) -> list[str] | None:
    """Docker's own rule: a shell-form ENTRYPOINT/CMD runs as /bin/sh -c <string>."""
    if v is None:
        return None
    return v if isinstance(v, list) else ["/bin/sh", "-c", v]


def scan_dockerfiles(idx: Index, found: Found, contexts: dict[str, set[str]]) -> dict:
    images: dict = {}
    for p in sorted(idx.mode):
        if not in_scope(p) or not DOCKERFILE_RE.search(p) or not idx.is_file(p):
            continue
        for ctx in sorted(contexts.get(p) or {posixpath.dirname(p)}):
            img = DockerImage(idx, p, ctx)
            images[(p, ctx)] = img
            start = (image_argv(img.entrypoint) or []) + (image_argv(img.cmd) or []) \
                if isinstance(img.entrypoint, list) or img.entrypoint is None \
                else image_argv(img.entrypoint)
            lines = [("start", ln) for ln in argv_lines(start or [])]
            lines += [("RUN", r) for r in img.runs]

            def resolve(tok, img=img, ctx=ctx):
                rp = img.resolve(tok)
                if rp is None and tok.startswith("./"):
                    rp = norm(ctx, tok)
                return rp if rp and idx.is_file(rp) else None

            for label, ln in lines:
                for m in _TOKEN.finditer(ln):
                    tok = m.group(0)
                    rp = resolve(tok)
                    if not rp:
                        continue
                    kind = classify(ln[: m.start()])
                    note = ("the image chmods it" if posixpath.normpath(tok) in img.chmodded
                            else "the image does NOT chmod it, so the mode comes from git")
                    what = "ENTRYPOINT/CMD" if label == "start" else "RUN"
                    found.add(kind, rp, f"dockerfile {p} {what} runs {tok} ({note})")
    return images


# ------------------------------------------------------------------------ compose
def is_compose(idx: Index, p: str) -> bool:
    if not re.search(r"\.ya?ml$", p) or not in_scope(p) or p.startswith(".github/"):
        return False
    return re.search(r"(?m)^services:\s*$", idx.text(p)) is not None


def keys_at(lines: list[str], start: int, end: int, ind: int) -> dict[str, tuple[int, int]]:
    keys: dict[str, tuple[int, int]] = {}
    cur, cur_start = None, 0
    for i in range(start, end):
        ln = lines[i]
        if is_blank(ln):
            continue
        if indent(ln) < ind:
            break
        if indent(ln) == ind:
            m = re.match(r"\s*([A-Za-z0-9_.\-]+)\s*:", ln)
            if m:
                if cur is not None:
                    keys[cur] = (cur_start, i)
                cur, cur_start = m.group(1), i
    if cur is not None:
        keys[cur] = (cur_start, end)
    return keys


def compose_services(idx: Index, p: str):
    """(name, own block lines, merged-anchor block lines, anchors) per service."""
    lines = idx.text(p).split("\n")
    anchors: dict[str, list[str]] = {}
    for i, ln in enumerate(lines):
        m = re.match(r"(\s*)[A-Za-z0-9_.\-]+\s*:\s*&([A-Za-z0-9_\-]+)\s*$", ln)
        if m:
            j = i + 1
            while j < len(lines) and (is_blank(lines[j]) or indent(lines[j]) > len(m.group(1))):
                j += 1
            anchors[m.group(2)] = lines[i + 1: j]
    top = keys_at(lines, 0, len(lines), 0)
    if "services" not in top:
        return
    s0, s1 = top["services"]
    ci = next((indent(ln) for ln in lines[s0 + 1: s1] if not is_blank(ln)), None)
    if ci is None:
        return
    for name, (a, b) in keys_at(lines, s0 + 1, s1, ci).items():
        own = lines[a + 1: b]
        merged: list[str] = []
        for ln in own:
            for am in re.finditer(r"<<:\s*\*([A-Za-z0-9_\-]+)", ln):
                merged.extend(anchors.get(am.group(1), []))
        yield name, own, merged, anchors


def dedent_block(lines: list[str]) -> str:
    rows = [ln for ln in lines if ln.strip()]
    if not rows:
        return ""
    cut = min(indent(ln) for ln in rows)
    return "\n".join(ln[cut:] for ln in lines)


def _scalar(s: str) -> str:
    s = s.strip()
    if s[:1] in "\"'" and s[-1:] == s[:1]:
        return s[1:-1]
    return re.sub(r"\s+#.*$", "", s)


def yaml_value(block: list[str], key: str, anchors: dict | None = None):
    """`key`'s value at the block's top indent: str, list[str], or None if absent.

    Enough YAML for compose's entrypoint/command/test: a plain or quoted scalar, a flow
    list, a `|`/`>` block, a block list whose items may be `|` blocks, and an alias
    (`*name`) to a top-level anchor.
    """
    rows = [ln for ln in block if not is_blank(ln)]
    if not rows:
        return None
    top = min(indent(ln) for ln in rows)
    for i, ln in enumerate(block):
        if is_blank(ln) or indent(ln) != top:
            continue
        m = re.match(r"\s*" + re.escape(key) + r"\s*:(\s+(.*?))?\s*$", ln)
        if not m:
            continue
        rest = (m.group(2) or "").strip()
        body = []
        j = i + 1
        while j < len(block) and (not block[j].strip() or indent(block[j]) > top):
            body.append(block[j])
            j += 1
        if rest.startswith("*") and anchors is not None:
            return ("alias", anchors.get(rest[1:].strip(), []))
        if rest.startswith("["):
            ef = exec_form(rest)
            if ef is not None:
                return ef
            return [_scalar(x) for x in rest.strip("[] ").split(",") if x.strip()]
        if rest.startswith(("|", ">")):
            return dedent_block(body)
        if rest and not rest.startswith("#"):
            return _scalar(rest)
        items: list[str] = []
        k = 0
        while k < len(body):
            b = body[k]
            if is_blank(b) or not b.strip().startswith("-"):
                k += 1
                continue
            item = b.strip()[1:].strip()
            iind = indent(b)
            sub = []
            k += 1
            while k < len(body) and (not body[k].strip() or indent(body[k]) > iind):
                sub.append(body[k])
                k += 1
            items.append(dedent_block(sub) if item.startswith(("|", ">")) else _scalar(item))
        return items if items else None
    return None


def service_mounts(idx: Index, cdir: str, block: list[str]) -> dict[str, str]:
    """container path -> repo path, for this service's binds of a tracked file or dir."""
    mounts: dict[str, str] = {}
    vol_ind = None
    src = None
    for ln in block:
        if is_blank(ln):
            continue
        s = ln.strip()
        if re.match(r"volumes\s*:\s*$", s):
            vol_ind = indent(ln)
            continue
        if vol_ind is None:
            continue
        if indent(ln) <= vol_ind and not s.startswith("-"):
            vol_ind = None
            continue
        m = re.match(r"-\s*[\"']?([^\"'\s#]+)[\"']?\s*(#.*)?$", s)
        if m and ":" in m.group(1):
            spec = re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^}]*)\}", r"\1", m.group(1))
            parts = spec.split(":")
            if len(parts) >= 2 and parts[0].startswith("."):
                rp = norm(cdir, parts[0])
                if rp and (idx.is_file(rp) or rp in idx.dirs):
                    mounts[posixpath.normpath(parts[1])] = rp
            continue
        ms = re.match(r"-?\s*source\s*:\s*[\"']?([^\"'\s#]+)", s)
        if ms:
            src = ms.group(1)
            continue
        mt = re.match(r"-?\s*target\s*:\s*[\"']?([^\"'\s#]+)", s)
        if mt and src and src.startswith("."):
            rp = norm(cdir, src)
            if rp and (idx.is_file(rp) or rp in idx.dirs):
                mounts[posixpath.normpath(mt.group(1))] = rp
            src = None
    return mounts


def build_of(cdir: str, block: list[str]) -> tuple[str, str] | None:
    """(context, dockerfile) as repo paths for a service's `build:`, or None."""
    ctx, df = None, "Dockerfile"
    for i, ln in enumerate(block):
        m = re.match(r"\s*build\s*:\s*(.*?)\s*$", ln)
        if not m or is_blank(ln):
            continue
        if m.group(1) and not m.group(1).startswith("#"):
            ctx = _scalar(m.group(1))
        for ln2 in block[i + 1:]:
            if ln2.strip() and indent(ln2) <= indent(ln):
                break
            mc = re.match(r"\s*context\s*:\s*(\S+)", ln2)
            if mc:
                ctx = _scalar(mc.group(1))
            md = re.match(r"\s*dockerfile\s*:\s*(\S+)", ln2)
            if md:
                df = _scalar(md.group(1))
        break
    if ctx is None:
        return None
    c = norm(cdir, ctx)
    return (c, posixpath.normpath(posixpath.join(c, df))) if c is not None else None


def sub_block(block: list[str], key: str) -> list[str] | None:
    for i, ln in enumerate(block):
        if not is_blank(ln) and re.match(r"\s*" + re.escape(key) + r"\s*:\s*(\*\S+)?\s*$", ln):
            out = []
            for ln2 in block[i + 1:]:
                if ln2.strip() and indent(ln2) <= indent(ln):
                    break
                out.append(ln2)
            return out
    return None


def healthcheck_lines(v) -> list[str]:
    if isinstance(v, str):
        return v.split("\n")
    if isinstance(v, list) and v:
        if v[0] == "CMD-SHELL":
            return " ".join(v[1:]).split("\n")
        if v[0] == "CMD":
            return argv_lines(v[1:])
    return []


def scan_compose(idx: Index, found: Found, images: dict) -> None:
    for p in sorted(idx.mode):
        if not idx.is_file(p) or not is_compose(idx, p):
            continue
        cdir = posixpath.dirname(p)
        for name, own, merged, anchors in compose_services(idx, p):
            targets: dict[str, str] = {}
            for cpath, rp in service_mounts(idx, cdir, merged + own).items():
                if idx.is_file(rp):
                    targets[cpath] = rp
                else:
                    pre = rp + "/"
                    for f in idx.mode:
                        if f.startswith(pre) and idx.is_file(f):
                            targets[posixpath.join(cpath, f[len(pre):])] = f
            if not targets:
                continue

            def pick(key: str):
                v = yaml_value(own, key)
                return v if v is not None else yaml_value(merged, key)

            ep, cmd = pick("entrypoint"), pick("command")
            via = ""
            b = build_of(cdir, merged + own)
            img = None
            if b:
                img = images.get((b[1], b[0]))
                if img is None and idx.is_file(b[1]):
                    img = DockerImage(idx, b[1], b[0])
            if img is not None:
                took = []
                if ep is None and img.entrypoint is not None:
                    ep = image_argv(img.entrypoint)
                    took.append("ENTRYPOINT")
                if cmd is None and pick("entrypoint") is None and img.cmd is not None:
                    cmd = image_argv(img.cmd)
                    took.append("CMD")
                if took:
                    via = f" with its image's {'+'.join(took)} ({img.dockerfile})"
            argv = (to_argv(ep) or []) + (to_argv(cmd) or [])
            why = f"compose {p} service {name}{via} (bind mount)"
            for ln in argv_lines(argv):
                scan_line(ln, targets.get, found, why)
            hc = sub_block(own, "healthcheck")
            if hc is None:
                hc = sub_block(merged, "healthcheck")
            test = yaml_value(hc, "test") if hc else None
            if hc is not None and not [x for x in hc if not is_blank(x)]:
                # `healthcheck: *alias` - the anchor block holds `test:`
                for blk in (own, merged):
                    for ln in blk:
                        am = re.match(r"\s*healthcheck\s*:\s*\*(\S+)", ln)
                        if am:
                            test = yaml_value(anchors.get(am.group(1), []), "test")
            for ln in healthcheck_lines(test):
                scan_line(ln, targets.get, found, f"compose {p} service {name} healthcheck (bind mount)")


# ------------------------------------------------------------------ cron, docs, CI
def scan_cron(idx: Index, found: Found) -> None:
    for p in sorted(idx.mode):
        b = posixpath.basename(p)
        if not in_scope(p) or not idx.is_file(p):
            continue
        if not (b == "crontab" or b.endswith(".cron") or "/cron.d/" in p):
            continue
        for ln in idx.text(p).split("\n"):
            s = ln.strip()
            if not s or s.startswith("#") or re.match(r"[A-Z_]+=", s):
                continue
            m = re.match(r"(?:@\w+|\$\{[^}]+\}|(?:\S+\s+){4}\S+)\s+(.*)$", s)
            if m:
                scan_line(m.group(1), lambda t: t if idx.is_file(t) and not t.startswith("/") else None,
                          found, f"cron {p}")


PATHISH = re.compile(r"(?<![\w/.$-])((?:\.{1,2}/)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+|\./[A-Za-z0-9_.-]+)")
SHELL_FENCES = {"text", "sh", "bash", "shell", "console", "shell-session", "zsh"}


def doc_line(idx: Index, found: Found, line: str, base_dir: str, why: str) -> None:
    """A documented command line. Only a file with a #! line can be run by path at all,
    so a path to anything else (a config in a directory-tree listing) is a mention."""
    if re.search("[─-╿]", line):  # box-drawing: a directory-tree listing
        return
    s = re.sub(r"^\s*(?:#+|//|\*|>|\$|PS>|%)?\s*", "", line)
    for m in PATHISH.finditer(s):
        tok = m.group(1)
        order = (base_dir, "") if tok.startswith(".") else ("", base_dir)
        rp = next((c for c in (norm(order[0], tok), norm(order[1], tok)) if c and idx.is_file(c)), None)
        if rp and idx.has_shebang(rp):
            found.add(classify(s[: m.start()]), rp, why)


def scan_docs(idx: Index, found: Found) -> None:
    for p in sorted(idx.mode):
        if not in_scope(p) or not idx.is_file(p):
            continue
        d = posixpath.dirname(p)
        if p.endswith(".md"):
            fence = None
            for ln in idx.text(p).split("\n"):
                fm = re.match(r"\s*(```+|~~~+)\s*([\w+-]*)", ln)
                if fm:
                    fence = (fm.group(2).lower() or "text") if fence is None else None
                    continue
                if fence in SHELL_FENCES:
                    doc_line(idx, found, ln, d, f"docs {p} (code block)")
        elif idx.has_shebang(p):
            usage = False
            for ln in idx.text(p).split("\n")[:80]:
                body = re.sub(r"^\s*(#|//|\*)?", "", ln)
                if not usage:
                    um = re.search(r"(?i)\busage\b[^:]*:(.*)$", ln)
                    if um:
                        usage = True
                        if um.group(1).strip():
                            doc_line(idx, found, um.group(1), d, f"docs {p} (header Usage)")
                    continue
                if not body.strip() or '"""' in ln:
                    usage = False
                    continue
                doc_line(idx, found, body, d, f"docs {p} (header Usage)")


def scan_ci(idx: Index, found: Found) -> None:
    for p in sorted(idx.mode):
        if not (p.startswith(".github/workflows/") and re.search(r"\.ya?ml$", p)):
            continue
        run_ind = None
        for ln in idx.text(p).split("\n"):
            m = re.match(r"\s*(?:-\s*)?run\s*:\s*(.*)$", ln)
            if m:
                rest = m.group(1).strip()
                if rest and not rest.startswith(("|", ">")):
                    doc_line(idx, found, rest, "", f"ci {p}")
                    run_ind = None
                else:
                    run_ind = indent(ln)
                continue
            if run_ind is not None:
                if ln.strip() and indent(ln) <= run_ind:
                    run_ind = None
                    continue
                doc_line(idx, found, ln, "", f"ci {p}")


# ------------------------------------------------------------------------- driver
def derive(idx: Index) -> Found:
    idx.preload([p for p in idx.mode if in_scope(p) or p.startswith(".github/")])
    found = Found()
    contexts: dict[str, set[str]] = {}
    for p in sorted(idx.mode):
        if idx.is_file(p) and is_compose(idx, p):
            for _n, own, merged, _a in compose_services(idx, p):
                b = build_of(posixpath.dirname(p), merged + own)
                if b:
                    contexts.setdefault(b[1], set()).add(b[0])
    images = scan_dockerfiles(idx, found, contexts)
    scan_compose(idx, found, images)
    scan_cron(idx, found)
    scan_docs(idx, found)
    scan_ci(idx, found)
    return found


def render(found: Found) -> str:
    head = ("# GENERATED by scripts/checks/check_exec_bits.py --write - do not edit by hand.\n"
            "# Every tracked file the stack or its docs EXECUTE DIRECTLY (by path, no interpreter).\n"
            "# Each must be mode 100755 in the index; pre-commit refuses the commit otherwise.\n"
            "# Format: <path><TAB><why>[; <why> ...]\n")
    return head + "".join(f"{p}\t{'; '.join(sorted(w))}\n" for p, w in sorted(found.direct.items()))


def inventory_paths(text: str) -> list[str]:
    return [ln.split("\t")[0] for ln in text.split("\n") if ln and not ln.startswith("#")]


def main() -> int:
    ap = argparse.ArgumentParser(description="Derive the directly-executed set and check its modes.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true", help="regenerate " + INVENTORY)
    g.add_argument("--check", action="store_true", help="exit 1 on inventory drift or a missing x bit")
    ap.add_argument("--explain", action="store_true", help="also list the INTERPRETED uses")
    a = ap.parse_args()

    os.chdir(git("rev-parse", "--show-toplevel").decode().strip())
    idx = Index()
    found = derive(idx)
    text = render(found)

    if a.write:
        with open(INVENTORY, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"wrote {INVENTORY}: {len(found.direct)} file(s) executed directly")
        print("  stage it, and give each file the bit: git update-index --chmod=+x <file>")
        return 0

    if a.check:
        bad = False
        if not idx.is_file(INVENTORY):
            print(f"[exec-bits] {INVENTORY} is not in the index.")
            print("  Fix: python3 scripts/checks/check_exec_bits.py --write, then git add it.")
            bad = True
        elif idx.text(INVENTORY) != text:
            old, new = set(inventory_paths(idx.text(INVENTORY))), set(found.direct)
            print(f"[exec-bits] INVENTORY DRIFT: what the tree executes directly no longer matches {INVENTORY}")
            for p in sorted(new - old):
                print(f"    + {p}   ({'; '.join(sorted(found.direct[p]))})")
            for p in sorted(old - new):
                print(f"    - {p}")
            if old == new:
                print("    (the same files; a reason changed)")
            print("  Fix: python3 scripts/checks/check_exec_bits.py --write, then git add it.")
            bad = True
        missing = [p for p in sorted(found.direct) if idx.mode.get(p) != "100755"]
        if missing:
            print("[exec-bits] executed directly but NOT 100755 in the index "
                  "(Linux/macOS: 'Permission denied'):")
            for p in missing:
                print(f"    {idx.mode.get(p, '?')} {p}   ({'; '.join(sorted(found.direct[p]))})")
            print("  Fix: git update-index --chmod=+x <file>   (works on Windows too, where chmod does not)")
            bad = True
        for p in sorted(found.direct):
            if not idx.has_shebang(p):
                print(f"[exec-bits] WARNING: {p} is executed directly but has no #! line")
        if bad:
            print("[exec-bits] FAILED")
            return 1
        print(f"[exec-bits] {len(found.direct)} directly-executed file(s), all 100755, inventory current")
        return 0

    for p, why in sorted(found.direct.items()):
        print(f"{idx.mode.get(p, '?')} {p}")
        for w in sorted(why):
            print(f"         direct: {w}")
    if a.explain:
        print("\n# INTERPRETED uses (no bit needed):")
        for p, why in sorted(found.interp.items()):
            print(f"{idx.mode.get(p, '?')} {p}")
            for w in sorted(why):
                print(f"         interp: {w}")
    n_bad = sum(1 for p in found.direct if idx.mode.get(p) != "100755")
    print(f"\n{len(found.direct)} file(s) executed directly; {n_bad} of them not 100755 in the index")
    return 0


if __name__ == "__main__":
    sys.exit(main())
