"""little-coder control-plane wrapper (Chapter 1: Tool).

See ../documentation/little-coder/Self-improving-little-coder-design.md.
"""

__version__ = "0.1.0"

# Envelope / config schema versions. Readers tolerate older shapes
# (forward-compat, design §12.9); bump on any schema-affecting change.
SCHEMA_VERSION = 1
