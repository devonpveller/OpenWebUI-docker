"""The pre-commit guard's decider. It contains NO rules of its own.

`check-judge-flag.ps1` used to answer "does this staged YAML turn
observer.judge_enabled on?" with a regex, while the daemon answered it with
`yaml.safe_load` + pydantic. Two parsers, two answers: a verifier turned the
flag on for the daemon with three ordinary YAML spellings and walked past the
grep, and a second did it with two quote characters. Fourteen such spellings
are pinned in scripts/checks/fixtures/judge-flag-corpus.json.

So this file is a TRANSPORT, not a parser. Every decision it reports comes
from `littlecoder.judge_gate` -- the same module the daemon's
`meta_wiring.build_meta_runner` calls at boot:

    enabled_in_yaml_text()   is this text turning the flag on?
    read_rating_record()     is this a valid human rating record?

If it cannot import that module it says so and exits 5, and the guard FAILS
CLOSED. A decider that answers from its own approximation when the real one is
unavailable is exactly the defect being removed.

It also reads the STAGED bytes itself (`git show :path`), rather than being
handed text by the caller: `git commit` commits the index, so a guard that
reads the working tree is defeated by `git add` followed by an edit-back, and
a guard handed re-encoded text is answering about a different file than the
one being committed.

Protocol: a JSON request on stdin, a JSON response on stdout.

  request  {"src":  "<path to little-coder/src>",
            "repo": "<path inside the git repository>",
            "paths": ["<repo-relative staged yaml>", ...],
            "rating_record_path": "<repo-relative>",
            "rating_record_staged": true|false,
            "tmp": "<directory for one scratch file>"}

  response {"ok": true,
            "candidates": [{"path", "where", "undecidable"}],
            "rating": {"present", "valid", "problem"}}

Exit 0 = decided (read `candidates`). Exit 5 = CANNOT TELL; the caller must
deny and say so in the audit record.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_CANNOT_TELL = 5


def _fail(message: str) -> int:
    json.dump({"ok": False, "error": message}, sys.stdout)
    sys.stdout.write("\n")
    return EXIT_CANNOT_TELL


def _staged_bytes(repo: str, rel: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", repo, "show", ":" + rel],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "git show :%s failed (%d): %s"
            % (rel, proc.returncode, proc.stderr.decode("utf-8", "replace").strip())
        )
    return proc.stdout


def main(argv: list[str]) -> int:
    try:
        # utf-8-sig, not utf-8: a caller that writes the request with a BOM
        # (PowerShell 5.1's `Set-Content -Encoding utf8` does) must not turn
        # into a cannot-tell that denies every commit staging YAML.
        request = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        return _fail("request is not JSON: %r" % (exc,))

    repo = request.get("repo") or "."
    src = request.get("src")
    if src:
        sys.path.insert(0, str(src))
    try:
        from littlecoder import judge_gate
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "littlecoder.judge_gate is not importable from %r: %r "
            "(the guard shares the daemon's decision; it does not have one of "
            "its own)" % (src, exc)
        )

    candidates = []
    for rel in request.get("paths") or []:
        try:
            text = _staged_bytes(repo, rel).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            return _fail("staged blob unreadable: %r" % (exc,))
        verdict = judge_gate.enabled_in_yaml_text(text)
        if verdict.enabled:
            candidates.append(
                {
                    "path": rel,
                    "where": verdict.where,
                    "undecidable": verdict.undecidable,
                }
            )

    rating = {"present": False, "valid": False, "problem": "no rating record staged"}
    rating_rel = request.get("rating_record_path")
    if rating_rel and request.get("rating_record_staged"):
        rating["present"] = True
        try:
            body = _staged_bytes(repo, rating_rel)
        except Exception as exc:  # noqa: BLE001
            return _fail("staged rating record unreadable: %r" % (exc,))
        tmp = Path(request.get("tmp") or ".") / "judge-rating-staged.yaml"
        try:
            tmp.write_bytes(body)
            record, problem = judge_gate.read_rating_record(tmp)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        rating["valid"] = record is not None
        rating["problem"] = problem

    json.dump(
        {
            "ok": True,
            "candidates": candidates,
            "rating": rating,
            "rating_record_repo_path": judge_gate.RATING_RECORD_REPO_PATH,
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
