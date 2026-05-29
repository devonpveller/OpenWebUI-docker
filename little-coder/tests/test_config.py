"""Centralized config — boot validation (design §12.8)."""

import textwrap

import pytest

from littlecoder.config import ConfigError, load_config


def _write(tmp_path, body: str):
    p = tmp_path / "cfg.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_minimal_config_applies_defaults(tmp_path):
    cfg = load_config(_write(tmp_path, "schema_version: 1\n"))
    assert cfg.sanitization.mode == "shadow"
    assert cfg.shutdown.drain_deadline_seconds == 1800
    assert cfg.tasks.abandoned_timeout_seconds["validation"] == 1800


def test_unknown_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, "schema_version: 1\nbogus_key: 7\n"))


def test_newer_schema_version_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="newer"):
        load_config(_write(tmp_path, "schema_version: 99\n"))


def test_missing_file_fails_boot(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "does-not-exist.yaml")


def test_bad_type_is_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(
            _write(tmp_path, "schema_version: 1\nmetrics:\n  port: not-a-number\n")
        )


def test_committed_config_is_valid():
    """The config file shipped in the repo must validate against the model."""
    from pathlib import Path

    repo_cfg = (
        Path(__file__).resolve().parent.parent
        / "config"
        / "little-coder.config.yaml"
    )
    cfg = load_config(repo_cfg)
    assert cfg.schema_version == 1


def test_committed_json_schema_matches_model():
    """The committed JSON schema must not drift from the pydantic model.
    Regenerate with: python -m littlecoder.config --schema > the file."""
    import json
    from pathlib import Path

    from littlecoder.config import Config

    schema_file = (
        Path(__file__).resolve().parent.parent
        / "config"
        / "little-coder.schema.json"
    )
    committed = json.loads(schema_file.read_text(encoding="utf-8"))
    assert committed == Config.model_json_schema(), (
        "little-coder.schema.json is stale — regenerate it"
    )
