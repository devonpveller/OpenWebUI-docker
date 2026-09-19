from llm_queue.policy import build_policy


def test_default_classes_ordering():
    p = build_policy("", default_wait_s=120.0)
    assert p.classify("owui-chat").rank == 0
    assert p.classify("ollama").rank == 1  # mnemory
    assert p.classify("llama").rank == 2  # lc-coder
    assert p.classify("ob-entity").rank == 3  # batch
    assert p.classify("ob-entity").max_concurrency == 2


def test_ob_digest_lane():
    # #27: openbrain-digest is wired to the gateway (LOCAL_LLM_BEARER /
    # OB_DIGEST_LLM_KEY) and gets its own batch lane — rank 3, generous wait
    # budget, capped so a digest burst can't starve interactive callers.
    p = build_policy("", default_wait_s=120.0)
    cls = p.classify("ob-digest")
    assert cls.name == "ob-digest"
    assert cls.rank == 3
    assert cls.acceptable_wait_s == 600.0
    assert cls.max_concurrency == 2


def test_unknown_key_is_default():
    p = build_policy("", default_wait_s=120.0)
    cls = p.classify("some-random-junk-key")
    assert cls.name == "default"
    assert cls.rank == 2


def test_none_key_is_default():
    p = build_policy("", default_wait_s=99.0)
    assert p.classify(None).acceptable_wait_s == 99.0


def test_substring_match():
    p = build_policy("", default_wait_s=120.0)
    # A caller presenting "owui-chat-session-42" still maps to the chat class.
    assert p.classify("owui-chat-session-42").rank == 0


def test_json_override():
    cfg = '{"vip": {"class": "vip", "rank": 0, "acceptable_wait_s": 5, "max_concurrency": 1}}'
    p = build_policy(cfg, default_wait_s=120.0)
    cls = p.classify("vip")
    assert cls.rank == 0
    assert cls.acceptable_wait_s == 5.0
    assert cls.max_concurrency == 1
    # default still applies to others
    assert p.classify("owui-chat").name == "default"


def test_runtime_set_key():
    p = build_policy("", default_wait_s=120.0)
    before = p.classify("newcaller")
    assert before.name == "default"
    from llm_queue.policy import PriorityClass

    p.set_key("newcaller", PriorityClass("newcaller", rank=0, acceptable_wait_s=10.0))
    assert p.classify("newcaller").rank == 0
