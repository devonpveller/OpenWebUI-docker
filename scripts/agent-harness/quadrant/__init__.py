"""The runner x target quadrant comparison (dark-factory-unification U4).

PLAN 1's L3 names two orthogonal axes - RUNNER (little-coder | claude-code) and TARGET
(self | project(<repo>)) - and 2's U4 row validates the phase by "same anchored item run
per quadrant (runner x target), outcomes compared".

This package is that comparison, and its whole design is aimed at ONE failure mode: a
comparison silently missing two of four quadrants reads as a completed comparison. The
mechanisms against it are stated as data in `schema.json` and listed, numbered, under
"The failure mode it is built against" in `MODULE.md` - which also carries the public
surface, what mechanism 4 does NOT defend against, and how to delete this package.
"""
