"""Plan-only turns (agent-org bridge, 2026-07-14) — headless plan mode.

A `plan_only` task runs the agent with `edit,write` merged into the
`--exclude-tools` denylist, so the turn can explore and PLAN but cannot
change a file (the upstream plan-mode extension's edit guard, enforced
by tool exclusion instead of a TUI toggle). Pins: the merge preserves
the config's own denylist, a normal task is untouched, and the trigger
route carries the flag into TaskState.
"""

from __future__ import annotations

from littlecoder.agent import AgentRunner
from littlecoder.config import AgentConfig, Config
from littlecoder.daemon import TriggerRequest


def _runner(extra_args: list[str]) -> AgentRunner:
    cfg = Config()
    cfg.agent = AgentConfig(
        command=["little-coder"],
        model="llamacpp/m",
        prompt_mode="arg",
        extra_args=extra_args,
        use_session=True,
        session_dir="/sessions",
    )
    return AgentRunner(cfg, journals=None, ot_client=None)  # type: ignore[arg-type]


def _ctx(plan_only: bool):
    class _State:
        pass

    st = _State()
    st.session_id = "chat-1"
    st.channel = "batch"
    st.prompt = "ignored"
    st.plan_only = plan_only

    class _Ctx:
        state = st

    return _Ctx()


def _exclude_value(cmd: list[str]) -> str:
    assert cmd.count("--exclude-tools") == 1, cmd
    return cmd[cmd.index("--exclude-tools") + 1]


def test_plan_only_merges_edit_write_into_the_config_denylist():
    runner = _runner(["--print", "--exclude-tools", "webfetch,websearch"])
    cmd, _ = runner._build_invocation("plan it", _ctx(plan_only=True))
    assert _exclude_value(cmd) == "webfetch,websearch,edit,write"
    assert "--print" in cmd                      # other extra_args untouched


def test_plan_only_without_a_config_denylist_still_excludes_edit_write():
    runner = _runner(["--print"])
    cmd, _ = runner._build_invocation("plan it", _ctx(plan_only=True))
    assert _exclude_value(cmd) == "edit,write"


def test_normal_task_keeps_the_config_denylist_verbatim():
    runner = _runner(["--print", "--exclude-tools", "webfetch,websearch"])
    cmd, _ = runner._build_invocation("do it", _ctx(plan_only=False))
    assert _exclude_value(cmd) == "webfetch,websearch"


def test_ctx_without_the_field_behaves_as_a_normal_task():
    # Older callers / test doubles without `.plan_only` must not break.
    runner = _runner(["--print", "--exclude-tools", "webfetch"])
    ctx = _ctx(plan_only=False)
    del ctx.state.plan_only
    cmd, _ = runner._build_invocation("do it", ctx)
    assert _exclude_value(cmd) == "webfetch"


def test_trigger_request_carries_plan_only_default_false():
    assert TriggerRequest(prompt="x").plan_only is False
    assert TriggerRequest(prompt="x", plan_only=True).plan_only is True
