# Worker environment templates (hot-swappable dev environments)

The agent-org execution plane is the **open-terminal sidecar** (`ao-ot-N`) — every worker command
(builds, tests, `check_cmd`) runs there, against the shared `/workspace` volume. So "give the
worker an authentic dev environment" means: give its **ao-ot container** the project's toolchain.

**The template pattern (operator decision 2026-07-05):** project toolchains are **image layers on
top of the security base**, prebuilt once and **hot-swapped per worker pair** — never rebuilt on a
project switch.

```
ghcr.io/open-webui/open-terminal:slim
  └─ little-coder-open-terminal:local       ← Dockerfile.open-terminal (git-proxy splice, SECURITY BASE)
       ├─ little-coder-open-terminal:dotnet8   ← envs/dotnet8.Dockerfile (.NET 8 SDK)
       │    └─ little-coder-open-terminal:dotnet8-gui-mgfx ← envs/dotnet8-gui-mgfx.Dockerfile
       │         (2026-07-09: Wine + mgfxc for MonoGame SHADER compilation. NOTE: built, but the
       │          Windows-dotnet-under-Wine muxer is fragile/failing on this base — mgfxc can't
       │          reliably compile here. LESSON: MonoGame shader compilation is effectively a
       │          Windows-HOST capability. The working pattern is HOST-PRODUCES-ARTIFACT +
       │          WORKER-VERIFIES-CONSUMPTION: compile the .fx→MGFX .fxb on the host (mgfxc runs
       │          native there), commit them, and let the worker VERIFY the load path — the
       │          monogame-engine check asserts every packed .fxb starts with the `MGFX` magic,
       │          which reproduces the "not a MonoGame MGFX file" crash with no Wine/GL. So the
       │          live env stays dotnet8-gui; this layer is kept for reference/experiments.)
       │    └─ little-coder-open-terminal:dotnet8-gui ← envs/dotnet8-gui.Dockerfile (2026-07-09:
       │         headless GUI runtime — Xvfb + Mesa software GL + SDL2/OpenAL, so a MonoGame
       │         DesktopGL app can LAUNCH for runtime verification: `xvfb-run -a timeout 25
       │         dotnet run` → exit 124 = still running = launched. Templates may layer on
       │         other templates when they EXTEND a toolchain.)
       └─ little-coder-open-terminal:<env>     ← envs/<env>.Dockerfile (add yours here)
```

- Every template MUST build `FROM little-coder-open-terminal:local` so the git-proxy splice,
  hook fencing, and pager fixes are inherited — a template adds TOOLCHAIN, never touches policy.
- Build once: `docker build -f docker/envs/dotnet8.Dockerfile -t little-coder-open-terminal:dotnet8 .`
  (context `./little-coder`, same as the base).
- Hot-swap per worker pair via compose vars (defaults keep the plain base):
  `AO_OT1_IMAGE` / `AO_OT2_IMAGE` in the stack `.env` →
  `docker compose -f agent-org/docker/docker-compose.yml up -d ao-ot-1 ao-ot-2`.
  The `/workspace` volume persists across the swap; a recreate costs seconds, not a rebuild.
- **Package-registry egress:** ao-ot traffic goes through `ao-git-egress` (allowlist proxy). A
  toolchain's package hosts must be allowed or restores fail with a proxy 403 — say it to bot-pm
  in plain language (e.g. _"let the workers reach api.nuget.org"_). Registry hosts per template
  are listed in the template header.
- After an env goes live, set the project check so D2 red-gates merges on a REAL build, e.g.
  `/project check monogame-engine "git submodule update --init --recursive && dotnet build vendor/murder/Murder.sln"`.

Follow-up (designed, not built): a per-project `env` field in the bridge registry + env-aware
worker acquire, so a heterogeneous pool routes each effort to a worker whose sidecar matches the
project's declared environment.
