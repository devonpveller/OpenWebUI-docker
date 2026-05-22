"""Pure parsing helpers in the CLI and ot-exec shim."""

import pytest

from littlecoder.cli import _parse_duration
from littlecoder.otexec import _parse_args


@pytest.mark.parametrize(
    "text,seconds",
    [("30m", 1800), ("1h", 3600), ("90s", 90), ("45", 45), ("2.5m", 150)],
)
def test_parse_duration(text, seconds):
    assert _parse_duration(text) == seconds


def test_otexec_dash_c_form():
    assert _parse_args(["-c", "pytest -q"]) == "pytest -q"


def test_otexec_bare_form():
    assert _parse_args(["ls", "-la", "/workspace"]) == "ls -la /workspace"


def test_otexec_empty():
    assert _parse_args([]) == ""
