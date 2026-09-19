"""Tester findings become durable checks (U3).

§0 A5: agent-org proved this pipeline (gym-007); the harness banked nine cycles of lessons
as prose in MERGE-PROTOCOL and is "currently the violator". These tests pin the properties
that make a banked check different from a noted one.

    python -m pytest scripts/agent-harness/test_durable_checks.py -q
"""

import json
import subprocess
from pathlib import Path

import pytest

import durable_checks as dc


@pytest.fixture
def repo(tmp_path):
    """A real git repo, because the registry location is derived from --git-common-dir."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


# ── where the registry lives ─────────────────────────────────────────────────
def test_the_registry_lives_in_the_SHARED_git_dir(repo):
    """Every worktree must see ONE registry.

    A check banked in one worktree and invisible to the next is not durable - it is a note
    in a directory that gets deleted.
    """
    p = dc.registry_path(repo)
    assert p.parent.name == "agent-worktrees"
    assert p.parent.parent.name == ".git"


def test_a_missing_registry_reads_as_empty_not_an_error(repo):
    assert dc.load(repo) == []


def test_a_CORRUPT_registry_raises_rather_than_reading_as_empty(repo):
    """The difference that matters.

    Reading a corrupt registry as empty would report "0 checks, all green" for a line that
    has banked dozens - a green that means the opposite of what it says.
    """
    p = dc.registry_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        dc.load(repo)


# ── banking a finding ────────────────────────────────────────────────────────
def test_a_finding_becomes_a_check_with_its_reason(repo):
    row = dc.add(repo, command="pytest -q", why="the suite must stay green",
                 source_item="widget")
    assert row["check"] == "pytest -q"
    assert row["why"] == "the suite must stay green"
    assert row["source"] == "tester-finding"
    assert row["source_item"] == "widget"
    assert dc.load(repo) == [row]


def test_a_check_without_a_command_is_refused(repo):
    # The command is what makes it durable; prose here would be the evaporation A5 names.
    for bad in ("", "   ", None):
        with pytest.raises(ValueError):
            dc.add(repo, command=bad, why="reason")


def test_a_check_without_a_WHY_is_refused(repo):
    """One that goes red years from now is unjudgeable without it.

    The reader cannot tell whether the check or the code is wrong - the same rule executable
    acceptance criteria enforce, for the same reason.
    """
    with pytest.raises(ValueError):
        dc.add(repo, command="pytest -q", why="   ")


def test_the_same_finding_twice_is_ONE_check(repo):
    a = dc.add(repo, command="pytest -q", why="first")
    b = dc.add(repo, command="pytest  -q", why="second wording")   # whitespace differs
    assert a["id"] == b["id"]
    assert len(dc.load(repo)) == 1
    # The FIRST reason is kept: it is the one that was true when the wall was hit.
    assert dc.load(repo)[0]["why"] == "first"


def test_two_testers_describing_one_wall_bank_one_check(repo):
    # Identity is the COMMAND, not the prose. Two people describing the same failure
    # differently have found the same thing, and the check they would write says so.
    dc.add(repo, command="ruff check .", why="lint is clean")
    dc.add(repo, command="ruff check .", why="no lint regressions please")
    assert len(dc.load(repo)) == 1


def test_different_checks_are_different_rows(repo):
    dc.add(repo, command="pytest -q", why="a")
    dc.add(repo, command="ruff check .", why="b")
    assert len(dc.load(repo)) == 2


# ── running them: the cannot-regress property ────────────────────────────────
def test_running_a_green_check_passes(repo):
    dc.add(repo, command="python -c \"raise SystemExit(0)\"", why="always green")
    out = dc.run(repo)
    assert out["total"] == 1 and out["failed"] == 0


def test_a_RED_check_is_reported_red_and_carries_its_reason(repo):
    """The whole point: a banked lesson that stops being true fails LOUDLY.

    A registry nobody runs is a list of lessons, which is the prose evaporation this exists
    to stop.
    """
    dc.add(repo, command="python -c \"raise SystemExit(7)\"", why="this must not regress")
    out = dc.run(repo)
    assert out["failed"] == 1
    red = [r for r in out["results"] if not r["passed"]][0]
    assert red["why"] == "this must not regress"
    assert "exit 7" in red["detail"]


def test_one_red_check_does_not_hide_the_others(repo):
    dc.add(repo, command="python -c \"raise SystemExit(1)\"", why="red")
    dc.add(repo, command="python -c \"raise SystemExit(0)\"", why="green")
    out = dc.run(repo)
    assert out["total"] == 2 and out["failed"] == 1


def test_a_command_that_cannot_run_is_a_FAILURE_not_a_skip(repo):
    # A check that errors is not a check that passed. Treating it as a skip is how a
    # registry quietly stops covering something.
    dc.add(repo, command="this-command-does-not-exist-anywhere", why="x")
    out = dc.run(repo)
    assert out["failed"] == 1


# ── the CLI, which is what anything else calls ───────────────────────────────
def _cli(repo, *args):
    return subprocess.run(
        ["python", str(Path(dc.__file__).resolve()), str(repo), *args],
        capture_output=True, text=True)


def test_cli_run_exits_NONZERO_on_a_red_check(repo):
    dc.add(repo, command="python -c \"raise SystemExit(2)\"", why="must not regress")
    out = _cli(repo, "run")
    assert out.returncode == 1, out.stdout
    assert "must not regress" in out.stdout


def test_cli_run_exits_zero_when_all_pass(repo):
    dc.add(repo, command="python -c \"raise SystemExit(0)\"", why="fine")
    assert _cli(repo, "run").returncode == 0


def test_cli_says_so_when_NOTHING_is_banked(repo):
    """"0 checks, all green" reads as coverage, and it is the state a line is weakest in."""
    out = _cli(repo, "run")
    assert out.returncode == 0
    assert "no durable checks banked yet" in out.stdout


def test_cli_add_refuses_without_a_why(repo):
    out = _cli(repo, "add", "--check", "pytest -q")
    assert out.returncode == 2
    assert "refused" in out.stdout


def test_cli_add_then_list_shows_it(repo):
    assert _cli(repo, "add", "--check", "pytest -q", "--why", "green suite",
                "--item", "widget").returncode == 0
    out = _cli(repo, "list")
    assert "pytest -q" in out.stdout and "green suite" in out.stdout and "widget" in out.stdout


def test_the_registry_is_readable_json(repo):
    dc.add(repo, command="pytest -q", why="green")
    data = json.loads(dc.registry_path(repo).read_text(encoding="utf-8"))
    assert isinstance(data["checks"], list)


# ── the memory-plane half (U3) ───────────────────────────────────────────────
def test_the_memory_payload_carries_the_COMMAND_not_a_description(repo):
    """A memory of a check that omitted the command is a memory ABOUT a check.

    That is the prose form this pipeline exists to replace.
    """
    row = dc.add(repo, command="pytest -q", why="the suite must stay green")
    p = dc.memory_payload(row)
    assert p["memory_type"] == "check"
    assert "pytest -q" in p["content"]
    assert p["metadata"]["check"] == "pytest -q"


def test_the_memory_write_is_idempotent_on_the_content_address(repo):
    row = dc.add(repo, command="pytest -q", why="green")
    assert dc.memory_payload(row)["idempotency_key"] == f"check-{row['id']}"


def test_the_payload_omits_everything_the_server_owns(repo):
    # §1's write defaults are the server's; a client restating them would drift silently.
    row = dc.add(repo, command="pytest -q", why="green")
    p = dc.memory_payload(row)
    for owned in ("review_status", "visibility", "exposure", "provenance_status",
                  "can_use_as_instruction", "requires_user_confirmation"):
        assert owned not in p


def test_no_key_means_no_write_and_no_exception(repo):
    row = dc.add(repo, command="pytest -q", why="green")
    assert dc.mirror_to_plane(row, key="") is False


def test_an_unreachable_door_returns_false_rather_than_raising(repo):
    """Fail-soft: the local registry is the durable artifact.

    A memory write that could block banking a check would make the unification cost you the
    thing being unified.
    """
    row = dc.add(repo, command="pytest -q", why="green")
    assert dc.mirror_to_plane(row, door="http://127.0.0.1:9", key="k") is False


def test_a_tool_level_failure_is_NOT_counted_as_a_write():
    # HTTP 200 with result.isError is how MCP reports tool failure.
    assert dc._mirror_succeeded('{"jsonrpc":"2.0","id":1,"result":{"isError":true}}') is False
    assert dc._mirror_succeeded('{"jsonrpc":"2.0","id":1,"error":{"code":-32601}}') is False
    assert dc._mirror_succeeded('{"jsonrpc":"2.0","id":1,"result":{"content":[]}}') is True


def test_an_sse_framed_reply_is_parsed():
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"content":[]}}\n\n'
    assert dc._mirror_succeeded(body) is True
