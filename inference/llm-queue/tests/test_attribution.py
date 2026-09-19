"""Caller-key attribution precedence (design §10.3.2)."""

from llm_queue.routes.data import _attribute_key


def test_real_auth_key_wins():
    assert _attribute_key("owui-chat", None) == "owui-chat"
    assert _attribute_key("owui-chat", "some-uuid") == "owui-chat"  # auth beats user


def test_sentinel_auth_falls_back_to_user():
    # LiteLLM forwards `dummy` as the api_key; a caller-set `user` rescues attribution.
    assert _attribute_key("dummy", "ob-entity") == "ob-entity"
    assert _attribute_key("not-needed", "owui-chat") == "owui-chat"


def test_sentinel_and_no_user_stays_sentinel():
    # Nothing identifying → returns the sentinel, which classify() maps to default.
    assert _attribute_key("dummy", None) == "dummy"
    assert _attribute_key(None, None) is None


def test_empty_auth_uses_user():
    assert _attribute_key("", "llama") == "llama"


def test_caller_header_wins_over_everything():
    # J.1: the gateway hook's x-ai-stack-caller header is THE identity signal
    # through LiteLLM (which strips both Authorization and `user`).
    assert _attribute_key("dummy", None, caller_header="ob-research") == "ob-research"
    assert _attribute_key("real-key", "some-user", caller_header="owui-chat") == "owui-chat"


def test_absent_caller_header_preserves_legacy_precedence():
    assert _attribute_key("owui-chat", "x", caller_header=None) == "owui-chat"
    assert _attribute_key("dummy", "ob-entity", caller_header=None) == "ob-entity"
