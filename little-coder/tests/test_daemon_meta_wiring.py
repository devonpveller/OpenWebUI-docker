"""Daemon's `_build_meta_runner` factory has three modes (design §3.2,
Chapter 3). Pin them so a flip of `observer.judge_enabled` does what the
operator expects: no LLM at boot when off, real LLM clients when on.
"""

from __future__ import annotations

import pytest

from littlecoder.config import Config, ObserverConfig
from littlecoder.judge import Judge
from littlecoder.judge_gate import RATING_RECORD_ENV, JudgeNotCalibratedError
from littlecoder.meta import MetaRunner, default_similarity
from littlecoder.meta_wiring import build_meta_runner
from littlecoder.similarity import EmbeddingSimilarity

VALID_RATING = (
    "rated_by: operator\n"
    "rated_at: 2026-08-30T12:00:00Z\n"
    "rated_report: dryrun/judge-dryrun-little-coder.json\n"
    "verdict: approve\n"
)


@pytest.fixture
def rated(tmp_path, monkeypatch):
    """A valid human rating record in force, via the env override the gate
    resolves before its container default. Wiring the judge REQUIRES one
    (judge_gate.require) -- these tests would otherwise assert a state the
    daemon refuses to boot into."""
    rec = tmp_path / "judge-enablement-rating.yaml"
    rec.write_text(VALID_RATING, encoding="utf-8")
    monkeypatch.setenv(RATING_RECORD_ENV, str(rec))
    return rec


def _cfg(observer_enabled: bool, judge_enabled: bool) -> Config:
    """Build a Config with just the observer fields tweaked."""
    cfg = Config()
    cfg.observer = ObserverConfig(
        enabled=observer_enabled,
        judge_enabled=judge_enabled,
    )
    return cfg


def test_disabled_observer_uses_stub_similarity_no_judge():
    """Observer off → no LLM clients constructed (no network at boot)."""
    cfg = _cfg(observer_enabled=False, judge_enabled=False)
    runner = build_meta_runner(cfg)
    assert isinstance(runner, MetaRunner)
    assert runner.similarity is default_similarity
    assert runner.judge is None


def test_observer_enabled_but_judge_disabled_uses_stub_similarity():
    """The intermediate state — Observer reports work, no minting. Useful
    during prompt-calibration (operator hasn't dry-run the judge yet)."""
    cfg = _cfg(observer_enabled=True, judge_enabled=False)
    runner = build_meta_runner(cfg)
    assert runner.similarity is default_similarity
    assert runner.judge is None


def test_observer_and_judge_enabled_wires_real_clients(rated):
    """Both on AND a rating record in force → EmbeddingSimilarity wraps
    the embedding client; Judge
    holds a ChatClient. No network hits at construction time — both
    clients are lazy."""
    cfg = _cfg(observer_enabled=True, judge_enabled=True)
    runner = build_meta_runner(cfg)
    assert isinstance(runner.similarity, EmbeddingSimilarity)
    assert isinstance(runner.judge, Judge)
    # The judge holds the chat client (constructed but unused yet).
    assert runner.judge.chat is not None
    # The judge's founding-knowledge paths come from the config default.
    paths = [str(p) for p in runner.judge.founding_knowledge_paths]
    assert any("environment.md" in p for p in paths)
    assert any("engineering-principles.md" in p for p in paths)
    # Chapter 4 §4e — when judge is wired, drafting is too. The skill
    # dir lands on the runner from `PathsConfig.skill_dir`.
    assert runner.skill_dir is not None
    assert "skill" in str(runner.skill_dir)


def test_observer_enabled_no_judge_has_no_skill_dir():
    """Drafting is gated on the judge being wired — `skill_dir` stays
    None until both flags are on, so a partially-enabled Observer
    never tries to draft into a real path."""
    cfg = _cfg(observer_enabled=True, judge_enabled=False)
    runner = build_meta_runner(cfg)
    assert runner.skill_dir is None
    assert runner.judge is None


def test_observer_config_carries_auto_iterate_flag():
    """The flag exists and defaults to False — auto-iteration is opt-in."""
    cfg = Config()
    assert cfg.observer.auto_iterate_on_task_end is False

    cfg.observer = ObserverConfig(auto_iterate_on_task_end=True)
    assert cfg.observer.auto_iterate_on_task_end is True


def test_judge_requested_without_rating_record_refuses_to_boot(tmp_path, monkeypatch):
    """The chokepoint. `judge_enabled: true` with no valid rating record does
    not quietly wire a judge and does not quietly drop one - it raises, and the
    daemon does not start. This is what holds when the pre-commit guard is
    absent: a config edited inside the container, a `--no-verify` commit, a
    branch that does not carry the hook."""
    monkeypatch.setenv(RATING_RECORD_ENV, str(tmp_path / "does-not-exist.yaml"))
    cfg = _cfg(observer_enabled=True, judge_enabled=True)
    with pytest.raises(JudgeNotCalibratedError) as exc:
        build_meta_runner(cfg)
    assert "judge_enabled" in str(exc.value)


def test_judge_requested_with_rejecting_record_refuses_to_boot(tmp_path, monkeypatch):
    """A record that exists but does not APPROVE buys nothing - the same rule
    the pre-commit guard applies, from the same function."""
    rec = tmp_path / "rating.yaml"
    rec.write_text(VALID_RATING.replace("approve", "reject"), encoding="utf-8")
    monkeypatch.setenv(RATING_RECORD_ENV, str(rec))
    cfg = _cfg(observer_enabled=True, judge_enabled=True)
    with pytest.raises(JudgeNotCalibratedError):
        build_meta_runner(cfg)


def test_judge_requested_while_observer_off_still_refuses(tmp_path, monkeypatch):
    """`observer.enabled: false` is NOT an escape hatch. If it were, a config
    could carry an unrated `judge_enabled: true` indefinitely and the judge
    would switch on the day someone flipped the innocuous flag - exactly the
    neighbouring-case shape this round exists to close."""
    monkeypatch.setenv(RATING_RECORD_ENV, str(tmp_path / "does-not-exist.yaml"))
    cfg = _cfg(observer_enabled=False, judge_enabled=True)
    with pytest.raises(JudgeNotCalibratedError):
        build_meta_runner(cfg)
