"""The judge-enablement CHOKEPOINT -- the one place that answers
"may the LLM-in-the-loop judge run?".

WHY THIS MODULE EXISTS
----------------------
`observer.judge_enabled` is the flag that lets the Observer mint expertise
with an LLM in the loop (design section 13 open item 2: do not flip it until a
human has rated a dry run). Before this module the question was answered in
TWO places that disagreed:

  * the daemon, via `yaml.safe_load` + pydantic (`config.py` -> `ObserverConfig
    .judge_enabled`), consumed at `meta_wiring.build_meta_runner`;
  * the pre-commit guard, via a REGEX over staged text
    (`scripts/checks/check-judge-flag.ps1`).

A regex approximating a YAML parser is not a guard, it is a second and worse
parser -- and the two answers differed on TEN of eighteen deliberately chosen
spellings (`YES`, `ON`, `y`, `"true"`, `'yes'`, a quoted key, a flow mapping,
`1`, `!!bool "true"`, and an `\\x65`-escaped key all turned the flag ON for the
daemon while the regex saw nothing). Enumerate-and-patch loses that race; the
fix is that only ONE parser exists, and both the daemon and the hook ask it.

WHAT IS ENFORCED, AND WHERE
---------------------------
Two enforcement points, ONE rule (`read_rating_record` below), ONE artifact
(`little-coder/config/judge-enablement-rating.yaml`, which the coder compose
project mounts read-only at `/app/config/judge-enablement-rating.yaml` -- so
the record a commit stages is byte-for-byte the record the daemon reads):

  1. RUNTIME (the chokepoint). `require()` is called by
     `meta_wiring.build_meta_runner`, the single site that constructs a
     `Judge`. A config that requests the judge without a valid approving
     rating record raises `JudgeNotCalibratedError` and the daemon does not
     boot. This holds against a config edited INSIDE the container, a commit
     made with `--no-verify`, a branch without the hook, and any YAML spelling
     whatsoever -- because the value being tested is the one pydantic already
     produced.
  2. COMMIT TIME (the perimeter). `enabled_in_yaml_text()` is what
     `check-judge-flag.ps1` calls -- through
     `scripts/checks/lib/judge_flag_decide.py`, not through a pattern -- so a
     commit that turns the flag on is denied and audited unless it stages a
     valid record.

`tests/test_judge_gate_chokepoint.py` is the completeness proof: it AST-scans
the package and fails if ANY module other than this one reads `judge_enabled`,
or if any module other than `meta_wiring` constructs a `Judge`. A future
wiring path cannot bypass the gate by omission, because the only two ways to
decide the question -- call this module, or read the field -- are both watched.

WHAT THIS DOES NOT DO, stated so nothing reads wider than it is:
  * It does not stop someone EDITING this module, or deleting the call in
    `meta_wiring`. The completeness test turns the second one red; nothing
    but review catches the first.
  * It does not authenticate the rating record. `rated_by: me` is a valid
    record. The record is an operator artifact under review, not a credential.
  * It says nothing about whether the judge's OUTPUT is good. That is
    JUDGE-CALIBRATION.md's dry run, a different instrument.
  * `LC_JUDGE_RATING_RECORD` lets a caller point the gate at any file, which
    means anyone who controls the daemon's ENVIRONMENT can satisfy it. That is
    not a new class of power: `daemon.py:1086` already resolves the whole
    config from `LC_CONFIG`, so environment control was always config control.
    The override exists so tests and drills need no writable /app/config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import TypeAdapter

from .config import Config, ObserverConfig

# The key whose value this module is the sole reader of.
FLAG_KEY = "judge_enabled"
# The block it lives in, in the daemon's schema.
FLAG_SECTION = "observer"

# Container path (coder/docker-compose.yml mounts ../little-coder/config ->
# /app/config:ro) and the repository path that produces it. Both are asserted
# against the compose file and against the hook's default by
# tests/test_judge_gate_chokepoint.py -- three readers of one fact.
DEFAULT_RATING_RECORD_PATH = "/app/config/judge-enablement-rating.yaml"
RATING_RECORD_REPO_PATH = "little-coder/config/judge-enablement-rating.yaml"
RATING_RECORD_ENV = "LC_JUDGE_RATING_RECORD"

# Every key a rating record must carry with a non-empty value. Design section
# 13 exit criterion 3 is a HUMAN rating of the emitted judge prompts, so the
# record names the human, when, and what they read.
RATING_REQUIRED = ("rated_by", "rated_at", "rated_report", "verdict")

# The daemon's OWN annotation for the flag, not a re-declaration of it. If the
# field's type ever changes, this coercion follows it instead of drifting.
_FLAG_ADAPTER = TypeAdapter(ObserverConfig.model_fields[FLAG_KEY].annotation)


class JudgeNotCalibratedError(RuntimeError):
    """The config requests the judge and no valid approving rating record is
    in force. Raised at boot; the daemon does not start."""


@dataclass(frozen=True)
class JudgeAdmission:
    """The decision, with its reason attached so callers never re-derive it."""

    requested: bool  # what the config asks for
    permitted: bool  # what the gate allows
    rating_path: str  # where the record was looked for
    problem: str  # empty when permitted, or when nothing was requested


# --- the rating record: ONE definition ------------------------------------


def read_rating_record(path: Path | str) -> tuple[dict | None, str]:
    """Return (record, problem). A record is valid when it parses as YAML (or
    as YAML frontmatter), carries every key in RATING_REQUIRED with a non-empty
    value, and its verdict is 'approve'.

    This is the ONLY definition of a valid rating record. The pre-commit guard
    reaches it through scripts/checks/lib/judge_flag_decide.py and the dry-run
    instrument through scripts/checks/lib/judge_dryrun.py, which delegates
    here. Two copies of this rule would drift, and the copy that drifts is the
    one nobody looks at.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        return None, "rating record unreadable: %s" % exc
    body = text
    if text.lstrip().startswith("---"):
        stripped = text.lstrip()
        end = stripped.find("\n---", 3)
        if end < 0:
            return None, "rating record frontmatter is unterminated"
        body = stripped[stripped.find("\n") + 1 : end]
    try:
        data = yaml.safe_load(body)
    except Exception as exc:  # noqa: BLE001 - any parse failure is "not valid"
        return None, "rating record is not valid YAML: %s" % exc
    if not isinstance(data, dict):
        return None, "rating record must be a YAML mapping"
    missing = [k for k in RATING_REQUIRED if not str(data.get(k, "")).strip()]
    if missing:
        return None, "rating record is missing required key(s): " + ", ".join(missing)
    if str(data.get("verdict", "")).strip().lower() != "approve":
        return None, "rating record verdict is %r, not 'approve'" % data.get("verdict")
    return data, ""


def resolve_rating_record_path(
    explicit: str | Path | None = None, env: dict | None = None
) -> str:
    """Where the record is read from: an explicit argument, else
    `LC_JUDGE_RATING_RECORD`, else the container path the compose file mounts."""
    if explicit:
        return str(explicit)
    src = os.environ if env is None else env
    return str(src.get(RATING_RECORD_ENV) or DEFAULT_RATING_RECORD_PATH)


# --- the runtime decision --------------------------------------------------


def admit(
    config: Config,
    *,
    rating_record_path: str | Path | None = None,
    env: dict | None = None,
) -> JudgeAdmission:
    """Decide whether the judge may run for this config. Never raises.

    THIS FUNCTION HOLDS THE ONLY READ OF `observer.judge_enabled` in the
    package (tests/test_judge_gate_chokepoint.py enforces that by AST scan).

    Note what is deliberately NOT consulted: `observer.enabled`. A config that
    requests the judge is judged on that alone, so that flipping the innocuous
    `observer.enabled` later cannot silently switch the judge on -- the
    neighbouring-case failure this gate exists to stop.
    """
    requested = bool(getattr(config.observer, FLAG_KEY))
    path = resolve_rating_record_path(rating_record_path, env)
    if not requested:
        return JudgeAdmission(False, False, path, "")
    record, problem = read_rating_record(path)
    if record is None:
        return JudgeAdmission(True, False, path, problem)
    return JudgeAdmission(True, True, path, "")


def require(
    config: Config,
    *,
    rating_record_path: str | Path | None = None,
    env: dict | None = None,
) -> bool:
    """Return whether the judge is permitted, raising if it was REQUESTED and
    is not permitted. A silent downgrade would leave the operator believing the
    judge is on; a bad config fails the boot, which is what `load_config`
    already does for every other config error."""
    decision = admit(config, rating_record_path=rating_record_path, env=env)
    if decision.requested and not decision.permitted:
        raise JudgeNotCalibratedError(
            "observer.%s is on but no valid rating record is in force at %s: %s. "
            "Design section 13 exit criterion 3 is a HUMAN rating of the emitted "
            "judge prompts -- produce them with check-judge-dryrun.ps1 -EmitPrompts "
            "and record the verdict at %s."
            % (
                FLAG_KEY,
                decision.rating_path,
                decision.problem,
                RATING_RECORD_REPO_PATH,
            )
        )
    return decision.permitted


# --- the commit-time decision, from the same parser ------------------------


@dataclass(frozen=True)
class TextVerdict:
    """What a candidate YAML text asks for."""

    enabled: bool  # the flag is turned on somewhere in this text
    undecidable: str  # non-empty => the caller must FAIL CLOSED
    where: str  # human-readable location, for the audit line


def _coerce(value) -> tuple[bool | None, str]:
    """Coerce a YAML scalar with the daemon's own field type."""
    try:
        return bool(_FLAG_ADAPTER.validate_python(value)), ""
    except Exception as exc:  # noqa: BLE001
        return None, "%s: %r is not a boolean the daemon can read (%s)" % (
            FLAG_KEY,
            value,
            type(exc).__name__,
        )


def _walk(node, trail: str, hits: list) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            here = "%s.%s" % (trail, k) if trail else str(k)
            if k == FLAG_KEY:
                hits.append((here, v))
            _walk(v, here, hits)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            _walk(v, "%s[%d]" % (trail, i), hits)


def enabled_in_yaml_text(text: str) -> TextVerdict:
    """Does this YAML text turn the judge on, read the way the daemon reads it?

    Uses `yaml.safe_load` -- the daemon's parser, from `config.load_config` --
    and the daemon's own pydantic coercion for the value, so no spelling of
    YAML true can mean one thing here and another there.

    TWO deliberate widenings over `load_config`, both of which can only make
    the guard say YES more often (over-denying a commit is safe; under-denying
    is the bug this replaces):
      * every document of a multi-document stream is examined, not just the
        first, and
      * `judge_enabled` is looked for at ANY depth, not only under `observer`,
        so a future schema move or a partial/templated copy of the config is
        still seen.
    A text that is not loadable YAML is NOT a candidate: the daemon's
    `load_config` raises `ConfigError` on it, so it cannot turn the flag on.
    """
    try:
        docs = list(yaml.safe_load_all(text))
    except Exception:  # noqa: BLE001 - unparseable => the daemon cannot load it
        return TextVerdict(False, "", "")
    hits: list = []
    for i, doc in enumerate(docs):
        _walk(doc, "" if len(docs) == 1 else "doc[%d]" % i, hits)
    for where, value in hits:
        ok, problem = _coerce(value)
        if ok is None:
            return TextVerdict(True, problem, where)
        if ok:
            return TextVerdict(True, "", where)
    return TextVerdict(False, "", "")
