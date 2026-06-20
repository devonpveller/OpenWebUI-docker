# little-coder update notes

How to bump the upstream little-coder version without wiping out the local
extensions, and what to watch for. Append a dated section per bump.

## How our extensions relate to upstream

Upstream `little-coder` is an npm package (a Node CLI on the `pi` framework);
`Dockerfile.agent` installs it fresh on every build (`npm install -g
little-coder@${LITTLE_CODER_VERSION}`). **None of our work lives inside that
package** — it is all git-tracked and layered on top at build/entrypoint time:

| Extension | File | Applied by |
|---|---|---|
| Python control-plane wrapper | `src/littlecoder/` | `pip install .` |
| `bash` → open-terminal routing | `pi-extension/open-terminal-exec/index.ts` | entrypoint copies into `.pi/extensions/` |
| git-proxy safety gate | `git-proxy/git_proxy.py` | baked into the **open-terminal** image |
| llama-swap model override | `config/models.json` | entrypoint copies to `~/.config/little-coder/` |
| Founding knowledge | `agent-knowledge/` | `--append-system-prompt` (config) |
| CLI flags / session policy | `config/little-coder.config.yaml` + `agent.py` | daemon builds argv |

So a version bump can never **delete** our work. The real risk is **silent API
drift**: the new upstream changes a contract an overlay depends on, and several
overlays fail silently. The compatibility matrix below is how we check that.

## Core architectural invariant (do not regress)

**little-coder (control plane) decides; open-terminal (workspace plane) executes
and egresses.** Every tool that runs a command or reaches the network must do so
*inside* open-terminal (behind the git-proxy + `lc-egress` allowlist + network
isolation), never in the little-coder container. The `bash` override
(`bash → ot-exec → open-terminal /execute`) is the seam that enforces this.

Rule for new upstream extensions: **keep one only if its execution/egress can
live in open-terminal.** An extension that executes or fetches *in-process*
(in the little-coder Node process) does not qualify until it is routed.

---

## 2026-06-20 — 1.4.3 → 1.9.7

Bumped from the installed `1.4.3` to npm `latest` (`1.9.7`) — 5 minor versions.
Verified against a throwaway build of 1.9.7 before touching the live container.

### Compatibility matrix (verified)

| Surface | Status | Note |
|---|---|---|
| `registerTool` API + `{content,details,isError}` return | OK | identical to bundled `extra-tools` |
| `.pi/extensions/` dir path | OK | unchanged |
| `shell-session` / `browser` extension names (our `rm` targets) | OK | still exist → `rm` still hits them |
| CLI flags `--print --mode json --append-system-prompt --model --session --session-dir --no-session` | OK | all present in `--help` |
| `--append-system-prompt <file>` reads a path | OK | help: "text **or file contents**" |
| `LITTLE_CODER_PERMISSION_MODE=accept-all` | OK | now a `permission-gate` ext; `accept-all` still short-circuits |
| `models.json` override (schema + `~/.config/little-coder/` path + `LLAMACPP_BASE_URL`) | OK | new `llama-cpp-provider` validator: `id` is the only hard requirement; our schema matches the shipped default |
| Node engine `>=22.19.0` | OK | NodeSource `setup_22.x` installs current 22.x |
| `bash` override **shadows** built-in bash | **SMOKE-TEST** | cannot be proven statically — see gate below |

### New 1.9.7 extensions vs. the invariant

- `subagent` (`dispatch`): spawns child coders. Child `bash` re-wires our
  override → routes to open-terminal (OK). Child browse (`webfetch`/`websearch`/
  `browser`) is in-process → fails closed in our topology (no internet from the
  control plane; no chromium). **Kept** — its exec honors the invariant; the
  dead browse sub-capability is harmless.
- `browser` (`Browser*`, playwright): launches chromium **in-process**
  (`playwright.chromium.launch()`); **no `connect`/CDP** support, and the
  open-terminal base is python-only (no node/playwright/chromium). Cannot honor
  the invariant as shipped → **excluded** (Path A). Routing the browser into
  open-terminal is a tracked follow-up (would need playwright+chromium in the
  open-terminal image + a routed `Browser*` override; see options B1/B2 below).
- `extra-tools` → `webfetch`/`websearch`: node `fetch` egress from the control
  plane (undici won't honor a proxy) → **excluded**. `glob` (local fs) **kept**.

### Changes made for this bump

1. `.env` / `.env.example` — pin `LITTLE_CODER_VERSION=1.9.7` (dropped `latest`).
2. `docker/Dockerfile.agent` — `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` on the npm
   install (no chromium in the control plane; we never launch it here).
3. `docker/entrypoint-agent.sh` — `rm` now also removes `browser` +
   `browser-extract-retention`, logs each removal, and warns on a miss
   (guards against an upstream dir rename).
4. `config/little-coder.config.yaml` — `--exclude-tools` denylist as the
   declarative backstop for the removals (`ShellSession*`, `Browser*`,
   `webfetch`, `websearch`). `bash` and `glob` deliberately not excluded.
5. `docker-compose.yml` (little-coder env) — `LITTLE_CODER_NO_CTX_PROBE=1`
   (the gateway doesn't forward `/props`; skip the futile per-task probe).

### Build + smoke-test gate (run after rebuild)

```
LITTLE_CODER_VERSION=1.9.7 docker compose build little-coder lc-mcpo
docker compose up -d open-terminal little-coder
```

Then verify (the `bash`-shadowing item cannot be proven any other way):

1. A task's `bash` call is **journaled through `ot-exec`** (runs in
   open-terminal). If the agent instead loops on `bash`, the override stopped
   shadowing the built-in → read a bundled extension for the current API
   (`docker exec little-coder sh -c 'cat $(npm root -g)/little-coder/.pi/extensions/extra-tools/index.ts'`)
   and adjust `pi-extension/open-terminal-exec/index.ts`, or set
   `LC_ROUTE_EXEC=0` while you do.
2. A `git rebase` / `git push --force` is **still blocked** by the git-proxy.
3. The model resolves to `qwen36-27b` (our override), not the upstream-shipped
   `qwen3.6-27b` — confirms `models.json` loaded.
4. `shell-session` / `browser` are gone: entrypoint logs `removed extension: …`
   for each; no `Browser*`/`ShellSession*` tools offered.

### Follow-up: browser inside the invariant (not done — Path A chosen)

If browser automation is wanted later, keep it in open-terminal:
- **B1 (simpler):** add node+playwright+chromium to `Dockerfile.open-terminal`,
  drop the browser extension, let the agent drive the browser via **bash**
  (already routed + git-proxied). Loses structured `Browser*` tools.
- **B2 (structured):** run a playwright server in open-terminal + write a routed
  `Browser*` override extension (mirrors the bash override). More to maintain.
Either needs `lc-egress` allowlist entries beyond GitHub (a privacy decision).
