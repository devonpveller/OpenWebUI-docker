#!/usr/bin/env python3
"""Docker-side reclaim for sysadmin-mcp: unused image TAGS, old build cache, orphaned ANONYMOUS
volumes. Used by executor.reclaim_plan/reclaim_execute (the MCP tools) and by auto_reclaim.py (the
hourly low-disk path in scripts/maintenance/disk-guard.ps1).

WHY (2026-09-27): reclaim used to run `docker image prune -f` (dangling images only) and `docker
builder prune -f`, and the charter forbade touching any volume. The host then held ~34 GB of unused
TAGGED images and ~750 orphaned anonymous volumes, reclaim reported "images 0B, cache 0B", and C:
kept filling. The operator decided on 2026-09-27 exactly how far reclaim may go; this file is that
decision and nothing wider.

IMAGES - a TAG is removed only when ALL hold:
  * no container, running or stopped, uses that image id;
  * no plane's compose render names that repo:tag (every plane in stack.manifest.toml - OB1 and
    agent-org are planes there - rendered with every profile, `--profile *`); a digest reference
    (repo@sha256:...) protects the image carrying that digest;
  * the image was created `image_min_age_days` (default 14) or more days ago;
  * the tag matches no pattern in image-keep.txt;
  * no OTHER tag of the same image id is protected by compose or the keep-list (an image id with a
    protected tag is never removed through another tag).
  Untagged (dangling) images are removed by id when no container uses them - what the old
  `image prune -f` removed, now listed and re-checked like everything else.
  Removal is `docker image rm <repo:tag>` (or `<id>` for an untagged image), never with -f.

BUILD CACHE - `docker builder prune -af --filter until=<builder_keep_hours>h` (default 168h): all
  unused cache older than a week; the last week stays for rebuilds.

ANONYMOUS VOLUMES - a volume is removed only when ALL hold:
  * its name is exactly 64 lowercase hex characters (docker's anonymous-volume naming);
  * no container, running or stopped, references it - checked PER VOLUME with
    `docker ps -a --filter volume=<name>`, on top of a bulk read of every container's mounts;
  * its CreatedAt is `anon_volume_min_age_days` (default 7) or more days ago.
  Removal is `docker volume rm <name>`, one explicit name at a time. NAMED volumes are never
  removed here: they are listed as skipped, report-only.

NEVER: `docker volume prune`, `docker image prune` (with or without -a), `docker system prune`,
`docker rmi -f`. Every mutating docker call goes through _mutate(), which refuses any argv that is
not one of the three shapes above (test_docker_reclaim.py drives it with the forbidden ones).

FAIL-CLOSED: if the container list, the image inventory, the keep-list or ANY plane's compose
render cannot be read, the affected category removes nothing and says why. An age that cannot be
parsed counts as too new.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import sysadmin as sa  # noqa: E402

HEX64 = re.compile(r"^[0-9a-f]{64}$")
DAY = 86400.0
_TOP = 10  # largest entries shown per category


class InventoryError(Exception):
    """A read the safety rules depend on failed; the category it feeds must remove nothing."""


# -- settings ------------------------------------------------------------------
def settings(**overrides) -> dict:
    """Thresholds + file locations. config.json thresholds < explicit overrides (None = unset)."""
    cfg = sa.load_config()
    th = cfg.get("thresholds", {})

    def _path(key: str, default: str) -> str:
        p = cfg.get(key) or default
        return p if os.path.isabs(p) else os.path.join(sa._REPO_ROOT, p)

    s = {
        "image_min_age_days": float(th.get("image_min_age_days", 14)),
        "anon_volume_min_age_days": float(th.get("anon_volume_min_age_days", 7)),
        "builder_keep_hours": int(th.get("builder_keep_hours", 168)),
        "keep_file": _path("image_keep_file", os.path.join(_HERE, "image-keep.txt")),
        "manifest": _path("compose_manifest", os.path.join(sa._REPO_ROOT, "stack.manifest.toml")),
    }
    for k, v in overrides.items():
        if v is not None and k in s:
            s[k] = type(s[k])(v)
    return s


# -- the ONLY mutating door -----------------------------------------------------
def allowed_mutation(argv: list[str]) -> bool:
    """True only for the three removal shapes the operator approved."""
    if len(argv) == 3 and argv[0] == "image" and argv[1] == "rm":
        return bool(argv[2]) and not argv[2].startswith("-")
    if len(argv) == 3 and argv[0] == "volume" and argv[1] == "rm":
        return bool(HEX64.match(argv[2]))
    if len(argv) == 5 and argv[:4] == ["builder", "prune", "-af", "--filter"]:
        return bool(re.fullmatch(r"until=\d+h", argv[4]))
    return False


def _mutate(argv: list[str], timeout: int = 180) -> dict:
    if not allowed_mutation(argv):
        raise ValueError(f"refused docker mutation (not an approved shape): {argv}")
    return sa._docker(argv, timeout=timeout)


# -- pure helpers ---------------------------------------------------------------
_TS = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d+))?\s*(Z|[+-]\d{2}:?\d{2})?")


def parse_ts(s: str) -> float | None:
    """Docker timestamp -> epoch seconds. None when it cannot be read (callers treat as too new)."""
    m = _TS.match((s or "").strip())
    if not m:
        return None
    date, hms, frac, tz = m.groups()
    if tz is None:
        return None  # no zone: refuse to guess
    dt = datetime.strptime(f"{date}T{hms}", "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    off = 0
    if tz != "Z":
        sign = -1 if tz[0] == "-" else 1
        digits = tz[1:].replace(":", "")
        off = sign * (int(digits[:2]) * 3600 + int(digits[2:4]) * 60)
    return dt.timestamp() - off + (float("0." + frac) if frac else 0.0)


def norm_ref(ref: str) -> tuple[str, str]:
    """(repo:tag, digest) in the form `docker images` prints. Either part may be empty."""
    r = (ref or "").strip()
    digest = ""
    if "@" in r:
        r, digest = r.split("@", 1)
    for p in ("docker.io/library/", "index.docker.io/library/", "docker.io/", "index.docker.io/"):
        if r.startswith(p):
            r = r[len(p):]
            break
    if r and ":" not in r.rsplit("/", 1)[-1] and not digest:
        r += ":latest"
    return r, digest


def _repo(ref: str) -> str:
    """repo:tag -> repo (a registry port like host:5000/x is not a tag)."""
    head, _, last = ref.rpartition("/")
    if ":" in last:
        last = last.rsplit(":", 1)[0]
    return f"{head}/{last}" if head else last


def load_keep(path: str) -> list[tuple[str, str]]:
    """[(pattern, reason)]. Raises InventoryError when the file cannot be read."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as e:
        raise InventoryError(f"keep-list unreadable ({path}): {e}") from e
    out = []
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        pat, _, reason = s.partition("#")
        pat = pat.strip()
        if pat:
            out.append((pat, reason.strip() or "(no reason given)"))
    return out


def keep_match(ref: str, keep: list[tuple[str, str]]) -> str | None:
    for pat, reason in keep:
        if fnmatch.fnmatchcase(ref, pat):
            return f"{pat} ({reason})"
    return None


def _gb(n: float) -> float:
    return round((n or 0) / sa.GB, 2)


def human(n: float) -> str:
    """Decimal units, as docker prints them: 1.23 GB / 45.6 MB / 789 kB / 0 B."""
    n = float(n or 0)
    for unit, mult in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if n >= mult:
            return f"{n / mult:.2f} {unit}" if unit == "GB" else f"{n / mult:.1f} {unit}"
    return f"{n:.0f} B"


# -- read-only inventory ----------------------------------------------------------
def _chunks(seq: list, n: int = 100):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def containers() -> list[dict]:
    """Every container, running or stopped: id, name, image id, state, volume names it mounts."""
    ls = sa._docker(["ps", "-a", "-q", "--no-trunc"], timeout=60)
    if ls["rc"] != 0:
        raise InventoryError(f"docker ps -a failed: {ls['err'].strip()}")
    ids = [x.strip() for x in ls["out"].splitlines() if x.strip()]
    fmt = ('{{.Id}}|{{.Name}}|{{.Image}}|{{.State.Status}}|'
           '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}},{{end}}{{end}}')
    out = []
    for chunk in _chunks(ids):
        r = sa._docker(["inspect", "--format", fmt] + chunk, timeout=120)
        if r["rc"] != 0:
            raise InventoryError(f"docker inspect (containers) failed: {r['err'].strip()}")
        for ln in r["out"].splitlines():
            p = ln.split("|")
            if len(p) != 5:
                continue
            out.append({"id": p[0], "name": p[1].lstrip("/"), "image": p[2], "state": p[3],
                        "volumes": [v for v in p[4].split(",") if v]})
    if len(out) != len(ids):
        raise InventoryError(f"inspected {len(out)} of {len(ids)} containers")
    return out


def images() -> list[dict]:
    """Every top-level image (tagged or dangling): id, tags, digests, created epoch."""
    ls = sa._docker(["images", "--no-trunc", "--format", "{{.ID}}"], timeout=60)
    if ls["rc"] != 0:
        raise InventoryError(f"docker images failed: {ls['err'].strip()}")
    ids = sorted({x.strip() for x in ls["out"].splitlines() if x.strip()})
    fmt = "{{.Id}}|{{.Created}}|{{json .RepoTags}}|{{json .RepoDigests}}"
    out = []
    for chunk in _chunks(ids):
        r = sa._docker(["image", "inspect", "--format", fmt] + chunk, timeout=120)
        if r["rc"] != 0:
            raise InventoryError(f"docker image inspect failed: {r['err'].strip()}")
        for ln in r["out"].splitlines():
            p = ln.split("|", 3)
            if len(p) != 4:
                continue
            try:
                tags = json.loads(p[2]) or []
                digests = json.loads(p[3]) or []
            except ValueError:
                raise InventoryError(f"unparseable image inspect line: {ln[:120]}") from None
            out.append({"id": p[0], "created": parse_ts(p[1]), "created_raw": p[1],
                        "tags": [norm_ref(t)[0] for t in tags if t and "<none>" not in t],
                        "digests": sorted({"{}@{}".format(*norm_ref(d)) for d in digests if "@" in d})})
    return out


def volumes(names: list[str] | None = None) -> list[dict]:
    """Volumes (all, or just `names`): name, created epoch. A named volume that no longer exists is
    simply absent from the result."""
    if names is None:
        ls = sa._docker(["volume", "ls", "-q"], timeout=60)
        if ls["rc"] != 0:
            raise InventoryError(f"docker volume ls failed: {ls['err'].strip()}")
        names = [x.strip() for x in ls["out"].splitlines() if x.strip()]
    out = []
    for chunk in _chunks(list(names), 200):
        r = sa._docker(["volume", "inspect", "--format", "{{.Name}}|{{.CreatedAt}}"] + chunk,
                       timeout=120)
        # inspect exits non-zero when ANY name is missing but still prints the others
        if r["rc"] != 0 and not r["out"].strip():
            if len(chunk) == 1:
                continue  # that one volume is gone
            raise InventoryError(f"docker volume inspect failed: {r['err'].strip()}")
        for ln in r["out"].splitlines():
            p = ln.split("|", 1)
            if len(p) == 2 and p[0]:
                out.append({"name": p[0], "created": parse_ts(p[1]), "created_raw": p[1]})
    return out


def volume_users(name: str) -> list[str] | None:
    """Container ids referencing `name`, via `docker ps -a --filter volume=`. None = check failed."""
    r = sa._docker(["ps", "-a", "-q", "--no-trunc", "--filter", f"volume={name}"], timeout=60)
    if r["rc"] != 0:
        return None
    return [x.strip() for x in r["out"].splitlines() if x.strip()]


def compose_refs(manifest_path: str) -> tuple[set, set, list]:
    """(repo:tag names, repo@digest refs, errors) from every plane's compose render, all profiles."""
    import tomllib
    names, digests, errors = set(), set(), []
    try:
        with open(manifest_path, "rb") as fh:
            planes = tomllib.load(fh).get("planes", {})
    except (OSError, ValueError) as e:
        return names, digests, [f"manifest unreadable ({manifest_path}): {e}"]
    if not planes:
        return names, digests, [f"manifest lists no planes ({manifest_path})"]
    root = os.path.dirname(os.path.abspath(manifest_path))
    for plane, spec in sorted(planes.items()):
        rel = (spec or {}).get("compose")
        if not rel:
            errors.append(f"{plane}: no compose file declared")
            continue
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            errors.append(f"{plane}: {rel} not found")
            continue
        r = sa._docker(["compose", "-f", path, "--profile", "*", "config", "--images"], timeout=120)
        if r["rc"] != 0:
            errors.append(f"{plane}: compose config failed: {(r['err'] or r['out']).strip()[:200]}")
            continue
        for ln in r["out"].splitlines():
            if not ln.strip():
                continue
            n, d = norm_ref(ln)
            if d:
                digests.add(f"{_repo(n)}@{d}")
                if n != _repo(n):
                    names.add(n)  # repo:tag@digest names the tag too
            elif n:
                names.add(n)
    return names, digests, errors


def df_verbose() -> dict:
    """Sizes from `docker system df -v`: image unique bytes by id, volume bytes by name, cache rows."""
    r = sa._docker(["system", "df", "-v", "--format", "json"], timeout=300)
    if r["rc"] != 0:
        return {"error": r["err"].strip() or "docker system df -v failed"}
    try:
        d = json.loads(r["out"])
    except ValueError:
        return {"error": "docker system df -v: unparseable output"}
    img = {}
    for row in d.get("Images") or []:
        img[row.get("ID", "")] = sa.parse_size_to_bytes(row.get("UniqueSize") or row.get("Size") or "")
    vol = {row.get("Name", ""): sa.parse_size_to_bytes(row.get("Size") or "")
           for row in d.get("Volumes") or []}
    return {"images": img, "volumes": vol, "cache": d.get("BuildCache") or []}


def df_totals() -> dict:
    """{Images, Local Volumes, Build Cache} -> SIZE in bytes, from `docker system df`."""
    r = sa._docker(["system", "df", "--format", "{{.Type}}|{{.Size}}"], timeout=120)
    out = {}
    if r["rc"] != 0:
        return {"error": r["err"].strip() or "docker system df failed"}
    for ln in r["out"].splitlines():
        p = ln.split("|")
        if len(p) == 2:
            out[p[0].strip()] = sa.parse_size_to_bytes(p[1])
    return out


# -- classification (pure: every input passed in) ---------------------------------
def _who(cids: list[str], by_id: dict) -> str:
    parts = []
    for c in cids[:3]:
        ct = by_id.get(c)
        parts.append(f"{ct['name']} ({ct['state']})" if ct else c[:12])
    more = f" +{len(cids) - 3} more" if len(cids) > 3 else ""
    return ", ".join(parts) + more


def classify_images(imgs: list[dict], ctrs: list[dict], compose_names: set, compose_digests: set,
                    keep: list, min_age_days: float, now: float, sizes: dict | None = None) -> dict:
    sizes = sizes or {}
    by_cid = {c["id"]: c for c in ctrs}
    users: dict = {}
    for c in ctrs:
        users.setdefault(c["image"], []).append(c["id"])
    remove, skipped = [], []
    for im in imgs:
        iid = im["id"]
        age = None if im.get("created") is None else (now - im["created"]) / DAY
        base = {"id": iid, "age_days": None if age is None else round(age, 1),
                "size_bytes": sizes.get(iid, 0.0)}
        if not im["tags"]:  # dangling: what `image prune -f` used to take, now by explicit id
            if iid in users:
                skipped.append({**base, "ref": iid, "kind": "in use",
                                "reason": f"in use by {_who(users[iid], by_cid)}"})
            else:
                remove.append({**base, "ref": iid, "untagged": True})
            continue
        digest_hit = sorted(d for d in im.get("digests", []) if d in compose_digests)
        protected_by = {}
        for t in im["tags"]:
            if t in compose_names:
                protected_by[t] = ("compose-named", "compose-named")
            else:
                k = keep_match(t, keep)
                if k:
                    protected_by[t] = ("keep-list", f"keep-list: {k}")
        for t in im["tags"]:
            if iid in users:
                kind, reason = "in use", f"in use by {_who(users[iid], by_cid)}"
            elif t in protected_by:
                kind, reason = protected_by[t]
            elif digest_hit:
                kind, reason = "compose-named", f"compose-named by digest {digest_hit[0]}"
            elif protected_by:
                other = sorted(protected_by)[0]
                kind = "shares id with protected tag"
                reason = f"shares image id with protected tag {other} ({protected_by[other][0]})"
            elif age is None:
                kind, reason = "too new", "age unknown (Created unreadable) - treated as too new"
            elif age < min_age_days:
                kind, reason = "too new", f"too new ({age:.1f} d < {min_age_days:g} d)"
            else:
                kind = reason = None
            if reason:
                skipped.append({**base, "ref": t, "kind": kind, "reason": reason})
            else:
                remove.append({**base, "ref": t})
    return {"remove": remove, "skipped": skipped}


def classify_volumes(vols: list[dict], ctrs: list[dict], min_age_days: float, now: float,
                     users_of=volume_users, sizes: dict | None = None, workers: int = 8) -> dict:
    sizes = sizes or {}
    by_cid = {c["id"]: c for c in ctrs}
    bulk: dict = {}
    for c in ctrs:
        for v in c["volumes"]:
            bulk.setdefault(v, []).append(c["id"])
    anon = [v for v in vols if HEX64.match(v["name"])]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        per = dict(zip([v["name"] for v in anon], pool.map(users_of, [v["name"] for v in anon])))
    remove, skipped = [], []
    for v in vols:
        n = v["name"]
        age = None if v.get("created") is None else (now - v["created"]) / DAY
        row = {"name": n, "age_days": None if age is None else round(age, 1),
               "size_bytes": sizes.get(n, 0.0)}
        if not HEX64.match(n):
            skipped.append({**row, "kind": "named volume",
                            "reason": "named volume (report-only, never removed)"})
            continue
        cids = per.get(n)
        if cids is None:
            kind, reason = "usage check failed", "usage check failed (docker ps -a --filter volume=)"
        elif cids or n in bulk:
            kind = "in use"
            reason = f"in use by {_who(sorted(set(cids) | set(bulk.get(n, []))), by_cid)}"
        elif age is None:
            kind, reason = "too new", "age unknown (CreatedAt unreadable) - treated as too new"
        elif age < min_age_days:
            kind, reason = "too new", f"too new ({age:.1f} d < {min_age_days:g} d)"
        else:
            kind = reason = None
        if reason:
            skipped.append({**row, "kind": kind, "reason": reason})
        else:
            remove.append(row)
    return {"remove": remove, "skipped": skipped}


def _cache_estimate(rows: list, keep_hours: int, now: float) -> tuple[float, int]:
    total, n = 0.0, 0
    for r in rows:
        if str(r.get("InUse", "")).lower() == "true":
            continue
        ts = parse_ts(r.get("LastUsedAt") or "") or parse_ts(r.get("CreatedAt") or "")
        if ts is None or now - ts < keep_hours * 3600:
            continue
        total += sa.parse_size_to_bytes(r.get("Size") or "")
        n += 1
    return total, n


def _reason_counts(skipped: list) -> dict:
    out: dict = {}
    for x in skipped:
        out[x["kind"]] = out.get(x["kind"], 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _largest_images(remove: list) -> list:
    by_id: dict = {}
    for r in remove:
        e = by_id.setdefault(r["id"], {"refs": [], "size_bytes": r["size_bytes"],
                                       "age_days": r["age_days"]})
        e["refs"].append(r["ref"])
    rows = [{"refs": v["refs"], "id": k[7:19] if k.startswith("sha256:") else k[:12],
             "gb": _gb(v["size_bytes"]), "age_days": v["age_days"]} for k, v in by_id.items()]
    return sorted(rows, key=lambda x: -x["gb"])[:_TOP]


# -- plan -------------------------------------------------------------------------------
def plan(**overrides) -> dict:
    """READ-ONLY. What the docker reclaim would remove, what it skips and why, estimated bytes."""
    s = settings(**overrides)
    now = time.time()
    sizes = df_verbose()
    size_note = sizes.get("error")
    res = {"settings": s, "generated_at": int(now), "size_note": size_note}

    try:
        ctrs = containers()
        ctr_err = None
    except InventoryError as e:
        ctrs, ctr_err = [], str(e)

    # images
    img = {"enabled": False, "disabled_reason": None, "remove": [], "skipped": []}
    try:
        if ctr_err:
            raise InventoryError(ctr_err)
        keep = load_keep(s["keep_file"])
        names, digests, errs = compose_refs(s["manifest"])
        if errs:
            raise InventoryError("compose render incomplete - " + "; ".join(errs))
        img.update(classify_images(images(), ctrs, names, digests, keep, s["image_min_age_days"],
                                   now, sizes.get("images")))
        img["enabled"] = True
        img["compose_named_count"] = len(names)
    except InventoryError as e:
        img["disabled_reason"] = str(e)
    ids = {r["id"] for r in img["remove"]}
    img["count"] = len(img["remove"])
    img["image_ids"] = len(ids)
    img["bytes"] = sum((sizes.get("images") or {}).get(i, 0.0) for i in ids)
    img["largest"] = _largest_images(img["remove"])
    img["skipped_by_reason"] = _reason_counts(img["skipped"])

    # anonymous volumes
    vol = {"enabled": False, "disabled_reason": None, "remove": [], "skipped": []}
    try:
        if ctr_err:
            raise InventoryError(ctr_err)
        vol.update(classify_volumes(volumes(), ctrs, s["anon_volume_min_age_days"], now,
                                    sizes=sizes.get("volumes")))
        vol["enabled"] = True
    except InventoryError as e:
        vol["disabled_reason"] = str(e)
    vol["count"] = len(vol["remove"])
    vol["bytes"] = sum(r["size_bytes"] for r in vol["remove"])
    vol["largest"] = [{"name": r["name"], "gb": _gb(r["size_bytes"]), "age_days": r["age_days"]}
                      for r in sorted(vol["remove"], key=lambda r: -r["size_bytes"])[:_TOP]]
    vol["skipped_by_reason"] = _reason_counts(vol["skipped"])

    est, n = _cache_estimate(sizes.get("cache") or [], s["builder_keep_hours"], now)
    cache = {"command": f"docker builder prune -af --filter until={s['builder_keep_hours']}h",
             "until_hours": s["builder_keep_hours"], "bytes": est, "entries": n}

    res.update({"images": img, "volumes": vol, "build_cache": cache})
    res["listed"] = listed_set(res)
    return res


def listed_set(p: dict) -> dict:
    """The exact set a confirm_token covers."""
    return {
        "images": sorted([r["ref"], r["id"]] for r in p["images"]["remove"]),
        "volumes": sorted(r["name"] for r in p["volumes"]["remove"]),
        "builder_until_hours": p["build_cache"]["until_hours"],
    }


# -- execute ------------------------------------------------------------------------------
def execute(listed: dict, s: dict, audit=None) -> dict:
    """Remove ONLY what `listed` names, each item re-validated now; anything no longer eligible is
    skipped with the current reason. Freed bytes per category from `docker system df` before/after."""
    audit = audit or (lambda ev, d: None)
    now = time.time()
    before = df_totals()
    out = {"images": {"removed": [], "skipped": []}, "volumes": {"removed": [], "skipped": []},
           "build_cache": {}}

    try:
        ctrs = containers()
        ctr_err = None
    except InventoryError as e:
        ctrs, ctr_err = [], str(e)

    # images: fresh inventory + fresh compose render + fresh keep-list, same thresholds
    want = [tuple(x) for x in listed.get("images", [])]
    if want:
        try:
            if ctr_err:
                raise InventoryError(ctr_err)
            keep = load_keep(s["keep_file"])
            names, digests, errs = compose_refs(s["manifest"])
            if errs:
                raise InventoryError("compose render incomplete - " + "; ".join(errs))
            fresh = classify_images(images(), ctrs, names, digests, keep,
                                    s["image_min_age_days"], now)
            ok = {(r["ref"], r["id"]) for r in fresh["remove"]}
            why = {(r["ref"], r["id"]): r["reason"] for r in fresh["skipped"]}
            now_ref, now_ids = {}, set()
            for r in fresh["remove"] + fresh["skipped"]:
                now_ref.setdefault(r["ref"], r["id"])
                now_ids.add(r["id"])
            for ref, iid in want:
                if (ref, iid) not in ok:
                    if (ref, iid) in why:
                        reason = why[(ref, iid)]
                    elif ref in now_ref:
                        reason = f"changed since plan: {ref} now points to {now_ref[ref][7:19]}"
                    elif iid in now_ids:
                        reason = "changed since plan: the image's tags changed"
                    else:
                        reason = "gone since plan"
                    out["images"]["skipped"].append({"ref": ref, "reason": reason})
                    continue
                r = _mutate(["image", "rm", ref])
                if r["rc"] == 0:
                    out["images"]["removed"].append(ref)
                    audit("reclaim_image_rm", {"ref": ref, "id": iid})
                else:
                    out["images"]["skipped"].append({"ref": ref, "reason": "docker refused: " + r["err"].strip()[:160]})
        except InventoryError as e:
            out["images"]["skipped"] += [{"ref": ref, "reason": f"re-validation failed: {e}"} for ref, _ in want]

    # anonymous volumes: only the listed names, each re-checked
    wantv = list(listed.get("volumes", []))
    if wantv:
        try:
            if ctr_err:
                raise InventoryError(ctr_err)
            present = volumes(wantv)
            fresh = classify_volumes(present, ctrs, s["anon_volume_min_age_days"], now)
            ok = {r["name"] for r in fresh["remove"]}
            why = {r["name"]: r["reason"] for r in fresh["skipped"]}
            for n in wantv:
                if n not in ok:
                    out["volumes"]["skipped"].append({"name": n, "reason": why.get(n, "gone since plan")})
                    continue
                r = _mutate(["volume", "rm", n])
                if r["rc"] == 0:
                    out["volumes"]["removed"].append(n)
                    audit("reclaim_volume_rm", {"name": n})
                else:
                    out["volumes"]["skipped"].append({"name": n, "reason": "docker refused: " + r["err"].strip()[:160]})
        except InventoryError as e:
            out["volumes"]["skipped"] += [{"name": n, "reason": f"re-validation failed: {e}"} for n in wantv]

    h = listed.get("builder_until_hours")
    if h:
        r = _mutate(["builder", "prune", "-af", "--filter", f"until={int(h)}h"])
        out["build_cache"] = {"ran": f"docker builder prune -af --filter until={int(h)}h",
                              "rc": r["rc"], "err": r["err"].strip()[:160]}
        audit("reclaim_builder_prune", {"until_hours": int(h), "rc": r["rc"]})

    after = df_totals()
    freed = {}
    for key, typ in (("images", "Images"), ("build_cache", "Build Cache"), ("volumes", "Local Volumes")):
        if "error" in before or "error" in after or typ not in before or typ not in after:
            freed[key] = None
        else:
            freed[key] = before[typ] - after[typ]
    out["freed_bytes"] = freed
    out["df_before"], out["df_after"] = before, after
    return out


def summary_line(freed: dict, result: dict) -> str:
    """One line for alerts: per-category freed bytes (from docker system df) + counts."""
    def f(key):
        v = freed.get(key)
        if v is None:
            return "n/a"
        if v < 0:  # the category GREW while we ran (e.g. removed image layers kept as build cache)
            return f"0 B (grew {human(-v)})"
        return human(v)
    ir, vr = result.get("images", {}), result.get("volumes", {})
    return (f"images {f('images')} ({len(ir.get('removed', []))} removed, {len(ir.get('skipped', []))} skipped) | "
            f"build cache {f('build_cache')} | "
            f"anon volumes {f('volumes')} ({len(vr.get('removed', []))} removed, {len(vr.get('skipped', []))} skipped)")
