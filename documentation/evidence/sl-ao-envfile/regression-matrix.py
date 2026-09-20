"""Run EVERY matrix row against the check at each attempt tip, and refuse a row that
went from RED to GREEN.

Five rounds each fixed the previous counter-example and shipped a new one. This is what
stops a sixth: it does not ask whether the current script is right, it asks whether the
current script sees everything its predecessors saw.

Usage: python documentation/evidence/sl-ao-envfile/regression-matrix.py <clone-root> <out.md>

Runtime is about 12 minutes: it launches one PowerShell per row per tip.
"""
import io
import os
import re
import subprocess
import sys

ROOT = sys.argv[1]
OUT = sys.argv[2]
REL = 'agent-org/docker/docker-compose.yml'
REL2 = 'inference/compose/upstreams.yml'
TARGET = os.path.join(ROOT, *REL.split('/'))
TARGET2 = os.path.join(ROOT, *REL2.split('/'))

TIPS = [
    ('4b714de', 'attempt 1'),
    ('1bf6802', 'attempt 2'),
    ('86b5a7b', 'attempt 3'),
    ('2168396', 'attempt 4'),
    ('5ee330c', 'attempt 5'),
    ('ab430e7', 'attempt 6'),
    ('HEAD', 'this tip'),
]

A = 'x-root-env: &root_env ../../.env\n'
AOWN = 'x-own-env: &own_env .env\n'
AFLOW = 'x-envs: &envs [../../.env]\n'
AXBLOCK = 'x-shared:\n  env: &blk_env ../../.env\n'
ADUP = 'x-a: &root_env .env\nx-b: &root_env ../../.env\n'
AMAP = 'x-tpl: &tpl\n  env_file:\n    - ../../.env\n'

# (table, name, which-file, prelude, block, expected-at-this-tip)
ROWS = [
    ('i', 'scalar', 1, '', '    env_file: ../../.env\n', 'R'),
    ('i', 'scalar-quoted', 1, '', '    env_file: "../../.env"\n', 'R'),
    ('i', 'scalar-comment', 1, '', '    env_file: ../../.env  # shared\n', 'R'),
    ('i', 'flow-seq', 1, '', '    env_file: [../../.env]\n', 'R'),
    ('i', 'flow-seq-quoted', 1, '', '    env_file: ["../../.env"]\n', 'R'),
    ('i', 'flow-seq-two-one-bad', 1, '', '    env_file: [.env, ../../.env]\n', 'R'),
    ('i', 'flow-seq-unterminated', 1, '', '    env_file: [../../.env\n', 'R'),
    ('i', 'block-item', 1, '', '    env_file:\n      - ../../.env\n', 'R'),
    ('i', 'block-item-quoted-cmt', 1, '', '    env_file:\n      - "../../.env" # shared\n', 'R'),
    ('i', 'block-item-dotslash', 1, '', '    env_file:\n      - ./../../.env\n', 'R'),
    ('i', 'longform-path', 1, '', '    env_file:\n      - path: ../../.env\n        required: false\n', 'R'),
    ('i', 'longform-path-quoted', 1, '', '    env_file:\n      - path: "../../.env"\n        required: true\n', 'R'),
    ('i', 'longform-required-1st', 1, '', '    env_file:\n      - required: false\n        path: ../../.env\n', 'R'),
    ('i', 'longform-flow-map', 1, '', '    env_file:\n      - {path: ../../.env, required: false}\n', 'R'),
    ('i', 'deeper-root', 1, '', '    env_file:\n      - ../../../.env\n', 'R'),
    ('i', 'absolute', 1, '', '    env_file:\n      - D:/x/.env\n', 'R'),
    ('i', 'root-envtest', 1, '', '    env_file:\n      - ../../.env.test\n', 'R'),
    ('i', 'cross-plane', 1, '', '    env_file:\n      - ../../coder/.env\n', 'R'),
    ('i', 'interpolated', 1, '', '    env_file:\n      - ${SOME_ENV_FILE}\n', 'R'),
    ('i', 'own-dir', 1, '', '    env_file:\n      - .env\n', 'G'),
    ('i', 'own-dir-quoted-cmt', 1, '', '    env_file:\n      - ".env"  # own\n', 'G'),
    ('i', 'own-dir-dotslash', 1, '', '    env_file:\n      - ./.env\n', 'G'),
    ('i', 'own-dir-flow', 1, '', '    env_file: [.env]\n', 'G'),
    ('i', 'own-dir-longform', 1, '', '    env_file:\n      - path: .env\n        required: false\n', 'G'),
    ('i', 'blank-line-in-list', 1, '', '    env_file:\n      - .env\n\n      - ../../.env\n', 'R'),
    ('i', 'climb-back-to-own-dir', 1, '', '    env_file:\n      - ../docker/.env\n', 'R'),
    ('i', 'parent-plane-own', 1, '', '    env_file:\n      - ../.env\n', 'G'),
    ('ii', 'plane-own-scalar', 2, '', '    env_file: ../.env\n', 'G'),
    ('ii', 'plane-own-flow', 2, '', '    env_file: [../.env]\n', 'G'),
    ('ii', 'plane-own-block', 2, '', '    env_file:\n      - ../.env\n', 'G'),
    ('ii', 'plane-own-longform', 2, '', '    env_file:\n      - path: ../.env\n        required: false\n', 'G'),
    ('ii', 'root-scalar', 2, '', '    env_file: ../../.env\n', 'R'),
    ('ii', 'root-flow', 2, '', '    env_file: [../../.env]\n', 'R'),
    ('ii', 'root-block', 2, '', '    env_file:\n      - ../../.env\n', 'R'),
    ('ii', 'root-longform', 2, '', '    env_file:\n      - path: ../../.env\n        required: false\n', 'R'),
    ('ii', 'root-flow-map', 2, '', '    env_file:\n      - {path: ../../.env, required: false}\n', 'R'),
    ('ii', 'sibling-fragment-dir', 2, '', '    env_file:\n      - .env\n', 'G'),
    ('iii', 'alias-scalar', 1, A, '    env_file: *root_env\n', 'R'),
    ('iii', 'alias-list-item', 1, A, '    env_file:\n      - *root_env\n', 'R'),
    ('iii', 'alias-in-flow-seq', 1, A, '    env_file: [*root_env]\n', 'R'),
    ('iii', 'alias-longform-path', 1, A, '    env_file:\n      - path: *root_env\n        required: false\n', 'R'),
    ('iii', 'alias-anchor-in-xblock', 1, AXBLOCK, '    env_file: *blk_env\n', 'R'),
    ('iii', 'alias-to-flow-list', 1, AFLOW, '    env_file: *envs\n', 'R'),
    ('iii', 'alias-undefined', 1, '', '    env_file: *nosuch\n', 'R'),
    ('iii', 'alias-to-own-env', 1, AOWN, '    env_file: *own_env\n', 'R'),
    ('iii', 'alias-DUPLICATE-anchor', 1, ADUP, '    env_file: *root_env\n', 'R'),
    ('iii', 'anchor-inline-on-value', 1, '', '    env_file: &e ../../.env\n', 'R'),
    ('iii', 'anchor-inline-own', 1, '', '    env_file: &e .env\n', 'R'),
    ('iii', 'anchor-in-other-svc', 1, '', '    env_file:\n      - &shared ../../.env\n', 'R'),
    ('iii', 'tag-str', 1, '', '    env_file: !!str ../../.env\n', 'R'),
    ('iii', 'tag-custom', 1, '', '    env_file: !mytag ../../.env\n', 'R'),
    ('iii', 'block-scalar-folded', 1, '', '    env_file: >\n      ../../.env\n', 'R'),
    ('iii', 'block-scalar-literal', 1, '', '    env_file: |\n      ../../.env\n', 'R'),
    ('iii', 'interp-scalar', 1, '', '    env_file: ${SOME_ENV_FILE}\n', 'R'),
    ('iii', 'merge-key-from-xblock', 1, AMAP, '    labels:\n      - merged=1\n', 'R'),
    ('iii', 'absolute-drive', 1, '', '    env_file:\n      - D:/x/.env\n', 'R'),
    ('iii', 'absolute-posix', 1, '', '    env_file:\n      - /etc/shared/.env\n', 'R'),
    ('iii', 'space-in-path', 1, '', '    env_file:\n      - "../../my env/.env"\n', 'R'),
    ('iii', 'tab-in-path', 1, '', '    env_file:\n      - "../..\t/.env"\n', 'R'),
    ('iii', 'tab-as-separator', 1, '', '    env_file:\t../../.env\n', 'R'),
    ('iii', 'trailing-backslash', 1, '', '    env_file:\n      - ../../.env\\\n', 'R'),
    ('iii', 'subdir-of-compose-dir', 1, '', '    env_file:\n      - config/dev.env\n', 'R'),
    ('iii', 'tilde-home', 1, '', '    env_file:\n      - ~/.env\n', 'R'),
    ('iv', 'next-line-scalar', 1, '', '    env_file:\n      ../../.env\n', 'R'),
    ('iv', 'next-line-scalar-quoted', 1, '', '    env_file:\n      "../../.env"\n', 'R'),
    ('iv', 'next-line-scalar-own', 1, '', '    env_file:\n      .env\n', 'G'),
    ('iv', 'bare-dash-item', 1, '', '    env_file:\n      -\n        ../../.env\n', 'R'),
    ('iv', 'bare-dash-item-quoted', 1, '', '    env_file:\n      -\n        "../../.env"\n', 'R'),
    ('iv', 'bare-dash-path-key', 1, '', '    env_file:\n      -\n        path: ../../.env\n', 'R'),
    ('iv', 'bare-dash-item-own', 1, '', '    env_file:\n      -\n        .env\n', 'G'),
    ('iv', 'deeper-second-item', 1, '', '    env_file:\n      - .env\n          - ../../.env\n', 'R'),
    ('iv', 'comment-inside-extent', 1, '', '    env_file:\n      # the shared file\n      - ../../.env\n', 'R'),
    ('iv', 'blank-inside-extent', 1, '', '    env_file:\n\n      - ../../.env\n', 'R'),
    ('iv', 'comment-then-blank-then', 1, '', '    env_file:\n      # why\n\n      ../../.env\n', 'R'),
    ('iv', 'value-at-key-indent', 1, '', '    env_file:\n    ../../.env\n', 'G'),
    ('iv', 'shallower-ends-extent', 1, '', '    env_file:\n      - .env\n    image: x\n', 'G'),
    ('iv', 'longform-required-deeper', 1, '', '    env_file:\n      - path: ../../.env\n          required: false\n', 'R'),
    ('iv', 'longform-required-same', 1, '', '    env_file:\n      - path: ../../.env\n        required: false\n', 'R'),
    ('iv', 'two-items-second-bad', 1, '', '    env_file:\n      - .env\n      - ../../.env\n', 'R'),
    ('iv', 'tab-indented-item', 1, '', '    env_file:\n\t\t- ../../.env\n', 'R'),
    ('iv', 'own-then-sibling-key', 1, '', '    env_file:\n      - .env\n    labels:\n      - a=b\n', 'G'),
    ('v', 'dash-at-key-indent', 1, '', '    env_file:\n    - ../../.env\n', 'R'),
    ('v', 'dash-at-key-indent-own', 1, '', '    env_file:\n    - .env\n', 'G'),
    ('v', 'dash-at-key-indent-quoted', 1, '', '    env_file:\n    - "../../.env"\n', 'R'),
    ('v', 'dash-at-key-indent-two', 1, '', '    env_file:\n    - .env\n    - ../../.env\n', 'R'),
    ('v', 'bare-dash-at-key-indent', 1, '', '    env_file:\n    -\n      ../../.env\n', 'R'),
    ('v', 'dash-then-sibling-key', 1, '', '    env_file:\n    - .env\n    image: x\n', 'G'),
    # ---- T6a-vi: a trailing COMMENT on the key is not a value (review, 2026-09-20) ----
    ('vi', 'cmt-key-block-list', 1, '', '    env_file:  # the shared root file\n      - ../../.env\n', 'R'),
    ('vi', 'cmt-key-next-scalar', 1, '', '    env_file:  # note\n      ../../.env\n', 'R'),
    ('vi', 'cmt-key-dash-keyind', 1, '', '    env_file:  # note\n    - ../../.env\n', 'R'),
    ('vi', 'cmt-key-block-own', 1, '', '    env_file:  # the plane own file\n      - .env\n', 'G'),
    ('vi', 'cmt-key-scalar-own', 1, '', '    env_file:  # note\n      .env\n', 'G'),
    ('vi', 'cmt-key-dash-own', 1, '', '    env_file:  # note\n    - .env\n', 'G'),
    # ---- T6a-vii: the six rows the attempt-6 TESTER wrote, which lived only in their
    # evidence file until now. The plan's own rule is that a row living only in a
    # document is a row this detector cannot defend.
    ('vii', 'dash-key-indent-2sp', 1, '', '    env_file:\n    -  ../../.env\n', 'R'),
    ('vii', 'dash-key-indent-tab', 1, '', '    env_file:\n    -\t../../.env\n', 'R'),
    ('vii', 'dash-key-indent-then-path', 1, '', '    env_file:\n    - path: ../../.env\n', 'R'),
    ('vii', 'bare-dash-key-then-path', 1, '', '    env_file:\n    -\n      path: ../../.env\n', 'R'),
    ('vii', 'comment-between-key-dashes', 1, '', '    env_file:\n    - .env\n    # note\n    - ../../.env\n', 'R'),
    ('vii', 'sibling-key-then-other-list', 1, '', '    env_file:\n    - .env\n    hostname: x\n    dns:\n    - 1.1.1.1\n', 'G'),
    # ---- T6a-viii: the three the REVIEWER planted ------------------------------------
    ('viii', 'flow-map-quoted-path-key', 1, '', '    env_file:\n      - {"path": ../../.env, required: false}\n', 'R'),
    ('viii', 'multi-line-flow-seq', 1, '', '    env_file: [\n      ../../.env\n    ]\n', 'R'),
    ('viii', 'backslash-separators', 1, '', '    env_file:\n      - ..\\..\\.env\n', 'R'),
]

# Rows allowed to be GREEN here while an earlier tip was RED. Each needs a REASON, and
# the reason must be a documented decision, not "it is green now".
#
# KEYED BY ROW NAME, so a name must be unique across every table above and must not
# be reused for a different plant: renaming a row silently drops its exemption (the
# run then fails loudly, which is the safe direction), and REUSING a name silently
# lends one row's exemption to another, which is not. Add a name here only with the
# reason written out, and only for a row whose green is a decision someone made.
DELIBERATE_GREENS = {
    'own-dir': "the plane's own .env - the shape the rule exists to permit",
    'own-dir-quoted-cmt': 'same, quoted with a trailing comment',
    'own-dir-dotslash': 'same, ./ prefixed',
    'own-dir-flow': 'same, flow sequence',
    'own-dir-longform': 'same, long form',
    'parent-plane-own': "a parent that is not the repo root is a plane's own file",
    'value-at-key-indent': 'a bare SCALAR at the key indent is not the value; docker refuses the file',
    'next-line-scalar-own': "the plane's own .env, written on the next line",
    'bare-dash-item-own': "the plane's own .env, under a bare dash",
    'shallower-ends-extent': 'the shallower line ends the extent, as it must',
    'own-then-sibling-key': 'the extent must not swallow the rest of the service',
    'dash-at-key-indent-own': "the plane's own .env in the carve-out shape",
    'dash-then-sibling-key': 'a non-dash line at the key indent still ends the extent',
    'plane-own-scalar': "inference/.env is the plane's own file",
    'plane-own-flow': 'same, flow sequence',
    'plane-own-block': 'same, block sequence',
    'plane-own-longform': 'same, long form',
    'sibling-fragment-dir': "the fragment directory's own .env",
}


def run(args):
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


_, _head = run(['git', 'rev-parse', '--short', 'HEAD'])
HEAD_SHA = _head.strip()

scripts = {}
for sha, label in TIPS:
    dest = os.path.join(ROOT, '.reg-%s.ps1' % sha)
    code, out = run(['git', 'show', '%s:scripts/checks/check-env-file-scope.ps1' % sha])
    assert code == 0 and out.strip(), 'could not read the script at %s' % sha
    io.open(dest, 'w', encoding='ascii', newline='').write(out.replace('\r\n', '\n').replace('\n', '\r\n'))
    scripts[sha] = dest

base1 = io.open(TARGET, encoding='utf-8', newline='').read()
base2 = io.open(TARGET2, encoding='utf-8', newline='').read()
h1 = re.search(r'^  ao-ot-1:[ \t]*$', base1, re.M)
h2 = re.search(r'^  llama-cpp-upstream:[ \t]*$', base2, re.M)
svc1 = re.search(r'^services:[ \t]*$', base1, re.M)
assert h1 and h2 and svc1

results = []
for table, name, which, prelude, block, expect in ROWS:
    if which == 1:
        text = base1[:svc1.start()] + prelude + base1[svc1.start():]
        cut = h1.end() + 1 + len(prelude)
        text = text[:cut] + block + text[cut:]
        path, rel = TARGET, REL
    else:
        text = base2[:h2.end() + 1] + block + base2[h2.end() + 1:]
        path, rel = TARGET2, REL2
    io.open(path, 'w', encoding='utf-8', newline='').write(text)
    run(['git', 'add', '--', rel])
    row = {}
    for sha, label in TIPS:
        code, _ = run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', scripts[sha]])
        row[sha] = 'R' if code == 1 else 'G'
    io.open(path, 'w', encoding='utf-8', newline='').write(base1 if which == 1 else base2)
    run(['git', 'add', '--', rel])
    run(['git', 'reset', '-q'])
    results.append((table, name, expect, row))

NEW = TIPS[-1][0]
wrong_expect = [r for r in results if r[3][NEW] != r[2]]
greener = [(t, n, row) for t, n, e, row in results
           if row[NEW] == 'G' and any(row[s] == 'R' for s, _ in TIPS[:-1])]
undocumented = [g for g in greener if g[1] not in DELIBERATE_GREENS]

L = []
L.append('# sl-ao-envfile - regression matrix across every attempt tip')
L.append('')
L.append('Every matrix row is planted, staged, and run against the')
L.append('`check-env-file-scope.ps1` blob from EACH attempt tip and from the current one.')
L.append('`R` = the check refused it (exit 1), `G` = it passed.')
L.append('')
L.append('**The assertion is not "the current script is right".** It is *the current script')
L.append('sees everything its predecessors saw*: no row may be `G` at this tip where an')
L.append('earlier tip was `R`, unless it is a documented deliberate green. Five rounds each')
L.append('fixed the previous counter-example and shipped a new one, and four of those five')
L.append('defects are visible below as a lone `G` in a row of `R`s.')
L.append('')
L.append('Generated against the `check-env-file-scope.ps1` blob at `%s` for the last' % HEAD_SHA)
L.append('column. Re-run it if that blob changes; the other five columns are historical and')
L.append('do not move.')
L.append('')
L.append('| table | row | ' + ' | '.join('%s<br>`%s`' % (lbl, sha) for sha, lbl in TIPS) + ' | expected |')
L.append('|---|---|' + '---|' * (len(TIPS) + 1))
for table, name, expect, row in results:
    cells = ' | '.join(('**%s**' % row[s]) if row[s] == 'G' else row[s] for s, _ in TIPS)
    L.append('| T6a-%s | `%s` | %s | %s |' % (table, name, cells, expect))
L.append('')
L.append('## Verdict')
L.append('')
L.append('* rows **%d**, tips **%d**, cells **%d**' % (len(results), len(TIPS), len(results) * len(TIPS)))
L.append('* result at this tip differs from expected: **%d**%s'
         % (len(wrong_expect), '' if not wrong_expect else ' -> ' + ', '.join(r[1] for r in wrong_expect)))
L.append('* GREEN here, RED at an earlier tip: **%d**%s'
         % (len(greener), '' if not greener else ' -> ' + ', '.join(r[1] for r in greener)))
L.append('* of those, UNDOCUMENTED - i.e. a regression: **%d**%s'
         % (len(undocumented), '' if not undocumented else ' -> ' + ', '.join(r[1] for r in undocumented)))
L.append('')
if greener:
    L.append('Deliberate greens found in this run, each with the reason it is allowed:')
    L.append('')
    for table, name, row in greener:
        L.append('* `%s` - %s' % (name, DELIBERATE_GREENS.get(name, '**UNDOCUMENTED**')))
    L.append('')
L.append('## Rerunning it')
L.append('')
L.append('From a scratch clone at the tip under test:')
L.append('')
L.append('```bash')
L.append('python documentation/evidence/sl-ao-envfile/regression-matrix.py <clone-root> <out.md>')
L.append('```')
L.append('')
L.append('The generator lives beside this file. It stages plants into the clone and resets')
L.append('after each row, so run it in a SCRATCH clone, never in a worktree you care about.')

io.open(OUT, 'w', encoding='utf-8', newline='\n').write('\n'.join(L) + '\n')

for dest in scripts.values():
    os.remove(dest)

print('rows', len(results), 'cells', len(results) * len(TIPS))
print('wrong-expect:', [r[1] for r in wrong_expect])
print('greener-than-before:', [r[1] for r in greener])
print('UNDOCUMENTED greener:', [r[1] for r in undocumented])
sys.exit(1 if (wrong_expect or undocumented) else 0)
