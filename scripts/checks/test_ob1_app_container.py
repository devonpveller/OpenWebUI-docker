#!/usr/bin/env python3
"""Static guards for the openbrain-workbench-app container (rwr-app-container, round 2).

No docker. Run with pytest, or `python scripts/checks/test_ob1_app_container.py`.

1. Every build `context` and `additional_contexts` path in OB1/docker/docker-compose.yml
   must exist on disk. Compose resolves BOTH against the compose project directory
   (OB1/docker), never against the service's build context; a wrong one makes
   `docker compose up -d` exit 1 for the WHOLE plane while the image is absent.
2. emergency-recovery.ps1 lists openbrain-workbench-app right after openbrain-workbench.
3. check-openbrain-health.ps1 section 5c treats an ABSENT app as a fault, not a skip.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "OB1" / "docker" / "docker-compose.yml"
RECOVERY = ROOT / "scripts" / "recovery" / "emergency-recovery.ps1"
HEALTH = ROOT / "scripts" / "checks" / "check-openbrain-health.ps1"


def missing_contexts(compose_file: Path) -> list[str]:
    """Return 'service: key -> path' for each local context path that does not exist."""
    data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    base = compose_file.parent
    bad = []
    for name, svc in (data.get("services") or {}).items():
        build = (svc or {}).get("build")
        if build is None:
            continue
        if isinstance(build, str):
            build = {"context": build}
        paths = {}
        if "context" in build:
            paths["context"] = build["context"]
        for k, v in (build.get("additional_contexts") or {}).items() if isinstance(build.get("additional_contexts"), dict) else []:
            paths[f"additional_contexts.{k}"] = v
        for k, v in paths.items():
            v = str(v)
            if "://" in v or "${" in v or "$" in v:
                continue  # remote or interpolated: not checkable statically
            if not (base / v).resolve().exists():
                bad.append(f"{name}: {k} -> {v} (resolved {(base / v).resolve()})")
    return bad


def test_every_ob1_compose_build_context_exists():
    assert COMPOSE.is_file(), COMPOSE
    assert missing_contexts(COMPOSE) == []


def test_guard_fires_on_the_d1_line(tmp_path):
    d = tmp_path / "docker"
    (d / "app").mkdir(parents=True)
    (d / "wiki-viewer" / "lib").mkdir(parents=True)
    f = d / "docker-compose.yml"
    f.write_text("services:\n  app:\n    build:\n      context: ./app\n      additional_contexts:\n"
                 "        viewerlib: ../wiki-viewer/lib\n", encoding="utf-8")
    assert len(missing_contexts(f)) == 1
    f.write_text(f.read_text().replace("../wiki-viewer/lib", "./wiki-viewer/lib"), encoding="utf-8")
    assert missing_contexts(f) == []


def test_recovery_lists_the_app_right_after_the_workbench():
    text = RECOVERY.read_text(encoding="utf-8")
    m = re.search(r'"openbrain-workbench",\s*"openbrain-workbench-app"', text)
    assert m, "emergency-recovery.ps1 must list openbrain-workbench-app right after openbrain-workbench"


def test_health_section_5c_absent_app_is_a_fault_not_a_skip():
    text = HEALTH.read_text(encoding="utf-8")
    start = text.index("$wbApp = 'openbrain-workbench-app'")
    end = text.index("# ---- 6.", start)
    block = text[start:end]
    assert "'absent'" not in block and "skipped" not in block, \
        "5c must send an absent app to Confirm-ObContainer (a fault), like the viewer"
    assert "Confirm-ObContainer $wbApp" in block


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
