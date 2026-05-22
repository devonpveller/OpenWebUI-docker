"""Centralized typed config (design §12.8).

All tunables live in one YAML file, validated at boot. Unknown keys are
rejected (`extra="forbid"`) — prose-as-config does not survive contact with
operations. The pydantic model below is the single source of truth; the
committed `config/little-coder.schema.json` is generated from it (see
`python -m littlecoder.config --schema`) and a test guards against drift.

Tool-era tunables only. Later chapters add fields; readers tolerate older
shapes via `schema_version` (forward-compat, design §12.9).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

CONFIG_SCHEMA_VERSION = 1

# Default config path inside the container (mounted read-only from the repo).
DEFAULT_CONFIG_PATH = "/app/config/little-coder.config.yaml"


class _Strict(BaseModel):
    model_config = {"extra": "forbid"}


class InferenceConfig(_Strict):
    """llama-cpp backend (design §3.5). Two variants of the same model id."""

    base_url: str = "http://llama-cpp:8080/v1"
    api_key_env: str = "LC_LLAMA_API_KEY"
    model_reasoning: str = "qwen36-27b"  # judge, drafting, justifications
    model_fast: str = "qwen36-27b:nothink"  # cluster assignment, routing
    default: Literal["fast", "reasoning"] = "fast"


class AgentConfig(_Strict):
    """The upstream little-coder CLI invocation (design §3.1).

    INTEGRATION POINT: little-coder is a Node.js CLI on the `pi` framework;
    the exact flags and how the task prompt is delivered are pinned to the
    upstream version when the agent image is built. `command` is the base
    argv; the daemon appends `--model` and the prompt per `prompt_mode`."""

    command: list[str] = Field(default_factory=lambda: ["little-coder"])
    model: str = "llamacpp/qwen36-27b"
    prompt_mode: Literal["stdin", "arg"] = "stdin"
    extra_args: list[str] = Field(default_factory=list)


class WorkspaceConfig(_Strict):
    """Workspace plane (design §3.4). The repo lives on the shared volume."""

    path: str = "/workspace"
    open_terminal_url: str = "http://open-terminal:8000"
    open_terminal_key_env: str = "OPEN_TERMINAL_API_KEY"
    # Exec timeout for a single command sent into open-terminal.
    exec_timeout_seconds: int = 1800


class JournalsConfig(_Strict):
    """Append-only task journals (design §4)."""

    dir: str = "/var/lib/little-coder/journals"
    # Size-triggered rotation (design §4.3). 128 MiB per segment.
    rotation_max_bytes: int = 128 * 1024 * 1024
    # Append + fsync on every terminal and every error record (design §4.3).
    fsync_on_terminal: bool = True


class PathsConfig(_Strict):
    """Named-volume mount points. Declared in Tool, populated from Observer+."""

    skill_dir: str = "/var/lib/little-coder/skill"
    cohorts_dir: str = "/var/lib/little-coder/cohorts"
    polyglot_dir: str = "/var/lib/little-coder/polyglot"


class TasksConfig(_Strict):
    """Task lifecycle tunables (design §4.2). Timeouts are open item #3 —
    Tool defaults are usable; tune against observed channel p95."""

    # `task_abandoned` timeout per channel, seconds. Non-trivial: a long
    # interactive refactor must not be abandoned; a hung validation must not
    # consume a worker overnight.
    abandoned_timeout_seconds: dict[str, int] = Field(
        default_factory=lambda: {
            "owui": 21600,  # 6h
            "cli": 21600,  # 6h
            "validation": 1800,  # 30m
            "batch": 3600,  # 1h
        }
    )
    # Outcome-amendment window (design §4.2). 7 days; frozen outside.
    outcome_amend_window_seconds: int = 604800


class ShutdownConfig(_Strict):
    """SIGTERM drain mode (design §12.7)."""

    # Drain deadline default — shorter than the shortest channel p95.
    drain_deadline_seconds: int = 1800


class BudgetsConfig(_Strict):
    """Budget caps (design §12.5). Basic caps only in Tool."""

    queue_depth_soft: int = 50  # → operator alarm
    queue_depth_hard: int = 200  # → coalesce per cluster (from Learner)


class SanitizationConfig(_Strict):
    """Outbound sanitization filter (design §10.2). Shadow mode in Tool."""

    mode: Literal["shadow", "enforcing"] = "shadow"
    # File bodies larger than this are reduced to a structural digest.
    max_body_bytes: int = 8192


class MetricsConfig(_Strict):
    """Prometheus endpoint (design §9.3)."""

    enabled: bool = True
    port: int = 9090


class DaemonConfig(_Strict):
    """Control-daemon HTTP API (internal — reachable by lc-mcpo + the CLI)."""

    host: str = "0.0.0.0"
    port: int = 8090


class Config(_Strict):
    """Top-level config. Instantiating this validates the file (boot gate)."""

    schema_version: int = CONFIG_SCHEMA_VERSION
    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    journals: JournalsConfig = Field(default_factory=JournalsConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
    shutdown: ShutdownConfig = Field(default_factory=ShutdownConfig)
    budgets: BudgetsConfig = Field(default_factory=BudgetsConfig)
    sanitization: SanitizationConfig = Field(default_factory=SanitizationConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)


class ConfigError(RuntimeError):
    """Raised when the config file is missing, unparseable, or invalid."""


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load and validate the config file. Raises ConfigError on any problem —
    a bad config fails the boot, it does not fall back to defaults silently."""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config file must be a YAML mapping at the top level")

    version = data.get("schema_version", CONFIG_SCHEMA_VERSION)
    if version > CONFIG_SCHEMA_VERSION:
        # Forward-compat goes one way: a newer file cannot be safely read by
        # older code. Refuse rather than silently mis-parse.
        raise ConfigError(
            f"config schema_version {version} is newer than this build "
            f"supports ({CONFIG_SCHEMA_VERSION}); upgrade little-coder"
        )
    try:
        return Config.model_validate(data)
    except Exception as exc:  # pydantic ValidationError and friends
        raise ConfigError(f"config validation failed: {exc}") from exc


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="littlecoder.config")
    parser.add_argument(
        "--schema", action="store_true", help="print the JSON schema and exit"
    )
    parser.add_argument(
        "--check", metavar="PATH", help="validate a config file and exit"
    )
    args = parser.parse_args(argv)
    if args.schema:
        print(json.dumps(Config.model_json_schema(), indent=2, sort_keys=True))
        return 0
    if args.check:
        try:
            load_config(args.check)
        except ConfigError as exc:
            print(f"INVALID: {exc}", file=sys.stderr)
            return 1
        print("OK")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
