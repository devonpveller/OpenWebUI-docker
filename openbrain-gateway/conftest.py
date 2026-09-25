"""pytest config for openbrain-gateway.

smoke_test.py matches pytest's *_test.py pattern but is a live-door script (it
exits at import without OPENBRAIN_GATEWAY_KEY and talks to a running gateway),
not a unit test. Run it by hand: python openbrain-gateway/smoke_test.py
"""
collect_ignore = ["smoke_test.py"]
