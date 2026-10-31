"""search-post-gate's docker lab (harness item search-post-gate, acceptance lines 1 and 2).

    python tests/docker/run_post_gate_lab.py --gate-image private-search-gateway:wt-search-post-gate
        --fake-image private-search-gateway:wt-search-post-gate --out <evidence dir>
        [--compose-root <dir holding search/>]     # default: this checkout; RED: the base's `search/`

WHAT IT RUNS - one INTERNAL network, 10.243.52.0/24 (outside 192.168.0.0/16), labelled
ai-stack.test=search-post-gate; never an ai-stack_* network, no published port:
  redis    redis:7-alpine with the command search/docker-compose.yml renders, and a password file
  searxng  the REAL pinned image, with --compose-root's search/searxng/settings.yml AS SHIPPED except
           the engine list (replaced by one json_engine onto the fake engine, so no engine needs the
           internet) and `outgoing.proxies` (dropped: the fake engine is on the lab net), and the environment + healthcheck the compose file renders for it
  engine   tests/fake_engine.py
  gate     --gate-image, environment as compose sets it (GATE_DOH_URL left at its default,
           https://dns.mullvad.net/dns-query; GATE_FETCH_PROXY_URL=http://vpn:8888)
  vpn      LAB STAND-IN for the search-vpn container AND for Mullvad (tests/docker/lab_internet.py in
           --fake-image): a forward proxy aliased `vpn`, and behind it an HTTP/2-ONLY DoH endpoint for
           dns.mullvad.net with the shape measured on the live one. The real ones are only probed
           read-only (journal/evidence/search-post-gate/live/).
  intake   FakeIntake (same container as vpn; alias `openbrain-intake-callrelay`): the screen stand-in
LAB-ONLY TRUST: the gate trusts the lab CA through a sitecustomize.py that appends it to certifi's
bundle (mounted into the gate container only; the gate's code has no trust knob).

CHECKS: S SearXNG (the healthcheck command exits 0; / answers 200; no valkey error in its log;
GET /search?format=json through the gateway returns results), G the gate (/v1/fetch of a public URL
is ok; a private/mixed answer is refused with the `resolve` reason; the proxy log shows DoH and the
pinned-IP page connection and nothing else; nothing dialled a private address), N isolation.
Everything is removed in `finally` (unless --keep). Exit 1 if any check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
GATEWAY = TESTS.parent
REPO = GATEWAY.parents[1]
SUBNET = "10.243.52.0/24"
REDIS_IMAGE = "redis:7-alpine"
LABEL = "ai-stack.test=search-post-gate"
PUBLIC_IP = "93.184.215.14"

SITECUSTOMIZE = '''\
import os
import certifi

_orig = certifi.where
_path = "/tmp/lab-ca-bundle.pem"


def _where():
    if not os.path.exists(_path):
        with open(_orig(), "rb") as a, open("/lab/ca.pem", "rb") as b, open(_path, "wb") as out:
            out.write(a.read() + b"\\n" + b.read())
    return _path


certifi.where = _where
'''


def sh(*args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args[:3])}... exit {r.returncode}: {r.stderr[-600:]}")
    return r


class Lab:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.p = "psg-" + secrets.token_hex(3)
        self.net = f"{self.p}-lab"
        self.root = Path(a.compose_root).resolve()
        self.out = Path(a.out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.work = Path(tempfile.mkdtemp(prefix="psg-lab-"))
        self.containers: list[str] = []
        self.results: dict = {"checks": [], "prefix": self.p, "compose_root": str(self.root),
                              "gate_image": a.gate_image,
                              "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        self.redis_pw = "rp-" + secrets.token_hex(24)
        self.callers = {"owui": "ck-owui-" + secrets.token_hex(20)}
        self.intake_key = "ik-" + secrets.token_hex(24)

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.results["checks"].append({"name": name, "ok": bool(ok),
                                       "detail": "" if ok else str(detail)[:600]})
        print(f"  [{'OK' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {str(detail)[:300]}"),
              flush=True)

    def save(self) -> None:
        (self.out / "lab-results.json").write_text(json.dumps(self.results, indent=1, default=str),
                                                   encoding="utf-8")

    def container(self, name: str, image: str, *args: str, aliases: tuple[str, ...] = (),
                  env: dict[str, str] | None = None, cmd: tuple[str, ...] = ()) -> str:
        cname = f"{self.p}-{name}"
        argv = ["run", "-d", "--name", cname, "--label", LABEL, "--network", self.net]
        for al in aliases:
            argv += ["--network-alias", al]
        for k, v in (env or {}).items():
            argv += ["-e", f"{k}={v}"]
        argv += [*args, image, *cmd]
        sh(*argv)
        self.containers.append(cname)
        return cname

    def client(self, plan: list[dict], name: str) -> dict:
        pf = self.work / f"{name}.plan.json"
        pf.write_text(json.dumps(plan), encoding="utf-8")
        of = self.work / f"{name}.out.json"
        if of.exists():
            of.unlink()
        sh("run", "--rm", "--name", f"{self.p}-client-{name}", "--label", LABEL, "--network", self.net,
           "-v", f"{HERE}:/d:ro", "-v", f"{self.work}:/work", "--user", "0", "--entrypoint", "python",
           self.a.fake_image, "/d/gate_client.py", f"/work/{pf.name}", f"/work/{of.name}",
           timeout=600)
        return json.loads(of.read_text(encoding="utf-8"))

    def rendered(self) -> dict:
        r = subprocess.run(
            ["docker", "compose", "-f", str(self.root / "search" / "docker-compose.yml"), "config",
             "--format", "json"], capture_output=True, text=True, encoding="utf-8", timeout=120,
            env={**os.environ, "MULLVAD_WG_PRIVATE_KEY": "x",
                 "SEARXNG_SECRET_KEY": "lab-" + secrets.token_hex(16), "GATEWAY_API_KEY": "x"})
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-500:])
        return json.loads(r.stdout)["services"]

    def wait(self, what: str, fn, timeout_s: int) -> bool:
        end = time.time() + timeout_s
        while time.time() < end:
            if fn():
                return True
            time.sleep(2)
        self.check(f"{what} within {timeout_s}s", False, "timed out")
        return False

    def files(self) -> None:
        w = self.work
        for d in ("secrets/redis", "secrets/gate", "searxng", "pki", "trust"):
            (w / d).mkdir(parents=True)
        # secrets/redis/redis.conf is written LATER (go): the live plane's searxng started against a
        # redis with no password, and redis was recreated with one afterwards
        (w / "secrets/gate/caller_keys").write_text(
            "".join(f"{n}={k}\n" for n, k in self.callers.items()), encoding="utf-8")
        (w / "secrets/gate/intake_key").write_text(self.intake_key + "\n", encoding="utf-8")
        sys.path.insert(0, str(TESTS))
        from h2_doh import make_pki  # noqa: E402
        make_pki(w / "pki")
        (w / "trust/sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
        shipped = yaml.safe_load((self.root / "search/searxng/settings.yml").read_text(encoding="utf-8"))
        self.results["shipped_has_redis_block"] = "redis" in shipped
        shipped["use_default_settings"] = {"engines": {"keep_only": []}}
        shipped["engines"] = [{
            "name": "fakeengine", "engine": "json_engine", "shortcut": "fk",
            "categories": ["general"], "paging": False, "enable_http": True,
            "search_url": "http://fake-engine:8080/query?q={query}&n=10", "results_query": "hits",
            "url_query": "link", "title_query": "headline", "content_query": "summary",
            "disabled": False}]
        shipped["outgoing"]["request_timeout"] = 5.0
        shipped["outgoing"].pop("proxies", None)  # the fake engine is on the lab net, not the internet
        (w / "searxng/settings.yml").write_text(yaml.safe_dump(shipped, sort_keys=False),
                                                encoding="utf-8")

    def go(self) -> None:
        a = self.a
        print(f"prefix {self.p}  compose-root {self.root}", flush=True)
        svc = self.rendered()
        self.files()
        sh("network", "create", "--internal", "--subnet", SUBNET, "--label", LABEL, self.net)
        sec = self.work / "secrets"

        def redis() -> None:
            self.container("redis", REDIS_IMAGE, "--security-opt", "no-new-privileges:true",
                           "-v", f"{sec / 'redis'}:/run/secrets/search-redis:ro",
                           aliases=("redis",), cmd=tuple(svc["redis"]["command"]))
        redis()  # no redis.conf yet: the compose command's no-password fallback, as before the gate
        py = ("--user", "0", "--entrypoint", "python")
        self.container("engine", a.fake_image, *py, "-v", f"{TESTS}:/t:ro", aliases=("fake-engine",),
                       cmd=("/t/fake_engine.py", "8080"))
        self.container("vpn", a.fake_image, *py, "-v", f"{TESTS}:/t:ro",
                       "-v", f"{self.work / 'pki'}:/pki:ro",
                       aliases=("vpn", "openbrain-intake-callrelay"),
                       cmd=("/t/docker/lab_internet.py", "/pki", self.intake_key))
        sx_env = {}
        for item in svc["searxng"].get("environment", {}).items():
            sx_env[item[0]] = item[1]
        self.results["searxng_env_names"] = sorted(sx_env)
        self.container("searxng", svc["searxng"]["image"], "-v",
                       f"{self.work / 'searxng'}:/etc/searxng:rw", aliases=("searxng",), env=sx_env)
        time.sleep(15)  # searxng starts and connects to the passwordless redis (if its settings say so)
        warm = self.client([{"name": "w", "method": "GET", "url": "http://searxng:8080/",
                            "timeout": 30}], "warm")["w"]
        self.results["searxng_/_before_the_password"] = warm["status"]
        (sec / "redis" / "redis.conf").write_text(f"requirepass {self.redis_pw}\n", encoding="utf-8")
        sh("rm", "-f", f"{self.p}-redis")
        redis()  # the gate deploy: redis recreated WITH the password; searxng is NOT restarted
        genv = {
            "GATEWAY_API_KEY": "lk-" + secrets.token_hex(24),
            "SEARCH_ENGINE": "searxng", "SEARCH_ENGINE_URL": "http://searxng:8080",
            "REDIS_URL": "redis://redis:6379/1", "SEARCH_ANONYMOUS_LEGACY": "true",
            "REDIS_PASSWORD_FILE": "/run/secrets/search-redis/redis.conf",
            "GATE_CALLER_KEYS_FILE": "/run/secrets/search-gate/caller_keys",
            "INTAKE_KEY_FILE": "/run/secrets/search-gate/intake_key",
            "INTAKE_URL": "http://openbrain-intake-callrelay:8000/v1/intake",
            "GATE_FETCH_PROXY_URL": "http://vpn:8888", "GATE_FETCH_TIMEOUT_SECONDS": "10",
            "PYTHONPATH": "/lab/trust",
        }
        self.gate_name = self.container(
            "gate", a.gate_image, "--security-opt", "no-new-privileges:true",
            "-v", f"{sec / 'redis'}:/run/secrets/search-redis:ro",
            "-v", f"{sec / 'gate'}:/run/secrets/search-gate:ro",
            "-v", f"{self.work / 'trust'}:/lab/trust:ro", "-v", f"{self.work / 'pki' / 'ca.pem'}:/lab/ca.pem:ro",
            aliases=("gate",), env=genv,
            cmd=("uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8080",
                 "--no-access-log"))
        self.sx = f"{self.p}-searxng"
        self.hc = svc["searxng"]["healthcheck"]["test"][1]

        def up() -> bool:
            r = self.client([{"name": n, "method": "GET", "url": u, "timeout": 5} for n, u in (
                ("gate", "http://gate:8080/healthz"), ("fe", "http://fake-engine:8080/ping"))],
                "boot")
            return all(v["status"] == 200 for v in r.values())
        self.wait("gate and fake engine up", up, 120)
        time.sleep(6)  # searxng's first start
        self.part_s()
        self.part_g()
        self.part_n()

    def part_s(self) -> None:
        hc = sh("exec", self.sx, "sh", "-c", self.hc, check=False)
        self.check("S1 the compose healthcheck command exits 0 inside searxng", hc.returncode == 0,
                   (hc.returncode, hc.stderr[-200:]))
        r = self.client([{"name": "root", "method": "GET", "url": "http://searxng:8080/",
                          "timeout": 20}], "sx-root")["root"]
        self.check("S2 GET http://searxng:8080/ answers 200", r["status"] == 200, r["status"])
        log = sh("logs", self.sx, check=False)
        text = log.stdout + log.stderr
        self.check("S3 searxng's log holds no valkey AuthenticationError",
                   "AuthenticationError" not in text and "Authentication required" not in text,
                   "valkey AuthenticationError in the log")
        d = self.client([{"name": "d", "method": "GET", "url": "http://searxng:8080/search",
                          "params": {"q": "bread", "format": "json"}, "timeout": 60}], "direct")["d"]
        self.results["searxng_direct_json"] = {"status": d["status"], "body": str(d["body"])[:300]}
        s = self.client([{"name": "s", "method": "GET", "url": "http://gate:8080/search",
                          "params": {"q": "bread", "format": "json"}, "timeout": 60}], "legacy")["s"]
        n = len(s["body"].get("results", [])) if isinstance(s["body"], dict) else -1
        self.check("S4 GET /search?format=json through the gateway returns results",
                   s["status"] == 200 and n > 0, (s["status"], n))
        ping = sh("exec", f"{self.p}-redis", "redis-cli", "--no-auth-warning", "ping", check=False)
        self.check("S0 (lab fidelity) the lab redis refuses an unauthenticated client",
                   "NOAUTH" in ping.stdout + ping.stderr, ping.stdout + ping.stderr)

    def part_g(self) -> None:
        h = {"Authorization": "Bearer " + self.callers["owui"]}

        def fetch(name: str, url: str) -> dict:
            return self.client([{"name": name, "method": "POST", "url": "http://gate:8080/v1/fetch",
                                 "headers": h, "json": {"url": url}, "timeout": 60}], name)[name]
        ok = fetch("pub", "http://public.example.com/page")
        body = ok["body"] if isinstance(ok["body"], dict) else {}
        self.check("G1 /v1/fetch of a benign public URL returns status ok",
                   ok["status"] == 200 and body.get("status") == "ok", (ok["status"], str(body)[:300]))
        reason = "refused: the name does not resolve to public addresses only"
        for host in ("private.example.com", "mixed.example.com", "loopback.example.com"):
            r = fetch(host.split(".")[0], f"http://{host}/secret")
            b = r["body"] if isinstance(r["body"], dict) else {}
            self.check(f"G2 {host} (a non-public answer) is refused with the resolve reason",
                       b.get("status") == "refused" and b.get("reason") == reason, str(b)[:300])
        log = sh("logs", f"{self.p}-vpn", check=False)
        # FakeInternet prints from concurrent threads, so lines interleave (`CONNECT a:443CONNECT a:443`):
        # take each target up to the next CONNECT or whitespace, not one per line
        connects = re.findall(r"CONNECT (\S+?)(?=CONNECT |\s|$)", log.stdout + log.stderr)
        self.results["proxy_connects"] = connects
        allowed = {"dns.mullvad.net:443", f"{PUBLIC_IP}:80"}
        self.check("G3 the proxy saw only the DoH tunnel and the pinned public IP, nothing private",
                   bool(connects) and set(connects) <= allowed, connects)
        self.check("G4 the fetch dialled the pinned IP", f"{PUBLIC_IP}:80" in connects, connects)

    def part_n(self) -> None:
        bad = []
        for c in self.containers:
            nets = sh("inspect", "-f", "{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}",
                      c, check=False).stdout.split()
            if nets != [self.net]:
                bad.append((c, nets))
        self.check("N1 every lab container is on the lab network only (no ai-stack_*)", not bad, bad)
        pub = [c for c in self.containers
               if sh("port", c, check=False).stdout.strip()]
        self.check("N2 no lab container publishes a port", not pub, pub)

    def cleanup(self) -> None:
        if self.a.keep:
            return
        for c in self.containers:
            sh("rm", "-f", c, check=False)
        sh("network", "rm", self.net, check=False)
        shutil.rmtree(self.work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate-image", required=True)
    ap.add_argument("--fake-image", required=True,
                    help="image that runs the lab stand-ins (needs h2): the NEW gate image")
    ap.add_argument("--out", required=True)
    ap.add_argument("--compose-root", default=str(REPO))
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    lab = Lab(a)
    try:
        lab.go()
    except Exception as e:  # noqa: BLE001
        lab.check("lab ran to the end", False, repr(e))
    finally:
        lab.save()
        for c in lab.containers:
            for part in ("gate", "searxng", "vpn"):
                if c.endswith("-" + part):
                    r = sh("logs", c, check=False)
                    (lab.out / f"{part}.log").write_text(
                        (r.stdout + r.stderr)[-20000:], encoding="utf-8")
        lab.cleanup()
    bad = [c for c in lab.results["checks"] if not c["ok"]]
    print(f"{len(lab.results['checks']) - len(bad)} OK, {len(bad)} FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
