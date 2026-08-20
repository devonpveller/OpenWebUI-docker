"""P5.1/P5.2 scope-ledger + P3.3 floor-guard tests (hard-rule #2 / #4)."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.modules.audit_sink import AuditSink
from app.modules.floor_guard import FloorGuard
from app.modules.scope_ledger import ScopeDenied, ScopeLedger


async def _ledger(db) -> ScopeLedger:
    return ScopeLedger(db, AuditSink(db, Settings(_env_file=None)))


async def test_self_grant_denied(db):
    ledger = await _ledger(db)
    with pytest.raises(ScopeDenied):
        await ledger.grant("worker-1", "src/auth/**", granted_by="worker-1")


async def test_pm_grant_recorded(db):
    ledger = await _ledger(db)
    await ledger.grant("worker-1", "src/auth/**", granted_by="pm")
    assert await ledger.authorized("worker-1", "src/auth/login.py") is True
    assert await ledger.authorized("worker-1", "src/billing/x.py") is False


async def test_irreversible_scope_needs_human(db):
    ledger = await _ledger(db)
    with pytest.raises(ScopeDenied):
        await ledger.grant("worker-1", "publish-main", granted_by="pm")  # PM cannot grant irreversible
    await ledger.grant("worker-1", "publish-main", granted_by="human")   # human can
    assert await ledger.authorized("worker-1", "publish-main") is True


async def test_additive_push_is_reversible_and_pm_grantable(db):
    """A feature-branch push is NOT irreversible — the PM can grant 'write' and the floor lets an
    additive push through, so workers can commit + push their work (the correction)."""
    ledger = await _ledger(db)
    await ledger.grant("worker-1", "write", granted_by="pm")   # reversible → PM may grant
    assert await ledger.authorized("worker-1", "write") is True
    guard = FloorGuard(ledger)
    ok, reason = await guard.allowed("worker-1", "git push -u origin agent/effort-hello")
    assert ok is True and reason == "reversible"               # feature-branch push is routine


async def test_revoke_leaves_no_authority(db):
    ledger = await _ledger(db)
    await ledger.grant("worker-1", "src/**", granted_by="pm")
    await ledger.revoke_subject("worker-1")
    assert await ledger.authorized("worker-1", "src/x.py") is False


async def test_role_catalog_approval(db):
    ledger = await _ledger(db)
    await ledger.catalog_add("auth", "charters/worker-default.md", approved=True)
    await ledger.catalog_add("exotic", "charters/worker-default.md", approved=False)
    assert await ledger.is_role_approved("auth") is True
    assert await ledger.is_role_approved("exotic") is False


# ── floor guard (hard-rule #4) ──────────────────────────────────────────────
def test_floor_classify():
    # Publishing to main + destructive ops are gated; additive commit/push are NOT.
    assert FloorGuard.classify("git push origin main") == "publish-main"
    assert FloorGuard.classify("git push -f origin agent/x") == "destructive-git"
    assert FloorGuard.classify("git push origin :oldbranch") == "destructive-git"
    assert FloorGuard.classify("git reset --hard HEAD~3") == "destructive-git"
    assert FloorGuard.classify("git branch -D feature") == "destructive-git"
    assert FloorGuard.classify("rm -rf /workspace/x") == "delete"
    assert FloorGuard.classify("docker compose up -d") == "deploy"
    assert FloorGuard.classify("cat file.txt") is None
    assert FloorGuard.classify("git commit -m x") is None            # local, reversible
    assert FloorGuard.classify("git push -u origin agent/effort-hi") is None  # feature-branch push
    assert FloorGuard.classify("git checkout -b agent/effort-hi") is None


async def test_floor_blocks_uncleared_irreversible(db):
    ledger = await _ledger(db)
    guard = FloorGuard(ledger)
    ok, reason = await guard.allowed("worker-1", "git push origin main")
    assert ok is False and "BLOCKED" in reason


async def test_floor_allows_after_human_grant(db):
    ledger = await _ledger(db)
    guard = FloorGuard(ledger)
    await ledger.grant("worker-1", "publish-main", granted_by="human")
    ok, _ = await guard.allowed("worker-1", "git push origin main")
    assert ok is True


async def test_floor_allows_reversible(db):
    ledger = await _ledger(db)
    guard = FloorGuard(ledger)
    ok, _ = await guard.allowed("worker-1", "pytest -q")
    assert ok is True
