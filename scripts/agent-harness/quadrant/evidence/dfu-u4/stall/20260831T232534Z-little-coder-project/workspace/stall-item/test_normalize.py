"""The stall item's tests. FROZEN - editing this file is a recorded scope violation.

READ THIS BEFORE READING THE ASSERTIONS. This item is DELIBERATELY UNSATISFIABLE, and it is
the only fixture in this package that is. It exists to produce a REAL stall - a runner that
genuinely cannot converge, round after round - so that frontier-oracle-on-stall can be
observed firing on a stall that HAPPENED rather than on one written into a fixture
(dark-factory-unification PLAN §2, U4: "stall -> oracle observed firing at least once").

WHY IT CANNOT BE SATISFIED. `test_trims_and_lowercases` and `test_trims_and_preserves_case`
require DIFFERENT outputs for the SAME input. No pure function of one argument can return
two values, so no implementation passes both. Nothing here is hidden from the runner in a
way that would be unfair: the tests are planted in the workspace and are the only
specification, exactly as in every other item. The task is simply impossible, which is what
"a task that will genuinely stall it" means.

WHY THE FAILURE SIGNATURE IS STABLE, which is the property the detector needs. `guards.py`
executes these functions DIRECTLY rather than under pytest, so a bare `assert` raises
`AssertionError` with an EMPTY message. The guard therefore prints the same line -
`FAIL .../test_normalize.py::<name>: AssertionError:` - whatever the implementation
returned. Two rounds that fail the same test produce the same normalized failure tail, so
`oracle_on_stall.failure_signature` sees "a failure already seen on this item (a cycle, not
a step)" - which is the truth about a runner going round in circles, and is the whole point.

WHAT THIS MUST NOT BECOME. If someone "fixes" this file so it can pass, the item stops
being a stall probe and quietly becomes a second baseline. Its `_why` in item.json says the
same thing.
"""

from normalize import normalize


def test_collapses_internal_whitespace():
    assert normalize("alpha    beta") == "alpha beta"


def test_trims_and_lowercases():
    assert normalize("  Gamma  Delta  ") == "gamma delta"


def test_trims_and_preserves_case():
    # Same input as the test above, different required output. This is the contradiction.
    assert normalize("  Gamma  Delta  ") == "Gamma Delta"
