# sl-docs-posture - test plan

**Item:** `sl-docs-posture` (stack-layers). **Branch:** `work/sl-docs-posture`,
cut from `development` at `a2d3644`. **Worktree:**
`.claude/worktrees/wt-sl-docs-posture`. **Anchor:**
`../documentation-plans-ai-stack/implementation-guide/stack-layers/anchors/sl-docs-posture.json`.

**This is a documentation-only item.** The diff is Markdown and nothing else.
Two consequences for the tester: (a) *no* behaviour changed, so every case below
is a claim-vs-source check or a read-only render, and (b) the discrepancies this
item found in non-Markdown files were written to the findings sink rather than
fixed - `documentation/notes/stack-layers-sl-docs-posture-findings.md` - and
F1/F2 there are part of what you are checking, because the shipped prose asserts
them.

**Nothing in this plan starts, stops, restarts or recreates a container, builds
an image, or writes to any `ai-stack_*` network.** The only commands that write
anything write a JSON file under a scratch path you choose. No plane lease is
needed. Do not print the contents of any real `.env`.

**Set up once** (a scratch clone, so every render uses the shipped examples and
never the live host's `.env`):

```bash
WT="D:/Open WebUI/ai-stack/.claude/worktrees/wt-sl-docs-posture"
COPY="$(mktemp -d)/ai-stack"          # or any scratch path you like
git clone --no-hardlinks "$WT" "$COPY"
cd "$COPY" && git checkout work/sl-docs-posture
for p in . frontend inference memory search coder portal agent-org/docker; do
  cp "$p/.env.example" "$p/.env"
done
SCRATCH="$(mktemp -d)"                # for the state-file cases in T5
```

The clone has an empty `OB1/` (submodule, not initialised). That is fine: every
OB1 claim below is checked by reading `OB1/docker/*.yml` **in the worktree**,
which has the submodule checked out at the pinned `fe3e045`.

---

## T1 - acceptance 1: the posture section lists every cloud-capable component,
## and lists nothing that does not exist

**The claim:** the table in `README.md` under "Posture: local-first,
cloud-capable" enumerates every component in this repo that can reach a
non-local endpoint, or that exists to let a non-local client reach in.

**Run the sweep the acceptance names.** From `$WT` (so the OB1 submodule is
present), over the whole tree:

```bash
git grep -niI -E 'openrouter|openai|anthropic|api_key|egress|proxy|tunnel|cloud|mullvad' \
  -- '*docker-compose*.yml' '*.env.example' '*.yaml' '*.yml' '*.py' '*.ts' '*.conf' \
  ':!documentation/archive' ':!scripts/archive' ':!.claude/skills'
# and, separately, inside the submodule:
git -C OB1 grep -niI -E 'gmail|google|oauth|credentials\.json|proxy|gateway' -- docker/
```

For every hit, decide: is this a component that can reach a non-local endpoint,
or a door a non-local client can reach in through? If yes, it must appear in the
table. **One missing FAILS. One listed that does not exist FAILS.**

**The fifteen rows shipped, and the one file that settles each.** Check the row
against the file; do not take the row's word for it.

| # | Row | Settled by reading |
|---|---|---|
| 1 | LiteLLM cloud model group `cloud-large` / `cloud-small` | `inference/config/litellm/model_list/cloud.openrouter.yaml` (two `model_name:` entries, both `api_key: os.environ/OPENROUTER_API_KEY`) |
| 2 | agent-org `llm-gateway-cloud`, `llm-gateway-cloud-db`, `ao-egress` | `agent-org/docker/docker-compose.yml` - all three carry `profiles: ["cloud"]` |
| 3 | `ao-git-egress` + the `ao-worker-*` / `ao-ot-*` pool | same file - `profiles: ["workers"]` on all five |
| 4 | `agent-bridge`'s GitHub App | `agent-org/agent-bridge/app/config.py` - `github_api_base`, `github_app_enabled` |
| 5 | `lc-egress` + `open-terminal` | `coder/docker-compose.yml` - `lc-egress` has no `profiles:` key; `open-terminal`'s `HTTP_PROXY=http://lc-egress:8888` |
| 6 | search `vpn` (Mullvad) | `search/docker-compose.yml` - `VPN_SERVICE_PROVIDER=mullvad`, no `profiles:` key |
| 7 | `cloudflared` | `portal/docker-compose.yml` - `profiles: [internet]`, `TUNNEL_TOKEN` |
| 8 | `portal-alerter` | same file - `notify-net # outbound to oauth2.googleapis.com + gmail.googleapis.com` |
| 9 | `tailscale` | `frontend/docker-compose.yml` - `profiles: [ tailscale ]`, `TAILSCALE_AUTH_KEY` |
| 10 | `openwebui` on `gpu` | same file - the `gpu` definition joins `default` (`ai-stack_default`); the `stock` one joins only `owui-net` |
| 11 | `mnemory-cloud-gateway` | `memory/docker-compose.yml` - `ports: 127.0.0.1:8060`; `memory/mnemory-gateway/app.py` |
| 12 | `openbrain-gateway` / `openbrain-ops-gateway` | `OB1/docker/docker-compose.yml`; the image source is `openbrain-gateway/app.py` in this repo |
| 13 | OB1 `openbrain-digest` / `-gmail-pull` / `-gmail-prune` / `-podcast` | `OB1/docker/docker-compose.scheduled.yml` - the `secrets/google/...` mounts |
| 14 | `openbrain-research` | `OB1/docker/docker-compose.yml` - `FETCH_PROXY_URL`, `SEARCH_API_BASE` |
| 15 | (row 2's note) `AO_EGRESS_ALLOWLIST` is inert | see T6/F1 |

**Known hits that are NOT components and must not be in the table** - if you
think one of these belongs, say so, because that is a real disagreement:
`OWUI_CHAT_LLM_API_KEY`, `LC_LLAMA_API_KEY`, `MCP_API_KEY`, `OB_*_LLM_KEY`,
`MCPO_API_KEY`, `GATEWAY_API_KEY` (all bearer tokens for LOCAL services);
`LITELLM_MASTER_KEY` / `LITELLM_UI_*` (the local gateway's own auth);
`openbrain-postgrest` / `openbrain-rest` (a path-stripping proxy in front of a
local DB); `image_proxy: false` in `search/searxng/settings.yml`; the retired
`search-mcpo` / `lc-mcpo` comments; `.claude/skills/**` (agent skills, not
stack components).

**Fails if:** a component in the sweep that can egress is absent from the table;
a table row names a service, file or variable that does not exist; a row's
"where it is defined" column points at a file that does not contain it.

---

## T2 - acceptance 2a: for every listed component, the enabling variable and
## file are correct

For each row, the "what turns it on" cell names a variable, a profile, or a
script. Check each against the render or the source. The profile ones are
checkable with a render in `$COPY`; the credential ones are checkable by reading
the interpolation.

```bash
cd "$COPY"
# 1. inference cloud models: set the key in a SCRATCH env and re-render the assembler's input
grep -n 'OPENROUTER_API_KEY' inference/docker-compose.yml inference/compose/gateway.yml
#    expect: `- OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}` on llm-gateway
python - <<'PY'
import os, subprocess, sys, tempfile, shutil, pathlib
# the assembler is pure: run it directly against the repo's fragments, twice.
src = pathlib.Path("inference/config/litellm")
out = pathlib.Path(tempfile.mkdtemp())
base = out / "base.yaml"; base.write_text("general_settings: {}\n")
env = dict(os.environ, LITELLM_BASE_CONFIG=str(base),
           LITELLM_FRAGMENT_DIR=str(src / "model_list"),
           LITELLM_EFFECTIVE_CONFIG=str(out / "eff.yaml"),
           COMPOSE_PROFILES="", OPENROUTER_API_KEY="")
r = subprocess.run([sys.executable, str(src / "assemble-config.py")], env=env,
                   capture_output=True, text=True)
print("=== key BLANK ===\n", r.stderr)
env["OPENROUTER_API_KEY"] = "sk-not-a-real-key"
r = subprocess.run([sys.executable, str(src / "assemble-config.py")], env=env,
                   capture_output=True, text=True)
print("=== key SET ===\n", r.stderr)
PY
```

**Pass:** with the key blank, stderr carries
`DROP cloud-large (cloud.openrouter.yaml) - env not set: OPENROUTER_API_KEY`
and the same for `cloud-small`, and the final `wrote … with 0 model(s)` (the
`local` fragment is skipped too, for want of the profile). With the key set,
`LOAD cloud.openrouter.yaml - 2 model(s) registered` and the two names in the
`wrote …` line. **That is the whole of "what turns it on" for row 1** - and note
it says *registered*, never *reachable*; T4 is the other half.
**Fail:** the models survive a blank key (the admission rule is not fail-closed),
or the key set does not produce them (the variable named in the table is wrong).

```bash
# 2/3. the agent-org profiles
docker compose -f agent-org/docker/docker-compose.yml config --services | sort > /tmp/ao-none.txt
docker compose -f agent-org/docker/docker-compose.yml --profile cloud config --services | sort > /tmp/ao-cloud.txt
docker compose -f agent-org/docker/docker-compose.yml --profile workers config --services | sort > /tmp/ao-workers.txt
diff /tmp/ao-none.txt /tmp/ao-cloud.txt; diff /tmp/ao-none.txt /tmp/ao-workers.txt
```

**Pass:** the `cloud` diff adds exactly `llm-gateway-cloud`,
`llm-gateway-cloud-db`, `ao-egress`; the `workers` diff adds exactly
`ao-worker-1`, `ao-worker-2`, `ao-ot-1`, `ao-ot-2`, `ao-git-egress` and the two
journal backups. **Fail:** a service the table puts behind a profile appears in
the bare render, or a service the table does not list appears under one.

```bash
# 7. the portal's internet profile
docker compose -f portal/docker-compose.yml config --services | grep -c cloudflared        # expect 0
docker compose -f portal/docker-compose.yml --profile internet config --services | grep -c cloudflared  # expect 1
grep -n 'internet' scripts/portal/portal-on.ps1 | head            # the script that passes it
# 9/10. the frontend profiles
docker compose -f frontend/docker-compose.yml config --services | sort   # from the example: stock
docker compose -f frontend/docker-compose.yml --profile gpu --profile tailscale config --services | sort
# 4. the GitHub App gate
sed -n '/github_app_enabled/,/return bool/p' agent-org/agent-bridge/app/config.py
```

**Pass:** `cloudflared` renders only with `--profile internet`, and
`scripts/portal/portal-on.ps1` is what passes it; the frontend's bare render
(with `COMPOSE_PROFILES=stock` from the example) is `openwebui-stock` +
`openwebui-backup` and nothing else, while `gpu,tailscale` renders `openwebui`,
`tailscale`, `tailscale-backup`, `openwebui-backup`; `github_app_enabled`
returns `bool(self.github_app_id) and os.path.isfile(...)`.

---

## T3 - acceptance 2b: "off by default" is true of a fresh clone's examples

```bash
cd "$COPY"
for f in .env frontend/.env inference/.env memory/.env search/.env coder/.env portal/.env agent-org/docker/.env; do
  echo "== $f"; grep -nE 'OPENROUTER_API_KEY|CLOUDFLARE_TUNNEL_TOKEN|AO_CLOUD_API_KEY|AO_CLOUD_ENABLED|TAILSCALE_AUTH_KEY|MULLVAD_WG_PRIVATE_KEY|LC_DEPLOY_TOKEN|LC_SELF_REMOTE_PAT|AO_GITHUB_APP|COMPOSE_PROFILES' "$f"
done
for p in frontend inference memory search coder portal; do
  echo "== $p"; docker compose -f $p/docker-compose.yml config --services | sort
done
docker compose -f agent-org/docker/docker-compose.yml config --services | sort
```

**Pass, and this is the acceptance sentence, so read it carefully:**

- Every cloud credential is blank (`OPENROUTER_API_KEY=`,
  `CLOUDFLARE_TUNNEL_TOKEN=`, `AO_CLOUD_API_KEY=`, `LC_DEPLOY_TOKEN=`,
  `LC_SELF_REMOTE_PAT=`) or an unmistakable placeholder
  (`TAILSCALE_AUTH_KEY=putyourtskeyhere`,
  `MULLVAD_WG_PRIVATE_KEY=change-me-real-wg-private-key`). `AO_CLOUD_ENABLED` is
  `false`. `AO_GITHUB_APP_*` does not appear at all (finding F5).
- The only `COMPOSE_PROFILES` assignment in any example is
  `frontend/.env.example`'s `COMPOSE_PROFILES=stock`. `inference`'s is commented
  out (`#COMPOSE_PROFILES=local`); `memory`, `search`, `coder`, `portal`,
  `agent-org/docker` and the root have none. **No example names `cloud`,
  `internet`, `workers` or `tailscale`.**
- No egress service renders **except one**: `search`'s `vpn`, which has no
  `profiles:` key. That is the documented exception (README's Mullvad row,
  finding F6), and its tunnel cannot establish on the placeholder key. If you
  read the acceptance as "literally zero egress containers render", this row is
  the disagreement to raise - the shipped prose states the exception rather than
  hiding it.

**Fail:** any example ships a real-looking credential; any example's
`COMPOSE_PROFILES` names `cloud`/`internet`/`workers`/`tailscale`; a
profile-gated egress service appears in a bare render; or the posture text
claims "nothing egresses by default" without the Mullvad carve-out.

---

## T4 - acceptance 3: the D14 sentence about the gateway being internal-only

**The claim, in three places** (`README.md` "The D14 mechanism, exactly.",
`CLAUDE.md` "THE MECHANISM, stated because it is the part that gets misread",
`inference/README.md` "The key makes the models LISTED, not REACHABLE"):
`llm-gateway` is attached to `llm-net` and `llm-backend-net` and nothing else,
both are `internal: true`, so a listed cloud model is unreachable until an
egress path exists - and the docs say what such a path would be without adding
one.

```bash
cd "$WT"
sed -n '/^  llm-gateway:/,/^    volumes:/p' inference/compose/gateway.yml   # the networks: block
grep -n -A2 'llm-backend-net:' inference/docker-compose.yml                  # internal: true, native
grep -n -A2 '  llm-net:' docker-compose.yml                                  # internal: true, on the ANCHOR
grep -n -A3 '  llm-net:' inference/docker-compose.yml                        # external: true, name: ai-stack_llm-net
docker compose -f inference/docker-compose.yml config --format json \
  | python -c "import json,sys; d=json.load(sys.stdin); print(sorted(d['services']['llm-gateway']['networks']))"
```

**Pass:** `llm-gateway`'s networks are exactly `llm-net` (with the two aliases)
and `llm-backend-net`; `llm-backend-net` is declared `internal: true` in
`inference/docker-compose.yml`; `llm-net` is `external: true` there and is
declared `internal: true` by the root `docker-compose.yml`. **Note the
subtlety the prose is careful about: the `internal: true` for `llm-net` is on
the ANCHOR, not in the inference file.** A sentence that said "both are declared
internal in inference/docker-compose.yml" would be wrong; check that none does.
`llm-gateway-ui` IS on `app-net` (a plain bridge) - confirm the prose never
claims otherwise, and that it carries no alias and serves no inference.

**Pass, second half:** each of the three documents says what making cloud real
would require (an internet-capable network, or `HTTP_PROXY` at a dual-homed
allowlisted proxy the way `llm-gateway-cloud` uses `ao-egress`) and says not to
do it. **Fail:** the diff adds such a path anywhere; or the prose asserts the
models are reachable; or it omits what would be needed.

---

## T5 - acceptance 4: no document tells a reader to run `init --force` to add a
## product on an existing host

```bash
cd "$WT"
git grep -n 'init --force'
git grep -nE 'init.*--product'
```

**Pass:** every survivor is one of these, and you can say which for each line:

1. **The fresh / scratch case** - the invocation carries `--state <scratch>` or
   `--root <scratch>`: `documentation/evidence/sl-ob1-gitlink/test-plan.md`,
   `documentation/evidence/stack-layers/sl-manifest-test-plan.md`,
   `documentation/evidence/sl-ob1-profiles/test-plan.md`, and the measurement
   lines in the two findings notes.
2. **A quotation of what the driver prints**, not an instruction:
   `documentation/evidence/sl-driver-parity/test-plan.md:846`,
   `scripts/stack/stack.py`'s `cmd_inventory` hint.
3. **Explains the replacement** - the new `scripts/stack/README.md` paragraph
   under `init`. (The other `stack.py` hit, in `State.load`'s unreadable-file
   refusal - *"delete it or re-run `init --force`"* - is about a CORRUPT state
   file, which is the start-over case.)
4. **Non-Markdown, recorded in the findings sink as F2 and deliberately not
   touched**: `scripts/stack/stack.py`, `scripts/stack/stack.ps1`,
   `stack.manifest.toml`, `scripts/stack/test_stack.py`.

The five that were changed, and what each now says - check all five:

| File | Now says |
|---|---|
| `CLAUDE.md` (Open Brain row) | `stack.py enable research` - NOT `init --force`, which REPLACES the state file |
| `documentation/runbooks/SERVICE-LIFECYCLE.md` (row 8a, item (ii)) | `enable research`, with the six-to-four measurement and "both print the same summary" |
| `.claude/skills/stack-map/references/workspace-stacks.md` (the OB1 landing step) | same, with the measurement |
| `scripts/stack/README.md` (the `fe3e045` paragraph) | `enable research` + a pointer to the new `init` paragraph |
| `documentation/notes/stack-layers-sl-ob1-gitlink-findings.md` and `…-sl-driver-parity-findings.md` | a dated CORRECTION block under the original text, which stands |

**Now reproduce the semantics yourself.** Do not trust the numbers in the docs:

```bash
cd "$WT"
for p in frontend inference memory search coder agent-org; do
  python scripts/stack/stack.py --state "$SCRATCH/a.json" enable "$p" >/dev/null
done
cp "$SCRATCH/a.json" "$SCRATCH/b.json"
python -c "import json;print(sorted(json.load(open(r'$SCRATCH/a.json'))['planes']))"
python scripts/stack/stack.py --state "$SCRATCH/a.json" init --product research --force
python -c "import json;print(sorted(json.load(open(r'$SCRATCH/a.json'))['planes']))"
python scripts/stack/stack.py --state "$SCRATCH/b.json" enable research
python -c "import json;print(sorted(json.load(open(r'$SCRATCH/b.json'))['planes']))"
python scripts/stack/stack.py --state "$SCRATCH/b.json" init --product research; echo "exit=$?"
```

**Pass:** before, six planes. After `init --product research --force`: **four**
- `['frontend', 'inference', 'ob1', 'search']`, with `memory`, `coder` and
`agent-org` gone. After `enable research` on the untouched copy: **seven** -
the same six plus `ob1`, whose profiles are
`idea-refinery, research, wiki, notebook`. Both commands print an identical
`enabled product research:` block listing `inference, frontend, search, ob1`;
only `init` adds `wrote <path>` and the `list` dump. The last command refuses:
`refused: <path> already exists (re-run with --force to overwrite it)`, exit 1.

**Fail:** the counts differ (the docs are wrong - report the real ones); `init
--force` merges (then the whole item's premise is wrong); the two outputs differ
in a way that WOULD have warned an operator (then the "the screen does not warn
you" sentences are wrong).

`--state` only ever writes the scratch path. Confirm the live
`.stack/state.json` in the main checkout is untouched:
`git -C "D:/Open WebUI/ai-stack" status --short` shows nothing new, and the
file's mtime is unchanged.

---

## T6 - the two findings this item asserts in shipped prose (F1, F2)

The posture sections do not only describe; they make two factual claims about
defects. Both are in `documentation/notes/stack-layers-sl-docs-posture-findings.md`
and both are quoted in the READMEs, so a wrong one ships as a wrong sentence.

**F1 - `ao-egress`'s `EGRESS_ALLOWLIST` is inert.**

```bash
cd "$WT"
sed -n '/^  ao-egress:/,/^  # /p' agent-org/docker/docker-compose.yml
cat little-coder/docker/Dockerfile.egress
cat little-coder/docker/tinyproxy.conf
cat little-coder/docker/egress-allowlist.txt
sed -n '/^  ao-git-egress:/,/^  # /p' agent-org/docker/docker-compose.yml
```

**Pass:** `Dockerfile.egress` has no `ENTRYPOINT` and its `CMD` is
`tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf`; that conf sets
`FilterDefaultDeny Yes` and `Filter "/etc/tinyproxy/egress-allowlist.txt"`; the
baked allowlist is `^(.*\.)?github\.com$` and `^(.*\.)?githubusercontent\.com$`;
`ao-egress` overrides no `command`, no `entrypoint` and no volume onto that
path, and nothing in the image reads `EGRESS_ALLOWLIST`; while `ao-git-egress`
overrides BOTH the conf (`./egress/tinyproxy.conf`, whose `Filter` is
`/egress/egress-allowlist.txt`) and the command (`/egress-reload.sh`).
Therefore `openrouter.ai` would be denied. **Fail:** you find a reader of
`EGRESS_ALLOWLIST` anywhere in the image or an override that makes it effective
- then the shipped sentence in `README.md` and `agent-org/README.md` is wrong
and must come out. (A live proof would need building the image and starting the
`cloud` profile, which is out of scope here; the source reading is the evidence,
and if you want more, `docker build`ing the egress image to a `:wt-` tag and
`cat`ing the file inside it is the read-only way.)

**F2 - three non-Markdown files still say `init --product research --force`.**
Check each line exists as quoted, and that none of them is in this item's diff:
`scripts/stack/stack.py` (`cmd_inventory`'s `[ ~~ ]` follow-up line),
`scripts/stack/stack.ps1` (header comment), `stack.manifest.toml` (the
`[planes.ob1.profiles.*]` preamble), plus `scripts/stack/test_stack.py`'s
`assert "init --product research" in out`. **Fail:** any of them is in the diff
(this item is Markdown-only), or the quoted text does not match.

---

## T7 - acceptance 5: gates, and the diff is Markdown only

```bash
cd "$WT"
git diff --stat development...HEAD
git diff --name-only development...HEAD | grep -v '\.md$'      # expect NO output
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/plan-store.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-doc-placement.ps1 -All
ruff check .
git log --format='%H %s%n%b' development..HEAD | grep -c 'Co-Authored-By: Claude'
```

**Pass:** the second command prints nothing (Markdown only); `plan-store.ps1`
exits 0; `check-doc-placement.ps1 -All` exits 0 (this item writes no plan file
into this repo - the anchor lives in the plan store, the findings note is a
note, and the test plan is evidence); `ruff check .` is clean; every commit
carries the attestation trailer and the pre-commit hooks ran.

**Fail:** a non-Markdown file in the diff; a new file under
`documentation/implementation-guide/` other than the two kept directories; a
commit without the trailer.

---

## T8 - the claim ledger (the T10 rule)

Every sentence this item introduced, and what settles it. A sentence not in this
table is a sentence the tester was not asked to check, which is the failure this
rule exists to prevent - **if you find one in the diff that is not here, that is
a FAIL in itself.**

### `README.md`, section "Posture: local-first, cloud-capable"

| Sentence / cell | Settled by |
|---|---|
| "Nothing in this stack sends anything to a model provider by default." | T3 (no cloud key set, no cloud profile named in any example) + T1 (the component set is complete) |
| "the gateway that holds the `llama-cpp` alias is attached to two networks that are both `internal: true`" - and the attribution of each declaration to the anchor / to `inference/docker-compose.yml` | T4 |
| "each one is gated behind a compose profile, a credential, or a script the operator runs by hand" | T2 + T3, per row |
| "That is stack-layers decision D14 - keep the components, ship them off" | `../documentation-plans-ai-stack/implementation-guide/stack-layers/DECISIONS.md`, D14 (resolved 2026-09-20) |
| Row 1, "assemble-config.py drops every model entry whose `os.environ/VAR` reference is unset or empty, and logs the drop with its reason" | T2, case 1 (both stderr lines) |
| Row 1, "`OPENROUTER_API_KEY` in `inference/.env`. It is blank in `inference/.env.example`." | T3 |
| Row 1, egress "Nowhere" | T4 |
| Row 2, "All three carry `profiles: ["cloud"]`, and `agent-org/docker/.env.example` sets no `COMPOSE_PROFILES` at all" | T2 case 2 + T3 |
| Row 2, "`AO_CLOUD_ENABLED=false` separately keeps `agent-bridge` on the local lane" | `agent-org/docker/docker-compose.yml`: `AO_CLOUD_ENABLED: ${AO_CLOUD_ENABLED:-false}`; `agent-org/docker/.env.example`: `AO_CLOUD_ENABLED=false` |
| Row 2, "`llm-gateway-cloud` has no internet leg of its own: `ao-net` and `ao-cloud-egress-net` (`internal: true`) with `HTTP_PROXY`/`HTTPS_PROXY` pointed at `ao-egress`" | the service block + the `networks:` block at the end of `agent-org/docker/docker-compose.yml` |
| Row 3, "`profiles: ["workers"]`, so with no `COMPOSE_PROFILES` none of them renders" | T2 case 2 |
| Row 3, the allowlist file lives on `ao-egress-config`, seeded by `egress-reload.sh` with the GitHub pair and SIGHUPed when `agent-bridge` rewrites it | `agent-org/docker/egress/egress-reload.sh` (the `printf` seed + the `kill -HUP` poll loop); `agent-org/docker/egress/tinyproxy.conf` (`Filter "/egress/egress-allowlist.txt"`) |
| Row 4, "`Settings.github_app_enabled` is false unless `github_app_id` is set and the private-key file exists" | T2, the `sed` on `config.py` |
| Row 4, "they are not in the example - this plane leaves `${VAR:-}` names out on purpose" | `grep -c AO_GITHUB agent-org/docker/.env.example` -> 0; `agent-org/README.md`'s "Environment" paragraph states the convention |
| Row 4, "`https://api.github.com`, directly from `ao-net` … does not go through `ao-egress`" | `config.py`'s `github_api_base`; `agent-bridge`'s `networks:` are `ao-net` + `llm-net`, and it sets no `HTTP_PROXY` |
| Row 5, "it is unprofiled and starts whenever the coder plane starts" | `coder/docker-compose.yml`, `lc-egress` has no `profiles:` key |
| Row 5, "`FilterDefaultDeny Yes`", the baked allowlist, "`CONNECT` to 443 and 22" | T6/F1 (`tinyproxy.conf`, `egress-allowlist.txt`) |
| Row 5, "`open-terminal` has no other route: `lc-net` (`internal: true`) and `llm-net`" | `coder/docker-compose.yml`: the service's `networks:` and the bottom `networks:` block |
| Row 6, "unprofiled, so it renders and starts with the search plane" and the placeholder-key sentence | T3 |
| Row 6, "`searxng` sits on `search-net` (`internal: true`) and `search/searxng/settings.yml` points `outgoing.proxies` at `http://vpn:8888`" | those two files |
| Row 6, "HTTPS `CONNECT` resolves DNS at the far end of the tunnel" | `search/docker-compose.yml`'s `HTTPPROXY` comment + `search/searxng/settings.yml` |
| Row 7, "`profiles: [internet]` … the whole plane is `manual` in `stack.manifest.toml`" | `portal/docker-compose.yml`; `stack.manifest.toml` `[planes.portal] manual = ...` |
| Row 7, "`caddy` publishes no host port" | the `caddy` block has no `ports:` key |
| Row 8, "`oauth2.googleapis.com` and `gmail.googleapis.com`, on `notify-net`" and the two-non-internal-networks sentence | `portal/docker-compose.yml`: the alerter's `networks:` comment + the `networks:` block (`edge-net` bridge, `auth-net` internal, `notify-net` bridge) |
| Row 9, "`profiles: [tailscale]` … necessarily together with `gpu`, because `network_mode: service:openwebui` names the `gpu` definition" | `frontend/docker-compose.yml` |
| Row 10, "the `stock` profile's Open WebUI is on the project-local `owui-net` and nothing else"; "the `gpu` one joins `ai-stack_default`" | the two service definitions + the `networks:` block |
| Row 10, "Its web search does not: `SEARXNG_QUERY_URL` points at the search plane's gateway" | the `gpu` definition's `SEARXNG_QUERY_URL` |
| Row 11, "a door in, not a way out: cloud clients dial it, it dials nothing"; ":8060 loopback" | `memory/mnemory-gateway/app.py` (its only upstream is `MNEMORY_URL`); the `ports:` line |
| Row 11, "that client never holds `MCP_API_KEY`, which the gateway injects upstream" | `memory/mnemory-gateway/app.py` header + the `MNEMORY_KEY` / `GATEWAY_KEY` split |
| Row 12, the CLOUD door force-filters to `share == "cloud"` and blocks the aggregate tools, the OPS door runs `GATEWAY_PROFILE: ops` / `exposure == "ops"` with an agent-memory allowlist, the two keys must differ, ":8061 / :8062" | `openbrain-gateway/app.py` (docstring + the `GATEWAY_PROFILE` block); `OB1/docker/docker-compose.yml`, the `openbrain-gateway` and `openbrain-ops-gateway` blocks - including `${OPS_GATEWAY_KEY:?openbrain-ops-gateway needs its OWN key, never the cloud one}` |
| Row 13, "each mounts a Google OAuth client secret and token from `OB1/secrets/`, which is gitignored and absent from a fresh clone" | `OB1/docker/docker-compose.scheduled.yml`; `ls OB1/secrets` in a fresh clone |
| Row 13, "Gmail read and send, Calendar read … plus `wttr.in` for the digest's weather brief" | the same file's `openbrain-digest` comments (gmail.send scope, calendar-token, the wttr.in note) |
| Row 14, "`FETCH_PROXY_URL`, defaulting to `http://vpn:8888` … searches to `http://gateway:8080`" | `OB1/docker/docker-compose.yml`, `openbrain-research` |
| "The D14 mechanism, exactly." (whole paragraph) | T4 |
| "keeping the gateway off every internet-capable bridge is the supply-chain posture that also explains `LITELLM_LOCAL_MODEL_COST_MAP=True`" | `inference/compose/gateway.yml`, the `LITELLM_LOCAL_MODEL_COST_MAP` line and its comment |
| "agent-org/config/litellm-cloud.config.yaml says in as many words not to add OpenRouter to the local gateway" | that file, lines beginning "Do NOT add OpenRouter to it" |
| "Before you enable the agent-org cloud lane, read its allowlist." (whole paragraph) | T6/F1 |
| "What a fresh clone actually does." (whole paragraph) | T3 |

### `CLAUDE.md`, the "Posture" paragraph

Same claims, compressed; every clause maps to a `README.md` row above and is
settled by the same check. Two additions to verify separately:

| Sentence | Settled by |
|---|---|
| The enumeration is complete and ordered (1)-(10) and matches the README table's fifteen rows with no component dropped | read both side by side; the CLAUDE.md list folds rows 11+12 into "(9) the two INBOUND doors" and rows 1/2/3/4/5/6/7+8/9+10 onto (1)-(8)/(10). A component in the README table with no home in the CLAUDE.md list is a FAIL |
| "`lc-egress` in the coder plane - UNPROFILED, so it is up whenever coder is" | T1 row 5 |
| The Open Brain row now says `stack.py enable research` - NOT `init --force` | T5 |

### `inference/README.md`, section "Posture: local-first, cloud-capable"

| Sentence | Settled by |
|---|---|
| "This plane holds exactly one cloud-capable component" | T1's sweep restricted to `inference/` - the only hit is the cloud fragment |
| the two model names, the `openrouter/qwen/qwen-2.5-*` placeholders, `data_collection: "deny"` | `inference/config/litellm/model_list/cloud.openrouter.yaml` |
| the two admission rules, fail-closed, and "the cloud fragment has no `x-requires-profile` on purpose" | `assemble-config.py` (rules A and B in its docstring and in `main`); `grep -n x-requires-profile` over the two fragments - `local.yaml` has it, `cloud.openrouter.yaml` does not |
| the exact log line `[assemble-config] DROP cloud-large (cloud.openrouter.yaml) - env not set: OPENROUTER_API_KEY` | T2 case 1 - compare the string character for character |
| "`inference/.env.example` ships it blank, and `llm-gateway` passes it through as `${OPENROUTER_API_KEY:-}`" | T3 + `inference/compose/gateway.yml` |
| the whole "LISTED, not REACHABLE" paragraph, including which file declares which network internal | T4 |
| "publishes no host port" and the `LITELLM_LOCAL_MODEL_COST_MAP=True` reason | `inference/compose/gateway.yml`'s comment block on `llm-gateway` |
| "Making it real is an egress decision, not a config one" + the two shapes it would take + "neither is done here" | T4, second half; and `git diff` shows no compose change |
| the `litellm-cloud.config.yaml` quotation | that file |
| "the fragment exists because D11 requires the gateway to be ABLE to run cloud-only" | `assemble-config.py`'s "WHY THIS EXISTS (stack-layers D11)"; `inference/docker-compose.yml`'s `local`-profile header |

### `agent-org/README.md`, section "Posture: local-first, cloud-capable"

| Sentence | Settled by |
|---|---|
| "This plane holds four of them, and a default `up` starts none" | T2 case 2 + T3 (no `COMPOSE_PROFILES` in this plane's example) |
| the four table rows (cloud gateway + db; `ao-egress`; `ao-git-egress` + pool; the GitHub App) | T1 rows 2/3/4 and their settling files |
| "`agent-bridge` keeps every role on the local lane because `AO_CLOUD_ENABLED` defaults to `false`" | the compose interpolation + the example |
| the `ao-egress` allowlist paragraph, including "`ao-git-egress` is unaffected - it overrides both the conf file and the command" | T6/F1 |
| "What is NOT in this plane." - the two-gateways split, `ao-net` being a plain bridge, "no cloud credential is set on any default service" | `agent-org/docker/docker-compose.yml`'s `networks:` comment on `ao-net`; a read of every default service's `environment:` for a cloud key (there is none) |

### `scripts/stack/README.md`

| Sentence | Settled by |
|---|---|
| "`init --force` REPLACES the state file; `enable` MERGES into it." | T5 |
| "`init --product X` calls `cmd_enable` internally, so it prints the same `enabled product X:` block" | `scripts/stack/stack.py`, `cmd_init` calls `cmd_enable(manifest, fresh, ...)`; T5's transcripts |
| "`cmd_init` builds a fresh `State({})` and saves that, while `cmd_enable` mutates the state it loaded" | the two functions in `scripts/stack/stack.py` |
| the six-to-four / six-to-seven table, and the `ob1` profile list | T5 |
| "Without `--force`, `init` refuses an existing file outright: `refused: … already exists …`, exit 1" | T5's last command |
| "`init --force` is for a host whose state you intend to start over from" | judgement, but it must not contradict T5 |
| the amended `fe3e045` paragraph ("Use `enable`, not `init --force`") | T5 |

### `CLAUDE.md`, `SERVICE-LIFECYCLE.md` row 8a, `workspace-stacks.md`, and the two findings-note corrections

| Sentence | Settled by |
|---|---|
| each now names `enable research` as the landing step | T5's five-file table |
| the six-to-four measurement quoted in SERVICE-LIFECYCLE and workspace-stacks | T5 |
| "Both print the same `enabled product research:` summary, so the screen does not warn you" | T5's transcripts |
| the correction blocks leave the original text standing | `git diff` on the two notes - additions only, no deletions |
| "this host's `.stack/state.json` now lists SEVEN planes - `anchor, coder, frontend, inference, memory, ob1, search`" (in the ob1-gitlink correction and in finding F3) | `python -c "import json;print(sorted(json.load(open('.stack/state.json'))['planes']))"` in the MAIN checkout, read-only. It listed six when `sl-ob1-gitlink` wrote its note (no `ob1`); if it has moved again since, the correction's number is stale and that is a FAIL - report the real list |

### `documentation/notes/stack-layers-sl-docs-posture-findings.md`

Findings F1-F6 are each settled by T6 (F1, F2), T5 (F3), T5's survivor
classification (F4), T3 (F5), T3 (F6).

---

## What is NOT in this item

- No compose, config, script, `.env.example` or source change. Every defect
  found went to the findings sink. In particular **F1 is not fixed**, and the
  agent-org `cloud` profile still cannot reach openrouter.ai.
- The `init --force` strings in `scripts/stack/stack.py`, `stack.ps1`,
  `stack.manifest.toml` and `test_stack.py` (F2). Changing the first requires
  changing the fourth.
- `OB1`'s own README (`sl-ob1-docs` owns it) and the OB1 gitlink.
- Any egress path, any key, any rotation (D2, operator).
