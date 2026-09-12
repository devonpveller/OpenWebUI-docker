# research-trust-report — evidence

**The canonical copies of the three files below are the FIXTURES at
`OB1/integrations/research-service/fixtures/`; these are the human-facing record and are
byte-identical to them (`cmp` them if you want to check).** An OB1 test may never read a file
outside OB1 — the submodule is tested and built on its own, and a test that climbs into the
parent repo passes in a full checkout and fails everywhere else.

| file | what it is |
|---|---|
| `live-owui-33250e9b.result.json` | the live OWUI run, whole: synthesis, needs, needs_status, search record, and the document it delivered |
| `rendered-BEFORE-33250e9b.md` | what the reader actually got — the RED for this item |
| `rendered-AFTER-33250e9b.md` | this branch's render of the SAME synthesis, through the buyer's-guide template and the shipped fidelity check |
| `rendered-AFTER-v1-33250e9b.md` | attempt 1's render, kept as the defect the tester found: a cell that turned "makes it difficult" into "no ATX or SFX drop-in available" |

`TEST-PLAN.md` is the plan the tester executes.
