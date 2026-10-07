#!/usr/bin/env python3
"""A service that publishes a host port must say whether it is healthy (SERVICE-LIFECYCLE row 5).

Called by check-project-configs.ps1 (gate 1, the compose renders) with the JSON of every
compose render it made; usable by hand the same way:

    docker compose -f search/docker-compose.yml --env-file search/.env.example \
        config --format json > search.json
    python scripts/checks/check_port_healthcheck.py --render search=search.json

WHY. `stack.ps1 health` and the watchdog read `docker ps --filter health=unhealthy`, so a
container with no healthcheck can never be reported unhealthy - it is "Up" while it
crash-loops or serves errors. openbrain-curator restart-looped for a day on 2026-09-05
with nothing noticing, and it had a published port and no healthcheck; so did
openbrain-research. A port is the signal that something outside the container depends on
the service answering.

A service with at least one PUBLISHED port passes when ONE of these holds:
  1. its rendered compose definition has a `healthcheck:` that is not `disable: true`;
  2. it is built from a Dockerfile in this checkout whose last HEALTHCHECK instruction is
     a real check (not `HEALTHCHECK NONE`) and compose does not disable it - the image
     carries the probe, which compose's render cannot show;
  3. it has a row in the allow-list (published-port-healthcheck-allowlist.json) with a
     non-empty reason. A row is a decision someone can read, never a silent skip.

An allow-list row for a service that a render shows NO LONGER needs it (it gained a
healthcheck, lost its port, or matched rule 2) is refused as stale, so the list shrinks as
the gaps close instead of quietly outliving them. A row for a service no render produced is
reported as not verified (e.g. OB1 renders only where its gitignored env exists), never as
a pass.

Exit: 0 = every published-port service accounted for; 1 = a refusal; 2 = bad input.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ALLOWLIST = os.path.join(HERE, "published-port-healthcheck-allowlist.json")


def published_ports(service: dict) -> list[str]:
    """Host-published ports of one rendered service, as printable strings."""
    out = []
    for p in service.get("ports") or []:
        if isinstance(p, dict):
            if p.get("published") in (None, ""):
                continue
            ip = p.get("host_ip") or ""
            out.append(f"{ip + ':' if ip else ''}{p.get('published')}->{p.get('target')}")
        elif isinstance(p, str) and ":" in p:  # short syntax only survives an unrendered file
            out.append(p)
    return out


def compose_healthcheck(service: dict) -> str:
    """'defined', 'disabled' or 'absent' for the service's compose healthcheck."""
    hc = service.get("healthcheck")
    if not hc:
        return "absent"
    if hc.get("disable") is True:
        return "disabled"
    test = hc.get("test")
    if isinstance(test, list) and test and str(test[0]).upper() == "NONE":
        return "disabled"
    return "defined"


_HC_RE = re.compile(r"^\s*HEALTHCHECK\s+(.*)$", re.IGNORECASE)


def dockerfile_healthcheck(service: dict) -> str | None:
    """The Dockerfile path when the service builds from one whose final HEALTHCHECK is real."""
    build = service.get("build")
    if not isinstance(build, dict) or not build.get("context"):
        return None
    ctx = build["context"]
    if "://" in ctx:  # a git/URL context is not in this checkout
        return None
    path = os.path.join(ctx, build.get("dockerfile") or "Dockerfile")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    # Join backslash continuations so `HEALTHCHECK --interval=... \` + `CMD ...` is one line.
    text = re.sub(r"\\\r?\n", " ", text)
    last = None
    for line in text.splitlines():
        m = _HC_RE.match(line)
        if m:
            last = m.group(1).strip()
    if last is None or last.upper().startswith("NONE"):
        return None
    return path


def load_allowlist(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data.get("services")
    if not isinstance(rows, dict):
        raise ValueError(f"{path}: top-level 'services' object missing")
    for name, row in rows.items():
        if not isinstance(row, dict) or not str(row.get("reason", "")).strip():
            raise ValueError(f"{path}: allow-list row '{name}' has no reason")
        if row.get("kind") not in ("exception", "gap"):
            raise ValueError(f"{path}: allow-list row '{name}' kind must be 'exception' or 'gap'")
    return rows


def evaluate(renders: list[tuple[str, dict]], allow: dict):
    """Return (lines, refusals, unverified_rows). Pure: no I/O beyond reading Dockerfiles."""
    lines: list[str] = []
    refusals: list[str] = []
    seen: dict[str, bool] = {}  # allow-list name -> did any render still NEED the row
    counts = {"compose": 0, "image": 0, "allowed": 0}
    total_services = 0
    for label, doc in renders:
        services = doc.get("services") or {}
        total_services += len(services)
        for name in sorted(services):
            svc = services[name]
            ports = published_ports(svc)
            hc = compose_healthcheck(svc)
            needs_row = False
            if ports:
                if hc == "defined":
                    counts["compose"] += 1
                elif hc == "absent" and dockerfile_healthcheck(svc):
                    counts["image"] += 1
                else:
                    needs_row = True
            if name in allow:
                seen[name] = seen.get(name, False) or needs_row
            if not needs_row:
                continue
            if name in allow:
                counts["allowed"] += 1
                continue
            why = ("its healthcheck is DISABLED" if hc == "disabled"
                   else "it has no healthcheck (compose or Dockerfile)")
            refusals.append(
                f"PUBLISHED PORT WITHOUT HEALTHCHECK: {label}/{name} publishes "
                f"{', '.join(ports)} and {why}. Add a cheap `healthcheck:` (no model load), "
                f"or add a reasoned row to {os.path.basename(DEFAULT_ALLOWLIST)}.")
    for name, needed in sorted(seen.items()):
        if not needed:
            refusals.append(
                f"STALE ALLOW-LIST ROW: '{name}' was rendered and no longer needs its row "
                f"(it has a healthcheck or no published port) - remove it.")
    unverified = sorted(n for n in allow if n not in seen)
    lines.append(
        f"published-port healthcheck rule: {len(renders)} render(s), {total_services} service(s) "
        f"scanned; ports covered by compose healthcheck {counts['compose']}, by Dockerfile "
        f"HEALTHCHECK {counts['image']}, by allow-list {counts['allowed']}")
    return lines, refusals, unverified


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--render", action="append", default=[], metavar="LABEL=PATH",
                    help="a `docker compose config --format json` output file")
    ap.add_argument("--allowlist", default=DEFAULT_ALLOWLIST)
    args = ap.parse_args(argv)
    if not args.render:
        print("no --render given: nothing scanned (a check that reads nothing does not pass)")
        return 2
    try:
        allow = load_allowlist(args.allowlist)
    except (OSError, ValueError) as exc:
        print(f"ALLOW-LIST UNREADABLE: {exc}")
        return 2
    renders = []
    for spec in args.render:
        label, sep, path = spec.partition("=")
        if not sep:
            print(f"bad --render '{spec}' (want LABEL=PATH)")
            return 2
        try:
            with open(path, encoding="utf-8") as fh:
                renders.append((label, json.load(fh)))
        except (OSError, ValueError) as exc:
            print(f"RENDER UNREADABLE: {label} ({path}): {exc}")
            return 2
    lines, refusals, unverified = evaluate(renders, allow)
    for line in lines:
        print(line)
    if unverified:
        print("NOT VERIFIED (no render produced them): allow-list row(s) " + ", ".join(unverified))
    for r in refusals:
        print(r)
    return 1 if refusals else 0


if __name__ == "__main__":
    sys.exit(main())
