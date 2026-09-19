"""Keep pytest out of the item fixtures.

`items/*/files/test_*.py` are FIXTURES, not tests of this repository. They are planted into
a run workspace and executed there, by `quadrant/guards.py`, against whatever implementation
the runner produced. Collected by the repo's own pytest they fail every time, because the
implementation they are pointed at is the shipped stub - which is the whole point of the
item and would otherwise read as nine broken tests in the harness suite.

Found by running `python -m pytest scripts/agent-harness -q` before committing, which is
the exact command the merge protocol asks a tester for.
"""

collect_ignore_glob = ["*"]
