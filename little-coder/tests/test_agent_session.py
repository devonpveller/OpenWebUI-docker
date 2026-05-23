"""Agent session-per-trigger wiring (design §3.1 follow-up).

Pins: session-flag filtering (daemon owns session policy), session-id
fallback per channel, path-safety on the id, use_session=False path.
"""

from __future__ import annotations

from littlecoder.agent import AgentRunner
from littlecoder.config import AgentConfig, Config
from littlecoder.tasks import TaskContext


def _runner(**agent_overrides) -> AgentRunner:
    """Build an AgentRunner with a tweaked AgentConfig for testing."""
    cfg = Config()
    cfg.agent = AgentConfig(
        command=["little-coder"],
        model="llamacpp/m",
        prompt_mode="arg",
        extra_args=agent_overrides.get(
            "extra_args",
            ["--print", "--mode", "json"],
        ),
        use_session=agent_overrides.get("use_session", True),
        session_dir=agent_overrides.get("session_dir", "/sessions"),
    )
    # AgentRunner only reads cfg.agent in _build_invocation; pass
    # dummies for the other constructor args.
    return AgentRunner(cfg, journals=None, ot_client=None)  # type: ignore[arg-type]


def _ctx(session_id: str | None = "chat-abc", channel: str = "owui") -> TaskContext:
    """Minimal TaskContext-shape for testing — only `.state.session_id`
    and `.state.channel` are read by `_session_id_for`."""

    class _State:
        pass

    st = _State()
    st.session_id = session_id
    st.channel = channel
    st.prompt = "ignored"  # not used in these tests

    class _Ctx:
        state = st

    return _Ctx()  # type: ignore[return-value]


# --- use_session=True path ---------------------------------------------


def test_use_session_appends_session_path_and_session_dir():
    """The `--session` argument is a FULL PATH (not a bare id) so pi's
    resolveSessionPath creates the file if missing. A bare id would
    trigger id-LOOKUP which fails on first-touch chats."""
    runner = _runner()
    cmd, _ = runner._build_invocation("hello", _ctx(session_id="chat-abc"))
    assert "--session" in cmd
    session_arg = cmd[cmd.index("--session") + 1]
    # Includes `/` (so pi treats as path) AND ends with `.jsonl`.
    assert "/" in session_arg or "\\" in session_arg
    assert session_arg.endswith("chat-abc.jsonl")
    assert "--session-dir" in cmd
    assert cmd[cmd.index("--session-dir") + 1] == "/sessions"
    assert "--no-session" not in cmd


def test_use_session_false_uses_no_session():
    runner = _runner(use_session=False)
    cmd, _ = runner._build_invocation("hi", _ctx())
    assert "--no-session" in cmd
    assert "--session" not in cmd
    assert "--session-dir" not in cmd


# --- session-flag filtering (daemon owns the policy) -------------------


def test_extra_args_no_session_is_filtered_when_session_on():
    """Operator left --no-session in extra_args; daemon owns session
    policy and filters it out."""
    runner = _runner(
        extra_args=["--print", "--no-session", "--mode", "json"]
    )
    cmd, _ = runner._build_invocation("hi", _ctx())
    assert "--no-session" not in cmd
    assert "--session" in cmd


def test_extra_args_session_flag_with_value_is_filtered():
    """If extra_args contains `--session foo`, the daemon's --session
    wins — the operator-supplied one is dropped along with its value."""
    runner = _runner(
        extra_args=["--print", "--session", "operator-id", "--mode", "json"]
    )
    cmd, _ = runner._build_invocation("hi", _ctx(session_id="daemon-id"))
    # Only the daemon's session path appears in the cmd; the path
    # carries the daemon-supplied id, not the operator's.
    session_values = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--session"]
    assert len(session_values) == 1
    assert "daemon-id.jsonl" in session_values[0]
    # 'operator-id' shouldn't appear anywhere as a bare arg.
    assert "operator-id" not in cmd


def test_extra_args_resume_continue_fork_filtered():
    """All session-affecting flags from the upstream pi CLI get stripped
    so the daemon's policy is the only session story."""
    runner = _runner(
        extra_args=["-r", "-c", "--continue", "--fork", "old-id"]
    )
    cmd, _ = runner._build_invocation("hi", _ctx())
    for forbidden in ("-r", "-c", "--continue", "--fork", "--resume", "old-id"):
        assert forbidden not in cmd, f"{forbidden!r} should have been filtered"


# --- session-path resolution -------------------------------------------


def test_session_path_uses_explicit_value_when_present():
    runner = _runner()
    path = runner._session_path_for(_ctx(session_id="chat-deadbeef"))
    assert path.endswith("chat-deadbeef.jsonl")
    assert path.startswith("/sessions/") or path.startswith("/sessions\\")


def test_session_path_falls_back_to_per_channel_default():
    runner = _runner()
    # session_id=None should pull `default_session_ids["cli"]` (= "cli-default").
    path_cli = runner._session_path_for(_ctx(session_id=None, channel="cli"))
    assert path_cli.endswith("cli-default.jsonl")
    path_owui = runner._session_path_for(_ctx(session_id="", channel="owui"))
    assert path_owui.endswith("owui-default.jsonl")


def test_session_path_falls_back_to_channel_named_default_for_unknown_channel():
    runner = _runner()
    path = runner._session_path_for(_ctx(session_id=None, channel="custom"))
    assert path.endswith("custom-default.jsonl")


def test_session_path_filename_is_path_safe():
    """pi uses the filename component as a path segment — strip unsafe
    chars from the id portion (the dir separator must remain a `/`)."""
    runner = _runner()
    path = runner._session_path_for(_ctx(session_id="a/b\\c:d e*f?"))
    # The FILENAME component must not contain these chars.
    import os
    fname = os.path.basename(path)
    for bad in ("\\", ":", " ", "*", "?"):
        assert bad not in fname
    # Path-separator `/` does appear at the directory boundary, which
    # is what tells pi to treat this as a path.
    assert "/" in path


def test_session_path_filename_is_length_bounded():
    """Pathologically long ids are truncated in the filename portion."""
    runner = _runner()
    path = runner._session_path_for(_ctx(session_id="a" * 500))
    import os
    fname = os.path.basename(path)
    # safe[:128] + ".jsonl" = at most 134 chars in the filename.
    assert len(fname) <= 134


def test_session_path_includes_slash_so_pi_treats_as_path():
    """The KEY invariant — the `--session` arg MUST contain a `/` so
    pi's resolveSessionPath returns `type: path` (create-or-open)
    rather than `type: id-lookup` (fail-on-miss). Regression guard
    for the `No session found matching <id>` bug."""
    runner = _runner()
    path = runner._session_path_for(_ctx(session_id="any-id"))
    assert "/" in path or "\\" in path
    assert path.endswith(".jsonl")
