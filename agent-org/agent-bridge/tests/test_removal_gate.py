"""ANTI-DELETE-TO-PASS gate: a green reached by DELETING features must NEVER open a PR.

Live 2026-07-14: the FNA→MonoGame port reached `burndown_green` by DELETING
`src/Murder.Editor/Core/Cursor/MouseCursor.Sdl.cs` (181 lines — the editor CURSOR, a critical
component) and gutting input/game code, and the org opened PRs off it. Removals were DISCLOSED, but
disclosure never blocks and ran AFTER the PR was already open. Operator, twice: "removing the cursor
is a repeated issue … a critical component to a functioning piece of software."

`_gate_removals` now BLOCKS before any PR, and the PM auto-iterates with steering that names what was
deleted. A goal that genuinely ASKS for removal still passes."""

from __future__ import annotations

from types import SimpleNamespace


from app.modules.capabilities import BranchDelivery


def _orch(monkeypatch, *, removal_summary: dict, goal: str):
    """Unit-scope the gate seam — no full orchestrator boot."""
    from app.orchestrator import Orchestrator

    o = object.__new__(Orchestrator)
    o.github = SimpleNamespace()
    o.s = SimpleNamespace(github_app_enabled=True, github_api_base="https://api.github.com")
    o._gh_transport = None
    o.charters = SimpleNamespace(current_goal=lambda _e: _goal_tuple(goal))
    o.audit = SimpleNamespace(log=_anoop)
    o.comms = SimpleNamespace(post=_anoop)
    o.router = SimpleNamespace(update_effort_card=_anoop)
    o.iterated = []
    o._mgmt_thread_of = lambda _e: None

    async def _auto_iterate(effort_id, reason, evolved):
        o.iterated.append((reason, evolved))
        return True          # an iteration is available → gate returns False (no PR)

    o._auto_iterate = _auto_iterate

    async def _read_removal_summary(*_a, **_k):
        return removal_summary

    monkeypatch.setattr("app.orchestrator.read_removal_summary", _read_removal_summary)
    return o


async def _anoop(*_a, **_k):
    return None


async def _goal_tuple(goal: str):
    return (1, goal, None)


PORT_GOAL = "Port the Murder engine backend from FNA to MonoGame."


async def test_deleted_feature_file_blocks_the_pr(monkeypatch):
    # The exact incident: the cursor implementation deleted to get past FNA errors.
    o = _orch(
        monkeypatch,
        removal_summary={
            "deleted_files": ["src/Murder.Editor/Core/Cursor/MouseCursor.Sdl.cs"],
            "gutted_files": [], "removed_symbols": [], "insertions": 60, "deletions": 181,
        },
        goal=PORT_GOAL,
    )
    ok = await o._gate_removals("e1", "https://github.com/x/murder",
                                BranchDelivery(landed=True, branch="agent/e1", head_sha="abc"))
    assert ok is False                                  # BLOCKED → caller opens no PR
    assert o.iterated, "the PM must re-drive it, not just refuse"
    _, steering = o.iterated[0]
    assert "MouseCursor.Sdl.cs" in steering             # steering NAMES what was deleted
    assert "RESTORE" in steering.upper() and "PORT" in steering.upper()


async def test_gutted_file_blocks_the_pr(monkeypatch):
    o = _orch(
        monkeypatch,
        removal_summary={
            "deleted_files": [], "gutted_files": [{"file": "src/Murder/Game.cs", "removed": 90,
                                                   "added": 3}],
            "removed_symbols": [], "insertions": 5, "deletions": 95,
        },
        goal=PORT_GOAL,
    )
    assert await o._gate_removals("e1", "https://github.com/x/murder",
                                  BranchDelivery(landed=True, branch="agent/e1")) is False


async def test_clean_additive_port_passes(monkeypatch):
    # A real port ADDS MonoGame equivalents — nothing deleted/gutted → PR proceeds.
    o = _orch(
        monkeypatch,
        removal_summary={"deleted_files": [], "gutted_files": [], "removed_symbols": [],
                         "insertions": 240, "deletions": 30},
        goal=PORT_GOAL,
    )
    assert await o._gate_removals("e1", "https://github.com/x/murder",
                                  BranchDelivery(landed=True, branch="agent/e1")) is True
    assert not o.iterated


async def test_removal_goal_is_allowed_to_remove(monkeypatch):
    # When the operator ASKED for removal, deleting IS the job — must not block.
    o = _orch(
        monkeypatch,
        removal_summary={"deleted_files": ["src/Old/Legacy.cs"], "gutted_files": [],
                         "removed_symbols": [], "insertions": 0, "deletions": 300},
        goal="Remove the deprecated legacy serializer and delete its dead files.",
    )
    assert await o._gate_removals("e1", "https://github.com/x/murder",
                                  BranchDelivery(landed=True, branch="agent/e1")) is True
    assert not o.iterated
