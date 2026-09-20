# sl-ao-envfile - regression matrix across every attempt tip

Every matrix row is planted, staged, and run against the
`check-env-file-scope.ps1` blob from EACH attempt tip and from the current one.
`R` = the check refused it (exit 1), `G` = it passed.

**The assertion is not "the current script is right".** It is *the current script
sees everything its predecessors saw*: no row may be `G` at this tip where an
earlier tip was `R`, unless it is a documented deliberate green. Five rounds each
fixed the previous counter-example and shipped a new one, and four of those five
defects are visible below as a lone `G` in a row of `R`s.

Generated against the `check-env-file-scope.ps1` blob at `264217d` for the last
column. Re-run it if that blob changes; the other five columns are historical and
do not move.

| table | row | attempt 1<br>`4b714de` | attempt 2<br>`1bf6802` | attempt 3<br>`86b5a7b` | attempt 4<br>`2168396` | attempt 5<br>`5ee330c` | attempt 6<br>`ab430e7` | this tip<br>`HEAD` | expected |
|---|---|---|---|---|---|---|---|---|---|
| T6a-i | `scalar` | R | R | R | R | R | R | R | R |
| T6a-i | `scalar-quoted` | R | R | R | R | R | R | R | R |
| T6a-i | `scalar-comment` | R | R | R | R | R | R | R | R |
| T6a-i | `flow-seq` | **G** | R | R | R | R | R | R | R |
| T6a-i | `flow-seq-quoted` | **G** | R | R | R | R | R | R | R |
| T6a-i | `flow-seq-two-one-bad` | **G** | R | R | R | R | R | R | R |
| T6a-i | `flow-seq-unterminated` | **G** | R | R | R | R | R | R | R |
| T6a-i | `block-item` | R | R | R | R | R | R | R | R |
| T6a-i | `block-item-quoted-cmt` | R | R | R | R | R | R | R | R |
| T6a-i | `block-item-dotslash` | R | R | R | R | R | R | R | R |
| T6a-i | `longform-path` | **G** | R | R | R | R | R | R | R |
| T6a-i | `longform-path-quoted` | **G** | R | R | R | R | R | R | R |
| T6a-i | `longform-required-1st` | **G** | R | R | R | R | R | R | R |
| T6a-i | `longform-flow-map` | **G** | R | R | R | R | R | R | R |
| T6a-i | `deeper-root` | R | R | R | R | R | R | R | R |
| T6a-i | `absolute` | R | R | R | R | R | R | R | R |
| T6a-i | `root-envtest` | R | R | R | R | R | R | R | R |
| T6a-i | `cross-plane` | R | R | R | R | R | R | R | R |
| T6a-i | `interpolated` | **G** | R | R | R | R | R | R | R |
| T6a-i | `own-dir` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-i | `own-dir-quoted-cmt` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-i | `own-dir-dotslash` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-i | `own-dir-flow` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-i | `own-dir-longform` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-i | `blank-line-in-list` | **G** | R | R | R | R | R | R | R |
| T6a-i | `climb-back-to-own-dir` | **G** | R | R | R | R | R | R | R |
| T6a-i | `parent-plane-own` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-ii | `plane-own-scalar` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-ii | `plane-own-flow` | R | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-ii | `plane-own-block` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-ii | `plane-own-longform` | R | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-ii | `root-scalar` | R | R | R | R | R | R | R | R |
| T6a-ii | `root-flow` | **G** | R | R | R | R | R | R | R |
| T6a-ii | `root-block` | R | R | R | R | R | R | R | R |
| T6a-ii | `root-longform` | **G** | R | R | R | R | R | R | R |
| T6a-ii | `root-flow-map` | **G** | R | R | R | R | R | R | R |
| T6a-ii | `sibling-fragment-dir` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-iii | `alias-scalar` | **G** | **G** | R | R | R | R | R | R |
| T6a-iii | `alias-list-item` | **G** | **G** | R | R | R | R | R | R |
| T6a-iii | `alias-in-flow-seq` | **G** | **G** | R | R | R | R | R | R |
| T6a-iii | `alias-longform-path` | **G** | **G** | R | R | R | R | R | R |
| T6a-iii | `alias-anchor-in-xblock` | **G** | **G** | R | R | R | R | R | R |
| T6a-iii | `alias-to-flow-list` | **G** | **G** | R | R | R | R | R | R |
| T6a-iii | `alias-undefined` | **G** | **G** | R | R | R | R | R | R |
| T6a-iii | `alias-to-own-env` | **G** | **G** | **G** | R | R | R | R | R |
| T6a-iii | `alias-DUPLICATE-anchor` | **G** | **G** | **G** | R | R | R | R | R |
| T6a-iii | `anchor-inline-on-value` | **G** | R | R | R | R | R | R | R |
| T6a-iii | `anchor-inline-own` | **G** | R | R | R | R | R | R | R |
| T6a-iii | `anchor-in-other-svc` | **G** | R | R | R | R | R | R | R |
| T6a-iii | `tag-str` | **G** | R | R | R | R | R | R | R |
| T6a-iii | `tag-custom` | **G** | R | R | R | R | R | R | R |
| T6a-iii | `block-scalar-folded` | **G** | **G** | R | R | R | R | R | R |
| T6a-iii | `block-scalar-literal` | **G** | **G** | R | R | R | R | R | R |
| T6a-iii | `interp-scalar` | **G** | R | R | R | R | R | R | R |
| T6a-iii | `merge-key-from-xblock` | R | R | R | R | R | R | R | R |
| T6a-iii | `absolute-drive` | R | R | R | R | R | R | R | R |
| T6a-iii | `absolute-posix` | R | R | R | R | R | R | R | R |
| T6a-iii | `space-in-path` | R | R | R | R | R | R | R | R |
| T6a-iii | `tab-in-path` | R | R | R | R | R | R | R | R |
| T6a-iii | `tab-as-separator` | R | R | R | R | R | R | R | R |
| T6a-iii | `trailing-backslash` | R | R | R | R | R | R | R | R |
| T6a-iii | `subdir-of-compose-dir` | R | R | R | R | R | R | R | R |
| T6a-iii | `tilde-home` | R | R | R | R | R | R | R | R |
| T6a-iv | `next-line-scalar` | **G** | **G** | **G** | **G** | R | R | R | R |
| T6a-iv | `next-line-scalar-quoted` | **G** | **G** | **G** | **G** | R | R | R | R |
| T6a-iv | `next-line-scalar-own` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-iv | `bare-dash-item` | **G** | **G** | **G** | **G** | R | R | R | R |
| T6a-iv | `bare-dash-item-quoted` | **G** | **G** | **G** | **G** | R | R | R | R |
| T6a-iv | `bare-dash-path-key` | **G** | **G** | **G** | **G** | R | R | R | R |
| T6a-iv | `bare-dash-item-own` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-iv | `deeper-second-item` | R | R | R | R | R | R | R | R |
| T6a-iv | `comment-inside-extent` | R | R | R | R | R | R | R | R |
| T6a-iv | `blank-inside-extent` | **G** | R | R | R | R | R | R | R |
| T6a-iv | `comment-then-blank-then` | **G** | **G** | **G** | **G** | R | R | R | R |
| T6a-iv | `value-at-key-indent` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-iv | `shallower-ends-extent` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-iv | `longform-required-deeper` | **G** | R | R | R | R | R | R | R |
| T6a-iv | `longform-required-same` | **G** | R | R | R | R | R | R | R |
| T6a-iv | `two-items-second-bad` | R | R | R | R | R | R | R | R |
| T6a-iv | `tab-indented-item` | R | R | R | R | R | R | R | R |
| T6a-iv | `own-then-sibling-key` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-v | `dash-at-key-indent` | R | R | R | R | **G** | R | R | R |
| T6a-v | `dash-at-key-indent-own` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-v | `dash-at-key-indent-quoted` | R | R | R | R | **G** | R | R | R |
| T6a-v | `dash-at-key-indent-two` | R | R | R | R | **G** | R | R | R |
| T6a-v | `bare-dash-at-key-indent` | **G** | **G** | **G** | **G** | **G** | R | R | R |
| T6a-v | `dash-then-sibling-key` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-vi | `cmt-key-block-list` | **G** | **G** | **G** | **G** | **G** | **G** | R | R |
| T6a-vi | `cmt-key-next-scalar` | **G** | **G** | **G** | **G** | **G** | **G** | R | R |
| T6a-vi | `cmt-key-dash-keyind` | **G** | **G** | **G** | **G** | **G** | **G** | R | R |
| T6a-vi | `cmt-key-block-own` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-vi | `cmt-key-scalar-own` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-vi | `cmt-key-dash-own` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-vii | `dash-key-indent-2sp` | R | R | R | R | **G** | R | R | R |
| T6a-vii | `dash-key-indent-tab` | R | R | R | R | **G** | R | R | R |
| T6a-vii | `dash-key-indent-then-path` | **G** | R | R | R | **G** | R | R | R |
| T6a-vii | `bare-dash-key-then-path` | **G** | **G** | **G** | **G** | **G** | R | R | R |
| T6a-vii | `comment-between-key-dashes` | R | R | R | R | **G** | R | R | R |
| T6a-vii | `sibling-key-then-other-list` | **G** | **G** | **G** | **G** | **G** | **G** | **G** | G |
| T6a-viii | `flow-map-quoted-path-key` | **G** | R | R | R | R | R | R | R |
| T6a-viii | `multi-line-flow-seq` | **G** | R | R | R | R | R | R | R |
| T6a-viii | `backslash-separators` | R | R | R | R | R | R | R | R |

## Verdict

* rows **102**, tips **7**, cells **714**
* result at this tip differs from expected: **0**
* GREEN here, RED at an earlier tip: **2** -> plane-own-flow, plane-own-longform
* of those, UNDOCUMENTED - i.e. a regression: **0**

Deliberate greens found in this run, each with the reason it is allowed:

* `plane-own-flow` - same, flow sequence
* `plane-own-longform` - same, long form

## Rerunning it

From a scratch clone at the tip under test:

```bash
python documentation/evidence/sl-ao-envfile/regression-matrix.py <clone-root> <out.md>
```

The generator lives beside this file. It stages plants into the clone and resets
after each row, so run it in a SCRATCH clone, never in a worktree you care about.
