#!/usr/bin/env python3
"""
LLM Traffic Module — per-caller GPU-demand attribution from the LiteLLM gateway.

Manifest-driven module (mirrors modules/gpu-status, modules/system-health).
Answers "status of litellm / the queue / who is using the GPU" with the LIVE
QUEUE BOARD: what request is running per model (caller key + elapsed + est
remaining), who is waiting (position + est start + est completion), free
permits, held connections.

As-deployed reality (J.1 master-key era, 2026-08-22 rework):
  - The gateway runs on the internal-only `llm-net`; reachable from the OWUI
    container at http://llm-gateway:8080 (host :4000 is inert — internal net).
  - The read-only /observe/* queue pass-through accepts any VIRTUAL key; this
    module authenticates with OWUI's own low-privilege key
    (OWUI_CHAT_LLM_API_KEY env, passed by frontend/docker-compose.yml).
  - `GET /spend/logs` is ADMIN-ONLY under the master key, which deliberately
    never enters this container (A.2 hardening). The per-caller history table
    therefore degrades to a pointer at the host-side `stack.ps1 stats` view
    unless an operator ever provides LITELLM_KEY_ADMIN here.
"""

import json
import logging
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    return logging.getLogger("llm_traffic_module")


logger = setup_logging()

# Gateway base URL — in-network only (llm-net is internal). Overridable for tests.
GATEWAY_URL = os.environ.get("LLM_GATEWAY_URL", "http://llm-gateway:8080").rstrip("/")
# Optional admin/master key — ONLY for the /spend ledger; deliberately absent
# in this container (A.2: no admin secrets in the internet-facing frontend).
ADMIN_KEY = os.environ.get("LITELLM_KEY_ADMIN") or os.environ.get("LITELLM_MASTER_KEY") or ""
# OWUI's own low-privilege virtual key — enough for /health/liveliness and the
# read-only /observe/* queue board (required since the J.1 master-key flip).
VIRTUAL_KEY = os.environ.get("OWUI_CHAT_LLM_API_KEY") or ADMIN_KEY

# Permissive-mode junk/empty key string → friendly caller name. These are the
# literal keys each caller sends today (verified in the spend ledger). Update
# only if a caller's key env changes; once real virtual keys exist this map is
# bypassed (the alias is already human-readable).
KEY_FRIENDLY_NAMES = {
    "ollama": "openwebui (chat + embed)",
    "not-needed": "openbrain (research / mcp / wiki / entity)",
    "llama": "little-coder",
    "mnemory": "mnemory",
    "": "unkeyed / anonymous",
    "no-key": "unkeyed / anonymous",
    "sk-admin": "admin / ad-hoc",
}


def _friendly(api_key: str) -> str:
    if api_key in KEY_FRIENDLY_NAMES:
        label = KEY_FRIENDLY_NAMES[api_key]
        return f"{label}  (`{api_key or 'none'}`)"
    return f"`{api_key}`"


def _parse_window(text: str) -> (str, datetime):
    """Map a free-text qualifier to (label, cutoff_datetime_utc). Default 1h."""
    now = datetime.now(timezone.utc)
    t = (text or "").lower()
    if "since boot" in t or "all" in t or "everything" in t:
        return ("since boot (capped ~7d)", now - timedelta(days=7))
    if "week" in t or "7d" in t or "7 d" in t:
        return ("last 7 days", now - timedelta(days=7))
    if "today" in t:
        return ("today", now.replace(hour=0, minute=0, second=0, microsecond=0))
    if "24h" in t or "24 h" in t or "day" in t:
        return ("last 24h", now - timedelta(hours=24))
    if "hour" in t or "1h" in t or "60m" in t:
        return ("last 1h", now - timedelta(hours=1))
    return ("last 1h (default)", now - timedelta(hours=1))


def _http_get_json(url: str, timeout: float = 8.0, key: str = "") -> Any:
    headers = {"User-Agent": "ai-stack-llm-traffic/1"}
    bearer = key or VIRTUAL_KEY
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


class LLMTrafficModule:
    def __init__(self):
        self.module_id = "llm-traffic"
        self.version = "1.0.0"

    # ------------------------------------------------------------------ probes
    def _gateway_liveness(self) -> Dict[str, Any]:
        try:
            _http_get_json(f"{GATEWAY_URL}/health/liveliness", timeout=4.0)
            return {"reachable": True, "healthy": True}
        except urllib.error.HTTPError as exc:
            return {"reachable": True, "healthy": False, "http_code": exc.code}
        except Exception as exc:
            return {"reachable": False, "healthy": False, "error": str(getattr(exc, "reason", exc))[:120]}

    def _fetch_logs(self, cutoff: datetime) -> List[Dict[str, Any]]:
        """Pull /spend/logs and filter client-side by cutoff.

        The server-side start_date/end_date params behave inconsistently across
        LiteLLM builds (some return [] even when rows exist), so the plain
        endpoint is authoritative and the date-narrowed call is only an
        optimization that we discard if it comes back empty."""
        rows: List[Dict[str, Any]] = []
        start_date = cutoff.strftime("%Y-%m-%d")
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            narrowed = _http_get_json(
                f"{GATEWAY_URL}/spend/logs?start_date={start_date}&end_date={end_date}", key=ADMIN_KEY
            )
            if isinstance(narrowed, list) and narrowed:
                rows = narrowed
        except Exception:
            rows = []
        if not rows:
            # Authoritative: the unfiltered ledger (this build returns recent rows).
            plain = _http_get_json(f"{GATEWAY_URL}/spend/logs", key=ADMIN_KEY)
            rows = plain if isinstance(plain, list) else []
        out = []
        for r in rows:
            ts = _parse_ts(r.get("startTime") or r.get("endTime") or "")
            if ts is None or ts >= cutoff:
                out.append(r)
        return out

    def _fetch_queue_live(self) -> Optional[Dict[str, Any]]:
        """B2 live board (design §9): the llm-queue admission controller's
        real-time {running, waiting, avg_T, depth} state, surfaced to llm-net via
        the gateway's read-only /observe/* pass-through. Returns None (board
        skipped) if the queue isn't fronted/reachable — degrades gracefully."""
        try:
            board = _http_get_json(f"{GATEWAY_URL}/observe/queue", timeout=4.0)
            stats = _http_get_json(f"{GATEWAY_URL}/observe/queue/stats", timeout=4.0)
            return {"board": board, "stats": stats}
        except Exception:
            return None

    def _render_live_board(self, live_queue: Optional[Dict[str, Any]]) -> List[str]:
        """The live queue board — what's running/waiting RIGHT NOW (complements
        the historical ledger table below it)."""
        if not live_queue:
            return []
        board = live_queue.get("board") or {}
        models = board.get("models") or {}
        if not models:
            return []
        lines = [
            "### Live queue (llm-queue admission controller)",
            "",
            "| Model | Running | Waiting | Free slots | Avg T (s) | P | In-flight by key |",
            "|---|--:|--:|--:|--:|--:|---|",
        ]
        for name, m in models.items():
            running = len(m.get("running") or [])
            waiting = len(m.get("waiting") or [])
            ikey = m.get("inflight_by_key") or {}
            ikey_s = ", ".join(f"{_friendly(k).split('  ')[0]}×{v}" for k, v in ikey.items()) or "—"
            lines.append(
                f"| `{name}` | {running} | {waiting} | {m.get('permits_free', '?')} | "
                f"{m.get('avg_T_s', '?')} | {m.get('P', '?')} | {ikey_s} |"
            )
        # RUNNING detail: caller key, elapsed, estimated remaining (avg_T bound).
        runners = []
        for name, m in models.items():
            avg_t = float(m.get("avg_T_s") or 0)
            for r in (m.get("running") or []):
                elapsed = float(r.get("elapsed_s") or 0)
                remaining = max(0, round(avg_t - elapsed))
                runners.append(
                    f"`{r.get('key', '?')}` on `{name}` — {round(elapsed)}s elapsed, ~{remaining}s remaining (avg {round(avg_t)}s)")
        if runners:
            lines += ["", "**Running now:**"] + [f"- {x}" for x in runners]

        # WAITING queue: position, caller key, est start + est completion.
        waiters = []
        for name, m in models.items():
            avg_t = float(m.get("avg_T_s") or 0)
            for i, w in enumerate(m.get("waiting") or [], 1):
                est_wait = float(w.get("est_wait_s") or 0)
                waiters.append((i, name, w, est_wait, avg_t))
        if waiters:
            lines += ["", "**In queue (next first):**"]
            for i, name, w, est_wait, avg_t in waiters[:8]:
                done = round(est_wait + avg_t)
                lines.append(
                    f"- #{i} `{w.get('key', '?')}` on `{name}` — waited {round(float(w.get('waited_s') or 0))}s, "
                    f"est start in ~{round(est_wait)}s, est completion ~{done}s (prio {w.get('prio', '?')})")
            extra = len(waiters) - 8
            if extra > 0:
                lines.append(f"- … +{extra} more waiting")
        if not runners and not waiters:
            lines += ["", "_Queue idle — nothing running or waiting._"]
        held = board.get("held_total")
        cap = board.get("max_total_connections")
        if held is not None:
            lines.append(f"\n_Held connections: {held}/{cap}._")
        lines.append("")
        return lines

    # ------------------------------------------------------------- aggregation
    def _aggregate(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        agg: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            key = r.get("api_key", "")
            a = agg.setdefault(key, {
                "api_key": key, "requests": 0, "prompt_tokens": 0,
                "completion_tokens": 0, "total_tokens": 0, "failures": 0,
                "duration_ms_sum": 0.0, "duration_n": 0, "models": set(),
                "last_seen": None,
            })
            a["requests"] += 1
            a["prompt_tokens"] += int(r.get("prompt_tokens") or 0)
            a["completion_tokens"] += int(r.get("completion_tokens") or 0)
            a["total_tokens"] += int(r.get("total_tokens") or 0)
            meta = r.get("metadata") or {}
            if str(meta.get("status", "")).lower() == "failure":
                a["failures"] += 1
            dur = r.get("request_duration_ms")
            if isinstance(dur, (int, float)) and dur > 0:
                a["duration_ms_sum"] += float(dur)
                a["duration_n"] += 1
            mg = r.get("model_group") or r.get("model") or ""
            if mg:
                a["models"].add(mg)
            ts = _parse_ts(r.get("startTime") or "")
            if ts and (a["last_seen"] is None or ts > a["last_seen"]):
                a["last_seen"] = ts
        result = []
        for a in agg.values():
            a["avg_latency_ms"] = round(a["duration_ms_sum"] / a["duration_n"]) if a["duration_n"] else 0
            a["models"] = sorted(a["models"])
            a["last_seen"] = a["last_seen"].isoformat() if a["last_seen"] else None
            result.append(a)
        result.sort(key=lambda x: x["requests"], reverse=True)
        return result

    # --------------------------------------------------------------- rendering
    def _render(self, window_label: str, live: Dict[str, Any], agg: List[Dict[str, Any]], raw_count: int, live_queue: Optional[Dict[str, Any]] = None) -> str:
        head = "🟢 healthy" if live.get("healthy") else ("🟡 reachable" if live.get("reachable") else "🔴 UNREACHABLE")
        lines = [
            "## LLM Traffic — GPU demand attribution",
            "",
            f"**Gateway:** `{GATEWAY_URL}` — {head}  ·  **Window:** {window_label}  ·  **Requests:** {raw_count}",
            "",
        ]
        # B2 live board first (real-time), then the historical ledger table.
        lines += self._render_live_board(live_queue)
        if live_queue:
            lines += ["### Historical demand (ledger)", ""]
        if not live.get("reachable"):
            lines += [
                f"> Could not reach the LiteLLM gateway: {live.get('error', 'unknown')}",
                "> The module runs from the OWUI container over `llm-net`. Check the gateway is up:",
                "> `docker compose exec llm-gateway python -c \"import urllib.request;print(urllib.request.urlopen('http://localhost:8080/health/liveliness').status)\"`",
            ]
            return "\n".join(lines)
        if not agg:
            if live.get("log_error") and "401" in str(live.get("log_error")):
                lines.append(
                    "_Per-caller history needs the ADMIN ledger, which never enters this "
                    "container (J.1 master-key posture). Use the host view instead: "
                    "`.\\scripts\\stack\\stack.ps1 stats`._")
            else:
                lines.append("_No requests logged in this window._ (Widen it: append `today`, `last 24h`, `last week`, or `since boot`.)")
            return "\n".join(lines)
        lines += [
            "| Caller (presented key) | Reqs | Tok in | Tok out | Total tok | Fails | Avg ms | Models | Last seen (UTC) |",
            "|---|--:|--:|--:|--:|--:|--:|---|---|",
        ]
        for a in agg:
            last = (a["last_seen"] or "")[:19].replace("T", " ")
            models = ", ".join(m.split("/")[-1] for m in a["models"]) or "—"
            lines.append(
                f"| {_friendly(a['api_key'])} | {a['requests']} | {a['prompt_tokens']} | "
                f"{a['completion_tokens']} | {a['total_tokens']} | {a['failures']} | "
                f"{a['avg_latency_ms']} | {models} | {last} |"
            )
        lines += [
            "",
            "_Per-caller virtual keys since J.1 (2026-08-21): the key alias IS the "
            "caller. `Fails` counts ledger rows with a failure status._",
        ]
        return "\n".join(lines)

    # ----------------------------------------------------------------- execute
    def execute(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        request_id = request_data.get("request_id", "unknown")
        try:
            input_data = request_data.get("input", "")
            user_input = input_data.get("query", "") if isinstance(input_data, dict) else str(input_data)
            window_label, cutoff = _parse_window(user_input)

            live = self._gateway_liveness()
            rows: List[Dict[str, Any]] = []
            if live.get("reachable"):
                try:
                    rows = self._fetch_logs(cutoff)
                except Exception as exc:
                    logger.error(f"spend/logs fetch failed: {exc}")
                    live["log_error"] = str(exc)[:120]
            agg = self._aggregate(rows)
            live_queue = self._fetch_queue_live()
            content = self._render(window_label, live, agg, len(rows), live_queue)

            return {
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "ok" if live.get("reachable") else "error",
                "content": content,
                "structured_data": {
                    "window": window_label,
                    "gateway": GATEWAY_URL,
                    "liveness": live,
                    "callers": agg,
                    "request_count": len(rows),
                    "live_queue": (live_queue or {}).get("board"),
                },
                "diagnostics": {"execution_time_ms": int((time.time() - start) * 1000)},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.error(f"❌ llm-traffic execution error: {e}")
            return {
                "request_id": request_id,
                "module_id": self.module_id,
                "status": "error",
                "content": f"❌ **LLM Traffic Error**: {str(e)}",
                "error": {"code": "EXECUTION_ERROR", "message": str(e), "retriable": True},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    # ----------------------------------------------------------- module contract
    def describe(self) -> Dict[str, Any]:
        return {
            "module_id": self.module_id,
            "version": self.version,
            "description": "Per-caller LLM/GPU demand attribution from the LiteLLM spend ledger.",
            "triggers": ["llm traffic", "who is using gpu", "llm demand", "llm spend", "gateway traffic"],
        }

    def health(self) -> Dict[str, Any]:
        return {"module_id": self.module_id, "status": "ok", "gateway": self._gateway_liveness()}

    def validate(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        if "input" not in input_data:
            return {"valid": False, "errors": ["missing 'input'"]}
        return {"valid": True, "errors": []}


llm_traffic_module = LLMTrafficModule()


def main(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for the module."""
    return llm_traffic_module.execute(input_data)


def describe() -> Dict[str, Any]:
    return llm_traffic_module.describe()


def health() -> Dict[str, Any]:
    return llm_traffic_module.health()


def validate(input_data: Dict[str, Any]) -> Dict[str, Any]:
    return llm_traffic_module.validate(input_data)


if __name__ == "__main__":
    from pathlib import Path as _Path
    import sys as _sys
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
    from utilities.module_cli import run_module_cli
    run_module_cli(main, describe, health)
