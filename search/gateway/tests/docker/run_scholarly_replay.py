"""rw-p5a-intake: the scholarly-engine replay lab (acceptance: the 2026-09-11 audit query, replayed with the
academic class, reaches a scholarly index and returns the PubMed source the audit missed - PMID 40128952).

    python tests/docker/run_scholarly_replay.py --gateway-image private-search-gateway:wt-rw-p5a-intake
        --research-image openbrain-research:wt-rw-p5a-intake --settings ../searxng --out <evidence dir>
        [--expect-red]      # RED: the base images and the base settings.yml - the PubMed source must NOT come back

The replay needs the real PubMed, so it needs the internet - and takes it ONLY through the search plane's own
egress path: the LIVE plane's Mullvad proxy (`search-vpn`, http://vpn:8888), reached from ONE lab container.
WHAT IT RUNS (labelled ai-stack.harness.owner=wt-rw-p5a-intake, no published port, removed at the end):
  net       a NEW --internal bridge on 10.243.77.0/24 (outside 192.168.0.0/16 and the daemon's pools)
  redis     redis:7-alpine, the lab gateway's cache (no password; lab net only)
  searxng   the pinned image the plane runs, with --settings (this checkout's search/searxng: the scholarly engines
            named enabled; RED: the base's) - on the lab net AND on the live plane's internal `search_search-net`,
            with NO network alias there (its container name only, so nothing live can resolve it as `searxng`),
            for one reason: its settings send every engine request to http://vpn:8888, the live search-vpn. It is
            never on an ai-stack_* network; the live searxng, gateway and redis are not touched.
  gateway   --gateway-image (this checkout's gateway; RED: the base's) on the lab net only
  replay    --research-image, the research service's own modules (/app/contract.ts, /app/harness.ts) running
            replay_academic.ts on the lab net only: resolveContract({classes: ["academic"]}) + withContract over
            GET /search of the lab gateway - and the same queries with no contract, for the record
The live gateway is never called. Exit 1 if a check fails (with --expect-red: if the PubMed source comes back).
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUBNET = "10.243.77.0/24"
OWNER = "wt-rw-p5a-intake"
LABEL = f"ai-stack.harness.owner={OWNER}"
SEARXNG_IMAGE = "searxng/searxng:2026.9.11-61d660276"
REDIS_IMAGE = "redis:7-alpine"
LIVE_SEARCH_NET = "search_search-net"
PMID = "40128952"
# the audit's 100 Hz job, as the audit replayed it (journal/notes/research-audit-optiplex-100hz-2026-09-11.md)
# and the keyword form the engine study measured (search-engine-alternatives-2026-09-11.md, row L)
QUERIES = [
    "clinical trial 100 Hz tone VR motion sickness reduction study",
    "100 Hz sound motion sickness vestibular study",
    "100 Hz tone motion sickness study",
]
SCHOLARLY = {"pubmed", "europepmc", "semantic scholar", "crossref", "openalex", "arxiv", "google scholar"}


def sh(*args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    for a in args:
        if a.startswith("ai-stack_"):
            raise RuntimeError(f"refused: an ai-stack_* name ({a})")
        if a in ("-p", "-P", "--publish") or a.startswith("--publish"):
            raise RuntimeError("refused: a published port")
        if a.endswith(":local"):
            raise RuntimeError(f"refused: a :local image ({a})")
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:3])}... exit {r.returncode}: {r.stderr[-600:]}")
    return r


class Lab:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.tag = secrets.token_hex(3)
        self.net = f"rwp5a-replay-{self.tag}"
        self.names = {k: f"rwp5a-{k}-{self.tag}" for k in ("redis", "searxng", "gateway")}
        self.made: list[str] = []
        self.net_made = False
        self.checks: list[dict] = []
        self.out = Path(a.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="rwp5a-replay-"))

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.checks.append({"check": name, "ok": bool(ok), "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  {'' if ok else detail}", flush=True)

    def run(self, name: str, image: str, *args: str) -> None:
        sh("run", "-d", "--name", name, "--label", LABEL, "--network", self.net, *args, image)
        self.made.append(name)

    def go(self) -> None:
        sh("network", "inspect", LIVE_SEARCH_NET)  # the search plane is up (its egress path exists)
        sh("network", "create", "--internal", "--subnet", SUBNET, "--label", LABEL, self.net)
        self.net_made = True
        cfg = self.tmp / "searxng"
        shutil.copytree(self.a.settings, cfg)
        gw_key = secrets.token_hex(16)
        self.run(self.names["redis"], REDIS_IMAGE)
        self.run(self.names["searxng"], SEARXNG_IMAGE, "-e", f"SEARXNG_SECRET={secrets.token_hex(32)}",
                 "-v", f"{cfg}:/etc/searxng:rw", "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "SETGID",
                 "--cap-add", "SETUID", "--cap-add", "DAC_OVERRIDE", "--security-opt", "no-new-privileges:true")
        # the ONE attachment to the live plane: its internal net, for vpn:8888 only (no alias)
        sh("network", "connect", LIVE_SEARCH_NET, self.names["searxng"])
        self.run(self.names["gateway"], self.a.gateway_image, "-e", f"GATEWAY_API_KEY={gw_key}", "-e", "SEARCH_ENGINE=searxng",
                 "-e", f"SEARCH_ENGINE_URL=http://{self.names['searxng']}:8080", "-e", f"REDIS_URL=redis://{self.names['redis']}:6379/1",
                 "-e", "SEARCH_ANONYMOUS_LEGACY=true", "--security-opt", "no-new-privileges:true")
        gw = f"http://{self.names['gateway']}:8080"
        up = False
        for _ in range(60):
            r = sh("run", "--rm", "--label", LABEL, "--network", self.net, "--entrypoint", "curl", self.a.gateway_image,
                   "-fsS", "--max-time", "60", f"{gw}/search?q=healthcheck&format=json", check=False, timeout=120)
            if r.returncode == 0:
                up = True
                break
            time.sleep(3)
        self.check("the lab gateway answers through the lab SearXNG", up)
        if not up:
            (self.out / "searxng.log").write_text(sh("logs", "--tail", "80", self.names["searxng"], check=False).stdout, encoding="utf-8")
            return
        # warm-up (recorded): a cold SearXNG's first science query initialises every engine; one that is slow then
        # (measured: 30 s) outlasts the gateway's 20 s timeout and the answer is empty. A running plane is warm.
        warm = []
        for _ in range(6):
            r = sh("run", "--rm", "--label", LABEL, "--network", self.net, "--entrypoint", "curl", self.a.gateway_image, "-sS",
                   "--max-time", "90", "-G", f"{gw}/search", "--data-urlencode", "q=motion sickness", "--data-urlencode", "format=json",
                   "--data-urlencode", "categories=general,science", check=False, timeout=180)
            try:
                n = int(json.loads(r.stdout).get("number_of_results", 0))
            except ValueError:
                n = -1
            warm.append(n)
            if n > 0:
                break
            time.sleep(10)
        self.check("the lab plane warmed up (a science query answered)", warm[-1] > 0, warm)
        results = {}
        for mode in ("contract", "none"):
            r = sh("run", "--rm", "--label", LABEL, "--network", self.net, "-v", f"{HERE}:/replay:ro",
                   "-e", f"GW={gw}", "-e", f"QUERIES={json.dumps(QUERIES)}", "-e", f"MODE={mode}", "-e", "CLASS=academic",
                   "--entrypoint", "deno", self.a.research_image, "run", "--allow-net", "--allow-env", "--allow-read",
                   "/replay/replay_academic.ts", check=False, timeout=900)
            line = next((x for x in r.stdout.splitlines() if x.startswith("REPLAY ")), "")
            results[mode] = json.loads(line[7:]) if line else {"error": (r.stdout + r.stderr)[-1500:]}
        (self.out / "replay.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        # what SearXNG itself said for the first query (engine attribution and unresponsive engines), and its log
        direct = sh("run", "--rm", "--label", LABEL, "--network", self.net, "--entrypoint", "curl", self.a.gateway_image,
                    "-sS", "--max-time", "90", "-G", f"http://{self.names['searxng']}:8080/search", "--data-urlencode", f"q={QUERIES[0]}",
                    "--data-urlencode", "format=json", "--data-urlencode", "categories=general,science", check=False, timeout=180)
        try:
            d = json.loads(direct.stdout)
            (self.out / "searxng-direct.json").write_text(json.dumps({
                "number_of_results": len(d.get("results") or []), "unresponsive_engines": d.get("unresponsive_engines"),
                "engines": sorted({e for r in d.get("results") or [] for e in (r.get("engines") or [r.get("engine")])}),
                "urls": [r.get("url") for r in (d.get("results") or [])][:40]}, indent=2), encoding="utf-8")
        except ValueError:
            (self.out / "searxng-direct.json").write_text(json.dumps({"raw": (direct.stdout + direct.stderr)[-2000:]}), encoding="utf-8")
        log = sh("logs", "--tail", "120", self.names["searxng"], check=False)
        (self.out / "searxng.log").write_text(log.stdout + log.stderr, encoding="utf-8")
        acad = results.get("contract")
        if not isinstance(acad, list):
            self.check("the replay ran", False, acad)
            return
        scholarly = sorted({e for q in acad for h in q["raw"] for e in ([h.get("engine")] + (h.get("engines") or [])) if e in SCHOLARLY})
        pubmed_kept = [u for q in acad for u in q["kept"] if PMID in u and ("ncbi.nlm.nih.gov" in u or "pubmed" in u)]
        cats = sorted({",".join(q["categories_sent"] or []) for q in acad})
        summary = {"warm_up_answers": warm, "categories_sent": cats, "scholarly_engines_answering": scholarly, "pubmed_kept": pubmed_kept,
                   "per_query": [{"query": q["query"], "raw_hits": q["raw_hits"], "kept": q["kept"][:12]} for q in acad],
                   "no_contract_pubmed": [u for q in results.get("none", []) for u in q.get("kept", []) if PMID in u]}
        (self.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)
        if self.a.expect_red:
            self.check("RED: the academic class does not return the PubMed source on the base", not pubmed_kept, pubmed_kept)
        else:
            self.check("the academic class routes the search to general,science", cats == ["general,science"], cats)
            self.check("at least one scholarly index answered", bool(scholarly), scholarly)
            self.check(f"the PubMed source the audit missed (PMID {PMID}) comes back and survives the class", bool(pubmed_kept), pubmed_kept)

    def isolation(self) -> None:
        live = json.loads(sh("network", "inspect", LIVE_SEARCH_NET).stdout)[0]
        mine = [c["Name"] for c in (live.get("Containers") or {}).values() if c["Name"].startswith("rwp5a-")]
        self.check("only the lab SearXNG is on the live search net", mine == [self.names["searxng"]], mine)
        for n in ("gateway", "redis"):
            nets = json.loads(sh("inspect", self.names[n]).stdout)[0]["NetworkSettings"]["Networks"]
            self.check(f"the lab {n} is on the lab net only", list(nets) == [self.net], list(nets))

    def cleanup(self) -> None:
        for c in self.made:
            sh("rm", "-f", "-v", c, check=False)
        if self.net_made:
            sh("network", "rm", self.net, check=False)
        left = sh("ps", "-a", "--filter", f"label={LABEL}", "--format", "{{.Names}}", check=False).stdout.split()
        left = [x for x in left if x.startswith("rwp5a-")]
        self.check("no lab container is left", not left, left)
        nets = [x for x in sh("network", "ls", "--format", "{{.Name}}", check=False).stdout.split() if x.startswith("rwp5a-replay-")]
        self.check("no lab network is left", not nets, nets)
        live = json.loads(sh("network", "inspect", LIVE_SEARCH_NET, check=False).stdout or "[{}]")[0]
        self.check("nothing of the lab remains on the live search net",
                   not [c for c in (live.get("Containers") or {}).values() if c["Name"].startswith("rwp5a-")])
        shutil.rmtree(self.tmp, ignore_errors=True)
        (self.out / "checks.json").write_text(json.dumps(self.checks, indent=2), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--gateway-image", required=True)
    p.add_argument("--research-image", required=True)
    p.add_argument("--settings", required=True, help="a search/searxng directory (settings.yml + limiter.toml)")
    p.add_argument("--out", required=True)
    p.add_argument("--expect-red", action="store_true")
    p.add_argument("--keep", action="store_true", help="leave the lab up (debugging); remove it by its label after")
    a = p.parse_args()
    for img in (a.gateway_image, a.research_image):
        if ":wt-" not in img:
            print(f"refused: {img} is not a :wt-* image")
            return 2
    lab = Lab(a)
    try:
        lab.go()
        lab.isolation()
    except Exception as e:  # noqa: BLE001
        lab.check("the lab ran", False, str(e)[-800:])
    finally:
        if a.keep:
            print(f"KEPT: net {lab.net}, containers {lab.made}")
        else:
            lab.cleanup()
    failed = [c for c in lab.checks if not c["ok"]]
    print(f"== {'ALL PASSED' if not failed else f'FAILED ({len(failed)})'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
