"""ao-thinkon: the image carries NO client-side thinking cap and no extension that reads thinking.

Upstream little-coder's bundled `thinking-budget` extension aborts a thought at ~14.3k characters
(4,096 tokens x 3.5) and re-asks with the thought discarded. The operator's decision (after ao-think
stopped): remove it, add no client steer; the server's reasoning budget is the only bound. The build
step `docker/remove-thinking-cap.mjs` parks upstream's extension and fails the build if anything left
in the discovered set (or our own pi-extension/) reads thinking. These tests run that script under
node against fake package trees; no docker, no network, no model. The real-pi evidence (the built
image's launcher + pi against a stub and a real llama.cpp) is `tests/thinking_cap_e2e.mjs`, run by
the test plan.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

LC = Path(__file__).resolve().parent.parent
SCRIPT = LC / "docker" / "remove-thinking-cap.mjs"
DOCKERFILE = LC / "docker" / "Dockerfile.agent"
OURS = LC / "pi-extension"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not on PATH")

UPSTREAM_LIKE = ('pi.on("message_update", (event, ctx) => { const ev = event.assistantMessageEvent; '
                 'if (ev.type !== "thinking_delta") return; '
                 'pi.sendUserMessage("[thinking budget exceeded] Please commit"); ctx.abort(); });\n')


def _tree(tmp: Path, upstream: str | None, extra: dict[str, str] | None = None) -> Path:
    root = tmp / "npm"
    ext = root / "little-coder" / ".pi" / "extensions"
    (ext / "read-guard").mkdir(parents=True)
    (ext / "read-guard" / "index.ts").write_text("export default function () {}\n", encoding="utf-8")
    (ext / "_shared").mkdir()
    (ext / "_shared" / "intervention.ts").write_text("// mentions the thinking-budget cap in prose\n",
                                                     encoding="utf-8")
    if upstream is not None:
        (ext / "thinking-budget").mkdir()
        (ext / "thinking-budget" / "index.ts").write_text(upstream, encoding="utf-8")
        (ext / "thinking-budget" / "budget.test.ts").write_text('"thinking_delta"\n', encoding="utf-8")
    for rel, text in (extra or {}).items():
        p = ext / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _run(root: Path, park: Path, *scan: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, REMOVE_THINKING_CAP_NPM_ROOT=str(root))
    return subprocess.run([NODE, str(SCRIPT), str(park), *map(str, scan)], capture_output=True,
                          text=True, env=env, timeout=60)


@needs_node
def test_the_cap_is_parked_and_never_discovered(tmp_path):
    root = _tree(tmp_path, UPSTREAM_LIKE)
    r = _run(root, tmp_path / "park", OURS)
    assert r.returncode == 0, r.stderr
    ext = root / "little-coder" / ".pi" / "extensions"
    assert not (ext / "thinking-budget").exists()
    assert (tmp_path / "park" / "thinking-budget" / "index.ts").read_text(encoding="utf-8") == UPSTREAM_LIKE
    # the launcher's discovery rule (bin/little-coder.mjs step 4): <ext>/<name>/index.ts
    discovered = sorted(p.parent.name for p in ext.glob("*/index.ts"))
    assert discovered == ["read-guard"]
    assert "discovered set (1: read-guard)" in r.stdout


@needs_node
@pytest.mark.parametrize("case", ["missing", "changed"])
def test_an_upstream_change_to_the_cap_fails_the_build(tmp_path, case):
    root = _tree(tmp_path, None if case == "missing" else "export default function () { /* rewritten */ }\n")
    r = _run(root, tmp_path / "park")
    assert r.returncode == 1 and "failing the build" in r.stderr


@needs_node
@pytest.mark.parametrize("reader", [
    'if (e.assistantMessageEvent.type === "thinking_delta") count();',
    'pi.on("message_update", (e) => { if (e.assistantMessageEvent.type == "thinking_start") t0(); });',
    'const t = msg.content.filter((c) => c.type === "thinking");',
    "const n = block.thinking.length;",
    "const r = chunk.choices[0].delta.reasoning_content;",
    'pi.sendUserMessage("[Thinking Budget Exceeded] stop");',
])
def test_any_other_thinking_reader_fails_the_build(tmp_path, reader):
    """No extension may read or abort on thinking: a second guard (any spelling we know) anywhere in
    the discovered set - including a nested helper file - fails the build."""
    root = _tree(tmp_path, UPSTREAM_LIKE, {"other-guard/lib/helper.ts": reader + "\n",
                                           "other-guard/index.ts": "export default function () {}\n"})
    r = _run(root, tmp_path / "park")
    assert r.returncode == 1 and "failing the build" in r.stderr
    assert "other-guard/lib/helper.ts" in r.stderr


@needs_node
def test_a_thinking_reader_among_our_own_extensions_fails_the_build(tmp_path):
    root = _tree(tmp_path, UPSTREAM_LIKE)
    ours = tmp_path / "ours" / "thinking-steer"
    ours.mkdir(parents=True)
    (ours / "index.ts").write_text('if (ev.type !== "thinking_delta") return;\n', encoding="utf-8")
    r = _run(root, tmp_path / "park", tmp_path / "ours")
    assert r.returncode == 1 and "thinking-steer/index.ts" in r.stderr


@needs_node
def test_test_files_and_prose_mentions_do_not_fail_the_build(tmp_path):
    """The scan must not be so wide that it bites upstream's own tests or a comment naming the cap
    (both exist in 1.20.0: thinking-budget/budget.test.ts, _shared/intervention.ts)."""
    root = _tree(tmp_path, UPSTREAM_LIKE, {"quality-monitor/index.ts":
                                           "// a harness abort (thinking-budget, turn-cap) is not a failure\n"
                                           "export default function () {}\n",
                                           "quality-monitor/quality.test.ts": '"thinking_delta"\n'})
    r = _run(root, tmp_path / "park")
    assert r.returncode == 0, r.stderr


def test_our_pi_extensions_read_no_thinking():
    """The repo side of the seam, without node: nothing under pi-extension/ reads thinking."""
    import re
    m = re.search(r"^export const THINKING_READER = /(.+)/;$", SCRIPT.read_text(encoding="utf-8"), re.M)
    assert m, "THINKING_READER not found in the build script"
    reader = re.compile(m.group(1))                   # the script's own pattern (same syntax in both)
    assert reader.search('x.type === "thinking_delta"') and not reader.search("thinking-budget cap")
    hits = [str(p.relative_to(OURS)) for p in OURS.rglob("*") if p.is_file()
            and p.suffix in {".ts", ".mts", ".js", ".mjs"} and reader.search(p.read_text(encoding="utf-8"))]
    assert hits == []
    assert not (OURS / "thinking-steer").exists()


def test_dockerfile_removes_the_cap_after_install_patches_and_our_extensions_as_root():
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    install = next(i for i, ln in enumerate(lines) if "npm install -g" in ln and "little-coder@" in ln)
    patches = next(i for i, ln in enumerate(lines) if ln.startswith("RUN node /tmp/apply-pi-patches.mjs"))
    ours = next(i for i, ln in enumerate(lines) if ln.startswith("COPY pi-extension/ /opt/little-coder/pi-extensions/"))
    step = next(i for i, ln in enumerate(lines) if ln.startswith("RUN node /tmp/remove-thinking-cap.mjs"))
    assert install < patches < ours < step
    assert "/opt/little-coder/pi-extensions" in lines[step]           # our extensions are scanned too
    users = [i for i, ln in enumerate(lines) if ln.startswith("USER ")]
    assert not users or min(users) > step
    text = "\n".join(lines)
    assert "thinking-steer" not in text and "LC_THINKING_STEER" not in text
