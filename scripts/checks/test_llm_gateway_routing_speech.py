"""rw-p7a-tts round 2: the gateway-routing guard allows exactly ONE kokoro-tts-upstream reference - the
gateway's own speech route line in inference/config/litellm/model_list/local.tts.yaml - and nothing
else in that file (the tester planted an llama-cpp-upstream api_base there and it was not flagged).

Runs both twins (the .ps1 when PowerShell is on PATH, the .py always) against scratch roots.
    python -m pytest -q scripts/checks/test_llm_gateway_routing_speech.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FRAGMENT = os.path.join("inference", "config", "litellm", "model_list", "local.tts.yaml")
UP = "upstream"  # split, so this file never reads as a bypass itself


def _root(extra_lines, name="local.tts.yaml"):
    root = tempfile.mkdtemp(prefix="routing-speech-")
    dst = os.path.join(root, os.path.dirname(FRAGMENT))
    os.makedirs(dst)
    with open(os.path.join(REPO, FRAGMENT), encoding="utf-8") as f:
        text = f.read()
    with open(os.path.join(dst, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text + "".join(line + "\n" for line in extra_lines))
    return root


def _runs(root):
    out = [subprocess.run([sys.executable, os.path.join(HERE, "check_llm_gateway_routing.py"), "--root", root],
                          capture_output=True, text=True)]
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if ps:
        out.append(subprocess.run([ps, "-NoProfile", "-File", os.path.join(HERE, "check-llm-gateway-routing.ps1"), "-Root", root],
                                  capture_output=True, text=True))
    return out


def test_the_shipped_speech_route_line_is_allowed():
    root = _root([])
    try:
        for r in _runs(root):
            assert r.returncode == 0, r.stdout + r.stderr
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_any_other_upstream_line_in_the_speech_fragment_is_flagged():
    for planted in ([f"      api_base: http://llama-cpp-{UP}:8080/v1"],
                    [f"      api_base: http://kokoro-tts-{UP}:8880/v1/extra"],
                    [f"      api_base: http://llama-cpp-embed-{UP}:8080/v1"]):
        root = _root(planted)
        try:
            for r in _runs(root):
                assert r.returncode == 1, (planted, r.stdout + r.stderr)
                assert "local.tts.yaml" in r.stdout, r.stdout
        finally:
            shutil.rmtree(root, ignore_errors=True)


def test_the_speech_route_line_in_another_fragment_is_flagged():
    root = _root([], name="local.other.yaml")
    try:
        for r in _runs(root):
            assert r.returncode == 1, r.stdout + r.stderr
    finally:
        shutil.rmtree(root, ignore_errors=True)
