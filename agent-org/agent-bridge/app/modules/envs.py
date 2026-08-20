"""Worker environment templates — the bridge-side manifest (operator principle 2026-07-06:
project fixes become ORCHESTRATION abstractions; nothing project-specific typed by hand).

The execution sidecars (ao-ot) run toolchain template images (little-coder/docker/envs/). Each
template's package registries live HERE, so activating a template (AO_OT*_IMAGE, the operator's
compose var = the clearance) automatically widens the default-deny worker egress to exactly the
registries that toolchain needs — no NL step, no manual allow. Add an entry when you add a
template Dockerfile (the envs/README.md checklist)."""

from __future__ import annotations

# template tag (the part after ':' in the sidecar image) → package-registry hosts it needs
ENV_HOSTS: dict[str, list[str]] = {
    "dotnet8": ["api.nuget.org", "www.nuget.org", "nuget.org"],
    # "node20": ["registry.npmjs.org"],
    # "rust":   ["crates.io", "static.crates.io", "index.crates.io"],
}


def hosts_for_images(*images: str) -> set[str]:
    """Registry hosts needed by the ACTIVE sidecar template images. Unknown/absent tags
    contribute nothing (the plain security base needs no package registries)."""
    hosts: set[str] = set()
    for img in images:
        tag = (img or "").rsplit(":", 1)[-1] if ":" in (img or "") else ""
        hosts.update(ENV_HOSTS.get(tag, []))
    return hosts
