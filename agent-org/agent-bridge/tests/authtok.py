"""Fake bridge tokens for tests (ao-auth). Built from parts so no literal looks like a credential;
they authenticate only against a test app built with them."""

OP_TOKEN = "-".join(["fake", "operator", "0p3r", "4t0r", "a1b2c3d4e5f6"])
WK_TOKEN = "-".join(["fake", "worker", "w0rk", "3r", "f6e5d4c3b2a1"])
OP_HEADERS = {"Authorization": "Bearer " + OP_TOKEN}
WK_HEADERS = {"Authorization": "Bearer " + WK_TOKEN}
