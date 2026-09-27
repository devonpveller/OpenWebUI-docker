#!/usr/bin/env python3
"""Tests for the docker reclaim (ac-sysadmin-reclaim, 2026-09-27). Stdlib only, NO real docker:
every subprocess call the sysadmin code makes goes to FakeDocker, an in-memory daemon that answers
the CLI forms the code uses and records every call. Nothing here can touch a real daemon.

Run:  python scripts/sysadmin-mcp/test_docker_reclaim.py

The tests drive executor.reclaim_plan()/reclaim_execute() - the MCP tools' own entry points - so
they measure BEHAVIOUR, and they were written to fail against the code before this change
(a7f3a80), where reclaim ran `docker image prune -f` + `docker builder prune -f` and never
removed a tagged image or an anonymous volume.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
import sysadmin as sa  # noqa: E402
import executor as ex  # noqa: E402
import _testguard  # noqa: E402  - fail-closed: dead DOCKER_HOST unless set, fake call stub
_testguard.install(sa, "fake", "test_docker_reclaim")
_testguard_real_sa_run = _testguard.UNGUARDED_RUN

_passed = 0
_failed = 0
DAY = 86400


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}  {detail}")


def hexid(seed: str) -> str:
    import hashlib
    return hashlib.sha256(seed.encode()).hexdigest()


def iso(days_ago: float) -> str:
    t = time.time() - days_ago * DAY
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".123456789Z"


# ── the fake daemon ────────────────────────────────────────────────────────────
class FakeDocker:
    MUTATING = ("rm", "prune", "rmi", "stop", "kill", "create", "run", "start", "up", "down")

    def __init__(self):
        self.images = {}      # id -> {tags, created, digests, size}
        self.containers = {}  # id -> {name, image, state, volumes}
        self.volumes = {}     # name -> {created, size}
        self.cache = []       # df -v BuildCache rows + "_age_h"
        self.compose = {}     # abs compose path -> [image refs]
        self.compose_volumes = {}  # abs compose path -> {key: {"name": ...}}
        self.calls = []
        self.fail = set()     # command prefixes (tuples) to fail
        # docker CLI 29.x: `docker images` HIDES untagged images unless -a or --filter dangling=true.
        # False = the older behaviour (shows them), to prove the union does not double-count.
        self.cli29 = True
        self.hide_inspect_mounts = False    # bulk mount read misses every volume
        self.ps_volume_filter_blind = False  # `ps -a --filter volume=` finds nothing

    # builders
    def image(self, tag_or_tags, days, size=1e9, digests=()):
        tags = [tag_or_tags] if isinstance(tag_or_tags, str) else list(tag_or_tags)
        iid = "sha256:" + hexid("img:" + "|".join(tags) + str(days))
        self.images[iid] = {"tags": tags, "created": iso(days), "digests": list(digests), "size": size}
        return iid

    def container(self, name, image_id, state="running", volumes=()):
        cid = hexid("ctr:" + name)
        self.containers[cid] = {"name": name, "image": image_id, "state": state, "volumes": list(volumes)}
        return cid

    def volume(self, name, days, size=1e8):
        self.volumes[name] = {"created": iso(days).replace(".123456789", ""), "size": size}
        return name

    def mutating_calls(self):
        out = []
        for c in self.calls:
            a = c[1:] if c and c[0] == "docker" else c
            if a and a[0] in ("image", "volume", "builder", "system", "container") and len(a) > 1 \
                    and a[1] in ("rm", "prune", "remove"):
                out.append(a)
            elif a and a[0] in ("rmi",):
                out.append(a)
        return out

    # dispatcher: the signature of sysadmin._run
    def run(self, cmd, timeout=30):
        self.calls.append(list(cmd))
        for pre in self.fail:
            if tuple(cmd[:len(pre)]) == pre:
                return {"rc": 1, "out": "", "err": "injected failure"}
        if cmd[0] == "wsl":
            return {"rc": 1, "out": "", "err": "no wsl in the fake"}
        if cmd[0] != "docker":
            return {"rc": 127, "out": "", "err": "not found"}
        a = cmd[1:]
        try:
            return self._docker(a)
        except Exception as e:  # noqa: BLE001
            return {"rc": 1, "out": "", "err": f"fake: {e}"}

    def _ok(self, out=""):
        return {"rc": 0, "out": out, "err": ""}

    def _users(self, iid):
        return [c for c in self.containers.values() if c["image"] == iid]

    def _vol_users(self, name):
        return [cid for cid, c in self.containers.items() if name in c["volumes"]]

    def _docker(self, a):
        if a[0] == "ps":
            flt = [a[i + 1] for i, x in enumerate(a) if x == "--filter"]
            rows = [(cid, c) for cid, c in self.containers.items() if "-a" in a or c["state"] == "running"]
            for f in flt:
                k, v = f.split("=", 1)
                if k == "volume":
                    rows = [] if self.ps_volume_filter_blind else [(cid, c) for cid, c in rows if v in c["volumes"]]
            if "-q" in a:
                return self._ok("".join(cid + "\n" for cid, _ in rows))
            fmt = a[a.index("--format") + 1]
            return self._ok("".join(fmt.replace("{{.Names}}", c["name"]).replace("{{.ID}}", cid[:12])
                                    + "\n" for cid, c in rows))
        if a[0] == "inspect":
            out = []
            for cid in a[3:]:
                c = self.containers[cid]
                vols = [] if self.hide_inspect_mounts else c["volumes"]
                out.append(f"{cid}|/{c['name']}|{c['image']}|{c['state']}|" + "".join(v + "," for v in vols))
            return self._ok("\n".join(out) + "\n")
        if a[0] == "images":
            flt = [a[i + 1] for i, x in enumerate(a) if x == "--filter"]
            if "dangling=true" in flt:
                ids = [i for i, im in self.images.items() if not im["tags"]]
            elif "-a" in a or not self.cli29:
                ids = list(self.images)
            else:
                ids = [i for i, im in self.images.items() if im["tags"]]
            return self._ok("".join(i + "\n" for i in ids))
        if a[:2] == ["image", "inspect"]:
            out = []
            for iid in a[4:]:
                im = self.images[iid]
                out.append(f"{iid}|{im['created']}|{json.dumps(im['tags'])}|{json.dumps(im['digests'])}")
            return self._ok("\n".join(out) + "\n")
        if a[:2] == ["volume", "ls"]:
            return self._ok("".join(n + "\n" for n in self.volumes))
        if a[:2] == ["volume", "inspect"]:
            out, missing = [], []
            for n in a[4:]:
                if n in self.volumes:
                    out.append(f"{n}|{self.volumes[n]['created']}")
                else:
                    missing.append(n)
            return {"rc": 1 if missing else 0, "out": "\n".join(out) + ("\n" if out else ""),
                    "err": "".join(f"no such volume: {m}\n" for m in missing)}
        if a[:2] == ["system", "df"]:
            img_b = sum(i["size"] for i in self.images.values())
            vol_b = sum(v["size"] for v in self.volumes.values())
            bc_b = sum(r["_bytes"] for r in self.cache)
            if "-v" in a:
                return self._ok(json.dumps({
                    "Images": [{"ID": k, "UniqueSize": f"{v['size']:.0f}B", "Size": f"{v['size']:.0f}B"}
                               for k, v in self.images.items()],
                    "Volumes": [{"Name": k, "Size": f"{v['size']:.0f}B"} for k, v in self.volumes.items()],
                    "BuildCache": [{k: v for k, v in r.items() if not k.startswith("_")} for r in self.cache],
                    "Containers": []}))
            fmt = a[a.index("--format") + 1]
            rows = []
            for typ, b in (("Images", img_b), ("Containers", 0), ("Local Volumes", vol_b), ("Build Cache", bc_b)):
                rows.append(fmt.replace("{{.Type}}", typ).replace("{{.Size}}", f"{b:.0f}B")
                            .replace("{{.TotalCount}}", "1").replace("{{.Active}}", "1")
                            .replace("{{.Reclaimable}}", "0B"))
            return self._ok("\n".join(rows) + "\n")
        if a[:2] == ["image", "rm"]:
            ref = a[-1]
            force = "-f" in a or "--force" in a
            iid = ref if ref in self.images else next((k for k, v in self.images.items() if ref in v["tags"]), None)
            if iid is None:
                return {"rc": 1, "out": "", "err": f"No such image: {ref}"}
            if self._users(iid) and not force:
                return {"rc": 1, "out": "", "err": "conflict: image is being used by a container"}
            if ref != iid:
                self.images[iid]["tags"].remove(ref)
                if self.images[iid]["tags"]:
                    return self._ok(f"Untagged: {ref}\n")
            del self.images[iid]
            return self._ok(f"Deleted: {iid}\n")
        if a[:2] == ["volume", "rm"]:
            n = a[-1]
            if n not in self.volumes:
                return {"rc": 1, "out": "", "err": "no such volume"}
            if self._vol_users(n):
                return {"rc": 1, "out": "", "err": "volume is in use"}
            del self.volumes[n]
            return self._ok(n + "\n")
        if a[:2] == ["image", "prune"]:  # emulated faithfully so a regression SHOWS in state
            every = "-a" in a or "--all" in a
            for iid in list(self.images):
                if not self._users(iid) and (every or not self.images[iid]["tags"]):
                    del self.images[iid]
            return self._ok("Total reclaimed space: 0B\n")
        if a[:2] == ["volume", "prune"] or a[:2] == ["system", "prune"]:
            for n in list(self.volumes):
                if not self._vol_users(n):
                    del self.volumes[n]
            if a[0] == "system":
                for iid in list(self.images):
                    if not self._users(iid):
                        del self.images[iid]
            return self._ok("Total reclaimed space: 0B\n")
        if a[:2] == ["builder", "prune"]:
            until = None
            if "--filter" in a:
                m = re.fullmatch(r"until=(\d+)h", a[a.index("--filter") + 1])
                until = int(m.group(1)) if m else None
            if "-af" in a or "-a" in a or "--all" in a:
                self.cache = [r for r in self.cache
                              if r["InUse"] == "true" or (until is not None and r["_age_h"] < until)]
            return self._ok("Total:\t0B\n")
        if a[0] == "compose":
            path = a[a.index("-f") + 1]
            if path not in self.compose:
                return {"rc": 1, "out": "", "err": f"open {path}: no such file"}
            if "--format" in a and a[a.index("--format") + 1] == "json":
                return self._ok(json.dumps({"services": {}, "volumes": self.compose_volumes.get(path, {})}))
            return self._ok("".join(r + "\n" for r in self.compose[path]))
        if a[0] == "exec":
            return {"rc": 1, "out": "", "err": "container not running"}
        return {"rc": 1, "out": "", "err": f"fake: unhandled {a}"}


# ── harness ────────────────────────────────────────────────────────────────────
class Env:
    """A temp state dir + manifest + keep-list + config, with sa._run pointed at a FakeDocker."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="sysadm-reclaim-")
        self.fake = FakeDocker()
        self.compose_file = os.path.join(self.tmp, "plane-a.yml")
        open(self.compose_file, "w").write("services: {}\n")
        self.manifest = os.path.join(self.tmp, "stack.manifest.toml")
        open(self.manifest, "w").write('[planes.a]\ncompose = "plane-a.yml"\n')
        self.fake.compose[self.compose_file] = []
        self.keep = os.path.join(self.tmp, "image-keep.txt")
        open(self.keep, "w").write("# test keep-list\n*:local   # pin\n*-prev-*  # rollback\n")
        self.posts = []

    def __enter__(self):
        self._saved = (sa._run, sa._CONFIG_CACHE, ex._STATE, ex._AUDIT, sys.modules.get("mm_post"))
        sa._run = self.fake.run
        sa._CONFIG_CACHE = {
            "vhdx_path": os.path.join(self.tmp, "none.vhdx"), "system_drive": "C:", "data_drive": "D:",
            "docker_desktop_mount": "/mnt/x", "ao_workers": [], "protected_volume_substrings": [],
            "thresholds": {"container_log_warn_gb": 2},
            "image_keep_file": self.keep, "compose_manifest": self.manifest,
        }
        ex._STATE = os.path.join(self.tmp, "state")
        ex._AUDIT = os.path.join(ex._STATE, "audit.jsonl")
        stub = types.ModuleType("mm_post")
        stub.post = lambda m: self.posts.append(m) or True
        sys.modules["mm_post"] = stub
        return self

    def __exit__(self, *exc):
        sa._run, sa._CONFIG_CACHE, ex._STATE, ex._AUDIT, mm = self._saved
        if mm is not None:
            sys.modules["mm_post"] = mm
        else:
            sys.modules.pop("mm_post", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False


def tags(fake):
    return sorted(t for im in fake.images.values() for t in im["tags"])


def skipped_reason(plan, cat, key, value):
    """The skip reason the plan gives for an item, or None if not listed as skipped."""
    for s in (plan.get("docker") or {}).get(cat, {}).get("skipped", []):
        if s.get(key) == value:
            return s.get("reason")
    return None


def listed_refs(plan):
    return sorted(r["ref"] for r in (plan.get("docker") or {}).get("images", {}).get("remove", []))


def listed_vols(plan):
    return sorted(r["name"] for r in (plan.get("docker") or {}).get("volumes", {}).get("remove", []))


def run_case(fn):
    print(f"\n{fn.__name__} - {fn.__doc__.strip().splitlines()[0]}")
    try:
        fn()
    except Exception as e:  # noqa: BLE001 - a crash is a FAIL, never a skip
        check(f"{fn.__name__} ran without raising", False, f"{type(e).__name__}: {e}")
        traceback.print_exc(limit=3)


# ── cases ──────────────────────────────────────────────────────────────────────
def t01_base_red_scenario():
    """the reported incident: an unused 30-day-old TAGGED image and an orphaned anonymous volume"""
    with Env() as env:
        f = env.fake
        f.image("old-tool:1.0", 30, size=3e9)
        anon = f.volume(hexid("orphan"), 30, size=5e8)
        p = ex.reclaim_plan()
        check("plan lists the unused tagged image", listed_refs(p) == ["old-tool:1.0"], str(listed_refs(p)))
        check("plan lists the orphaned anonymous volume", listed_vols(p) == [anon], str(listed_vols(p)))
        r = ex.reclaim_execute(p["confirm_token"])
        check("execute not refused", not r.get("refused"), str(r)[:200])
        check("tagged image removed", tags(f) == [], str(tags(f)))
        check("anonymous volume removed", anon not in f.volumes)
        fg = r.get("freed_gb", {})
        check("freed bytes reported per category (images 3.0 GB)", fg.get("images") == 3.0, str(fg))
        check("freed bytes reported per category (anon volumes 0.5 GB)", fg.get("anon_volumes") == 0.5, str(fg))


def _seed_every_category(f, env):
    ids = {}
    ids["running"] = f.image("svc-running:1", 30)
    f.container("c-running", ids["running"], "running")
    ids["stopped"] = f.image("svc-stopped:1", 30)
    f.container("c-stopped", ids["stopped"], "exited")
    ids["compose"] = f.image("compose-named:2", 30)
    f.compose[env.compose_file] = ["compose-named:2", "docker.io/library/redis:7-alpine", "compose-bare",
                                   "ghcr.io/x/pinned@sha256:" + "d" * 64]
    ids["redis"] = f.image("redis:7-alpine", 30)
    ids["digest"] = f.image("ghcr.io/x/pinned:main", 30, digests=["ghcr.io/x/pinned@sha256:" + "d" * 64])
    ids["keep"] = f.image("pinned-build:local", 30)
    ids["prev"] = f.image("wiki:local-prev-20260828", 30)
    ids["new"] = f.image("fresh-build:wt-x", 3)
    ids["removable"] = f.image("stale-test:wt-old", 30, size=2e9)
    ids["latest"] = f.image("stale-latest:latest", 30, size=1e9)
    ids["bare"] = f.image("compose-bare:latest", 30)  # compose names it WITHOUT a tag
    ids["pair"] = f.image(["dual:local", "dual:experiment"], 30)
    ids["dangling"] = f.image([], 30, size=4e8)
    ids["dangling_used"] = f.image([], 31)
    f.container("c-dangling", ids["dangling_used"], "exited")
    v = {}
    v["run"] = f.volume(hexid("v-run"), 30)
    f.container("c-vrun", ids["running"], "running", volumes=[v["run"]])
    v["stop"] = f.volume(hexid("v-stop"), 30)
    f.container("c-vstop", ids["stopped"], "exited", volumes=[v["stop"]])
    v["new"] = f.volume(hexid("v-new"), 2)
    v["old"] = f.volume(hexid("v-old"), 30, size=7e8)
    v["named"] = f.volume("frontend_openwebui-data", 90)
    v["named_upper"] = f.volume(hexid("v-upper").upper(), 30)  # not lowercase hex: not anonymous
    f.cache = [
        {"ID": "old1", "InUse": "false", "LastUsedAt": iso(10), "CreatedAt": iso(10), "Size": "3000000B",
         "_age_h": 240, "_bytes": 3e6},
        {"ID": "new1", "InUse": "false", "LastUsedAt": iso(1), "CreatedAt": iso(1), "Size": "5000000B",
         "_age_h": 24, "_bytes": 5e6},
    ]
    return ids, v


def t02_every_category():
    """every category from the anchor: execute removes EXACTLY the removable set; each skip has its reason"""
    with Env() as env:
        f = env.fake
        ids, v = _seed_every_category(f, env)
        p = ex.reclaim_plan()
        want_refs = sorted(["stale-test:wt-old", "stale-latest:latest", ids["dangling"]])
        check("plan lists exactly the removable image refs", listed_refs(p) == want_refs, str(listed_refs(p)))
        check("plan lists exactly the removable anon volume", listed_vols(p) == [v["old"]], str(listed_vols(p)))
        exp = {
            "svc-running:1": "in use", "svc-stopped:1": "in use", "compose-named:2": "compose-named",
            "redis:7-alpine": "compose-named", "compose-bare:latest": "compose-named",
            "ghcr.io/x/pinned:main": "compose-named by digest",
            "pinned-build:local": "keep-list", "wiki:local-prev-20260828": "keep-list",
            "fresh-build:wt-x": "too new", "dual:experiment": "shares image id with protected tag dual:local",
            "dual:local": "keep-list", ids["dangling_used"]: "in use",
        }
        for ref, want in exp.items():
            got = skipped_reason(p, "images", "ref", ref)
            check(f"image skip reason {ref[:30]}: {want}", bool(got) and got.startswith(want), str(got))
        check("stopped-container image says (exited)", "(exited)" in (skipped_reason(p, "images", "ref", "svc-stopped:1") or ""))
        for key, want in (("run", "in use"), ("stop", "in use"), ("new", "too new"),
                          ("named", "named volume"), ("named_upper", "named volume")):
            got = skipped_reason(p, "volumes", "name", v[key])
            check(f"volume skip reason {key}: {want}", bool(got) and got.startswith(want), str(got))
        before_tags = tags(f)
        r = ex.reclaim_execute(p["confirm_token"])
        check("execute not refused", not r.get("refused"), str(r)[:200])
        gone = sorted(set(before_tags) - set(tags(f)))
        check("removed tags == exactly the two removable tags", gone == ["stale-latest:latest", "stale-test:wt-old"], str(gone))
        check("unused dangling image removed", ids["dangling"] not in f.images)
        check("dangling image used by a stopped container kept", ids["dangling_used"] in f.images)
        check("dual:local's image id kept (both tags)", f.images.get(ids["pair"], {}).get("tags") == ["dual:local", "dual:experiment"])
        check("volumes: only the removable anon volume gone",
              sorted(set(v.values()) - set(f.volumes)) == [v["old"]], str(sorted(set(v.values()) - set(f.volumes))))
        check("named volume untouched", v["named"] in f.volumes)
        check("recent build cache kept, old removed", [c["ID"] for c in f.cache] == ["new1"], str(f.cache))


def t03_revalidation():
    """an item that becomes in-use (or changes) between plan and execute is skipped, never removed"""
    with Env() as env:
        f = env.fake
        a = f.image("stale-a:1", 30)
        f.image("stale-b:1", 30)
        c_img = f.image("stale-c:1", 30)
        va = f.volume(hexid("va"), 30)
        vb = f.volume(hexid("vb"), 30)
        p = ex.reclaim_plan()
        check("plan lists all three images", listed_refs(p) == ["stale-a:1", "stale-b:1", "stale-c:1"], str(listed_refs(p)))
        check("plan lists both volumes", listed_vols(p) == sorted([va, vb]))
        # between plan and execute: a container starts on stale-a, a STOPPED one mounts vb,
        # and stale-c is re-pointed at a new build
        f.container("late-runner", a, "running")
        f.container("late-stopped", f.image("other:local", 30), "created", volumes=[vb])
        f.images[c_img]["tags"].remove("stale-c:1")
        f.image("stale-c:1", 20)
        r = ex.reclaim_execute(p["confirm_token"])
        check("execute not refused", not r.get("refused"), str(r)[:200])
        check("stale-a (now in use) NOT removed", "stale-a:1" in tags(f))
        check("stale-c (re-pointed) NOT removed", "stale-c:1" in tags(f))
        check("stale-b removed", "stale-b:1" not in tags(f))
        check("vb (now referenced) NOT removed", vb in f.volumes)
        check("va removed", va not in f.volumes)
        sk = {s.get("ref"): s["reason"] for s in r.get("docker", {}).get("images", {}).get("skipped", [])}
        check("stale-a reported skipped: in use", sk.get("stale-a:1", "").startswith("in use"), str(sk))
        check("stale-c reported skipped: changed since plan", sk.get("stale-c:1", "").startswith("changed since plan"), str(sk))
        skv = {s.get("name"): s["reason"] for s in r.get("docker", {}).get("volumes", {}).get("skipped", [])}
        check("vb reported skipped: in use", skv.get(vb, "").startswith("in use"), str(skv))
        new_tag = f.image("brand-new-unused:1", 30)  # appeared after the plan: never in the listed set
        check("an item NOT in the plan is never removed", new_tag in f.images)


def t04_token():
    """confirm_token covers exactly the listed set; unknown/edited/expired tokens are refused and mutate nothing"""
    with Env() as env:
        f = env.fake
        f.image("stale:1", 30)
        p1 = ex.reclaim_plan()
        p2 = ex.reclaim_plan()
        check("same listed set -> same token", p1["confirm_token"] == p2["confirm_token"])
        f.volume(hexid("extra"), 30)
        p3 = ex.reclaim_plan()
        check("different listed set -> different token", p3["confirm_token"] != p1["confirm_token"])
        n0 = len(f.mutating_calls())
        for bad in (None, "", "deadbeef", "../../x"):
            r = ex.reclaim_execute(bad)
            check(f"token {bad!r} refused", r.get("refused") is True, str(r)[:120])
        tokfile = os.path.join(ex._STATE, "reclaim-plans", p3["confirm_token"] + ".json")
        rec = json.load(open(tokfile))
        rec["plan"]["actions"]["docker"]["volumes"].append(hexid("smuggled"))
        json.dump(rec, open(tokfile, "w"))
        r = ex.reclaim_execute(p3["confirm_token"])
        check("a plan file edited after issue is refused", r.get("refused") is True)
        p4 = ex.reclaim_plan()
        tokfile = os.path.join(ex._STATE, "reclaim-plans", p4["confirm_token"] + ".json")
        rec = json.load(open(tokfile))
        rec["issued"] -= 25 * 3600
        json.dump(rec, open(tokfile, "w"))
        r = ex.reclaim_execute(p4["confirm_token"])
        check("an expired token (25 h > 24 h) is refused", r.get("refused") is True)
        check("no refused call mutated anything", len(f.mutating_calls()) == n0, str(f.mutating_calls()))
        check("image still present after refusals", "stale:1" in tags(f))


def t05_forbidden_shapes():
    """no code path can run volume prune / image prune -a / system prune (two guards + source grep)"""
    import docker_reclaim as dr
    bad = [["volume", "prune"], ["volume", "prune", "-f"], ["image", "prune", "-a"], ["image", "prune", "-af"],
           ["image", "prune", "-f"], ["system", "prune", "-af", "--volumes"], ["rmi", "-f", "x"],
           ["image", "rm", "-f", "x"], ["volume", "rm", "frontend_openwebui-data"],
           ["volume", "rm", "-f", hexid("x")], ["builder", "prune", "-af"],
           ["builder", "prune", "-af", "--filter", "until=0"], ["container", "prune"],
           ["volume", "rm", hexid("nl") + "\n"], ["builder", "prune", "-af", "--filter", "until=0h"],
           ["builder", "prune", "-af", "--filter", "until=23h"]]
    # (1) the upper guard, driven INSIDE Env: even if it were broken, the calls land in the fake
    with Env() as env:
        for argv in bad:
            check(f"refused shape {' '.join(argv)[:40]!r}", not dr.allowed_mutation(argv))
            try:
                dr._mutate(argv)
                check(f"_mutate raises on {' '.join(argv)[:40]!r}", False)
            except ValueError:
                check(f"_mutate raises on {' '.join(argv)[:40]!r}", True)
        check("the fake daemon received NO command from any forbidden shape", env.fake.calls == [],
              str(env.fake.calls[:3]))
    for argv in (["image", "rm", "x:1"], ["volume", "rm", hexid("y")],
                 ["builder", "prune", "-af", "--filter", "until=168h"]):
        check(f"allowed shape {' '.join(argv)[:40]}", dr.allowed_mutation(argv))
    # (2) outside Env, sysadmin._run is the test guard's stub: a leak raises, never runs
    saved_log = os.environ.pop("ACSR_CALL_LOG", None)  # a deliberate probe, not an escape
    try:
        sa._run(["docker", "ps"])
        check("outside Env, sysadmin._run raises (no real docker)", False)
    except _testguard.RealDockerCall:
        check("outside Env, sysadmin._run raises (no real docker)", True)
    finally:
        if saved_log:
            os.environ["ACSR_CALL_LOG"] = saved_log
    # (3) the LOWEST-level deny-list inside sysadmin._run, with subprocess.run replaced by a recorder so
    #     a broken deny-list would record a call instead of running one
    real_run = _testguard_real_sa_run
    started = []

    class _P:
        returncode, stdout, stderr = 0, "", ""

    saved = sa.subprocess.run
    sa.subprocess.run = lambda cmd, *a, **k: started.append(list(cmd)) or _P()
    try:
        deny = bad + [["compose", "-f", "x.yml", "down", "-v"], ["-H", "tcp://elsewhere", "ps"],
                      ["system", "info"], ["volume", "remove", hexid("z"), "frontend_openwebui-data"]]
        for argv in deny:
            r = real_run(["docker"] + argv)
            check(f"deny-list refuses docker {' '.join(argv)[:40]!r}", r["rc"] == 126 and "deny-list" in r["err"], str(r))
        check("deny-list: none of those started a process", started == [], str(started[:3]))
        for argv in (["ps", "-a"], ["system", "df"], ["builder", "prune", "-af", "--filter", "until=168h"],
                     ["volume", "rm", hexid("y")], ["image", "rm", "x:1"], ["compose", "-f", "x.yml", "config", "--images"]):
            real_run(["docker"] + argv)
        check("deny-list passes the six shapes the code needs", len(started) == 6, str(started))
    finally:
        sa.subprocess.run = saved
    # source grep: code lines (not comments/docstrings) in every file that runs docker for the sysadmin
    files = [os.path.join(_HERE, n) for n in sorted(os.listdir(_HERE))
             if n.endswith((".py", ".ps1")) and not n.startswith("test")]
    files += [os.path.join(_REPO, "scripts", "maintenance", n)
              for n in sorted(os.listdir(os.path.join(_REPO, "scripts", "maintenance"))) if n.endswith(".ps1")]
    # .py: every docker call is an argv LIST (sysadmin._run never uses a shell), so the command
    # shape is adjacent quoted tokens. .ps1: the docker CLI line itself.
    py_pats = [re.compile(r"['\"](volume|system|container)['\"]\s*,\s*['\"]prune['\"]"),
               re.compile(r"['\"]image['\"]\s*,\s*['\"]prune['\"]\s*,\s*['\"](-a|--all|-af|-fa)"),
               re.compile(r"[\[,]\s*['\"]rmi['\"]")]  # rmi as an argv element, not `a[0] == "rmi"`
    ps_pats = [re.compile(r"docker\s+(volume|system|container)\s+prune", re.I),
               re.compile(r"docker\s+image\s+prune\s+(-a|--all|-af|-fa)", re.I),
               re.compile(r"docker\s+rmi\b", re.I)]
    hits = []
    for fp in files:
        pats = py_pats if fp.endswith(".py") else ps_pats
        for i, ln in enumerate(io.open(fp, encoding="utf-8", errors="replace").read().splitlines(), 1):
            s = ln.strip()
            if s.startswith("#"):
                continue
            if any(p.search(s) for p in pats):
                hits.append(f"{os.path.basename(fp)}:{i}: {s[:90]}")
    # the grep must be able to see a violation: plant one per pattern family and expect it found
    planted = ['    sa._docker(["volume", "prune", "-f"])', '    _run(["docker", "image", "prune", "-a"])',
               '    sa._docker(["system", "prune"])', '    sa._docker(["rmi", "-f", ref])']
    blind = [x for x in planted if not any(p.search(x.strip()) for p in py_pats)]
    blind += [x for x in ("docker volume prune -f", "docker image prune -a", "docker system prune -af")
              if not any(p.search(x) for p in ps_pats)]
    check("the grep catches planted violations", not blind, str(blind))
    check(f"no forbidden prune in code ({len(files)} files scanned)", not hits and len(files) >= 10, "; ".join(hits))


def t06_plan_is_read_only_and_mutations_are_approved_shapes():
    """reclaim_plan makes no mutating call; every mutation execute makes is an approved shape"""
    import docker_reclaim as dr
    with Env() as env:
        f = env.fake
        _seed_every_category(f, env)
        ex.reclaim_plan()
        check("plan made zero mutating calls", f.mutating_calls() == [], str(f.mutating_calls()))
        p = ex.reclaim_plan()
        ex.reclaim_execute(p["confirm_token"])
        muts = f.mutating_calls()
        check("execute made mutating calls", len(muts) >= 4, str(muts))
        check("every mutating call is an approved shape", all(dr.allowed_mutation(m) for m in muts), str(muts))
        check("builder prune ran as `-af --filter until=168h`",
              ["builder", "prune", "-af", "--filter", "until=168h"] in muts, str(muts))


def t07_fail_closed():
    """an unreadable compose render / keep-list / container list removes nothing in the affected category"""
    with Env() as env:
        f = env.fake
        f.image("stale:1", 30)
        vol = f.volume(hexid("o"), 30)
        f.compose.clear()  # render of plane a now fails
        p = ex.reclaim_plan()
        check("compose failure -> images category disabled with reason",
              "compose" in str(p["docker"]["images"].get("disabled_reason")), str(p["docker"]["images"].get("disabled_reason")))
        check("compose failure -> no image listed", listed_refs(p) == [])
        check("compose failure -> volumes disabled too (declared names unknown)",
              listed_vols(p) == [] and "compose" in str(p["docker"]["volumes"].get("disabled_reason")),
              str(p["docker"]["volumes"].get("disabled_reason")))
        check("compose failure -> the orphan volume is still there", vol in f.volumes)
    with Env() as env:
        env.fake.image("stale:1", 30)
        os.remove(env.keep)
        p = ex.reclaim_plan()
        check("keep-list missing -> images disabled", "keep-list" in str(p["docker"]["images"].get("disabled_reason")))
        check("keep-list missing -> no image listed", listed_refs(p) == [])
    with Env() as env:
        env.fake.image("stale:1", 30)
        env.fake.volume(hexid("o"), 30)
        env.fake.fail.add(("docker", "ps", "-a", "-q", "--no-trunc"))
        p = ex.reclaim_plan()
        check("container list failure -> nothing listed", listed_refs(p) == [] and listed_vols(p) == [])
    with Env() as env:
        env.fake.image("stale:1", 30)
        p = ex.reclaim_plan()
        env.fake.compose.clear()
        r = ex.reclaim_execute(p["confirm_token"])
        check("compose failure at EXECUTE -> listed image skipped, not removed",
              "stale:1" in tags(env.fake) and "re-validation failed" in json.dumps(r.get("docker", {})))


def t08_thresholds_configurable():
    """image 14 d / volume 7 d / cache 168 h are defaults; config and overrides move them"""
    with Env() as env:
        f = env.fake
        f.image("ten-days:1", 10)
        vol = f.volume(hexid("five"), 5)
        p = ex.reclaim_plan()
        check("defaults: 10-day image too new", listed_refs(p) == [])
        check("defaults: 5-day volume too new", listed_vols(p) == [])
        sa._CONFIG_CACHE["thresholds"].update({"image_min_age_days": 7, "anon_volume_min_age_days": 3,
                                               "builder_keep_hours": 24})
        p = ex.reclaim_plan()
        check("config 7 d: 10-day image listed", listed_refs(p) == ["ten-days:1"])
        check("config 3 d: 5-day volume listed", listed_vols(p) == [vol])
        check("config 24 h: build-cache command", p["docker"]["build_cache"]["command"].endswith("until=24h"))
        sa._CONFIG_CACHE["thresholds"].clear()
        p = ex.reclaim_plan(image_min_age_days=0, anon_volume_min_age_days=0)
        check("override 0: both listed in the plan", listed_refs(p) == ["ten-days:1"] and listed_vols(p) == [vol])
        r = ex.reclaim_execute(p["confirm_token"])
        check("execute uses the STRICTER config (14 d / 7 d): nothing removed",
              tags(f) == ["ten-days:1"] and vol in f.volumes, str(r)[:200])
        check("... and says too new", "too new" in json.dumps(r.get("docker", {})))
        sa._CONFIG_CACHE["thresholds"].update({"image_min_age_days": 7, "anon_volume_min_age_days": 3})
        p = ex.reclaim_plan()
        ex.reclaim_execute(p["confirm_token"])
        check("config-lowered thresholds apply at execute", tags(f) == [] and vol not in f.volumes)


def t09_auto_path():
    """the low-disk path (disk-guard.ps1 -> auto_reclaim.py) runs this reclaim and reports per-category bytes"""
    import auto_reclaim
    with Env() as env:
        f = env.fake
        f.image("stale:1", 30, size=2e9)
        vol = f.volume(hexid("o"), 30, size=1e9)
        rc, line, res = auto_reclaim.run()
        check("auto run rc 0", rc == 0, line)
        check("auto run removed the image and the volume", tags(f) == [] and vol not in f.volumes)
        check("summary line has per-category bytes", line.startswith("RECLAIM images 2.00 GB") and "anon volumes 1.00 GB" in line
              and "build cache" in line, line)
        check("auto path does not post to Mattermost itself (disk-guard does)", env.posts == [])
    import docker_reclaim as dr
    grew = dr.summary_line({"images": 5e6, "build_cache": -2e6, "volumes": None}, {})
    check("summary line: MB units, growth and n/a stated plainly",
          "images 5.0 MB" in grew and "build cache 0 B (grew 2.0 MB)" in grew and "anon volumes n/a" in grew, grew)
    src = io.open(os.path.join(_REPO, "scripts", "maintenance", "disk-guard.ps1"), encoding="utf-8").read()
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    check("disk-guard.ps1 runs auto_reclaim.py", "auto_reclaim.py" in code)
    check("disk-guard.ps1 no longer runs `docker image prune`/`builder prune` itself",
          "image prune" not in code and "builder prune" not in code)
    check("disk-guard.ps1 alert carries the reclaim line", "$reclaimLine" in code and "$msg" in code
          and code.index("$reclaimLine") < code.rindex("$msg"))
    check("disk-guard.ps1 is ASCII", all(ord(ch) < 128 for ch in src))


def t10_render():
    """the MCP renderings show counts, GB, top entries, skip reasons and per-category freed bytes"""
    import server
    with Env() as env:
        f = env.fake
        _seed_every_category(f, env)
        p = ex.reclaim_plan()
        txt = server.render_reclaim_plan(p)
        for needle in ("stale-test:wt-old", "keep-list", "compose-named", "too new", "in use",
                       "named volume", "until=168h", p["confirm_token"]):
            check(f"plan render mentions {needle!r}", needle in txt, txt[:300])
        r = ex.reclaim_execute(p["confirm_token"])
        out = server.render_reclaim_result(r)
        check("result render has per-category freed line", "images" in out and "anon volumes" in out
              and "build cache" in out, out[:300])


def t11_docs_match():
    """charter.md / README.md / tool descriptions state the new rules and drop the old ones"""
    import server
    charter = io.open(os.path.join(_HERE, "charter.md"), encoding="utf-8").read()
    readme = io.open(os.path.join(_HERE, "README.md"), encoding="utf-8").read()
    desc = server.TOOLS["reclaim_plan"]["description"] + " " + server.TOOLS["reclaim_execute"]["description"]
    for name, doc in (("charter", charter), ("README", readme), ("tool descriptions", desc)):
        low = doc.lower()
        check(f"{name}: names anonymous volumes", "anonymous" in low)
        check(f"{name}: named volumes never removed", "named volume" in low and "never" in low)
        check(f"{name}: keep-list named", "image-keep.txt" in doc or "keep-list" in low)
        check(f"{name}: 14-day image rule", "14 days" in doc, name)
        check(f"{name}: 7-day volume rule", "7 days" in doc, name)
        check(f"{name}: build cache keeps a week", "168h" in doc, name)
        check(f"{name}: in-use includes STOPPED containers", "stopped" in low, name)
        check(f"{name}: no old 'never touches volumes' claim", "never touches volumes" not in low)
        check(f"{name}: no old dangling-only claim", "dangling-only" not in low and "dangling images/build cache" not in low)
    check("charter keeps `docker volume prune` forbidden", "volume prune" in charter)
    for tool in ("reclaim_plan", "reclaim_execute"):
        d = server.TOOLS[tool]["description"].lower()
        check(f"{tool} description: untagged images removed by id", "untagged" in d and "by id" in d, d[:200])


def t12_host_keep_list_seed():
    """the tracked keep-list parses and protects the host's pin conventions"""
    import docker_reclaim as dr
    keep = dr.load_keep(os.path.join(_HERE, "image-keep.txt"))
    check("keep-list has patterns, each with a reason", len(keep) >= 5 and all(r and r != "(no reason given)" for _, r in keep))
    for ref in ("openwebui:local", "open_notebook:iks-prepatch-20260829", "openbrain-wiki:local-prev-20260829",
                "open_notebook:iks-pre-ondemand-audio", "open_notebook:iks-podcastpatch-20260829"):
        check(f"keep-list protects {ref}", dr.keep_match(ref, keep) is not None)
    for ref in ("openbrain-mcp-server:wt-u6recall", "python:3.12-slim"):
        check(f"keep-list does NOT protect {ref}", dr.keep_match(ref, keep) is None)


def t13_untagged_on_cli29():
    """CLI 29 hides untagged images from `docker images`: they are still found, and digest pins protect them"""
    with Env() as env:
        f = env.fake
        pin = "ghcr.io/x/alerter@sha256:" + "a" * 64
        f.compose[env.compose_file] = ["ghcr.io/x/alerter@sha256:" + "a" * 64]
        dang = f.image([], 30, size=4e8)                       # rebuild leftover, unused
        dused = f.image([], 31)                                # untagged, only a STOPPED container uses it
        f.container("c-stopped-untagged", dused, "exited")
        pinned = f.image([], 32, digests=[pin])                # pulled by digest only, compose pins it, no container
        loose = f.image([], 33, digests=["ghcr.io/x/other@sha256:" + "b" * 64])  # pulled by digest, named nowhere
        f.image("tagged:1", 30)
        p = ex.reclaim_plan()
        check("plain `docker images` really hides them in this fake (CLI 29)",
              all(i not in f.run(["docker", "images", "--no-trunc", "--format", "{{.ID}}"])["out"]
                  for i in (dang, dused, pinned, loose)))
        check("plan lists the unused dangling image and the unnamed digest-pulled one (by id) + the tag",
              listed_refs(p) == sorted([dang, loose, "tagged:1"]), str(listed_refs(p)))
        check("untagged image of a stopped container: skipped in use",
              (skipped_reason(p, "images", "ref", dused) or "").startswith("in use"), str(skipped_reason(p, "images", "ref", dused)))
        check("digest-pinned untagged image with NO container: skipped compose-named by digest",
              (skipped_reason(p, "images", "ref", pinned) or "").startswith("compose-named by digest"),
              str(skipped_reason(p, "images", "ref", pinned)))
        ex.reclaim_execute(p["confirm_token"])
        check("execute: dangling + unnamed digest image gone; stopped-used + pinned kept",
              dang not in f.images and loose not in f.images and dused in f.images and pinned in f.images)
        muts = f.mutating_calls()
        check("untagged removals were by explicit id", ["image", "rm", dang] in muts and ["image", "rm", loose] in muts, str(muts))
    with Env() as env:
        env.fake.cli29 = False  # an older CLI that lists untagged images in the plain listing too
        dang = env.fake.image([], 30)
        p = ex.reclaim_plan()
        check("older CLI: the union lists the dangling image once", listed_refs(p) == [dang], str(listed_refs(p)))


def t14_empty_keep_list():
    """a READABLE keep-list with no pattern fails closed (removes no image), not 'protect nothing'"""
    with Env() as env:
        env.fake.image("pin:local", 30)
        env.fake.image("stale:1", 30)
        open(env.keep, "w").write("# only comments\n\n   \n")
        p = ex.reclaim_plan()
        check("empty keep-list -> images category OFF", "no patterns" in str(p["docker"]["images"].get("disabled_reason")),
              str(p["docker"]["images"].get("disabled_reason")))
        check("empty keep-list -> nothing listed", listed_refs(p) == [])
        open(env.keep, "w").write("")
        check("0-byte keep-list -> OFF too", listed_refs(ex.reclaim_plan()) == [])


def t15_forged_plan_thresholds():
    """a stored plan re-hashed with lowered thresholds cannot remove a too-new image or volume"""
    with Env() as env:
        f = env.fake
        f.image("two-days:1", 2)
        vol = f.volume(hexid("young"), 1)
        p = ex.reclaim_plan()
        act = json.loads(json.dumps(p["actions"]))
        act["docker"]["images"] = [["two-days:1", next(iter(f.images))]]
        act["docker"]["volumes"] = [vol]
        act["settings"] = {"image_min_age_days": 0, "anon_volume_min_age_days": 0, "builder_keep_hours": 24}
        tok = ex._plan_token(act)
        plan = dict(p, actions=act, confirm_token=tok)
        ex._save_plan(tok, plan)
        r = ex.reclaim_execute(tok)
        check("forged plan accepted as a token (it is well-formed)", not r.get("refused"), str(r)[:160])
        check("forged low thresholds: the 2-day image survives", "two-days:1" in tags(f))
        check("forged low thresholds: the 1-day volume survives", vol in f.volumes)
        check("builder prune ran at the config's 168h, not the forged 24h",
              ["builder", "prune", "-af", "--filter", "until=168h"] in f.mutating_calls(), str(f.mutating_calls()))


def t16_per_volume_check_isolated():
    """the per-volume `ps -a --filter volume=` check and the bulk mount read each protect on their own"""
    with Env() as env:
        f = env.fake
        img = f.image("x:local", 30)
        v = f.volume(hexid("used"), 30)
        f.container("c-user", img, "exited", volumes=[v])
        f.hide_inspect_mounts = True       # only the per-volume filter can see the reference
        p = ex.reclaim_plan()
        check("bulk read blind: per-volume check still skips it (in use)",
              listed_vols(p) == [] and (skipped_reason(p, "volumes", "name", v) or "").startswith("in use"),
              str(skipped_reason(p, "volumes", "name", v)))
        calls = [c for c in f.calls if c[:3] == ["docker", "ps", "-a"] and f"volume={v}" in c]
        check("the per-volume filter call was made for it", len(calls) >= 1)
        f.hide_inspect_mounts = False
        f.ps_volume_filter_blind = True    # only the bulk read can see it
        p = ex.reclaim_plan()
        check("per-volume filter blind: bulk read still skips it (in use)",
              listed_vols(p) == [] and (skipped_reason(p, "volumes", "name", v) or "").startswith("in use"))


def t17_compose_declared_hex_volume():
    """a 64-hex volume NAME that a compose file declares is a named volume: never removed"""
    with Env() as env:
        f = env.fake
        hexdecl = f.volume(hexid("declared"), 30)
        orphan = f.volume(hexid("orphan2"), 30)
        f.compose_volumes[env.compose_file] = {"data": {"name": hexdecl}}
        p = ex.reclaim_plan()
        check("declared 64-hex name skipped as named", (skipped_reason(p, "volumes", "name", hexdecl) or "").startswith("named volume"),
              str(skipped_reason(p, "volumes", "name", hexdecl)))
        check("the real orphan is still listed", listed_vols(p) == [orphan], str(listed_vols(p)))
        vcalls = [c for c in f.calls if c[1:2] == ["compose"] and "json" in c]
        check("volume render uses --no-consistency (frontend's alternative profiles fail without it)",
              vcalls and all("--no-consistency" in c for c in vcalls), str(vcalls[:1]))
        ex.reclaim_execute(p["confirm_token"])
        check("declared volume survives execute", hexdecl in f.volumes and orphan not in f.volumes)


def t18_meta_guards_mutated_open():
    """with BOTH production guards mutated to allow everything, the suite FAILS and nothing reaches docker"""
    if os.environ.get("ACSR_META_CHILD"):
        print("  (skipped inside the meta child)")
        return
    tmp = tempfile.mkdtemp(prefix="acsr-meta-")
    try:
        dst = os.path.join(tmp, "scripts", "sysadmin-mcp")
        shutil.copytree(_HERE, dst, ignore=shutil.ignore_patterns("state", "__pycache__"))
        shutil.copytree(os.path.join(_REPO, "scripts", "maintenance"), os.path.join(tmp, "scripts", "maintenance"))
        for rel, old, new in (
                ("docker_reclaim.py", "    \"\"\"True only for the three removal shapes the operator approved.\"\"\"\n",
                 "    \"\"\"True only for the three removal shapes the operator approved.\"\"\"\n    return True  # META MUTATION\n"),
                ("sysadmin.py", "    a = [str(x) for x in args]\n", "    return None  # META MUTATION\n    a = [str(x) for x in args]\n")):
            fp = os.path.join(dst, rel)
            src = open(fp, encoding="utf-8").read()
            check(f"meta: mutation applied to {rel}", old in src)
            open(fp, "w", encoding="utf-8", newline="\n").write(src.replace(old, new, 1))
        log = os.path.join(tmp, "calls.jsonl")
        env = dict(os.environ, DOCKER_HOST="tcp://127.0.0.1:1", ACSR_CALL_LOG=log, ACSR_META_CHILD="1",
                   PYTHONDONTWRITEBYTECODE="1")
        env.pop("DOCKER_CONTEXT", None)
        r = subprocess.run([sys.executable, os.path.join(dst, "test_docker_reclaim.py")], env=env,
                           capture_output=True, text=True, timeout=600)
        first = (r.stdout.splitlines() or [""])[0]
        check("meta: child printed its docker target first, the dead endpoint",
              "DOCKER_HOST=tcp://127.0.0.1:1" in first, first)
        check("meta: the suite FAILS with the guards mutated open", r.returncode != 0, r.stdout[-300:])
        check("meta: the failures are the guard checks",
              "FAIL  _mutate raises on" in r.stdout and "FAIL  deny-list refuses" in r.stdout, r.stdout[-400:])
        attempts = open(log, encoding="utf-8").read().splitlines() if os.path.exists(log) else []
        check("meta: ZERO calls escaped the fakes (call log empty: nothing reached sysadmin._run or subprocess)",
              attempts == [], "; ".join(attempts[:5]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    for case in (t01_base_red_scenario, t02_every_category, t03_revalidation, t04_token,
                 t05_forbidden_shapes, t06_plan_is_read_only_and_mutations_are_approved_shapes,
                 t07_fail_closed, t08_thresholds_configurable, t09_auto_path, t10_render,
                 t11_docs_match, t12_host_keep_list_seed, t13_untagged_on_cli29, t14_empty_keep_list,
                 t15_forged_plan_thresholds, t16_per_volume_check_isolated, t17_compose_declared_hex_volume,
                 t18_meta_guards_mutated_open):
        run_case(case)
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
