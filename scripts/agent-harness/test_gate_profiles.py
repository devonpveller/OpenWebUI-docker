"""Tests for the gate profiles and the andon declaration - including that both readers agree.

The cross-language test at the bottom is the point, for the same reason it is the point in
``test_harness_config.py``: two readers of one file is a standing invitation to drift, and a
gate profile that PowerShell reads as ``dark`` while the bridge reads as ``attended`` is a
silent removal of a human from the loop.

    python -m pytest scripts/agent-harness/test_gate_profiles.py -q
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("AI_STACK_HARNESS_CONFIG", "AI_STACK_HARNESS_ENABLED", "AI_STACK_HARNESS_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    config.load(fresh=True)
    yield
    config.load(fresh=True)


def test_the_shipped_default_is_attended():
    # The default must be the SAFE one. A typo that lands on the default should leave a
    # human at the gate, not remove one.
    for gate in config.GATES:
        assert config.resolve_gate(gate)["passer"] == "human", gate


def test_dark_auto_passes_every_gate():
    for gate in config.GATES:
        assert config.resolve_gate(gate, "dark")["passer"] == "auto", gate


def test_an_unknown_gate_profile_is_loud():
    # Never silently served by the default. Serving `attended` for a typo would be safe and
    # serving `dark` would not, and a rule that depends on which way the typo fell is not a
    # rule.
    with pytest.raises(config.HarnessConfigError) as e:
        config.resolve_gate("anchor", "drak")
    assert "drak" in str(e.value)


def test_an_unknown_gate_is_loud():
    with pytest.raises(config.HarnessConfigError):
        config.resolve_gate("post_merge")


def test_a_gate_profile_may_only_say_human_or_auto(monkeypatch, tmp_path):
    cfg = json.loads((HERE / "harness.config.json").read_text(encoding="utf-8"))
    cfg["gate_profiles"]["sloppy"] = {"anchor": "maybe", "pre_review": "human"}
    p = tmp_path / "harness.config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("AI_STACK_HARNESS_CONFIG", str(p))
    config.load(fresh=True)
    with pytest.raises(config.HarnessConfigError) as e:
        config.resolve_gate("anchor", "sloppy")
    assert "maybe" in str(e.value)


def test_every_gate_profile_assigns_every_gate():
    # A profile that omits a gate would leave that gate's behaviour undefined, which in an
    # unattended run means "nobody knows whether a human was meant to see this".
    for name in config.gate_profile_names():
        for gate in config.GATES:
            assert config.resolve_gate(gate, name)["passer"] in ("human", "auto")


def test_the_reserved_auto_namespace_is_declared_once():
    assert config.AUTO_PRINCIPAL_PREFIX == "auto:"
    # No shipped gate profile name may collide with the namespace, or an auto principal
    # would be indistinguishable from a profile-shaped human id.
    for name in config.gate_profile_names():
        assert not name.startswith(config.AUTO_PRINCIPAL_PREFIX)


# --- the andon declaration ------------------------------------------------------------

def _andon_conditions():
    return config.get("andon.conditions") or []


def _assert_declared_values(conds):
    """The guard body itself, so a test can prove it FIRES rather than describe it.

    Lifted out of ``test_every_andon_condition_is_fully_declared`` for exactly one reason:
    the previous two versions of that guard were vacuous, and a guard is only verified by
    making the thing it guards against and watching it go red. The mutation tests below call
    this and require an ``AssertionError``.
    """
    for c in conds:
        for field in ("id", "detects", "predicate", "on_fire", "on_indeterminate"):
            assert c.get(field), f"{c.get('id')} is missing '{field}'"
        for field in ("on_fire", "on_indeterminate"):
            assert c[field] in config.ALLOWED_ANDON_ACTIONS, (
                f"{c['id']} declares {field}={c[field]!r}, which the board does not implement "
                f"(allowed: {config.ALLOWED_ANDON_ACTIONS})")
        expected = config.REQUIRED_ANDON_CONDITIONS.get(c["id"])
        if expected is not None:
            assert c["predicate"] == expected, (
                f"{c['id']} is wired to predicate {c['predicate']!r}, not {expected!r} - "
                "the id is required, so what it RUNS is required with it")
        # PLAN section 0 A6: a condition whose detection is prose is FALSIFIED. Every one
        # must name an incident it came from, so nobody can add an invented condition
        # without noticing they have nothing to cite.
        assert c.get("incident"), f"{c['id']} cites no incident"


def test_every_andon_condition_is_fully_declared():
    """Every DECLARED condition carries every field, WITH THE VALUE THAT MATTERS.

    Two rounds of the same defect are pinned in this one test. It first asserted
    ``assert conds`` - non-emptiness - so four of five conditions could be deleted and it
    stayed green (fixed by the required-SET test below). Its replacement then asserted that
    ``predicate`` and ``on_fire`` were TRUTHY, which is the same class one level down: a
    condition keeping a required id while naming a different predicate, or downgrading
    ``on_fire`` to a word that does not halt, satisfied it completely. So the fields that
    DECIDE something are checked against their allowed values, not against emptiness:

    * ``on_fire`` / ``on_indeterminate`` must be words the board implements
      (``config.ALLOWED_ANDON_ACTIONS``, mirrored in ``andon.ps1``, which REFUSES any other);
    * ``predicate`` must be the one that id is supposed to run
      (``config.REQUIRED_ANDON_CONDITIONS``, declared in code).

    ``id``/``detects``/``incident`` stay truthiness checks because they are prose: nothing
    downstream branches on their content.

    SCOPE: this reads the COMMITTED ``harness.config.json``. It cannot see a swap made at run
    time or in a config named by ``AI_STACK_HARNESS_CONFIG`` - see
    ``test_a_predicate_swap_that_keeps_the_id_is_detected`` for what is and is not covered.
    """
    conds = _andon_conditions()
    assert conds, "the shipped config declares no andon conditions"
    _assert_declared_values(conds)


def test_no_shipped_condition_downgrades_on_fire():
    """The shipped board halts on every fire, and that is a decision, not an accident.

    ``warn`` is a legal word (a human at an attended board can use the severity), but no
    shipped condition uses it: all five come from incidents where continuing was the
    failure. The run-time half of this - a fired condition is never a ``clear`` board, so a
    downgrade cannot open an unattended gate either - is proven at the real gate by
    ``drill-dark-factory.ps1`` step J, not here.
    """
    for c in _andon_conditions():
        assert c["on_fire"] == "halt", f"{c['id']} declares on_fire={c['on_fire']!r}"


def test_no_shipped_condition_downgrades_on_indeterminate():
    """THE SIBLING KEY, and the reason this test exists as its own function.

    ``on_fire`` was pinned here on 2026-08-30 and ``on_indeterminate`` was not, so the
    identical downgrade stayed available on the sibling key and was reproduced end to end the
    same day: ``protected-ref-moved`` with no baseline printed ``ANDON BOARD: CLEAR`` at exit
    0, the dark gate auto-passed signed ``auto:dark``, and the condition that could not be
    evaluated appeared in no audit surface at all.

    A condition that could not be EVALUATED has not passed. The run-time half - an
    indeterminate condition is never a ``clear`` board whatever its action says - is proven
    at the real gate by ``drill-dark-factory.ps1`` step K, not here; this pins the shipped
    config so the question never has to be asked at run time.
    """
    for c in _andon_conditions():
        assert c["on_indeterminate"] == "halt", (
            f"{c['id']} declares on_indeterminate={c['on_indeterminate']!r}")


def test_an_unenumerated_outcome_lands_in_a_REFUSING_bucket():
    """THE GENERALISATION, pinned in the mirror as well as at the gate.

    Three rounds running, a fix closed one outcome key and left its sibling, because the
    verdict was computed by exception: any outcome nobody enumerated silently meant "fine".
    The bucket table is total - an unknown status, an unknown action, or an unknown
    combination of two known words all land in ``unrecognised``, which is a bucket the board
    refuses on. No branch names the new word, and none has to.
    """
    assert config.andon_bucket("ok", "none") == config.ANDON_CLEAR_BUCKET
    for status, action in [
        ("indeterminate", "warn"),   # the 2026-08-30 defect: a bucket, not a pass
        ("fire", "warn"),            # the round before it
    ]:
        assert config.andon_bucket(status, action) != config.ANDON_CLEAR_BUCKET
    for status, action in [
        ("fire", "quarantine"),      # an action word nobody wrote
        ("parked", "none"),          # a status no predicate returns
        ("skipped", "shrug"),        # neither word enumerated
        ("ok", "halt"),              # both words known, the PAIR is not
        ("", ""),                    # and nothing at all
    ]:
        assert config.andon_bucket(status, action) == config.ANDON_UNRECOGNISED_BUCKET, (
            f"({status!r}, {action!r}) fell somewhere other than the refusing bucket")
    # Every bucket the table can produce must have a board word, or the verdict would have an
    # outcome it cannot name - which is the same defect one level up.
    for bucket in set(config.ANDON_BUCKETS.values()) | {config.ANDON_UNRECOGNISED_BUCKET}:
        assert bucket in config.ANDON_BUCKET_BOARD, f"bucket {bucket!r} has no board word"
    # ...and only ``evaluated_ok`` may map to ``clear``.
    clear = [b for b, w in config.ANDON_BUCKET_BOARD.items() if w == "clear"]
    assert clear == [config.ANDON_CLEAR_BUCKET], clear


_WORD_NUMBERS = {"four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _board_words():
    """Every board word the code can actually produce.

    Read from the CODE, not from a mirror: the literals `andon.ps1` assigns to ``$board``,
    plus the words ``$script:AndonBucketBoard`` maps buckets to. A test that compared the doc
    against a hand-kept list would only prove the list and the doc agree.
    """
    andon = (HERE / "andon.ps1").read_text(encoding="ascii")
    literals = set(re.findall(r'\$board\s*=\s*"([a-z-]+)"', andon))
    assert literals, "no $board assignments found - the regex has drifted from the file"
    return literals | set(config.ANDON_BUCKET_BOARD.values())


def test_the_MODULE_verdict_table_matches_the_board():
    """A PROSE NUMBER BESIDE A TABLE IS A CLAIM THE TABLE CAN CONTRADICT.

    MODULE.md said "The verdict has five states" above a SIX-row table on 2026-08-30: the
    uncounted sixth was ``warned``, added that morning. It is the same defect class as the
    board's own - a statement nobody re-derives - so it is re-derived here, against the words
    ``andon.ps1`` can actually assign.
    """
    text = (HERE / "MODULE.md").read_text(encoding="utf-8")
    m = re.search(r"The verdict has \*\*(\w+)\*\* states", text)
    assert m, "MODULE.md no longer states how many verdict states there are"
    claimed = _WORD_NUMBERS.get(m.group(1).lower())
    assert claimed, f"unrecognised count word {m.group(1)!r}"

    # The rows of the verdict table: `| \`word\` | ... | exit |`
    rows = re.findall(r"^\| `([a-z-]+)` \|.*\| ([067]) \|$", text, re.M)
    assert rows, "the verdict table did not parse - has its shape changed?"
    listed = [w for w, _ in rows]
    assert len(listed) == len(set(listed)), f"duplicate rows: {listed}"
    assert len(listed) == claimed, f"MODULE claims {claimed} states, the table has {len(listed)}: {listed}"
    assert set(listed) == _board_words(), (
        f"table={sorted(listed)} code={sorted(_board_words())}")
    # ...and exactly one of them opens an unattended gate.
    zeroes = [w for w, code in rows if code == "0"]
    assert zeroes == ["clear"], zeroes


def test_the_MODULE_ledger_schema_number_matches_the_code():
    """MODULE.md described the ledger as schema 3 while ``gate-audit.ps1`` was at 4.

    A schema number is what tells a reader whether a record is too old to answer a question,
    so a doc trailing the code by a release is worse than no number at all.
    """
    ga = (HERE / "gate-audit.ps1").read_text(encoding="ascii")
    m = re.search(r"\$script:GateLedgerSchema\s*=\s*(\d+)", ga)
    assert m, "gate-audit.ps1 no longer declares $script:GateLedgerSchema"
    code = int(m.group(1))
    text = (HERE / "MODULE.md").read_text(encoding="utf-8")
    d = re.search(r"\*\*Ledger schema is (\d+)\*\*", text)
    assert d, "MODULE.md no longer states the ledger schema number"
    assert int(d.group(1)) == code, f"MODULE says schema {d.group(1)}, gate-audit.ps1 is at {code}"


def test_the_shipped_config_declares_every_required_condition():
    """THE SET, not its size.

    ``REQUIRED_ANDON_CONDITIONS`` lives in ``config.py``/``config.ps1`` - in code - precisely
    so this comparison has two independent sides. A required list kept inside
    ``harness.config.json`` beside the conditions would agree with itself no matter what was
    deleted from it.
    """
    declared = [c["id"] for c in _andon_conditions()]
    assert set(declared) == set(config.REQUIRED_ANDON_CONDITIONS), (
        f"declared={sorted(declared)} required={sorted(config.REQUIRED_ANDON_CONDITIONS)}")
    assert config.missing_andon_conditions() == []
    # ...and each of them runs the predicate it is supposed to run. The set test above
    # compares IDS, which an entry can satisfy while being a different check entirely.
    assert config.andon_predicate_mismatches() == []


def _write_cfg(tmp_path, monkeypatch, mutate):
    cfg = json.loads((HERE / "harness.config.json").read_text(encoding="utf-8"))
    mutate(cfg)
    p = tmp_path / "harness.config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("AI_STACK_HARNESS_CONFIG", str(p))
    config.load(fresh=True)
    return p


@pytest.mark.parametrize("keep", [
    # pruned to one - the shape reproduced at the real gate on 2026-08-30
    ["work-branch-on-remote"],
    # and the single deletion, which is the one that would actually go unnoticed
    ["operator-checkout-off-branch", "policy-declared-unread", "git-error-swallowed",
     "work-branch-on-remote"],
])
def test_a_thinned_board_is_detected_as_a_MISSING_SET(keep, tmp_path, monkeypatch):
    """RED-PROOF, kept permanently: deleting condition ENTRIES is not detectable by counting.

    The board that produced this test read ``clear, 1 declared, 1 evaluated, 0 switched off``
    on a genuinely detached checkout and auto-passed a dark gate at exit 0. Every counter it
    reported was relative to the config's own thinned list, so every counter was true. Only a
    set the config cannot edit can answer the question.
    """
    _write_cfg(tmp_path, monkeypatch, lambda c: c["andon"].__setitem__(
        "conditions", [x for x in c["andon"]["conditions"] if x["id"] in keep]))
    conds = _andon_conditions()

    # The vacuity, pinned so it cannot come back: the OLD assertion is still satisfied.
    assert conds, "precondition - a thinned board is not an empty one"
    assert len(conds) == len(keep)

    missing = config.missing_andon_conditions()
    assert set(missing) == set(config.REQUIRED_ANDON_CONDITIONS) - set(keep)
    assert missing, "a board missing required conditions must NAME them, not merely be short"


def test_the_required_set_is_not_reachable_from_the_config_file(tmp_path, monkeypatch):
    """A config may not edit the list it is checked against - in either direction.

    Both halves matter. Declaring ``andon.required_conditions`` must not narrow the required
    set (that would be the thinning with an extra step), and it must not widen it either, or
    a config could invent a requirement no predicate implements.
    """
    _write_cfg(tmp_path, monkeypatch, lambda c: c["andon"].__setitem__(
        "required_conditions", ["work-branch-on-remote"]))
    assert tuple(config.REQUIRED_ANDON_CONDITIONS) == (
        "operator-checkout-off-branch", "policy-declared-unread", "git-error-swallowed",
        "work-branch-on-remote", "protected-ref-moved")
    # ...and with every condition still declared, the board is still complete.
    assert config.missing_andon_conditions() == []


@pytest.mark.parametrize("swap_to", ["branch-on-remote", "config-key-unread"])
def test_a_predicate_swap_that_keeps_the_id_is_detected(swap_to, tmp_path, monkeypatch):
    """RED-PROOF, kept permanently: an id can be squatted on.

    ``operator-checkout-off-branch`` keeps its id, its ``detects`` prose and its incident,
    and is re-pointed at another implemented predicate. Nothing about the SET changes - five
    ids declared, five required, none missing - and the board still evaluates five
    conditions. What is gone is the detector the id names.

    WHAT THIS DOES AND DOES NOT COVER, stated because the sentence it replaced ("no route
    through the config opens the gates") was false: it covers the COMMITTED config, which is
    what a reviewer merges. It does not make a run-time swap detectable - ``andon.ps1`` reads
    the ids and runs whatever predicate the entry names - and neither does anything else.
    That route is open and is named as open in README.md and MODULE.md.
    """
    def mutate(c):
        for cond in c["andon"]["conditions"]:
            if cond["id"] == "operator-checkout-off-branch":
                cond["predicate"] = swap_to

    _write_cfg(tmp_path, monkeypatch, mutate)
    conds = _andon_conditions()

    # The vacuity, pinned so it cannot come back: the truthiness guard is fully satisfied.
    assert conds and len(conds) == 5
    for c in conds:
        assert c.get("predicate") and c.get("on_fire")
    # ...and so is the id-set check, which is why it cannot be the only one.
    assert config.missing_andon_conditions() == []

    # RED: the guard body goes off, and it names the id and both predicates.
    with pytest.raises(AssertionError) as e:
        _assert_declared_values(conds)
    assert "operator-checkout-off-branch" in str(e.value)
    assert swap_to in str(e.value) and "git-checkout-state" in str(e.value)

    mismatches = config.andon_predicate_mismatches()
    assert [m[0] for m in mismatches] == ["operator-checkout-off-branch"], mismatches
    assert mismatches[0][1] == "git-checkout-state"
    assert mismatches[0][2] == swap_to


def test_an_on_fire_the_board_does_not_implement_is_not_allowed(tmp_path, monkeypatch):
    """The other value the truthiness guard waved through: any non-empty string passed.

    ``andon.ps1`` refuses an unknown action at evaluation time (exit 1, no verdict, which
    every gate reads as "not clear"); this is the committed-config half of the same rule.
    """
    _write_cfg(tmp_path, monkeypatch, lambda c: c["andon"]["conditions"][0].__setitem__(
        "on_fire", "log-it-and-carry-on"))
    conds = _andon_conditions()
    assert conds[0]["on_fire"], "precondition - the vacuous guard takes any non-empty word"
    assert conds[0]["on_fire"] not in config.ALLOWED_ANDON_ACTIONS
    with pytest.raises(AssertionError) as e:
        _assert_declared_values(conds)
    assert "log-it-and-carry-on" in str(e.value)


def test_no_andon_condition_treats_indeterminate_as_a_pass():
    # A skip that counts as a pass is one of the failure shapes the board exists to catch;
    # it does not get to be the board's own behaviour.
    for c in _andon_conditions():
        assert c["on_indeterminate"] == "halt", c["id"]


def test_andon_condition_ids_are_unique():
    ids = [c["id"] for c in _andon_conditions()]
    assert len(ids) == len(set(ids)), ids


PS = shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(PS is None, reason="no PowerShell on PATH")
def test_every_declared_predicate_is_implemented():
    """A condition may not name a detector nobody wrote.

    Asked of the SHIPPED andon.ps1 rather than of a list kept here, because a list kept here
    is a list with a spell-checker - it would agree with itself while the file disagreed.
    """
    script = (
        "$ErrorActionPreference='Stop';"
        + "& '{0}' -List | Out-String".format((HERE / "andon.ps1").as_posix())
    )
    out = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    for c in _andon_conditions():
        line = [ln for ln in out.stdout.splitlines() if "predicate :" in ln and c["predicate"] in ln]
        assert line, f"andon.ps1 -List never mentions predicate '{c['predicate']}'"
        assert "MISSING" not in line[0], f"{c['id']} names an unimplemented predicate: {line[0]}"


@pytest.mark.skipif(PS is None, reason="no PowerShell on PATH")
def test_powershell_and_python_agree_about_the_gates():
    """The anti-drift test: same file, same questions, same answers."""
    script = (
        ". '{0}';".format((HERE / "config.ps1").as_posix())
        + "$o=[ordered]@{};"
        + "$o.gates=@(Get-GateNames);"
        + "$o.prefix=(Get-AutoPrincipalPrefix);"
        + "$o.profiles=@(Get-GateProfileNames);"
        + "$o.required=@(Get-RequiredAndonConditionIds);"
        + "$o.actions=@(Get-AllowedAndonActions);"
        + "$o.buckets=[ordered]@{};"
        + "foreach($k in $script:AndonBuckets.Keys){$o.buckets[$k]=$script:AndonBuckets[$k]};"
        + "$o.bucketboard=[ordered]@{};"
        + "foreach($k in $script:AndonBucketBoard.Keys){$o.bucketboard[$k]=$script:AndonBucketBoard[$k]};"
        + "$o.clearbucket=$script:AndonClearBucket;"
        + "$o.unrecognisedbucket=$script:AndonUnrecognisedBucket;"
        + "$o.predicates=[ordered]@{};"
        + "foreach($i in (Get-RequiredAndonConditionIds)){"
        + "$o.predicates[$i]=(Get-RequiredAndonPredicate -Id $i)};"
        + "$o.active=(Get-GateProfileName);"
        + "$o.resolved=[ordered]@{};"
        + "foreach($p in (Get-GateProfileNames)){"
        + "foreach($g in (Get-GateNames)){"
        + "$o.resolved[\"$p/$g\"]=(Resolve-Gate -Gate $g -Profile $p).passer}};"
        + "$o | ConvertTo-Json -Depth 6 -Compress"
    )
    out = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    ps = json.loads(out.stdout.strip())

    assert list(ps["gates"]) == list(config.GATES)
    assert ps["prefix"] == config.AUTO_PRINCIPAL_PREFIX
    assert sorted(ps["profiles"]) == sorted(config.gate_profile_names())
    # The required andon set is declared in BOTH readers, so it is exactly the kind of
    # duplicated constant that drifts. A PowerShell board that requires five conditions while
    # the bridge believes in one is a thinned board with a second opinion.
    assert list(ps["required"]) == list(config.REQUIRED_ANDON_CONDITIONS)
    # The id -> predicate map and the allowed actions are duplicated constants for the same
    # reason and drift the same way. A PowerShell board that allows a word the bridge does
    # not, or that expects a different predicate behind an id, is two boards.
    assert list(ps["actions"]) == list(config.ALLOWED_ANDON_ACTIONS)
    # THE BUCKET TABLE IS THE THIRD SUCH CONSTANT, and the one it would hurt most to drift:
    # if the two readers disagree about which outcomes count as `evaluated_ok`, they disagree
    # about when a human is needed. Compared as an ordered mapping because the board word is
    # chosen by SEVERITY ORDER.
    assert dict(ps["buckets"]) == {
        f"{status}|{action}": bucket for (status, action), bucket in config.ANDON_BUCKETS.items()}
    assert list(ps["bucketboard"].items()) == list(config.ANDON_BUCKET_BOARD.items())
    assert ps["clearbucket"] == config.ANDON_CLEAR_BUCKET
    assert ps["unrecognisedbucket"] == config.ANDON_UNRECOGNISED_BUCKET
    assert {k: str(v) for k, v in ps["predicates"].items()} == dict(config.REQUIRED_ANDON_CONDITIONS)
    assert ps["active"] == config.gate_profile_name()
    for name in config.gate_profile_names():
        for gate in config.GATES:
            assert ps["resolved"][f"{name}/{gate}"] == config.resolve_gate(gate, name)["passer"]


# ===================================================================================
# BOARD WORDS, WHEREVER THEY ARE WRITTEN DOWN
#
# The check above compares ONE table in ONE file. That is a derived gate whose alphabet has
# a single entry, which is the shape this effort keeps finding: the alphabet, not the
# comparison, decides how much a check covers. On 2026-08-30 the same list was wrong in four
# places at once - MODULE.md's `dark` bullet, and `andon.ps1` twice (its
# Invoke-AndonEvaluation comment and its exit-6 comment) - each omitting `indeterminate` and
# `unaccounted`; `queue.ps1`'s exit-code line had omitted `warned` one round earlier. Four
# instances, one defect, and a check that read only the MODULE table saw none of them.
#
# So the alphabet is now "every place in the repository where board words are enumerated",
# and THE LOCATIONS ARE DERIVED: `git ls-files` from the repository root, every tracked text
# file scanned. Nothing is listed by hand.
# ===================================================================================

REPO_ROOT = HERE.parents[1]


def _repo_text_files():
    """Every tracked text file in the repository, from git - not a hand-kept list.

    The OB1 submodule is excluded: it is a different project, and `git ls-files` reports it
    as one gitlink entry anyway. Files that are not valid UTF-8 are skipped rather than
    guessed at.
    """
    out = subprocess.run(["git", "ls-files"], cwd=str(REPO_ROOT),
                         capture_output=True, text=True, check=True).stdout
    for rel in out.splitlines():
        if rel.startswith("OB1/"):
            continue
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        try:
            yield rel, p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def _word_alternation(words):
    return "|".join(sorted(words, key=len, reverse=True))


def _inline_enumerations(text, words):
    """A run of board words joined ONLY by list punctuation, three or more of them.

    The gap may cross a line break and a comment sigil, because that is how these lists are
    actually written inside a `#` block: `queue.ps1`'s complete one wraps mid-list.
    """
    alt = _word_alternation(words)
    item = r"(?:``|`|\*\*)*(?:" + alt + r")(?:``|`|\*\*)*"
    gap = r"[ \t]*(?:\r?\n[ \t]*(?:#:|#|//|\*)?[ \t]*)?[ \t]*"
    sep = r"(?:(?:,|/|\||;)" + gap + r"(?:(?:or|and)[ \t]+)?|(?:or|and)[ \t]+)"
    run = re.compile(item + r"(?:" + gap + sep + gap + item + r"){2,}")
    for m in run.finditer(text):
        found = set(re.findall(r"\b(?:" + alt + r")\b", m.group(0)))
        yield text[:m.start()].count("\n") + 1, found, m.group(0)


#: How far apart two rows of one definition block may be. An entry whose description runs to
#: several continuation lines is still one block; a board word mentioned again twenty lines
#: later is not part of it.
_DEF_GAP = 6


def _block_enumerations(text, words):
    """A definition block or table: lines that each INTRODUCE a board word.

    `raised        - a condition halted the line` and `| `raised` | ... |` are both this
    shape. `` `clear` requires every bucket... `` is not: a comma or a single prose word
    after the term means a sentence about one word, not a row of a list. That distinction is
    what keeps the bucket lists in the findings note out of this - they name buckets, and
    their one shared word (`indeterminate`) is followed by a comma.
    """
    alt = _word_alternation(words)
    lead = re.compile(r"^[^A-Za-z0-9]*(?:``|`|\*\*)*(?P<w>" + alt +
                      r")(?:``|`|\*\*)*(?:[ \t]*[-:|>][ \t]|[ \t]+)(?=\S)")
    hits = []
    for i, line in enumerate(text.split("\n")):
        m = lead.match(line)
        if m:
            hits.append((i + 1, m.group("w")))
    block = []
    for h in hits:
        if block and h[0] - block[-1][0] > _DEF_GAP:
            yield block
            block = []
        block.append(h)
    if block:
        yield block


def test_every_enumeration_of_board_words_in_the_repo_is_complete():
    """A LIST OF THE WORDS THAT REFUSE IS A CLAIM ABOUT ALL OF THEM.

    Four sentences in three files listed some of the board's words as though they were all
    of them, and two of those omitted the two words added the same morning. A reader of any
    of them would conclude the missing words do NOT refuse - the opposite of what the code
    does.

    The rule is deliberately blunt, and stated so it can be obeyed: write three or more
    board words as a list and you are enumerating the alphabet, so write all of it - either
    the eight, or the seven that are not `clear`. If you mean a narrower set (the three
    cases one drill step covers, say), say so in words or write it as a mapping. Positional
    lists of board words are exactly how the four went wrong.

    DISCLOSED LIMITS. This finds ENUMERATIONS: one wrong board word in a sentence ("deleting
    the block reports `not-evaluated`") is not an enumeration and is not caught here. That
    class is covered, for the ways off the board, by the citation test below - and is not
    covered at all for anything else. And it cannot tell a QUOTATION of a bad list from a bad
    list, which is a real cost: the findings note describing this defect had to paraphrase
    the sentence it was reporting rather than quote it. Paraphrasing was chosen over an
    exemption on purpose - a rule with one file exempted from it is the shape that let four
    of these through.
    """
    words = _board_words()
    complete = (words, words - {"clear"})
    bad = []
    for rel, text in _repo_text_files():
        for lineno, found, snippet in _inline_enumerations(text, words):
            if len(found) >= 3 and found not in complete:
                bad.append("%s:%d lists %s and omits %s :: %r" % (
                    rel, lineno, sorted(found), sorted(words - found - {"clear"}),
                    snippet[:120]))
        for block in _block_enumerations(text, words):
            found = {w for _, w in block}
            if len(found) >= 3 and found not in complete:
                bad.append("%s:%d-%d lists %s and omits %s" % (
                    rel, block[0][0], block[-1][0], sorted(found),
                    sorted(words - found - {"clear"})))
    assert not bad, "incomplete enumerations of the board's words:\n  " + "\n  ".join(bad)


# ===================================================================================
# THE WAYS OFF THE BOARD: one table, cited by route id, proven by the drill
# ===================================================================================


def _ways_off_values():
    """What a ways-off row may claim: a board word, or `unavailable`.

    `unavailable` is not a board word - it is what a GATE records when the board could not
    produce a verdict at all - and it is the outcome of one row, so it is admitted here
    rather than failing the parse for being unrecognised.
    """
    return _board_words() | {"unavailable"}


def _ways_off_table():
    """(route id -> outcome word) parsed from README.md's ways-off table.

    That table is the ONE place stating which way off the board gives which state. It earns
    that by being the only one the drill proves and the only one anything cites.
    """
    text = (HERE / "README.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| `([a-z0-9-]+)` \|.*?\|([^|]*)\| halts \|$", text, re.M)
    assert rows, "README.md's ways-off table did not parse - has its shape changed?"
    out = {}
    for route, outcome_cell in rows:
        named = [w for w in re.findall(r"`([a-z-]+)`", outcome_cell) if w in _ways_off_values()]
        assert named, "ways-off row %r names no outcome this board can produce: %r" % (
            route, outcome_cell)
        assert route not in out, "duplicate ways-off route %r" % route
        out[route] = named[0]
    return out


def _ways_off_drill():
    """(route id -> outcome word) parsed from the drill's own $script:WaysOffProven.

    Read out of the drill rather than mirrored here, for the same reason `_board_words`
    reads `andon.ps1`: a test comparing two copies of a list proves only that the copies
    match each other.
    """
    text = (HERE / "drill-dark-factory.ps1").read_text(encoding="ascii")
    m = re.search(r"\$script:WaysOffProven = \[ordered\]@\{(.*?)\n\}", text, re.S)
    assert m, "drill-dark-factory.ps1 no longer declares $script:WaysOffProven"
    return dict(re.findall(r'"([a-z0-9-]+)"\s*=\s*"([a-z-]+)"', m.group(1)))


def test_the_ways_off_table_matches_the_drill_that_proves_it():
    """A ROW NOBODY DRILLS IS A CLAIM, NOT A PROOF.

    One row - an action word the board does not implement - asserted `andon.status =
    unavailable` in the ledger, and no drill check read that field: the exit code was
    checked and the sentence about the record was not. Half a verified row reads exactly
    like a whole one.
    """
    table = _ways_off_table()
    drill = _ways_off_drill()
    assert table == drill, "README table=%s drill=%s" % (
        sorted(table.items()), sorted(drill.items()))
    m = re.search(r"\*\*(\w+) ways of switching the board off",
                  (HERE / "README.md").read_text(encoding="utf-8"))
    assert m, "README.md no longer states how many ways off the board there are"
    claimed = _WORD_NUMBERS.get(m.group(1).lower())
    assert claimed == len(table), "README claims %r ways, the table has %d" % (
        m.group(1), len(table))


def test_every_citation_of_a_way_off_names_the_state_this_table_proves():
    """THE SAME MAPPING, WRITTEN OUT AGAIN, WENT WRONG IN THREE FILES AT ONCE.

    `andon.ps1`, `config.ps1` and `config.py` all said that `andon.enabled: false` and
    deleting the `andon` block "both report `not-evaluated`". Deleting the block reports
    `incomplete` - the whole point of the state that round added - and one commit wrote the
    sentence and made it false, in three files, none of them checked.

    So the mapping is stated once and CITED by route id everywhere else, and every citation
    in the repository is checked here. Locations are derived (`git ls-files`); matching is
    exact, because a route id is a unique token rather than a phrase to be recognised.

    DISCLOSED LIMITS, because they are the honest half. This polices CITATIONS: a fresh
    restatement naming no route id is invisible to it, exactly as the three false sentences
    were before they were rewritten as citations. What makes that less likely is that the
    table now says, in its own first sentence, that it is the only place the mapping lives.
    And, like the enumeration check, it cannot tell a quotation of a wrong citation from a
    wrong citation - the findings note lists its own red-proofs without naming the word each
    one used, for that reason.
    """
    table = _ways_off_table()
    # `clear` IS NOT A CLAIM ABOUT A ROUTE, and excluding it is not a convenience. No way off
    # the board yields `clear` - that a route no longer can is exactly what "closed" means -
    # so the word never appears beside a route id as an outcome claim. Where it does appear
    # is in the history each of these routes carries: "the gate AUTO-PASSED, exit 0, ledger
    # `clear`". Reading those as claims would have made the check fire on five true sentences
    # and taught the next reader to delete it.
    claims = _ways_off_values() - {"clear"}
    routes = re.compile(r"(?<![A-Za-z0-9-])(" +
                        "|".join(sorted(table, key=len, reverse=True)) + r")(?![A-Za-z0-9-])")
    word_rx = re.compile(r"(?<![A-Za-z0-9-])(" + _word_alternation(claims) + r")(?![A-Za-z0-9-])")
    bad = []
    for rel, text in _repo_text_files():
        marks = [(m.start(), m.end(), m.group(1)) for m in routes.finditer(text)]
        for i, (start, end, route) in enumerate(marks):
            stop = min(len(text), end + 240)
            if i + 1 < len(marks):
                stop = min(stop, marks[i + 1][0])
            named = set(word_rx.findall(text[end:stop]))
            if not named:
                continue          # a mention of the route, not a claim about its outcome
            if table[route] not in named:
                bad.append("%s:%d cites %s and names %s - the proven state is %r" % (
                    rel, text[:start].count("\n") + 1, route, sorted(named), table[route]))
    assert not bad, ("citations that disagree with README's ways-off table:\n  " +
                     "\n  ".join(bad))
