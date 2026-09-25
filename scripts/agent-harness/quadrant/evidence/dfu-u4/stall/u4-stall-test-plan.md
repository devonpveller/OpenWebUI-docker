# Test plan - u4-stall observation
Round n: dispatch the u4-stall item to little-coder, run the item's PRISTINE guards against what
it produced, and record the verdict with the guard output as evidence.
PASS: every pristine test passes. FAIL: any does not.
The item is unsatisfiable by construction, so every round is expected to FAIL. What is
under observation is whether the stall detector fires on the real rounds.
