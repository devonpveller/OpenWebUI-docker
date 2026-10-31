"""The search gate's way in to intake, in the lab (harness item intake-gate-relay).

    python search/gateway/tests/docker/run_gate_relay_e2e.py
        --gate-image private-search-gateway:wt-<id> --intake-image openbrain-intake:wt-<id>
        --model-dir <verified classifier dir> --out <evidence dir> [--keep]

THE LAB IS THE SHIPPED COMPOSE, RENDERED. Both planes are rendered the way check_intake_isolation.py
renders them (`docker compose -f <plane> --env-file <plane>/.env.example [--profile intake] config
--format json`) and each render is TRANSFORMED into a lab project that compose itself starts, so
network membership, aliases, hardening, sysctls, limits and environment are compose's, not ours:
  * only the services under test are kept; `build`, `ports` and `env_file` are dropped, `restart`
    is "no", container names get the run prefix, `:local` images become the --*-image test tags;
  * every network is mapped BY ITS RESOLVED NAME to a lab network `<p>-<key>` - INTERNAL, with a lab
    /24 in 10.243.90-95.0/24 (outside 192.168.0.0/16), labelled ai-stack.test=intake-gate-relay -
    and keeps its kind: a network the render declares natively stays native (compose creates it),
    an external one stays external. The two anchor networks (ai-stack_default, ai-stack_llm-net)
    are created by this runner, as the anchor creates them in production. Never an ai-stack_*
    network, never a published port;
  * stand-ins: `vpn` -> the fake internet (tests/fakes.py: forward proxy + DoH + the stub pages),
    `llm-gateway` -> intake's fake LiteLLM, a fake engine behind the REAL SearXNG image;
    openbrain-db is the render's own definition (its pinned image and initdb chain, so the I1
    migration) without its data volume;
  * bind sources (secrets, the model, SearXNG's settings) point at lab files; lab-only environment
    overrides are listed in the results (`overrides`).
Network map (production name -> lab key): ai-stack_default, search_search-net -> web, snet;
search_intake-callers -> callers; open-brain_obnet -> ob; open-brain_intake-net -> intake;
ai-stack_llm-net -> llm.

PARTS (verdicts in <out>/gate-relay-e2e-results.json):
  S  the search plane alone, on a host without intake (intake-callers absent beforehand): compose
     starts it and it serves today's routes (/healthz, keyless GET /search, POST /v1/search,
     /tavily/search) through the real SearXNG; POST /v1/fetch fails closed (no intake); negative
     control: the same render with intake-callers made external does NOT start
  R  reachability, from INSIDE the gateway: POST /v1/intake via the name it dials reaches intake and
     a screened answer comes back; every other host and port on intake-net (intake's IP, the db
     relay, the llm relay, a /24 sweep) and every other port of the call relay is unreachable; a
     NET_ADMIN peer on intake-callers routing via the relay reaches nothing; the relay has no
     route out
  H  the call relay's runtime hardening equals the two i5b relays' (read-only root, caps dropped,
     no-new-privileges, ip_forward 0, user, limits); half-close through it gets the reply; idle
     close frees the slot (a 3 s instance, as intake-i5b part J)
  E  end to end: a stub page through POST /v1/fetch with a lab caller key (`workbench`) comes back
     `clean`; the TAG audit repro page comes back withheld/refused; intake stored both under
     connector search-gate; with the relay down, then intake down, the gate serves nothing
     unscreened
  N  isolation: every container on this run's lab networks only; every lab network internal
Everything is removed in `finally` (unless --keep), then listed by label to prove zero leftovers.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
GATEWAY = TESTS.parent
REPO = GATEWAY.parents[1]
INTAKE_E2E = REPO / "OB1" / "integrations" / "intake" / "tests" / "e2e"
sys.path.insert(0, str(HERE))
from run_gate_e2e import SEARXNG_SETTINGS, TAG  # noqa: E402

LABEL_K, LABEL_V = "ai-stack.test", "intake-gate-relay"
LABEL = f"{LABEL_K}={LABEL_V}"
NETMAP = {"ai-stack_default": "web", "search_search-net": "snet", "search_intake-callers": "callers",
          "open-brain_obnet": "ob", "open-brain_intake-net": "intake", "ai-stack_llm-net": "llm"}
SUBNETS = {"web": "10.243.90.0/24", "snet": "10.243.91.0/24", "callers": "10.243.92.0/24",
           "ob": "10.243.93.0/24", "intake": "10.243.94.0/24", "llm": "10.243.95.0/24"}
PRECREATED = ("web", "llm")          # the anchor's networks: external in both renders
SEARCH_KEEP = ("vpn", "redis", "searxng", "gateway")
OB1_KEEP = ("openbrain-db", "openbrain-intake", "openbrain-intake-dbrelay", "openbrain-intake-llmrelay",
            "openbrain-intake-callrelay")
RELAYS = ("openbrain-intake-dbrelay", "openbrain-intake-llmrelay", "openbrain-intake-callrelay")
PAGE_TEXT = "ordinary article about bread"


def sh(*args: str, check: bool = True, timeout: int = 900, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace", cwd=cwd)
    if check and r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:4])}... exit {r.returncode}: {r.stderr[-800:]}")
    return r


def render(root: Path, compose: str, example: str, profiles: tuple[str, ...] = ()) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "COMPOSE_PROFILES"}
    argv = ["docker", "compose", "-f", str(root / compose), "--env-file", str(root / example)]
    for p in profiles:
        argv += ["--profile", p]
    r = subprocess.run([*argv, "config", "--format", "json"], capture_output=True, text=True,
                       encoding="utf-8", env=env, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"render {compose}: {r.stderr[-500:]}")
    return json.loads(r.stdout)


class Run:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.root = Path(a.compose_root).resolve()
        self.p = "igr-" + secrets.token_hex(3)
        self.out = Path(a.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.work = Path(tempfile.mkdtemp(prefix="igr-e2e-"))
        self.projects: list[tuple[str, Path]] = []
        self.extra: list[str] = []          # containers made with docker run
        self.networks: list[str] = []       # networks this runner created itself
        self.results: dict = {"checks": [], "prefix": self.p, "compose_root": str(self.root),
                              "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                              "overrides": {}}
        self.redis_pw = "rp-" + secrets.token_hex(24)
        self.legacy_key = "lk-" + secrets.token_hex(24)
        self.caller_key = "ck-workbench-" + secrets.token_hex(20)
        self.intake_key = "ik-" + secrets.token_hex(24)
        self.db_pw = "dbpw-" + secrets.token_hex(12)
        self.pg_pw = "pgpw-" + secrets.token_hex(12)

    # -- bookkeeping ----------------------------------------------------------------------------
    def check(self, name: str, ok: bool, detail: object = "") -> bool:
        self.results["checks"].append({"name": name, "ok": bool(ok), "detail": "" if ok else str(detail)[:900]})
        print(f"  [{'OK' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {str(detail)[:400]}"), flush=True)
        return bool(ok)

    def save(self) -> None:
        (self.out / "gate-relay-e2e-results.json").write_text(json.dumps(self.results, indent=1, default=str),
                                                               encoding="utf-8")

    def lab(self, key: str) -> str:
        return f"{self.p}-{key}"

    def cname(self, container_name: str) -> str:
        return f"{self.p}-{container_name}"

    # -- lab files -------------------------------------------------------------------------------
    def files(self) -> None:
        w = self.work
        (w / "secrets" / "redis").mkdir(parents=True)
        (w / "secrets" / "gate").mkdir(parents=True)
        (w / "secrets" / "redis" / "redis.conf").write_text(f"requirepass {self.redis_pw}\n", encoding="utf-8")
        # M8: `workbench` is the gate's first caller
        (w / "secrets" / "gate" / "caller_keys").write_text(f"workbench={self.caller_key}\n", encoding="utf-8")
        (w / "secrets" / "gate" / "intake_key").write_text(self.intake_key + "\n", encoding="utf-8")
        (w / "intake-secrets").mkdir()
        for fn, v in (("intake_caller_keys", f"search-gate={self.intake_key}"),
                      ("intake_db_password", self.db_pw), ("intake_second_opinion_key", "")):
            (w / "intake-secrets" / fn).write_text(v, encoding="utf-8")
        (w / "searxng").mkdir()
        (w / "searxng" / "settings.yml").write_text(SEARXNG_SETTINGS.format(secret=secrets.token_hex(32)),
                                                   encoding="utf-8")
        (w / "plain-results.json").write_text(json.dumps([{"url": "https://public.example.com/a", "title": "Result A",
                                                           "snippet": "A plain snippet."}]), encoding="utf-8")

    # -- the transform ---------------------------------------------------------------------------
    def transform(self, r: dict, keep: tuple[str, ...], images: dict[str, str], native: str = "") -> dict:
        """`native`: a different prefix for the NATIVE networks and container names (the negative
        control must not collide with the real lab project's names)."""
        svcs = {}
        used: set[str] = set()
        for key in keep:
            if key not in r["services"]:
                continue
            s = copy.deepcopy(r["services"][key])
            # `profiles` too: the render was taken WITH the profile, the lab project starts what it keeps
            for k in ("build", "ports", "expose", "env_file", "pull_policy", "profiles"):
                s.pop(k, None)
            s["restart"] = "no"
            s["container_name"] = native + self.cname(s.get("container_name") or key)
            s["image"] = images.get(s.get("image", ""), s.get("image"))
            s["labels"] = {**(s.get("labels") or {}), LABEL_K: LABEL_V}
            if s.get("depends_on"):
                s["depends_on"] = {d: v for d, v in s["depends_on"].items() if d in keep}
            used |= set(s.get("networks") or {})
            svcs[key] = s
        nets = {}
        for k in used:
            n = r["networks"][k]
            labkey = NETMAP[n["name"]]
            if n.get("external"):
                nets[k] = {"external": True, "name": self.lab(labkey)}
            else:
                nets[k] = {"name": native + self.lab(labkey), "driver": "bridge", "internal": True,
                           "ipam": {"config": [{"subnet": SUBNETS[labkey]}]}, "labels": {LABEL_K: LABEL_V}}
        return {"name": None, "services": svcs, "networks": nets}

    def bind(self, s: dict, target: str, source: Path, ro: bool = True) -> None:
        vols = [v for v in (s.get("volumes") or []) if v.get("target") != target]
        vols.append({"type": "bind", "source": str(source), "target": target, "read_only": ro})
        s["volumes"] = vols

    def search_project(self, r: dict, callers_external: bool = False) -> dict:
        a = self.a
        native = "neg-" if callers_external else ""
        d = self.transform(r, SEARCH_KEEP, {"private-search-gateway:local": a.gate_image}, native)
        sv = d["services"]
        # vpn -> the fake internet (forward proxy, DoH, the stub pages); same name and networks
        vpn = sv["vpn"]
        for k in ("cap_add", "devices", "environment", "healthcheck", "volumes"):
            vpn.pop(k, None)
        vpn.update({"image": a.gate_image, "user": "0", "entrypoint": ["python"], "working_dir": "/t",
                    "command": ["/t/fakes.py", "8888"],
                    "volumes": [{"type": "bind", "source": str(TESTS), "target": "/t", "read_only": True}],
                    "healthcheck": {"test": ["CMD", "python", "-c",
                                             "import socket; socket.create_connection(('127.0.0.1', 8888), 3)"],
                                    "interval": "2s", "retries": 30}})
        sv["fake-engine"] = {"image": a.gate_image, "container_name": native + self.cname("fake-engine"), "user": "0",
                             "entrypoint": ["python"], "command": ["/t/fake_engine.py", "8080",
                                                                   "/w/plain-results.json"],
                             "volumes": [{"type": "bind", "source": str(TESTS), "target": "/t", "read_only": True},
                                         {"type": "bind", "source": str(self.work), "target": "/w",
                                          "read_only": True}],
                             "networks": {"search-net": None}, "labels": {LABEL_K: LABEL_V}, "restart": "no"}
        self.bind(sv["redis"], "/run/secrets/search-redis", self.work / "secrets" / "redis")
        self.bind(sv["searxng"], "/etc/searxng", self.work / "searxng", ro=False)
        sv["searxng"]["environment"]["SEARXNG_SECRET"] = secrets.token_hex(32)
        g = sv["gateway"]
        self.bind(g, "/run/secrets/search-redis", self.work / "secrets" / "redis")
        self.bind(g, "/run/secrets/search-gate", self.work / "secrets" / "gate")
        ov = {"GATEWAY_API_KEY": "<lab legacy key>", "GATE_DOH_URL": "http://doh.test/dns-query",
              "GATE_FETCH_TIMEOUT_SECONDS": "6", "INTAKE_TIMEOUT_SECONDS": "120"}
        self.results["overrides"]["gateway"] = ov
        g["environment"].update({**ov, "GATEWAY_API_KEY": self.legacy_key})
        if callers_external and "intake-callers" in d["networks"]:
            d["networks"]["intake-callers"] = {"external": True, "name": d["networks"]["intake-callers"]["name"]}
        return d

    def ob1_project(self, r: dict) -> dict:
        a = self.a
        d = self.transform(r, OB1_KEEP, {"openbrain-intake:local": a.intake_image})
        sv = d["services"]
        db = sv["openbrain-db"]
        db["volumes"] = [v for v in db.get("volumes") or [] if v.get("type") == "bind"
                         and str(v.get("target", "")).startswith("/docker-entrypoint-initdb.d/")]
        db["environment"]["POSTGRES_PASSWORD"] = self.pg_pw
        self.bind(sv["openbrain-intake"], "/models", Path(a.model_dir))
        sv["llm-gateway"] = {"image": a.intake_image, "container_name": self.cname("fake-llm-gateway"),
                             "entrypoint": ["python"], "command": ["/e2e/fake_litellm.py"],
                             "volumes": [{"type": "bind", "source": str(INTAKE_E2E), "target": "/e2e",
                                          "read_only": True}],
                             "networks": {"llm-net": None}, "labels": {LABEL_K: LABEL_V}, "restart": "no"}
        if "llm-net" not in d["networks"]:
            d["networks"]["llm-net"] = {"external": True, "name": self.lab("llm")}
        d["secrets"] = {k: {"file": str(self.work / "intake-secrets" / k)} for k in r.get("secrets") or {}}
        self.results["overrides"]["openbrain-db"] = {"POSTGRES_PASSWORD": "<lab>", "volumes": "initdb binds only"}
        return d

    def compose(self, name: str, doc: dict, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        f = self.work / f"{name}.json"
        doc = {**doc, "name": name}
        f.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        if (name, f) not in self.projects:
            self.projects.append((name, f))
        return sh("compose", "-p", name, "-f", str(f), *args, check=check, cwd=self.work)

    # -- helpers --------------------------------------------------------------------------------
    def client(self, plan: list[dict], name: str, net: str = "web") -> dict:
        pf = self.work / f"{name}.plan.json"
        pf.write_text(json.dumps(plan), encoding="utf-8")
        of = self.work / f"{name}.out.json"
        if of.exists():
            of.unlink()
        sh("run", "--rm", "--label", LABEL, "--network", self.lab(net), "-v", f"{HERE}:/d:ro",
           "-v", f"{self.work}:/work", "--user", "0", "--entrypoint", "python", self.a.gate_image,
           "/d/gate_client.py", f"/work/{pf.name}", f"/work/{of.name}", timeout=1800)
        return json.loads(of.read_text(encoding="utf-8"))

    def pyexec(self, container: str, code: str, timeout: int = 600) -> dict:
        r = sh("exec", container, "python", "-c", code, check=False, timeout=timeout)
        try:
            return json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"error": (r.stdout + r.stderr)[-600:]}

    def ip(self, container: str, net: str) -> str:
        nets = json.loads(sh("inspect", container).stdout)[0]["NetworkSettings"]["Networks"]
        return nets[self.lab(net)]["IPAddress"]

    def wait(self, what: str, fn, timeout_s: int) -> bool:
        end = time.time() + timeout_s
        while time.time() < end:
            try:
                if fn():
                    return True
            except Exception:  # noqa: BLE001 - keep waiting
                pass
            time.sleep(3)
        return self.check(f"{what} within {timeout_s}s", False, "timed out")

    def psql(self, sql: str) -> list[list[str]]:
        r = sh("exec", "-e", f"PGPASSWORD={self.pg_pw}", self.cname("openbrain-db"), "psql", "-U", "postgres", "-d",
               "openbrain", "-v", "ON_ERROR_STOP=1", "-At", "-F", "\t", "-c", sql)
        return [ln.split("\t") for ln in r.stdout.strip().splitlines() if ln]

    # -- the run --------------------------------------------------------------------------------
    def go(self) -> None:
        print(f"prefix {self.p}  compose root {self.root}", flush=True)
        self.files()
        self.search_r = render(self.root, "search/docker-compose.yml", "search/.env.example")
        self.ob1_r = render(self.root, "OB1/docker/docker-compose.yml", "OB1/docker/.env.example", ("intake",))
        self.results["render"] = {
            "gateway_networks": sorted(self.search_r["services"]["gateway"].get("networks") or {}),
            "intake_networks": sorted(self.ob1_r["services"]["openbrain-intake"].get("networks") or {}),
            "call_relay_present": "openbrain-intake-callrelay" in self.ob1_r["services"],
            "gateway_INTAKE_URL": self.search_r["services"]["gateway"]["environment"].get("INTAKE_URL")}
        for k in PRECREATED:
            sh("network", "create", "--internal", "--subnet", SUBNETS[k], "--label", LABEL, self.lab(k))
            self.networks.append(self.lab(k))
        self.part_s()
        self.ob1_up()
        self.part_r()
        self.part_h()
        self.part_e()
        self.part_n()

    # -- S: the search plane without intake ------------------------------------------------------
    def part_s(self) -> None:
        callers = self.lab("callers")
        absent = sh("network", "inspect", callers, check=False).returncode != 0
        self.check("S intake-callers does not exist before the search plane starts (a host without intake)", absent)
        declared = "intake-callers" in (self.search_r.get("networks") or {})
        if declared:
            negdoc = self.search_project(self.search_r, callers_external=True)
            neg = self.compose(self.p + "-sneg", negdoc, "up", "-d", "--no-start", check=False)
            self.check("S negative control: the same render with intake-callers EXTERNAL does not start "
                       "(so the test can fail)", neg.returncode != 0 and "external" in neg.stderr
                       and self.lab("callers") in neg.stderr, neg.stderr[-300:])
            self.compose(self.p + "-sneg", negdoc, "down", "-v", "--remove-orphans", check=False)
        else:
            self.results["s_negative_control"] = "n/a: the render declares no intake-callers network"
        up = self.compose(self.p + "-s", self.search_project(self.search_r), "up", "-d", "--wait",
                          "--wait-timeout", "300", check=False)
        self.check("S compose starts the search plane on a host without intake (every service up/healthy)",
                   up.returncode == 0, up.stderr[-600:])
        if up.returncode != 0:
            raise RuntimeError("the search plane did not start")
        h = {"Authorization": f"Bearer {self.legacy_key}"}
        ck = {"Authorization": f"Bearer {self.caller_key}"}
        r = self.client([
            {"name": "healthz", "method": "GET", "url": "http://gateway:8080/healthz"},
            {"name": "search", "method": "GET", "url": "http://gateway:8080/search",
             "params": {"q": "bread", "format": "json"}},
            {"name": "v1search", "method": "POST", "url": "http://gateway:8080/v1/search", "headers": h,
             "json": {"query": "bread"}},
            {"name": "tavily", "method": "POST", "url": "http://gateway:8080/tavily/search", "headers": h,
             "json": {"query": "bread", "api_key": self.legacy_key}},
            {"name": "fetch", "method": "POST", "url": "http://gateway:8080/v1/fetch", "headers": ck,
             "json": {"url": "http://public.example.com/page"}, "timeout": 120},
        ], "s-routes")
        self.results["s_routes"] = r
        self.check("S /healthz 200", r["healthz"]["status"] == 200, r["healthz"])
        urls = [x.get("url") for x in (r["search"]["body"] or {}).get("results", [])] if isinstance(
            r["search"]["body"], dict) else []
        self.check("S keyless GET /search (today's legacy lane) answers from SearXNG",
                   r["search"]["status"] == 200 and "https://public.example.com/a" in urls, r["search"])
        self.check("S POST /v1/search with the legacy key answers from searxng",
                   r["v1search"]["status"] == 200 and r["v1search"]["body"].get("provider_used") == "searxng",
                   r["v1search"])
        self.check("S POST /tavily/search answers", r["tavily"]["status"] == 200, r["tavily"])
        f = r["fetch"]
        self.check("S POST /v1/fetch without intake fails closed (not ok, no page text)",
                   (not isinstance(f["body"], dict) or f["body"].get("status") != "ok")
                   and PAGE_TEXT not in json.dumps(f["body"]), f)
        self.results["s_callers_network_after_up"] = sh("network", "inspect", callers, check=False).returncode == 0

    # -- the ob1 side ---------------------------------------------------------------------------
    def ob1_up(self) -> None:
        doc = self.ob1_project(self.ob1_r)
        name = self.p + "-o"
        self.compose(name, doc, "up", "-d", "openbrain-db", "llm-gateway")
        db = self.cname("openbrain-db")

        def ready() -> bool:
            log = sh("logs", db, check=False)
            return (log.stdout + log.stderr).count("database system is ready") >= 2 and sh(
                "exec", db, "pg_isready", "-U", "postgres", "-d", "openbrain", check=False).returncode == 0
        if not self.wait("postgres initdb chain finished", ready, 420):
            raise RuntimeError("postgres did not come up")
        self.psql(f"ALTER ROLE intake PASSWORD '{self.db_pw}'")
        up = self.compose(name, doc, "up", "-d", "--wait", "--wait-timeout", "420", check=False)
        running = set(sh("ps", "--filter", f"label=com.docker.compose.project={name}", "--format",
                         "{{.Label \"com.docker.compose.service\"}}").stdout.split())
        self.check("O compose starts intake + its relays (every service healthy)",
                   up.returncode == 0 and set(doc["services"]) <= running, (up.stderr[-600:], sorted(running)))
        if up.returncode != 0:
            raise RuntimeError("intake did not start")
        self.containers = {k: v["container_name"] for k, v in doc["services"].items()}

    # -- R: reachability from inside the gateway -------------------------------------------------
    def part_r(self) -> None:
        gw = self.cname("search-gateway")
        intake_ip = self.ip(self.cname("openbrain-intake"), "intake")
        targets = {"intake-ip:8000": (intake_ip, 8000)}
        for rel in ("openbrain-intake-dbrelay", "openbrain-intake-llmrelay"):
            ipx = self.ip(self.cname(rel), "intake")
            for port in (5432, 8080, 8000):
                targets[f"{rel}@intake-net:{port}"] = (ipx, port)
        for n, hp in (("name openbrain-intake-dbrelay:5432", ("openbrain-intake-dbrelay", 5432)),
                      ("name openbrain-intake-llmrelay:8080", ("openbrain-intake-llmrelay", 8080)),
                      ("name llama-cpp:8080", ("llama-cpp", 8080)), ("name openbrain-db:5432", ("openbrain-db", 5432)),
                      ("name llm-gateway:8080", ("llm-gateway", 8080))):
            targets[n] = hp
        targets["openbrain-db ip:5432"] = (self.ip(self.cname("openbrain-db"), "ob"), 5432)
        intake_net = SUBNETS["intake"].rsplit(".", 1)[0]
        intake_url = self.results["render"]["gateway_INTAKE_URL"]
        relay_host = urllib.parse.urlsplit(intake_url).hostname
        code = f"""
import json, socket, urllib.request
from concurrent.futures import ThreadPoolExecutor
def conn(h, p, t=2.0):
    try:
        socket.create_connection((h, p), t).close(); return "OPEN"
    except Exception as e:
        return type(e).__name__ + ":" + str(getattr(e, "errno", ""))
out = {{"targets": {{k: conn(*v) for k, v in {json.dumps(targets)}.items()}}}}
try:
    ai = sorted({{a[4][0] for a in socket.getaddrinfo("openbrain-intake", 8000)}})
except Exception as e:
    ai = ["resolve failed: " + type(e).__name__]
out["openbrain-intake resolves to"] = ai
try:
    out["callrelay resolves to"] = sorted({{a[4][0] for a in socket.getaddrinfo("{relay_host}", 8000)}})
except Exception as e:
    out["callrelay resolves to"] = ["resolve failed: " + type(e).__name__]
key = open("/run/secrets/search-gate/intake_key").read().strip()
env = {{"source": "lab-probe", "connector": "search-gate", "external_id": "igr-probe-1",
       "fetched_at": "2026-10-30T00:00:00Z", "content_type": "text/plain", "raw": "A plain note about bread.",
       "metadata": {{}}, "links": []}}
try:
    req = urllib.request.Request("{intake_url}", data=json.dumps(env).encode(),
                                 headers={{"Content-Type": "application/json", "Authorization": "Bearer " + key}},
                                 method="POST")
    r = urllib.request.urlopen(req, timeout=120); body = json.loads(r.read())
    out["post"] = {{"status": r.status, "verdict": body.get("verdict"), "keys": sorted(body)}}
except Exception as e:
    out["post"] = {{"error": type(e).__name__ + ": " + str(e)[:200]}}
with ThreadPoolExecutor(64) as ex:
    sweep = list(ex.map(lambda i: (i, [conn("{intake_net}." + str(i), p, 1.0) for p in (8000, 5432, 8080)]), range(1, 255)))
out["intake-net /24 sweep open"] = [i for i, rs in sweep if "OPEN" in rs]
print(json.dumps(out))
"""
        res = self.pyexec(gw, code)
        self.results["reach"] = res
        if self.results["render"]["call_relay_present"] or "post" in res:
            post = res.get("post") or {}
            self.check("R from the gateway, POST /v1/intake via the name it dials reaches intake and a screened "
                       "answer comes back", post.get("status") == 200 and post.get("verdict") in
                       ("clean", "suspicious", "injected", "pending", "unscreened"), res)
        open_ = {k: v for k, v in (res.get("targets") or {}).items() if v == "OPEN"}
        self.check("R from the gateway: intake's own IP, both i5b relays (by IP, every port) and their names, "
                   "openbrain-db and llm-gateway are unreachable", "targets" in res and not open_, open_ or res)
        self.check("R from the gateway: a sweep of intake-net's /24 (ports 8000/5432/8080) reaches nothing",
                   res.get("intake-net /24 sweep open") == [], res.get("intake-net /24 sweep open"))
        if not self.results["render"]["call_relay_present"]:
            return
        relay = self.cname("openbrain-intake-callrelay")
        rip = self.ip(relay, "callers")
        scan = self.pyexec(gw, f"""
import json, socket
from concurrent.futures import ThreadPoolExecutor
def c(p):
    try:
        socket.create_connection(("{rip}", p), 1.0).close(); return p
    except Exception:
        return None
with ThreadPoolExecutor(128) as ex:
    print(json.dumps({{"open": [p for p in ex.map(c, range(1, 65536)) if p]}}))
""", timeout=900)
        self.results["relay_port_scan"] = scan
        self.check("R the call relay, scanned from the gateway on all 65535 ports, answers on 8000 only",
                   scan.get("open") == [8000], scan)
        self.check("R from the gateway, the INTAKE_URL host is the call relay (its intake-callers address) and "
                   "`openbrain-intake` resolves to nothing",
                   res.get("callrelay resolves to") == [rip]
                   and str((res.get("openbrain-intake resolves to") or [""])[0]).startswith("resolve failed"),
                   (res.get("callrelay resolves to"), res.get("openbrain-intake resolves to")))
        # a NET_ADMIN peer on intake-callers routes intake-net via the relay: ip_forward 0 drops it
        peer = self.cname("netadmin-peer")
        self.extra.append(peer)
        sh("run", "-d", "--name", peer, "--label", LABEL, "--network", self.lab("callers"), "--cap-add", "NET_ADMIN",
           "--entrypoint", "sh", "redis:7-alpine", "-c", "sleep 600")
        sh("exec", peer, "ip", "route", "add", SUBNETS["intake"], "via", rip)
        routed = sh("exec", peer, "sh", "-c", f"nc -z -w 3 {intake_ip} 8000; echo rc=$?", check=False).stdout.strip()
        control = sh("exec", peer, "sh", "-c", f"nc -z -w 3 {rip} 8000; echo rc=$?", check=False).stdout.strip()
        self.results["netadmin"] = {"routed_via_relay": routed, "control_relay_8000": control}
        self.check("R a NET_ADMIN peer on intake-callers routing intake-net via the relay reaches nothing "
                   "(control: the relay's 8000 answers it)", routed.endswith("rc=1") and control.endswith("rc=0"),
                   self.results["netadmin"])
        out = self.pyexec(relay, """
import json, socket
def c(h, p):
    try:
        socket.create_connection((h, p), 3).close(); return "OPEN"
    except Exception as e:
        return type(e).__name__
routes = [l.split()[1] for l in open("/proc/net/route").read().splitlines()[1:]]
print(json.dumps({"1.1.1.1:443": c("1.1.1.1", 443), "8.8.8.8:53": c("8.8.8.8", 53),
                  "openbrain-db:5432": c("openbrain-db", 5432), "llm-gateway:8080": c("llm-gateway", 8080),
                  "default_route": "00000000" in routes,
                  "ip_forward": open("/proc/sys/net/ipv4/ip_forward").read().strip()}))
""")
        self.results["relay_out"] = out
        lg = sh("logs", "--tail", "40", relay, check=False)
        self.results["callrelay_log_tail"] = (lg.stdout + lg.stderr).splitlines()[-40:]
        self.check("R the call relay has no route out (no default route; the internet, openbrain-db and "
                   "llm-gateway unreachable)", out.get("default_route") is False and all(
                       out.get(k) != "OPEN" for k in ("1.1.1.1:443", "8.8.8.8:53", "openbrain-db:5432",
                                                      "llm-gateway:8080")), out)

    # -- H: hardening, half-close, idle ----------------------------------------------------------
    def part_h(self) -> None:
        if not self.results["render"]["call_relay_present"]:
            self.check("H the call relay exists", False, "no openbrain-intake-callrelay in the render")
            return
        rows = {}
        for rel in RELAYS:
            i = json.loads(sh("inspect", self.cname(rel)).stdout)[0]
            hc = i["HostConfig"]
            rows[rel] = {"ReadonlyRootfs": hc.get("ReadonlyRootfs"), "CapDrop": hc.get("CapDrop"),
                         "CapAdd": hc.get("CapAdd"), "SecurityOpt": hc.get("SecurityOpt"),
                         "Sysctls": hc.get("Sysctls"), "Privileged": hc.get("Privileged"),
                         "User": i["Config"].get("User"), "Memory": hc.get("Memory"), "NanoCpus": hc.get("NanoCpus"),
                         "PidsLimit": hc.get("PidsLimit"), "Image": i["Config"].get("Image"),
                         "Cmd": i["Config"].get("Cmd"),
                         "ip_forward": sh("exec", self.cname(rel), "cat", "/proc/sys/net/ipv4/ip_forward").stdout.strip()}
        self.results["relay_hardening"] = rows
        call = rows["openbrain-intake-callrelay"]
        self.check("H the call relay's runtime hardening equals both i5b relays' (inspect: read-only root, caps, "
                   "no-new-privileges, sysctls, user, limits, image, command, ip_forward)",
                   call == rows["openbrain-intake-dbrelay"] == rows["openbrain-intake-llmrelay"], rows)
        self.check("H ...and that hardening is: read-only root, CapDrop ALL, no-new-privileges, ip_forward 0, uid 10030",
                   call["ReadonlyRootfs"] is True and call["CapDrop"] == ["ALL"] and not call["Privileged"]
                   and "no-new-privileges:true" in (call["SecurityOpt"] or []) and call["ip_forward"] == "0"
                   and call["User"] == "10030:10030", call)
        gw = self.cname("search-gateway")
        gfwd = sh("exec", gw, "cat", "/proc/sys/net/ipv4/ip_forward").stdout.strip()
        self.results["gateway_ip_forward"] = gfwd
        self.check("H search-gateway runs with net.ipv4.ip_forward=0 (tester T-F4)", gfwd == "0", gfwd)
        hc = self.pyexec(gw, """
import json, socket
s = socket.create_connection(("openbrain-intake-callrelay", 8000), 10)
s.sendall(b"GET /healthz HTTP/1.1\\r\\nHost: openbrain-intake\\r\\nConnection: close\\r\\n\\r\\n")
s.shutdown(socket.SHUT_WR)
s.settimeout(20)
data = b""
while True:
    b = s.recv(65536)
    if not b:
        break
    data += b
print(json.dumps({"first_line": data.split(b"\\r\\n", 1)[0].decode("latin-1")}))
""")
        self.results["half_close"] = hc
        self.check("H a gateway client that half-closes after its request still gets the reply through the call relay",
                   " 200 " in hc.get("first_line", ""), hc)
        # idle close: the relay code under the call relay's flags, with a 3 s idle and one slot (part J's method)
        i = json.loads(sh("inspect", self.cname("openbrain-intake-callrelay")).stdout)[0]
        env = {e.split("=", 1)[0]: e.split("=", 1)[1] for e in i["Config"]["Env"] if e.startswith("INTAKE_RELAY_")}
        env.update({"INTAKE_RELAY_IDLE_SECONDS": "3", "INTAKE_RELAY_MAX_CONNECTIONS": "1"})
        idle = self.cname("idlerelay")
        self.extra.append(idle)
        argv = ["create", "--name", idle, "--label", LABEL, "--network", self.lab("callers"), "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--sysctl", "net.ipv4.ip_forward=0",
                "--user", "10030:10030", "--network-alias", "idlerelay"]
        for k, v in env.items():
            argv += ["-e", f"{k}={v}"]
        sh(*argv, self.a.intake_image, "relay")
        sh("network", "connect", self.lab("intake"), idle)
        sh("start", idle)
        time.sleep(2)
        got = self.pyexec(gw, """
import json, socket, time
a = socket.create_connection(("idlerelay", 8000), 5)
time.sleep(5)
a.settimeout(3)
try:
    closed = a.recv(1) == b""
except Exception as e:
    closed = type(e).__name__
b = socket.create_connection(("idlerelay", 8000), 5)
b.sendall(b"GET /healthz HTTP/1.1\\r\\nHost: x\\r\\nConnection: close\\r\\n\\r\\n")
b.settimeout(20)
print(json.dumps({"idle_closed": closed, "second": b.recv(64).split(b"\\r\\n", 1)[0].decode("latin-1")}))
""")
        self.results["relay_idle"] = got
        self.check("H the relay closes a connection idle for INTAKE_RELAY_IDLE_SECONDS (3 s here) and frees its slot",
                   got.get("idle_closed") is True and " 200 " in got.get("second", ""), got)
        sh("rm", "-f", "-v", idle, check=False)

    # -- E: end to end -----------------------------------------------------------------------------
    def fetch(self, name: str, paths: list[str]) -> dict:
        ck = {"Authorization": f"Bearer {self.caller_key}"}
        return self.client([{"name": p.strip("/"), "method": "POST", "url": "http://gateway:8080/v1/fetch",
                             "headers": ck, "json": {"url": f"http://public.example.com{p}"}, "timeout": 300}
                            for p in paths], name)

    def part_e(self) -> None:
        r = self.fetch("e2e", ["/page", "/tag"])
        self.results["e2e"] = r
        c, t = r["page"]["body"], r["tag"]["body"]
        c = c if isinstance(c, dict) else {}
        t = t if isinstance(t, dict) else {}
        self.check("E a stub page through POST /v1/fetch (caller `workbench`) comes back clean, screened by the real "
                   "intake image", c.get("status") == "ok" and (c.get("labels") or {}).get("verdict") == "clean"
                   and (c.get("labels") or {}).get("connector") == "search-gate" and PAGE_TEXT in (c.get("text") or ""), c)
        self.check("E the TAG audit repro page comes back withheld or refused, with no text",
                   t.get("status") in ("withheld", "refused") and t.get("text") is None
                   and TAG not in json.dumps(t, ensure_ascii=False), t)
        rows = self.psql("SELECT external_id, verdict, connector FROM intake.items WHERE source = 'web-page' "
                         "ORDER BY external_id")
        self.check("E intake stored both under connector search-gate (page clean, repro injected)",
                   rows == [["http://public.example.com/page", "clean", "search-gate"],
                            ["http://public.example.com/tag", "injected", "search-gate"]], rows)
        for what, container in (("the call relay", "openbrain-intake-callrelay"), ("intake", "openbrain-intake")):
            if sh("inspect", self.cname(container), check=False).returncode != 0:
                self.check(f"E {what} exists (to take it down)", False, "absent")
                continue
            sh("stop", "-t", "5", self.cname(container))
            down = self.fetch(f"down-{container}", ["/page", "/tag"])
            self.results[f"down_{container}"] = down
            body = json.dumps(down, ensure_ascii=False)
            self.check(f"E with {what} down the gate fails closed: neither page is ok and no page text is served",
                       all(not isinstance(v["body"], dict) or v["body"].get("status") != "ok" for v in down.values())
                       and PAGE_TEXT not in body and TAG not in body and "Nice recipe" not in body, down)
            sh("start", self.cname(container))
            if container == "openbrain-intake":
                self.wait("intake healthy again", lambda c=container: json.loads(sh("inspect", self.cname(c)).stdout)[0]
                          ["State"].get("Health", {}).get("Status") == "healthy", 300)
        again = self.fetch("again", ["/page"])
        self.check("E after both come back the clean page is served again",
                   isinstance(again["page"]["body"], dict) and again["page"]["body"].get("status") == "ok", again)

    # -- N: isolation ------------------------------------------------------------------------------
    def part_n(self) -> None:
        ids = sh("ps", "-aq", "--filter", f"label={LABEL}", "--filter", f"name={self.p}").stdout.split()
        bad = {}
        for cid in ids:
            i = json.loads(sh("inspect", cid).stdout)[0]
            nets = list(i["NetworkSettings"]["Networks"])
            if any(n.startswith("ai-stack_") or not n.startswith(self.p) for n in nets) or i["HostConfig"].get(
                    "PortBindings"):
                bad[i["Name"]] = nets
        self.check("N every container is on this run's lab networks only, none publishes a port", not bad, bad)
        for nid in sh("network", "ls", "-q", "--filter", f"label={LABEL}", "--filter", f"name={self.p}").stdout.split():
            info = json.loads(sh("network", "inspect", nid).stdout)[0]
            sub = info["IPAM"]["Config"][0]["Subnet"]
            self.check(f"N {info['Name']} is internal, {sub} in the lab range",
                       info["Internal"] is True and sub.startswith("10.243.9"), info["Internal"])

    # -- teardown --------------------------------------------------------------------------------
    def own_volumes(self) -> set[str]:
        """Volumes attributable to THIS run (round 3, TG2-6: a whole-host diff false-FAILed on other
        testers' volumes): every volume a container of this run mounts (its prefix, our label) plus
        every volume compose labelled with one of this run's project names."""
        vols: set[str] = set()
        ids = sh("ps", "-aq", "--filter", f"label={LABEL}", check=False).stdout.split()
        for cid in ids:
            i = json.loads(sh("inspect", cid).stdout)[0]
            if not i["Name"].lstrip("/").startswith(self.p):
                continue
            vols |= {m["Name"] for m in i.get("Mounts") or [] if m.get("Type") == "volume" and m.get("Name")}
        for name, _f in self.projects:
            vols |= set(sh("volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={name}",
                           check=False).stdout.split())
        return vols

    def cleanup(self) -> None:
        mine = self.own_volumes()
        for c in self.extra:
            sh("rm", "-f", "-v", c, check=False)   # -v: the peer's image declares VOLUME /data (T-F7)
        for name, f in reversed(self.projects):
            sh("compose", "-p", name, "-f", str(f), "down", "-v", "--remove-orphans", check=False, cwd=self.work)
        for n in self.networks:
            sh("network", "rm", n, check=False)
        # this run's only (its prefix): another tester's lab carries the same label (TG2-6)
        left_c = sh("ps", "-a", "--filter", f"label={LABEL}", "--filter", f"name={self.p}",
                    "--format", "{{.Names}}").stdout.split()
        left_n = sh("network", "ls", "--filter", f"label={LABEL}", "--filter", f"name={self.p}",
                    "--format", "{{.Name}}").stdout.split()
        # this run's volumes, labelled or ANONYMOUS (T-F7: a peer's anonymous volume survived `rm -f`),
        # and only this run's (TG2-6)
        left_v = sorted(mine & set(sh("volume", "ls", "-q").stdout.split()))
        self.results["own_volumes_seen"] = len(mine)
        self.results["leftovers"] = {"containers": left_c, "networks": left_n, "volumes": left_v}
        shutil.rmtree(self.work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate-image", required=True)
    ap.add_argument("--intake-image", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--compose-root", default=str(REPO), help="the checkout whose compose files are rendered")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    for img in (a.gate_image, a.intake_image):
        assert ":wt-" in img and ":local" not in img, "test images only (:wt-*)"
    run = Run(a)
    try:
        run.go()
    except Exception as e:  # noqa: BLE001
        run.check("the run completed", False, repr(e))
    finally:
        run.results["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not a.keep:
            run.cleanup()
            lo = run.results["leftovers"]
            run.check("zero leftovers (containers, networks, volumes labelled for this run)",
                      not (lo["containers"] or lo["networks"] or lo["volumes"]), lo)
        run.save()
    checks = run.results["checks"]
    bad = [c for c in checks if not c["ok"]]
    print(f"{len(checks) - len(bad)}/{len(checks)} checks passed", flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
