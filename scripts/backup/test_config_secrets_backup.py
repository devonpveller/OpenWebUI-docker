"""Tests for scripts/backup/config_secrets_backup.py (config-backup, 2026-10-04).

Every test builds a SCRATCH git repo under pytest's tmp_path holding FAKE values
only (the fixture value string below) - nothing reads this checkout's or the live
host's gitignored files. No Docker daemon: the bind-mount renders are injected,
except the end-to-end coverage-gate test, which runs the real `docker compose
config` (CLI only; DOCKER_HOST is forced to the dead endpoint tcp://127.0.0.1:1).

The encryption tests need the official age binaries. Point
CONFIG_SECRETS_TEST_AGE_DIR at a folder holding age(.exe) and age-keygen(.exe)
from the release pinned in config-secrets.toml (the test plan says how to fetch
and verify it). A TEST key pair is generated per test in tmp_path and deleted at
teardown. Without the variable those tests SKIP and say so.

Run:  python -m pytest scripts/backup/test_config_secrets_backup.py -q
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import config_secrets_backup as csb  # noqa: E402

SHIPPED_POLICY = HERE / "config-secrets.toml"
REPO = HERE.parent.parent
EXE = ".exe" if os.name == "nt" else ""
AGE_DIR = os.environ.get("CONFIG_SECRETS_TEST_AGE_DIR", "")
MARK = "fixture-value-" + uuid.uuid4().hex  # appears in every fake file, nowhere else

os.environ["DOCKER_HOST"] = "tcp://127.0.0.1:1"


def _age_bins():
    if not AGE_DIR:
        return None
    age = Path(AGE_DIR) / f"age{EXE}"
    keygen = Path(AGE_DIR) / f"age-keygen{EXE}"
    if age.is_file() and keygen.is_file():
        return age, keygen
    return None


needs_age = pytest.mark.skipif(_age_bins() is None,
                               reason="CONFIG_SECRETS_TEST_AGE_DIR does not hold age + age-keygen")


# ----------------------------------------------------------------------------- fixtures

GITIGNORE = """.env
.env.bak*
secrets/
backups/
logs/
.stack/
portal/config/authelia/users_database.yml
portal/config/alerter/token.json
"""

MANIFEST = """[planes.anchor]
compose = "docker-compose.yml"

[planes.portal]
compose = "portal/docker-compose.yml"

[planes.portal.profiles.internet]
opt_in = true
"""

COMPOSE = """services:
  authelia:
    image: example/authelia:fixture
    volumes:
      - ./config/authelia/configuration.yml:/config/configuration.yml:ro
      - ./config/authelia/users_database.yml:/config/users_database.yml
      - ../secrets/alerter-token.json:/run/token.json:ro
  alerter:
    image: example/alerter:fixture
    profiles: [internet]
    volumes:
      - ./config/alerter/token.json:/app/token.json
"""

UNTRACKED = {
    ".env": f"EXAMPLE_ROOT_SETTING={MARK}-root\n",
    ".env.bak-pre-change": f"EXAMPLE_ROOT_SETTING={MARK}-old\n",
    "portal/.env": f"EXAMPLE_PORTAL_SETTING={MARK}-portal\n",
    "portal/config/authelia/users_database.yml": f"users:\n  someone:\n    note: {MARK}-users\n",
    "portal/config/alerter/token.json": json.dumps({"note": f"{MARK}-token"}) + "\n",
    "secrets/alerter-token.json": json.dumps({"note": f"{MARK}-secret"}) + "\n",
    "secrets/deploy_key": f"{MARK}-deploy-key\n",
    ".stack/state.json": json.dumps({"planes": ["portal"], "note": MARK}) + "\n",
    "notes/settings.env.example": "EXAMPLE=placeholder\n",  # ignored by name pattern
}

TRACKED = {
    ".gitignore": GITIGNORE,
    "stack.manifest.toml": MANIFEST,
    "docker-compose.yml": "services: {}\n",
    "portal/docker-compose.yml": COMPOSE,
    "portal/config/authelia/configuration.yml": "server: {}\n",
    "portal/config/alerter/alerter.ts": "export {};\n",
    "portal/.env.example": "EXAMPLE_PORTAL_SETTING=change-me\n",
}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                           "-c", "core.autocrlf=false", "-C", str(root), *args],
                          capture_output=True, text=True, check=True)


def write(root: Path, files: dict) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    write(root, TRACKED)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    write(root, UNTRACKED)
    return root


@pytest.fixture
def policy(tmp_path: Path) -> Path:
    """The SHIPPED policy (so its pins, patterns and required list are what is tested),
    with the fixture's own recipients path."""
    p = tmp_path / "policy.toml"
    shutil.copyfile(SHIPPED_POLICY, p)
    return p


def fake_renderer(root: Path, fail: tuple = ()):
    """What `docker compose config` would return for the fixture compose file."""
    def render(plane, spec):
        if plane != "portal":
            return [("(no profile)", {"services": {}})]
        base = {"services": {"authelia": {"volumes": [
            {"type": "bind", "source": str(root / "portal/config/authelia/configuration.yml")},
            {"type": "bind", "source": str(root / "portal/config/authelia/users_database.yml")},
            {"type": "bind", "source": str(root / "secrets/alerter-token.json")},
        ]}}}
        with_profile = json.loads(json.dumps(base))
        with_profile["services"]["alerter"] = {"volumes": [
            {"type": "bind", "source": str(root / "portal/config/alerter/token.json")}]}
        out = [("(this host's profiles)", base), ("(no profile)", base), ("internet", with_profile)]
        return [(label, None if label in fail else r) for label, r in out]
    return render


@pytest.fixture
def keys(tmp_path: Path):
    """A TEST age key pair in scratch; the private key is deleted at teardown."""
    bins = _age_bins()
    assert bins
    age, keygen = bins
    kdir = tmp_path / "test-key"
    kdir.mkdir()
    key = kdir / "test-identity.txt"
    subprocess.run([str(keygen), "-o", str(key)], check=True, capture_output=True)
    pub = next(ln.split(":", 1)[1].strip() for ln in key.read_text().splitlines()
               if ln.startswith("# public key:"))
    yield age, key, pub
    shutil.rmtree(kdir, ignore_errors=True)
    assert not key.exists()


def install_recipient(root: Path, pub: str) -> Path:
    p = root / "secrets/config-backup/age-recipients.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# test recipient\n{pub}\n", encoding="utf-8")
    return p


def run_cli(root: Path, policy: Path, *args: str, tmp: Path, extra_env=None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # All temp locations of the job's process point at one scratch dir, so the
    # no-plaintext proof can look there.
    tdir = tmp / "job-temp"
    tdir.mkdir(exist_ok=True)
    env.update({"TMP": str(tdir), "TEMP": str(tdir), "TMPDIR": str(tdir), "DOCKER_HOST": "tcp://127.0.0.1:1"})
    env.update(extra_env or {})
    return subprocess.run([sys.executable, str(HERE / "config_secrets_backup.py"), *args,
                           "--repo-root", str(root), "--policy", str(policy),
                           "--logs-dir", str(tmp / "logs")],
                          capture_output=True, text=True, env=env, timeout=600)


# ----------------------------------------------------------------------------- policy

def test_shipped_policy_loads_and_requires_the_users_db():
    pol = csb.load_policy(SHIPPED_POLICY)
    assert pol.retain_count >= 1 and pol.age_sha256
    assert {e["path"] for e in pol.required} >= {"portal/config/authelia/users_database.yml",
                                                 ".stack/state.json"}
    assert "backups" in pol.prune_paths and "secrets" in pol.secret_dirs


def test_policy_key_after_a_table_header_is_refused(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('[[age_pin]]\nsha256 = "00"\nretain_count = 3\n', encoding="utf-8")
    with pytest.raises(csb.Refused, match="belongs to that table"):
        csb.load_policy(p)


def test_exclude_without_reason_is_refused(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('[[exclude]]\nglob = "*.lock"\n', encoding="utf-8")
    with pytest.raises(csb.Refused, match="needs a reason"):
        csb.load_policy(p)


# ----------------------------------------------------------------------------- inventory

def test_inventory_derives_env_secrets_binds_required(repo, policy):
    pol = csb.load_policy(policy)
    inv = csb.derive_inventory(repo, pol, renderer=fake_renderer(repo))
    assert inv.problems == []
    assert set(inv.files) == {
        ".env", ".env.bak-pre-change", "portal/.env",
        "portal/config/authelia/users_database.yml", "portal/config/alerter/token.json",
        "secrets/alerter-token.json", "secrets/deploy_key", ".stack/state.json",
    }
    # tracked files and *.example are never archived
    assert "portal/config/authelia/configuration.yml" not in inv.files
    assert "notes/settings.env.example" not in inv.files


def test_bind_behind_an_off_profile_is_still_found(repo, policy):
    # token.json is mounted only under the `internet` profile and is not under any
    # secret dir nor named like an env file: only the per-profile render finds it.
    pol = csb.load_policy(policy)
    pol.include = []
    inv = csb.derive_inventory(repo, pol, renderer=fake_renderer(repo))
    assert inv.files["portal/config/alerter/token.json"] == "bind:portal"


def test_missing_users_db_and_docker_made_directory_are_errors(repo, policy):
    pol = csb.load_policy(policy)
    (repo / "portal/config/authelia/users_database.yml").unlink()
    inv = csb.derive_inventory(repo, pol, renderer=fake_renderer(repo))
    kinds = {(k, w) for k, w, _ in inv.problems}
    assert ("MISSING", "portal/config/authelia/users_database.yml") in kinds
    # The 2026-10-04 shape: Docker created an EMPTY DIRECTORY where the file was.
    (repo / "portal/config/authelia/users_database.yml").mkdir()
    inv = csb.derive_inventory(repo, pol, renderer=fake_renderer(repo))
    details = [d for k, w, d in inv.problems if w == "portal/config/authelia/users_database.yml"]
    assert details and any("DIRECTORY" in d for d in details)


def test_required_users_db_only_while_portal_is_configured(repo, policy):
    pol = csb.load_policy(policy)
    (repo / "portal/config/authelia/users_database.yml").unlink()
    (repo / "portal/.env").unlink()  # this host does not run the portal plane
    inv = csb.derive_inventory(repo, pol, renderer=fake_renderer(repo))
    assert not [p for p in inv.problems if p[1] == "portal/config/authelia/users_database.yml"]


def test_render_failure_oversize_outside_and_exclude(repo, policy, tmp_path):
    pol = csb.load_policy(policy)
    pol.max_file_bytes = 64
    (repo / "secrets/big.bin").write_bytes(b"x" * 65)
    (repo / "portal/docker.lock").write_text("x", encoding="utf-8")
    outside = tmp_path / "outside-cred.json"
    outside.write_text("{}", encoding="utf-8")
    base_render = fake_renderer(repo, fail=("internet",))

    def render(plane, spec):
        out = base_render(plane, spec)
        if plane == "portal":
            out[0][1]["services"]["authelia"]["volumes"].append({"type": "bind", "source": str(outside)})
        return out
    inv = csb.derive_inventory(repo, pol, renderer=render)
    kinds = {k for k, _, _ in inv.problems}
    assert {"RENDER", "OVERSIZE", "OUTSIDE"} <= kinds
    assert "secrets/big.bin" not in inv.files
    # an [[outside]] entry with a reason accounts for the outside file
    pol.outside_ok = [{"path": str(outside), "reason": "fixture: backed up elsewhere"}]
    inv = csb.derive_inventory(repo, pol, renderer=render)
    assert "OUTSIDE" not in {k for k, _, _ in inv.problems}


# ----------------------------------------------------------------------------- refusals

def test_run_refuses_loudly_without_a_recipient(repo, policy, tmp_path):
    proc = run_cli(repo, policy, "run", tmp=tmp_path)
    assert proc.returncode == csb.EXIT_REFUSED
    assert "NO AGE RECIPIENT CONFIGURED" in proc.stdout
    assert not (repo / "backups").exists()  # nothing written at all
    log = next((tmp_path / "logs").glob("config-secrets-backup-*.log")).read_text(encoding="utf-8")
    assert "[ERROR] REFUSED" in log and csb.SUCCESS_MARKER not in log


def test_run_refuses_a_private_key_in_the_recipients_file(repo, policy, tmp_path):
    p = install_recipient(repo, "age1placeholder")
    p.write_text("AGE-SECRET-KEY-1NOTAREALKEYJUSTTHEPREFIXFORTHETEST\n", encoding="utf-8")
    proc = run_cli(repo, policy, "run", tmp=tmp_path)
    assert proc.returncode == csb.EXIT_REFUSED
    assert "PRIVATE key" in proc.stdout
    assert "NOTAREALKEY" not in proc.stdout  # the line itself is never echoed


def test_run_refuses_an_unpinned_age_binary(repo, policy, tmp_path):
    install_recipient(repo, "age1" + "q" * 58)
    fake = tmp_path / f"age{EXE}"
    fake.write_bytes(b"not the pinned age build")
    proc = run_cli(repo, policy, "run", "--age", str(fake), tmp=tmp_path)
    assert proc.returncode == csb.EXIT_REFUSED
    assert "not a pinned age build" in proc.stdout


# ----------------------------------------------------------------------------- the archive

def _patch_render(monkeypatch, repo):
    monkeypatch.setattr(csb, "default_renderer", lambda root: fake_renderer(repo))


def _decrypt_extract(age: Path, key: Path, archive: Path, dest: Path) -> list[str]:
    plain = subprocess.run([str(age), "--decrypt", "--identity", str(key), str(archive)],
                           capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:") as tar:
        names = tar.getnames()
        tar.extractall(dest, filter="data")
    return names


@needs_age
def test_shipped_pin_matches_the_age_under_test():
    age, _ = _age_bins()
    assert csb.sha256_file(age) in csb.load_policy(SHIPPED_POLICY).age_sha256


@needs_age
def test_run_encrypts_leaves_no_plaintext_and_restores_byte_identical(repo, policy, tmp_path, keys, monkeypatch):
    age, key, pub = keys
    install_recipient(repo, pub)
    _patch_render(monkeypatch, repo)
    out_dir = tmp_path / "backups-out"
    rc = csb.main(["run", "--repo-root", str(repo), "--policy", str(policy), "--age", str(age),
                   "--backups-dir", str(out_dir), "--logs-dir", str(tmp_path / "logs"), "--no-pin"])
    assert rc == csb.EXIT_OK
    sets = csb.complete_sets(out_dir)
    assert len(sets) == 1
    s = sets[0]
    # sidecars: sha256 in the sentinel format the NAS sync verifies
    digest, name = s.sha.read_text(encoding="ascii").split()
    assert name == s.name and digest == csb.sha256_file(s.path)
    assert s.path.read_bytes().startswith(csb.AGE_MAGIC)
    # NO PLAINTEXT anywhere the job could have written: the output dir, the logs,
    # the job's temp dir. (In-process run: TEMP is this test's; scan tmp_path whole,
    # minus the repo's own source files and the test key.)
    for p in tmp_path.rglob("*"):
        if not p.is_file() or repo in p.parents or p == key:
            continue
        assert MARK.encode() not in p.read_bytes(), f"plaintext marker found in {p}"
    assert not list(out_dir.glob("*.partial"))
    assert set(os.listdir(out_dir)) == {s.name, s.sha.name, s.listing.name}
    # restore with the TEST private key: byte-identical
    dest = tmp_path / "restore"
    names = _decrypt_extract(age, key, s.path, dest)
    assert set(names) == s.files()
    for rel in names:
        assert (dest / rel).read_bytes() == (repo / rel).read_bytes(), rel
    log = next((tmp_path / "logs").glob("config-secrets-backup-*.log")).read_text(encoding="utf-8")
    assert csb.SUCCESS_MARKER in log and MARK not in log
    shutil.rmtree(dest)


@needs_age
def test_cli_run_in_a_child_process_writes_no_plaintext_temp(repo, policy, tmp_path, keys):
    """The same proof for the real entry point (child process, its own TEMP)."""
    age, key, pub = keys
    install_recipient(repo, pub)
    # no portal compose render without docker: use a manifest-less repo copy
    (repo / "stack.manifest.toml").unlink()
    proc = run_cli(repo, policy, "run", "--age", str(age), "--no-pin", tmp=tmp_path)
    # .stack/state.json etc. are present; users DB is required only while the portal
    # plane is rendered, so this run has no problems
    assert proc.returncode == csb.EXIT_OK, proc.stdout + proc.stderr
    assert MARK not in proc.stdout + proc.stderr
    temp = tmp_path / "job-temp"
    assert [p for p in temp.rglob("*") if p.is_file()] == []
    for p in (repo / "backups").rglob("*"):
        if p.is_file():
            assert MARK.encode() not in p.read_bytes()


@needs_age
def test_missing_required_file_still_archives_but_does_not_complete(repo, policy, tmp_path, keys, monkeypatch):
    age, key, pub = keys
    install_recipient(repo, pub)
    _patch_render(monkeypatch, repo)
    (repo / ".stack/state.json").unlink()
    out_dir = tmp_path / "out"
    rc = csb.main(["run", "--repo-root", str(repo), "--policy", str(policy), "--age", str(age),
                   "--backups-dir", str(out_dir), "--logs-dir", str(tmp_path / "logs"), "--no-pin"])
    assert rc == csb.EXIT_PROBLEMS
    assert len(csb.complete_sets(out_dir)) == 1
    log = next((tmp_path / "logs").glob("config-secrets-backup-*.log")).read_text(encoding="utf-8")
    assert "[ERROR] MISSING .stack/state.json" in log
    assert csb.SUCCESS_MARKER not in log


# ----------------------------------------------------------------------------- coverage gate

@needs_age
def test_coverage_fails_until_the_job_covers_every_file(repo, policy, tmp_path, keys, monkeypatch):
    age, key, pub = keys
    pol = csb.load_policy(policy)
    out_dir = tmp_path / "out"
    render = fake_renderer(repo)
    # no recipient, no archive -> NOT-CONFIGURED + UNCOVERED
    _, gaps = csb.coverage(repo, pol, out_dir, renderer=render)
    kinds = {k for k, _, _ in gaps}
    assert {"NOT-CONFIGURED", "UNCOVERED"} <= kinds
    assert ("UNCOVERED", "portal/config/authelia/users_database.yml") in {(k, w) for k, w, _ in gaps}
    # the job runs -> clean
    install_recipient(repo, pub)
    _patch_render(monkeypatch, repo)
    assert csb.main(["run", "--repo-root", str(repo), "--policy", str(policy), "--age", str(age),
                     "--backups-dir", str(out_dir), "--logs-dir", str(tmp_path / "logs"),
                     "--no-pin"]) == csb.EXIT_OK
    _, gaps = csb.coverage(repo, pol, out_dir, renderer=render)
    assert gaps == []
    # a NEW plane .env appears -> uncovered until the next run
    write(repo, {"search/.env": f"EXAMPLE_SEARCH_SETTING={MARK}\n"})
    _, gaps = csb.coverage(repo, pol, out_dir, renderer=render)
    assert [(k, w) for k, w, _ in gaps] == [("UNCOVERED", "search/.env")]
    # the users DB disappears -> MISSING
    (repo / "search/.env").unlink()
    (repo / "portal/config/authelia/users_database.yml").unlink()
    _, gaps = csb.coverage(repo, pol, out_dir, renderer=render)
    assert ("MISSING", "portal/config/authelia/users_database.yml") in {(k, w) for k, w, _ in gaps}


# ----------------------------------------------------------------------------- retention

def _fake_set(out_dir: Path, stamp: str, files) -> None:
    name = f"config-secrets-{stamp}.tar.age"
    (out_dir / name).write_bytes(csb.AGE_MAGIC + b"ciphertext")
    (out_dir / (name + ".sha256")).write_text("0" * 64 + "  " + name + "\n", encoding="ascii")
    (out_dir / (name + ".files.txt")).write_text("# x\n" + "".join(f + "\n" for f in files), encoding="utf-8")


def test_retention_by_count_keeps_the_last_copy_of_a_vanished_file(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    _fake_set(out, "20261001T023000Z", [".env", "portal/config/authelia/users_database.yml"])
    _fake_set(out, "20261002T023000Z", [".env", "portal/config/authelia/users_database.yml"])
    _fake_set(out, "20261003T023000Z", [".env"])   # the users DB is gone from here on
    _fake_set(out, "20261004T023000Z", [".env"])
    _fake_set(out, "20261005T023000Z", [".env"])
    (out / "config-secrets-20261005T023000Z.tar.age.partial").write_bytes(b"interrupted")
    lines = []
    csb.prune(out, 2, lambda lvl, msg: lines.append((lvl, msg)))
    left = sorted(s.name for s in csb.complete_sets(out))
    # newest two by count, plus the newest archive that still holds the users DB
    assert left == ["config-secrets-20261002T023000Z.tar.age",
                    "config-secrets-20261004T023000Z.tar.age",
                    "config-secrets-20261005T023000Z.tar.age"]
    assert not list(out.glob("*.partial"))
    assert not (out / "config-secrets-20261001T023000Z.tar.age.sha256").exists()
    assert any(lvl == "WARN" and "LAST copy" in msg for lvl, msg in lines)


# ----------------------------------------------------------------------------- root cause

def test_pin_ignores_stops_an_old_branch_from_unignoring_the_users_db(tmp_path):
    """Replays 2026-09-28 19:51-19:52: a branch from BEFORE the portal config moved
    under portal/ has no ignore rule for portal/config/authelia/users_database.yml, so
    on that branch the live file is UNTRACKED, and GitHub Desktop's branch switch
    stashes untracked changes ('!!GitHub_Desktop<branch>': stage, then git stash)
    - which deletes the file from disk. pin-ignores puts the path in info/exclude,
    which no branch can override."""
    root = tmp_path / "pin"
    root.mkdir()
    write(root, {"README": "old\n", ".gitignore": "logs/\n"})
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "old: no portal ignore rule yet")
    _git(root, "branch", "old-branch")
    write(root, {".gitignore": "logs/\nportal/config/authelia/users_database.yml\n"})
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "new: rule added")
    users = root / "portal/config/authelia/users_database.yml"
    write(root, {"portal/config/authelia/users_database.yml": f"fake: {MARK}\n"})

    def desktop_switch_and_back():
        _git(root, "checkout", "-q", "old-branch")
        status = _git(root, "status", "--porcelain", "--untracked-files=all").stdout
        if "users_database.yml" in status:  # what GitHub Desktop shows, and stashes
            _git(root, "add", "--", "portal/config/authelia/users_database.yml")
            _git(root, "stash", "push", "-q", "-m", "!!GitHub_Desktop<old-branch>")
        _git(root, "checkout", "-q", "main")
        return status

    # RED: without the pin the file is deleted by the switch
    status = desktop_switch_and_back()
    assert "?? portal/config/authelia/users_database.yml" in status
    assert not users.exists()
    # (recover it from the stash exactly as the operator can on the live host)
    _git(root, "checkout", "stash@{0}", "--", "portal/config/authelia/users_database.yml")
    _git(root, "reset", "-q")
    assert users.read_text(encoding="utf-8") == f"fake: {MARK}\n"
    _git(root, "stash", "drop", "-q")

    # GREEN: pinned, the old branch no longer sees it and the switch leaves it alone
    changed, pinned = csb.pin_ignores(root, ["portal/config/authelia/users_database.yml"])
    assert changed and pinned == ["portal/config/authelia/users_database.yml"]
    status = desktop_switch_and_back()
    assert "users_database.yml" not in status
    assert users.read_text(encoding="utf-8") == f"fake: {MARK}\n"
    # idempotent, and a file git does NOT ignore today is never pinned
    write(root, {"wip/new-file.yml": "x\n"})
    changed, pinned = csb.pin_ignores(root, ["portal/config/authelia/users_database.yml", "wip/new-file.yml"])
    assert not changed and pinned == ["portal/config/authelia/users_database.yml"]
    exclude = (root / ".git/info/exclude").read_text(encoding="utf-8")
    assert exclude.count(csb.PIN_BEGIN) == 1 and "wip/new-file.yml" not in exclude


# ----------------------------------------------------------------------------- the gate script

PS1_UNDER_TEST = os.environ.get("COVERAGE_PS1_UNDER_TEST", "")


@pytest.mark.skipif(os.name != "nt" or shutil.which("powershell") is None or shutil.which("docker") is None,
                    reason="needs Windows PowerShell and the docker CLI (no daemon)")
def test_coverage_gate_script_fails_on_an_uncovered_users_db(repo, tmp_path):
    """check-backup-coverage.ps1 itself, copied into the fixture repo (it audits the
    repo it lives in). The fixture has users_database.yml and .env files and NO
    config-secrets archive, and DOCKER_HOST is dead (no volumes): the gate must FAIL.
    At base 82001cc the gate only knew volumes and printed CLEAN (exit 0) - set
    COVERAGE_PS1_UNDER_TEST to the base copy to see the RED."""
    ps1 = Path(PS1_UNDER_TEST) if PS1_UNDER_TEST else REPO / "scripts/checks/check-backup-coverage.ps1"
    for rel, src in {"scripts/checks/check-backup-coverage.ps1": ps1,
                     "scripts/backup/config_secrets_backup.py": HERE / "config_secrets_backup.py",
                     "scripts/backup/config-secrets.toml": SHIPPED_POLICY}.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, repo / rel)
    env = dict(os.environ, DOCKER_HOST="tcp://127.0.0.1:1")

    def gate():
        proc = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                               str(repo / "scripts/checks/check-backup-coverage.ps1")],
                              capture_output=True, text=True, env=env, timeout=600)
        out = proc.stdout + proc.stderr
        assert MARK not in out
        return proc.returncode, out

    code, out = gate()
    assert code == 1, out
    assert "UNCOVERED" in out and "users_database.yml" in out
    bins = _age_bins()
    if bins is None:
        pytest.skip("pass-after-the-job half needs CONFIG_SECRETS_TEST_AGE_DIR")
    # ... and PASSES once the job (real `docker compose config` render, CLI only) has
    # covered every file - with a TEST key pair that is deleted right after.
    age, keygen = bins
    kdir = tmp_path / "gate-key"
    kdir.mkdir()
    try:
        subprocess.run([str(keygen), "-o", str(kdir / "k.txt")], check=True, capture_output=True)
        pub = next(ln.split(":", 1)[1].strip() for ln in (kdir / "k.txt").read_text().splitlines()
                   if ln.startswith("# public key:"))
    finally:
        shutil.rmtree(kdir)
    install_recipient(repo, pub)
    proc = run_cli(repo, repo / "scripts/backup/config-secrets.toml", "run", "--age", str(age),
                   "--no-pin", tmp=tmp_path)
    assert proc.returncode == csb.EXIT_OK, proc.stdout + proc.stderr
    code, out = gate()
    assert code == 0, out
    assert "config+secrets coverage: CLEAN" in out


# ============================================================================= attempt 2
# Added after cfg-backup attempt 1 (tester evidence: test-evidence/cfg-backup.attempt1.md).


def _pin_fixture(tmp_path: Path) -> Path:
    """Main checkout with an old branch that predates the ignore rules (the 09-28 shape)."""
    root = tmp_path / "main"
    root.mkdir()
    write(root, {"README": "old\n", ".gitignore": "logs/\n"})
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "old: no ignore rules yet")
    _git(root, "branch", "old-branch")
    write(root, {".gitignore": "logs/\nbackups/\nportal/config/authelia/users_database.yml\n.stack/\nsecrets/\n"})
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "new: rules added")
    write(root, {"portal/config/authelia/users_database.yml": f"fake: {MARK}\n",
                 ".stack/state.json": '{"fake": 1}\n'})
    return root


def _pinned_block(root: Path) -> list[str]:
    lines = (root / ".git/info/exclude").read_text(encoding="utf-8").splitlines()
    if csb.PIN_BEGIN not in lines:
        return []
    return lines[lines.index(csb.PIN_BEGIN) + 1: lines.index(csb.PIN_END)]


def test_a_linked_worktree_never_drops_the_main_checkouts_pins(tmp_path, policy):
    """Tester repro (pin-worktree-clobber-repro.sh): info/exclude is in the COMMON git dir.
    At 90adada a pin-ignores from a linked worktree REPLACED the block with that
    worktree's inventory, and an old-branch checkout showed the users DB as untracked
    again (stashable = deletable)."""
    root = _pin_fixture(tmp_path)
    assert csb.main(["pin-ignores", "--repo-root", str(root), "--policy", str(policy)]) == csb.EXIT_OK
    main_pins = _pinned_block(root)
    assert main_pins == ["/.stack/state.json", "/portal/config/authelia/users_database.yml"]
    wt = tmp_path / "linked"
    _git(root, "worktree", "add", "-q", str(wt), "-b", "wtb")
    write(wt, {"secrets/wt-only": f"{MARK}-wt\n"})
    before = (root / ".git/info/exclude").read_bytes()
    rc = csb.main(["pin-ignores", "--repo-root", str(wt), "--policy", str(policy)])
    assert rc == csb.EXIT_OK
    assert (root / ".git/info/exclude").read_bytes() == before, "a linked worktree rewrote info/exclude"
    assert _pinned_block(root) == main_pins
    # and the hazard stays closed: the old branch does not see the files, stash -u keeps them
    _git(root, "checkout", "-q", "old-branch")
    status = _git(root, "status", "--porcelain", "--untracked-files=all").stdout
    assert "users_database.yml" not in status and "state.json" not in status, status
    _git(root, "stash", "push", "-q", "--include-untracked", "-m", "probe")
    _git(root, "checkout", "-q", "main")
    assert (root / "portal/config/authelia/users_database.yml").read_text(encoding="utf-8") == f"fake: {MARK}\n"
    assert (root / ".stack/state.json").is_file()


@needs_age
def test_a_run_in_a_linked_worktree_logs_that_it_did_not_pin(tmp_path, policy, keys):
    age, _, pub = keys
    root = _pin_fixture(tmp_path)
    wt = tmp_path / "linked"
    _git(root, "worktree", "add", "-q", str(wt), "-b", "wtb")
    write(wt, {".stack/state.json": "{}\n"})
    install_recipient(wt, pub)
    proc = run_cli(wt, policy, "run", "--age", str(age), tmp=tmp_path)
    assert proc.returncode == csb.EXIT_OK, proc.stdout + proc.stderr
    assert "pin-ignores skipped" in proc.stdout and "linked worktree" in proc.stdout
    exclude = root / ".git/info/exclude"
    assert csb.PIN_BEGIN not in exclude.read_text(encoding="utf-8")


def test_pin_keeps_operator_lines_and_crlf(tmp_path):
    root = _pin_fixture(tmp_path)
    exclude = root / ".git/info/exclude"
    exclude.write_bytes(b"# operator line 1\r\n*.mine\r\n")
    changed, _ = csb.pin_ignores(root, ["portal/config/authelia/users_database.yml"])
    raw = exclude.read_bytes()
    assert changed and raw.startswith(b"# operator line 1\r\n*.mine\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b""), "line endings were normalised"


def test_non_utf8_exclude_is_a_clear_failure_not_a_traceback(tmp_path):
    root = _pin_fixture(tmp_path)
    exclude = root / ".git/info/exclude"
    exclude.write_bytes(b"# caf\xe9 operator note\n")
    with pytest.raises(csb.Failed, match="not UTF-8"):
        csb.pin_ignores(root, ["portal/config/authelia/users_database.yml"])
    assert exclude.read_bytes() == b"# caf\xe9 operator note\n"


@needs_age
def test_run_with_a_non_utf8_exclude_logs_error_and_does_not_complete(tmp_path, policy, keys):
    age, _, pub = keys
    root = _pin_fixture(tmp_path)
    (root / ".git/info/exclude").write_bytes(b"# caf\xe9\n")
    install_recipient(root, pub)
    proc = run_cli(root, policy, "run", "--age", str(age), tmp=tmp_path)
    assert proc.returncode == csb.EXIT_PROBLEMS, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    log = next((tmp_path / "logs").glob("config-secrets-backup-*.log")).read_text(encoding="utf-8")
    assert "[ERROR] pin-ignores FAILED" in log and csb.SUCCESS_MARKER not in log
    assert len(csb.complete_sets(root / "backups/config-secrets")) == 1  # the archive is still made


def test_non_integer_retain_count_is_refused_with_an_error_line(repo, policy, tmp_path):
    install_recipient(repo, "age1" + "q" * 58)
    proc = run_cli(repo, policy, "run", tmp=tmp_path, extra_env={"CONFIG_SECRETS_BACKUP_RETAIN_COUNT": "two"})
    assert proc.returncode == csb.EXIT_REFUSED, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    log = next((tmp_path / "logs").glob("config-secrets-backup-*.log")).read_text(encoding="utf-8")
    assert "[ERROR] REFUSED" in log and "not a whole number" in log


def test_a_bom_recipients_file_is_refused_and_says_why(repo, policy, tmp_path):
    p = install_recipient(repo, "age1" + "q" * 58)
    p.write_bytes(b"\xef\xbb\xbf" + ("age1" + "q" * 58 + "\n").encode())
    proc = run_cli(repo, policy, "run", tmp=tmp_path)
    assert proc.returncode == csb.EXIT_REFUSED
    assert "byte-order mark" in proc.stdout and "-Encoding ascii" in proc.stdout


@needs_age
def test_age_failure_logs_ages_own_error(repo, policy, tmp_path, monkeypatch):
    """age rejects a recipient our syntax check lets through (bad checksum). With more
    data than a pipe buffer the write fails first; the log must carry age's reason,
    not '[Errno 22] Invalid argument'."""
    age, _ = _age_bins()
    install_recipient(repo, "age1" + "q" * 58)
    (repo / "secrets/bulk.bin").write_bytes(os.urandom(2 * 1024 * 1024))
    _patch_render(monkeypatch, repo)
    out_dir = tmp_path / "out"
    rc = csb.main(["run", "--repo-root", str(repo), "--policy", str(policy), "--age", str(age),
                   "--backups-dir", str(out_dir), "--logs-dir", str(tmp_path / "logs"), "--no-pin"])
    assert rc == csb.EXIT_FAILED
    log = next((tmp_path / "logs").glob("config-secrets-backup-*.log")).read_text(encoding="utf-8")
    assert "malformed recipient" in log, log
    assert not out_dir.exists() or not list(out_dir.iterdir())


def _make_dir_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


def test_links_under_secret_dirs_are_reported_not_silently_skipped(repo, policy, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "cred.json").write_text("{}", encoding="utf-8")
    _make_dir_link(repo / "secrets/junc", outside)
    file_link = False
    try:
        os.symlink(outside / "cred.json", repo / "secrets/linked.json")
        file_link = True
    except OSError:
        pass  # no symlink privilege on this Windows account: the junction case still runs
    pol = csb.load_policy(policy)
    inv = csb.derive_inventory(repo, pol, renderer=fake_renderer(repo))
    links = {w for k, w, _ in inv.problems if k == "LINK"}
    assert "secrets/junc" in links
    if file_link:
        assert "secrets/linked.json" in links
    assert not any(f.startswith("secrets/junc/") for f in inv.files)
    # an [[exclude]] with a reason accounts for it
    pol.exclude.append({"glob": "secrets/junc", "reason": "fixture: deliberate link"})
    inv = csb.derive_inventory(repo, pol, renderer=fake_renderer(repo))
    assert "secrets/junc" not in {w for k, w, _ in inv.problems if k == "LINK"}


GUARD = (
    "import os, pathlib, stat, subprocess, sys\n"
    "mark = os.environ['CS_GUARD_MARK'].encode()\n"
    "if not stat.S_ISFIFO(os.fstat(0).st_mode):\n"
    "    sys.stderr.write('GUARD: the stdin of age is not a pipe - the tar was staged somewhere')\n"
    "    sys.exit(97)\n"
    "for d in os.environ['CS_GUARD_DIRS'].split(os.pathsep):\n"
    "    for p in pathlib.Path(d).rglob('*'):\n"
    "        try:\n"
    "            if p.is_file() and mark in p.read_bytes():\n"
    "                sys.stderr.write('GUARD: plaintext on disk DURING the run: ' + str(p))\n"
    "                sys.exit(98)\n"
    "        except OSError:\n"
    "            pass\n"
    "with open(os.environ['CS_GUARD_LOG'], 'a') as fh:\n"
    "    fh.write('checked' + chr(10))\n"
    "sys.exit(subprocess.call(sys.argv[1:]))\n"
)


@needs_age
def test_no_plaintext_on_disk_while_age_runs(repo, policy, tmp_path, keys, monkeypatch):
    """'None LEFT afterwards' is not 'none written': a mutant that tars to a temp file,
    has age encrypt that, then deletes it passed every attempt-1 test. Here age is
    started through a guard that, at the moment age starts, (a) requires its stdin to
    be a pipe and (b) scans the output dir and TEMP for the plaintext marker."""
    import tempfile
    age, _, pub = keys
    install_recipient(repo, pub)
    _patch_render(monkeypatch, repo)
    out_dir = tmp_path / "out"
    temp = tmp_path / "guarded-temp"
    temp.mkdir()
    guard = tmp_path / "guard.py"
    guard.write_text(GUARD, encoding="utf-8")
    glog = tmp_path / "guard.log"
    for var in ("TMP", "TEMP", "TMPDIR"):
        monkeypatch.setenv(var, str(temp))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("CS_GUARD_MARK", MARK)
    monkeypatch.setenv("CS_GUARD_DIRS", os.pathsep.join([str(out_dir), str(temp)]))
    monkeypatch.setenv("CS_GUARD_LOG", str(glog))
    real_popen = subprocess.Popen

    def guarded(cmd, *a, **k):
        if isinstance(cmd, (list, tuple)) and cmd and Path(str(cmd[0])).name.lower().startswith("age"):
            cmd = [sys.executable, str(guard), *map(str, cmd)]
        return real_popen(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "Popen", guarded)
    rc = csb.main(["run", "--repo-root", str(repo), "--policy", str(policy), "--age", str(age),
                   "--backups-dir", str(out_dir), "--logs-dir", str(tmp_path / "logs"), "--no-pin"])
    log = next((tmp_path / "logs").glob("config-secrets-backup-*.log")).read_text(encoding="utf-8")
    assert "GUARD" not in log, log
    assert rc == csb.EXIT_OK, log
    assert glog.read_text().count("checked") == 1  # the guard really ran in front of age


WATCHDOG_HARNESS = """
$src = '__SRC__'
$tok = $null; $err = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($src, [ref]$tok, [ref]$err)
$fns = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                      ($n.Name -eq 'Get-BackupSkipReason' -or $n.Name -eq 'Test-BackupRecency') }, $true)
foreach ($f in $fns) { . ([scriptblock]::Create($f.Extent.Text)) }
function Write-LogEntry { param($Message, $Level) Write-Output "LOG[$Level] $Message" }
$PROJECT_DIR = '__PROJ__'
$ExpectedBackupRecency = @(@{ Dir = 'config-secrets'; MaxAgeHours = 52 })
Test-BackupRecency
"""


@pytest.mark.skipif(os.name != "nt" or shutil.which("powershell") is None, reason="needs Windows PowerShell")
def test_watchdog_recency_counts_only_real_artifacts(tmp_path):
    """stack-watchdog.ps1's Test-BackupRecency, lifted out of the script by its AST and run
    against a scratch PROJECT_DIR: an old .tar.age beside a FRESH .files.txt and .partial
    is STALE (at 90adada the fresh sidecar files made it look fresh)."""
    proj = tmp_path / "proj"
    d = proj / "backups/config-secrets"
    d.mkdir(parents=True)
    (proj / "logs").mkdir()
    old = d / "config-secrets-20261001T063000Z.tar.age"
    old.write_bytes(csb.AGE_MAGIC)
    (d / (old.name + ".sha256")).write_text("x", encoding="ascii")
    t = os.path.getmtime(old) - 60 * 3600
    os.utime(old, (t, t))
    os.utime(d / (old.name + ".sha256"), (t, t))
    (d / (old.name + ".files.txt")).write_text("x", encoding="ascii")          # fresh
    (d / "config-secrets-20261004T063000Z.tar.age.partial").write_text("x", encoding="ascii")  # fresh
    ps = tmp_path / "run.ps1"
    ps.write_text(WATCHDOG_HARNESS.replace("__SRC__", str(REPO / "scripts/checks/stack-watchdog.ps1"))
                  .replace("__PROJ__", str(proj)), encoding="utf-8")
    env = dict(os.environ, DOCKER_HOST="tcp://127.0.0.1:1")
    out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)],
                         capture_output=True, text=True, env=env, timeout=300).stdout
    assert "BACKUP STALE - config-secrets" in out, out


# ----------------------------------------------------------------------------- submodule (cfg-pin-sub)

def _sub_fixture(tmp_path: Path) -> Path:
    """Main checkout WITH a submodule (the live shape: OB1/docker/.env). Both repos have an
    old-branch that predates their ignore rules (the 09-28 shape, once per repository)."""
    src = tmp_path / "subsrc"
    src.mkdir()
    write(src, {"README": "sub old\n"})
    _git(src, "init", "-q", "-b", "main")
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "sub old")
    _git(src, "branch", "old-branch")
    write(src, {".gitignore": ".env\n"})
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "sub: ignore rule")
    root = tmp_path / "main"
    root.mkdir()
    write(root, {"README": "old\n", ".gitignore": "logs/\n"})
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "old")
    _git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src), "OB1")
    _git(root, "commit", "-q", "-m", "add OB1")
    _git(root, "branch", "old-branch")
    _git(root / "OB1", "branch", "old-branch", "HEAD~1")
    write(root, {".gitignore": "logs/\n.stack/\n"})
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "new: rules added")
    write(root, {".stack/state.json": '{"fake": 1}\n', "OB1/docker/.env": f"FAKE={MARK}\n"})
    return root


def _sub_pins(root: Path) -> list[str]:
    gd = Path(_git(root / "OB1", "rev-parse", "--absolute-git-dir").stdout.strip())
    lines = (gd / "info/exclude").read_text(encoding="utf-8").splitlines() if (gd / "info/exclude").is_file() else []
    if csb.PIN_BEGIN not in lines:
        return []
    return lines[lines.index(csb.PIN_BEGIN) + 1: lines.index(csb.PIN_END)]


def _sub_hazard_closed(root: Path) -> None:
    """Old-branch checkout + `stash -u` in EACH repo leaves both secrets on disk."""
    for repo in (root, root / "OB1"):
        _git(repo, "checkout", "-q", "old-branch")
        status = _git(repo, "status", "--porcelain", "--untracked-files=all").stdout
        assert ".env" not in status and "state.json" not in status, (repo, status)
        _git(repo, "stash", "push", "-q", "--include-untracked", "-m", "probe")
        _git(repo, "checkout", "-q", "main")
    assert (root / "OB1/docker/.env").read_text(encoding="utf-8") == f"FAKE={MARK}\n"
    assert (root / ".stack/state.json").is_file()


FILES_SUB = [".stack/state.json", "OB1/docker/.env"]


def test_premise_git_check_ignore_refuses_a_path_inside_a_submodule(tmp_path):
    root = _sub_fixture(tmp_path)
    proc = subprocess.run(["git", "-C", str(root), "check-ignore", "-z", "--stdin"],
                          input=b"OB1/docker/.env\0", capture_output=True)
    assert proc.returncode == 128 and b"in submodule" in proc.stderr


def test_pin_ignores_pins_the_superproject_and_the_submodule_each_in_its_own_exclude(tmp_path):
    root = _sub_fixture(tmp_path)
    changed, pinned = csb.pin_ignores(root, FILES_SUB)
    assert changed and pinned == FILES_SUB
    assert _pinned_block(root) == ["/.stack/state.json"]
    assert _sub_pins(root) == ["/docker/.env"]
    # nothing was written to the superproject's exclude for the submodule's path
    assert "OB1" not in (root / ".git/info/exclude").read_text(encoding="utf-8")
    _sub_hazard_closed(root)


def test_without_the_pins_the_submodule_secret_is_stashed_away(tmp_path):
    root = _sub_fixture(tmp_path)
    _git(root / "OB1", "checkout", "-q", "old-branch")
    assert "?? docker/.env" in _git(root / "OB1", "status", "--porcelain", "--untracked-files=all").stdout
    _git(root / "OB1", "stash", "push", "-q", "--include-untracked", "-m", "probe")
    assert not (root / "OB1/docker/.env").exists()


def test_submodule_pins_are_idempotent_keep_operator_lines_and_crlf(tmp_path):
    root = _sub_fixture(tmp_path)
    gd = Path(_git(root / "OB1", "rev-parse", "--absolute-git-dir").stdout.strip())
    (gd / "info").mkdir(exist_ok=True)
    (gd / "info/exclude").write_bytes(b"# operator\r\nmine.tmp\r\n")
    assert csb.pin_ignores(root, FILES_SUB)[0]
    first = (gd / "info/exclude").read_bytes(), (root / ".git/info/exclude").read_bytes()
    raw = first[0]
    assert raw.startswith(b"# operator\r\nmine.tmp\r\n") and b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    changed, pinned = csb.pin_ignores(root, FILES_SUB)
    assert not changed and pinned == FILES_SUB
    assert ((gd / "info/exclude").read_bytes(), (root / ".git/info/exclude").read_bytes()) == first
    assert raw.count(csb.PIN_BEGIN.encode()) == 1


def test_a_linked_worktree_of_a_superproject_with_a_submodule_never_writes(tmp_path, policy):
    root = _sub_fixture(tmp_path)
    csb.pin_ignores(root, FILES_SUB)
    gd = Path(_git(root / "OB1", "rev-parse", "--absolute-git-dir").stdout.strip())
    before = ((root / ".git/info/exclude").read_bytes(), (gd / "info/exclude").read_bytes())
    wt = tmp_path / "linked"
    _git(root, "worktree", "add", "-q", str(wt), "-b", "wtb")
    write(wt, {".stack/state.json": "{}\n"})
    rc = csb.main(["pin-ignores", "--repo-root", str(wt), "--policy", str(policy)])
    assert rc == csb.EXIT_OK
    assert ((root / ".git/info/exclude").read_bytes(), (gd / "info/exclude").read_bytes()) == before
    with pytest.raises(csb.NotMainWorktree):
        csb.pin_ignores(wt, FILES_SUB)
    assert ((root / ".git/info/exclude").read_bytes(), (gd / "info/exclude").read_bytes()) == before


def test_a_submodule_that_is_not_checked_out_is_skipped_with_a_log_line(tmp_path):
    root = _sub_fixture(tmp_path)
    shutil.rmtree(root / "OB1")
    (root / "OB1").mkdir()  # declared in .gitmodules, empty: not checked out
    logs: list = []
    changed, pinned = csb.pin_ignores(root, FILES_SUB, lambda lvl, msg: logs.append((lvl, msg)))
    assert pinned == [".stack/state.json"] and changed
    assert any("OB1 is not checked out" in m for _, m in logs)


def test_cli_pin_ignores_on_a_checkout_with_a_submodule(tmp_path, policy, monkeypatch):
    """The failing live call: the inventory holds an OB1/... path. At 40968db this
    exits non-zero with `git check-ignore failed (exit 128)`."""
    root = _sub_fixture(tmp_path)
    inv = csb.Inventory()
    for f in FILES_SUB:
        inv.add(f, "env")
    monkeypatch.setattr(csb, "derive_inventory", lambda *a, **k: inv)
    assert csb.main(["pin-ignores", "--repo-root", str(root), "--policy", str(policy)]) == csb.EXIT_OK
    assert _pinned_block(root) == ["/.stack/state.json"] and _sub_pins(root) == ["/docker/.env"]
