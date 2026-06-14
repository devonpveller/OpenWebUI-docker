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
