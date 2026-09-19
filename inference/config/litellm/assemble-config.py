#!/usr/bin/env python3
"""Assemble the effective LiteLLM gateway config from a base file + model-list
fragments, registering ONLY models whose backend is actually present.

WHY THIS EXISTS (stack-layers D11, 2026-09-19)
----------------------------------------------
LiteLLM's YAML has no conditionals, but the gateway must be able to run
cloud-only: with the compose `local` profile off there is no llm-queue and no
`*-upstream`, so every local model would be a registered model with no backend
(a caller gets a connection error instead of "model not found"). Symmetrically,
a cloud provider whose API key is not set must not appear either.

So the gateway's entrypoint runs this script first. It reads:

  base config   $LITELLM_BASE_CONFIG      (default /app/config.base.yaml)
                general_settings + litellm_settings; NO model_list.
  fragments     $LITELLM_FRAGMENT_DIR     (default /app/conf.d)
                one *.yaml per model group, each a valid standalone LiteLLM
                config carrying only `model_list:` (+ the x- keys below).

and writes the merged result to

  effective     $LITELLM_EFFECTIVE_CONFIG (default /app/config.yaml)

which is the path the container's `command:` already points `--config` at, so
the compose command line is unchanged.

TWO ADMISSION RULES, both fail-CLOSED (a model is dropped, never invented):

  A. Fragment-level, by compose profile. A fragment may declare
         x-requires-profile: local
     and is skipped unless that profile appears in $COMPOSE_PROFILES. That is
     the SAME variable compose itself reads to decide whether to start the
     profiled services, so there is one source of truth rather than two.
     Consequence worth knowing: activating the profile with the `--profile
     local` CLI flag instead of COMPOSE_PROFILES does NOT set the variable
     inside the container, so the gateway under-registers (local backends up,
     not listed). That is the safe direction of the error - a listed model
     always has a backend - and it is why the compose header tells the
     operator to set COMPOSE_PROFILES in .env.

  B. Model-level, by credential. Any model entry whose litellm_params
     reference `os.environ/VAR` for a VAR that is unset or empty is dropped.
     This is what makes a cloud provider appear only when its key is set, and
     it generalises: a provider added later needs no change here.

Every decision is logged to stderr with a reason, so `docker logs llm-gateway`
answers "why is model X not listed?" without reading YAML.
"""
import os
import sys

import yaml

BASE = os.environ.get("LITELLM_BASE_CONFIG", "/app/config.base.yaml")
FRAGMENT_DIR = os.environ.get("LITELLM_FRAGMENT_DIR", "/app/conf.d")
OUT = os.environ.get("LITELLM_EFFECTIVE_CONFIG", "/app/config.yaml")

REQUIRES_PROFILE = "x-requires-profile"
ENV_PREFIX = "os.environ/"


def log(msg):
    sys.stderr.write("[assemble-config] %s\n" % msg)
    sys.stderr.flush()


def active_profiles():
    raw = os.environ.get("COMPOSE_PROFILES", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def missing_env_refs(node):
    """Every os.environ/VAR referenced under `node` whose VAR is unset/empty."""
    missing = []
    if isinstance(node, dict):
        for value in node.values():
            missing.extend(missing_env_refs(value))
    elif isinstance(node, list):
        for value in node:
            missing.extend(missing_env_refs(value))
    elif isinstance(node, str) and node.startswith(ENV_PREFIX):
        var = node[len(ENV_PREFIX):]
        if not os.environ.get(var):
            missing.append(var)
    return missing


def load_yaml(path):
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def main():
    profiles = active_profiles()
    log("COMPOSE_PROFILES=%s" % (",".join(sorted(profiles)) or "(none)"))

    config = load_yaml(BASE)
    if not isinstance(config, dict):
        log("FATAL: base config %s is not a mapping" % BASE)
        return 1
    if config.get("model_list"):
        log("NOTE: base config %s carries its own model_list; fragments are "
            "appended to it" % BASE)
    models = list(config.get("model_list") or [])

    if os.path.isdir(FRAGMENT_DIR):
        fragments = sorted(
            name for name in os.listdir(FRAGMENT_DIR)
            if name.endswith((".yaml", ".yml"))
        )
    else:
        fragments = []
        log("no fragment directory at %s" % FRAGMENT_DIR)

    for name in fragments:
        path = os.path.join(FRAGMENT_DIR, name)
        try:
            fragment = load_yaml(path)
        except yaml.YAMLError as exc:
            log("FATAL: %s is not valid YAML: %s" % (name, exc))
            return 1
        if not isinstance(fragment, dict):
            log("FATAL: %s is not a mapping" % name)
            return 1

        needed = fragment.get(REQUIRES_PROFILE)
        if needed and needed not in profiles:
            log("SKIP %s - needs compose profile '%s', active: %s"
                % (name, needed, ",".join(sorted(profiles)) or "(none)"))
            continue

        kept = 0
        for entry in fragment.get("model_list") or []:
            model_name = entry.get("model_name", "<unnamed>")
            missing = sorted(set(missing_env_refs(entry.get("litellm_params"))))
            if missing:
                log("DROP %s (%s) - env not set: %s"
                    % (model_name, name, ", ".join(missing)))
                continue
            models.append(entry)
            kept += 1
        log("LOAD %s - %d model(s) registered" % (name, kept))

    config["model_list"] = models
    with open(OUT, "w", encoding="utf-8") as handle:
        handle.write(
            "# GENERATED by assemble-config.py - do not edit, do not commit.\n"
            "# Sources: %s + %s\n" % (BASE, FRAGMENT_DIR)
        )
        yaml.safe_dump(config, handle, sort_keys=False, default_flow_style=False)
    log("wrote %s with %d model(s): %s"
        % (OUT, len(models),
           ", ".join(m.get("model_name", "?") for m in models) or "(none)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
