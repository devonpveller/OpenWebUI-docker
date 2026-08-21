@echo off
REM Pin CWD to the repo root (moved to scripts/recovery/ 2026-08-21)
cd /d "%~dp0..\.."
REM Emergency Tailscale Network Recovery - Legacy Batch Version
REM For PowerShell version with better GPU support, use: emergency-recovery.ps1
REM
REM Recovery stack scope (kept in sync with docker-compose.yml):
REM   core    openwebui, llama-cpp-upstream, llama-cpp-embed-upstream, llm-queue,
REM           llm-gateway-db, llm-gateway, llm-gateway-ui, tailscale
REM           (llm-queue = B2 admission controller; llm-gateway-ui = Admin-UI sidecar)
REM   memory  mnemory, mnemory-cloud-gateway
REM   search  vpn, tor, redis, searxng, gateway  (Private Search Gateway)
REM   coder   open-terminal, little-coder, lc-egress
REM   aux     smolcrawl-pipelines, surrealdb, open_notebook
REM   backup  mnemory-backup, openwebui-backup, little-coder-backup, smolcrawl-backup,
REM           tailscale-backup, lm-models-backup, open-notebook-backup,
REM           openbrain-db-backup, openbrain-wiki-backup (last two need OB1 up)
REM   OB1     Open Brain - SEPARATE compose project (OB1\docker\docker-compose.yml)
REM   AGORG   agent-org - SEPARATE compose project (agent-org\docker\docker-compose.yml);
REM           default plane only (mattermost, mattermost-db, agent-bridge, agent-bridge-db,
REM           + agent-bridge-db-backup, mattermost-db-backup); `up -d` brings the whole
REM           default plane, so the backup sidecars start automatically. Workers/cloud
REM           profiles are gated + NOT managed here
REM   PORTAL  caddy/authelia/cloudflared/portal-*/integrity-tripwire (+ caddy-backup,
REM           authelia-backup) are PROFILE-GATED (profiles: [internet]) and NOT
REM           managed here; use scripts\portal-on.ps1 / portal-off.ps1. A nuclear
REM           `docker compose down` stops a running portal; it is not auto-restored.

set "OB1_COMPOSE=OB1\docker\docker-compose.yml"
REM agent-org (teams-chat orchestration) — ALSO a separate compose project; like OB1 it
REM attaches to ai-stack_llm-net. Stopped first (before OB1), started last (after OB1).
REM The workers/cloud profiles are gated + NOT managed here (default plane only).
set "AGENTORG_COMPOSE=agent-org\docker\docker-compose.yml"

echo ========================================
echo EMERGENCY TAILSCALE NETWORK RECOVERY
echo ========================================
echo.

echo [INFO] Checking current container status...
docker compose ps

echo.
echo [INFO] Running pre-recovery diagnostics...
echo [INFO] Testing basic connectivity before destructive actions...

REM Check if containers are running
docker compose ps --format json >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Docker Compose not working properly
    goto :nuclear_option
)

REM Test OpenWebUI health
docker compose exec openwebui curl -f -s http://localhost:8080/ >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [INFO] OpenWebUI responding...

    REM Test llama-cpp-upstream connectivity
    docker compose exec llama-cpp-upstream curl -f -s http://localhost:8080/health >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo [INFO] llama-cpp-upstream connectivity working...

        REM Test external connectivity
        docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
        if %ERRORLEVEL% EQU 0 (
            echo [SUCCESS] All basic checks PASSED - trying minimal recovery first
            goto :minimal_recovery
        ) else (
            echo [WARN] External connectivity failed
        )
    ) else (
        echo [WARN] llama-cpp-upstream connectivity failed
    )
) else (
    echo [WARN] OpenWebUI health check failed
)

echo [INFO] Basic checks failed - proceeding with full recovery

REM Phase 1: Graceful shutdown in reverse dependency order
echo [INFO] Phase 1: Graceful shutdown
echo [WARN] This restarts the full workspace: core, memory, search, coder planes + OB1

echo [INFO] Stopping agent-org stack (downstream of OB1)...
if exist "%AGENTORG_COMPOSE%" docker compose -f "%AGENTORG_COMPOSE%" stop

echo [INFO] Stopping Open Brain (OB1) stack...
if exist "%OB1_COMPOSE%" docker compose -f "%OB1_COMPOSE%" stop

echo [INFO] Stopping Watchtower...

echo [INFO] Stopping little-coder control plane...
docker compose stop little-coder-backup lc-egress little-coder open-terminal
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] little-coder plane stop failed, attempting force kill...
    docker compose kill little-coder-backup lc-egress little-coder open-terminal
)

echo [INFO] Stopping Private Search Gateway...
docker compose stop gateway searxng redis tor vpn
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Search gateway stop failed, attempting force kill...
    docker compose kill gateway searxng redis tor vpn
)

echo [INFO] Stopping Tailscale container...
docker compose stop tailscale
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Tailscale stop failed, attempting force kill...
    docker compose kill tailscale
)

echo [INFO] Stopping SmolCrawl Pipelines container...
docker compose stop smolcrawl-pipelines
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] SmolCrawl Pipelines stop failed, attempting force kill...
    docker compose kill smolcrawl-pipelines
)

echo [INFO] Stopping open-notebook and surrealdb containers...
docker compose stop open_notebook surrealdb
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] open-notebook/surrealdb stop failed, attempting force kill...
    docker compose kill open_notebook surrealdb
)

echo [INFO] Stopping Mnemory containers...
docker compose stop mnemory-cloud-gateway mnemory mnemory-backup
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Mnemory stop failed, attempting force kill...
    docker compose kill mnemory-cloud-gateway mnemory mnemory-backup
)

echo [INFO] Stopping OpenWebUI backup scheduler...
docker compose stop openwebui-backup
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] OpenWebUI backup stop failed, attempting force kill...
    docker compose kill openwebui-backup
)

echo [INFO] Stopping LiteLLM gateway (before the upstream inference servers)...
docker compose stop llm-gateway-backup llm-gateway-ui llm-gateway llm-gateway-db

echo [INFO] Stopping llm-queue (B2 admission controller, after the gateway)...
docker compose stop llm-queue

echo [INFO] Stopping llama-cpp-upstream containers...
docker compose stop llama-cpp-upstream llama-cpp-embed-upstream
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] llama-cpp-upstream stop failed, attempting force kill...
    docker compose kill llama-cpp-upstream llama-cpp-embed-upstream
)

echo [INFO] Stopping OpenWebUI container...
docker compose stop openwebui
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] OpenWebUI stop failed, attempting force kill...
    docker compose kill openwebui
)

echo [INFO] Waiting for cleanup...
timeout /t 15 /nobreak >nul

REM Phase 2: Restart with proper timing for GPU/network dependencies
echo [INFO] Phase 2: Service restart
echo [INFO] Starting OpenWebUI with GPU support...
docker compose up -d openwebui
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to start OpenWebUI container
    goto :nuclear_option
)

echo [INFO] Waiting for OpenWebUI to be healthy (required for Tailscale network dependency)...
:wait_openwebui
docker compose ps openwebui | findstr "healthy" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] OpenWebUI not yet healthy, waiting 10 more seconds...
    timeout /t 10 /nobreak >nul
    goto :wait_openwebui
)
echo [SUCCESS] OpenWebUI is healthy - safe to start llama-cpp-upstream services

echo [INFO] Starting llama-cpp-upstream with GPU support...
docker compose up -d llama-cpp-upstream
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to start llama-cpp-upstream container
    goto :nuclear_option
)

echo [INFO] Waiting for llama-cpp-upstream to initialize...
timeout /t 30 /nobreak >nul

echo [INFO] Starting llama-cpp-embed-upstream...
docker compose up -d llama-cpp-embed-upstream

echo [INFO] Starting llm-queue (B2 admission controller, between upstreams and LiteLLM)...
docker compose up -d llm-queue
timeout /t 5 /nobreak >nul

echo [INFO] Starting LiteLLM gateway (db, then gateway) - the front door all callers use...
docker compose up -d llm-gateway-db
timeout /t 10 /nobreak >nul
docker compose up -d llm-gateway
timeout /t 10 /nobreak >nul
docker compose up -d llm-gateway-backup
REM llm-gateway-ui: master-key'd Admin-UI sidecar (analytics dashboard), shares
REM llm-gateway-db; serves no inference, non-critical — start it best-effort.
docker compose up -d llm-gateway-ui

echo [INFO] Starting Tailscale with shared network namespace...
docker compose up -d tailscale
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to start Tailscale container
    goto :nuclear_option
)

echo [INFO] Waiting for Tailscale network connectivity and serve configuration...
timeout /t 60 /nobreak >nul

echo [INFO] Starting Watchtower monitoring service...

echo [INFO] Starting Mnemory memory service...
docker compose up -d mnemory
timeout /t 15 /nobreak >nul

echo [INFO] Starting Mnemory gateway (cloud MCP proxy)...
docker compose up -d mnemory-cloud-gateway

echo [INFO] Starting backup schedulers (main/host resources)...
docker compose up -d mnemory-backup openwebui-backup smolcrawl-backup tailscale-backup lm-models-backup open-notebook-backup

echo [INFO] Starting SmolCrawl Pipelines...
docker compose up -d smolcrawl-pipelines

echo [INFO] Starting surrealdb (open-notebook database)...
docker compose up -d surrealdb
timeout /t 10 /nobreak >nul

echo [INFO] Starting open-notebook...
docker compose up -d open_notebook

echo [INFO] Starting Private Search Gateway (vpn, tor, redis, searxng, gateway)...
docker compose up -d vpn tor redis searxng gateway
echo [INFO] Allowing VPN tunnel + Tor circuit to build...
timeout /t 30 /nobreak >nul

echo [INFO] Starting open-terminal (little-coder workspace plane)...
docker compose up -d open-terminal
timeout /t 20 /nobreak >nul

echo [INFO] Starting little-coder control plane (daemon, MCP edge, egress)...
docker compose up -d little-coder
timeout /t 20 /nobreak >nul
docker compose up -d lc-egress little-coder-backup

echo [INFO] Starting Open Brain (OB1) stack...
if exist "%OB1_COMPOSE%" (
    docker compose -f "%OB1_COMPOSE%" --profile idea-refinery up -d
    echo [INFO] Starting OB1-attached backups (obnet + open-brain volumes now exist)...
    docker compose up -d openbrain-db-backup openbrain-wiki-backup
) else (
    echo [INFO] Open Brain (OB1) not deployed in this workspace - skipping
)

echo [INFO] Starting agent-org stack (default plane, downstream of OB1)...
if exist "%AGENTORG_COMPOSE%" (
    docker compose -f "%AGENTORG_COMPOSE%" up -d
) else (
    echo [INFO] agent-org not deployed in this workspace - skipping
)

REM Phase 3: Connectivity verification
echo [INFO] Phase 3: Testing connectivity...
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1

if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Basic recovery failed, attempting nuclear option...
    goto :nuclear_option
) else (
    echo [SUCCESS] Emergency recovery successful!
    goto :verify_services
)

:minimal_recovery
echo [INFO] ==========================================
echo [INFO] MINIMAL RECOVERY - GENTLE RESTART
echo [INFO] ==========================================
echo [INFO] Restarting with proper network dependency sequence...

REM Stop dependent containers first
echo [INFO] Stopping Tailscale and dependent services (network dependents)...
docker compose stop tailscale llama-cpp-upstream llama-cpp-embed-upstream mnemory mnemory-cloud-gateway mnemory-backup openwebui-backup smolcrawl-pipelines open_notebook surrealdb gateway searxng redis tor vpn little-coder-backup lc-egress little-coder open-terminal
if exist "%OB1_COMPOSE%" docker compose -f "%OB1_COMPOSE%" stop

REM Restart OpenWebUI first and wait for health
echo [INFO] Restarting OpenWebUI...
docker compose restart openwebui

echo [INFO] Waiting for OpenWebUI to be healthy...
:wait_openwebui_minimal
docker compose ps openwebui | findstr "healthy" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] OpenWebUI not yet healthy, waiting 10 more seconds...
    timeout /t 10 /nobreak >nul
    goto :wait_openwebui_minimal
)
echo [SUCCESS] OpenWebUI healthy - restarting dependent services

REM Now restart the dependent containers
echo [INFO] Starting llama-cpp-upstream with fresh network namespace...
docker compose up -d llama-cpp-upstream
timeout /t 15 /nobreak >nul

echo [INFO] Starting llama-cpp-embed-upstream...
docker compose up -d llama-cpp-embed-upstream
timeout /t 15 /nobreak >nul

echo [INFO] Starting llm-queue + LiteLLM gateway (admission plane, before callers)...
docker compose up -d llm-queue llm-gateway
timeout /t 5 /nobreak >nul

echo [INFO] Starting Tailscale with fresh network namespace...
docker compose up -d tailscale
timeout /t 30 /nobreak >nul


echo [INFO] Starting Mnemory services...
docker compose up -d mnemory mnemory-cloud-gateway mnemory-backup
timeout /t 15 /nobreak >nul

echo [INFO] Starting backup schedulers (main/host resources)...
docker compose up -d openwebui-backup smolcrawl-backup tailscale-backup lm-models-backup open-notebook-backup

echo [INFO] Starting SmolCrawl Pipelines...
docker compose up -d smolcrawl-pipelines

echo [INFO] Starting surrealdb (open-notebook database)...
docker compose up -d surrealdb
timeout /t 10 /nobreak >nul

echo [INFO] Starting open-notebook...
docker compose up -d open_notebook

echo [INFO] Starting Private Search Gateway...
docker compose up -d vpn tor redis searxng gateway

echo [INFO] Starting little-coder control plane...
docker compose up -d open-terminal little-coder lc-egress little-coder-backup

echo [INFO] Starting Open Brain (OB1) stack...
if exist "%OB1_COMPOSE%" (
    docker compose -f "%OB1_COMPOSE%" --profile idea-refinery up -d
    docker compose up -d openbrain-db-backup openbrain-wiki-backup
)
if exist "%AGENTORG_COMPOSE%" docker compose -f "%AGENTORG_COMPOSE%" up -d

echo [INFO] Testing if minimal recovery worked...
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Minimal recovery successful!
    goto :verify_services
) else (
    echo [WARN] Minimal recovery failed - proceeding with full recovery
    goto :full_recovery
)

:full_recovery
echo [INFO] ==========================================
echo [INFO] FULL RECOVERY - CONTAINER RESTART
echo [INFO] ==========================================

:nuclear_option
echo [WARN] ========================================
echo [WARN] PERFORMING NUCLEAR RECOVERY
echo [WARN] ========================================

echo [INFO] Last chance diagnostic check...
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo [SUCCESS] Wait - connectivity actually working! Trying minimal recovery instead...
    goto :minimal_recovery
)

echo [WARN] All diagnostics failed - proceeding with nuclear option
echo [WARN] This will DESTROY and REBUILD containers - all customizations will be lost
echo [WARN] NOTE: if the internet portal is running, 'compose down' stops it; it is
echo [WARN]       profile-gated and NOT auto-restored - re-run scripts\portal-on.ps1.
echo [INFO] Tearing down agent-org first (downstream of OB1)...
if exist "%AGENTORG_COMPOSE%" docker compose -f "%AGENTORG_COMPOSE%" down
echo [INFO] Tearing down Open Brain (OB1) next (it attaches to ai-stack_llm-net)...
if exist "%OB1_COMPOSE%" docker compose -f "%OB1_COMPOSE%" down
echo [INFO] Full main-stack restart with network namespace reset...
docker compose down
timeout /t 15 /nobreak >nul
docker compose up -d
timeout /t 90 /nobreak >nul
echo [INFO] Starting Open Brain (OB1) stack...
if exist "%OB1_COMPOSE%" (
    docker compose -f "%OB1_COMPOSE%" --profile idea-refinery up -d
    docker compose up -d openbrain-db-backup openbrain-wiki-backup
)
if exist "%AGENTORG_COMPOSE%" docker compose -f "%AGENTORG_COMPOSE%" up -d

echo [INFO] Testing post-nuclear connectivity...
docker compose exec tailscale ping -c 1 8.8.8.8 >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Nuclear recovery failed - manual intervention required
    echo [INFO] Try: docker system prune -f && docker compose build --no-cache
    pause
    exit /b 1
) else (
    echo [SUCCESS] Nuclear recovery successful!
)

:verify_services
echo.
echo [INFO] Verifying services...
echo [INFO] llama-cpp-upstream status:
docker compose exec llama-cpp-upstream curl -s http://localhost:8080/health

echo.
echo [INFO] llama-cpp-embed-upstream status:
docker compose exec llama-cpp-embed-upstream curl -s http://localhost:8080/health

echo.
echo [INFO] Tailscale status:
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock status

echo.
echo [INFO] Tailscale serve configuration:
docker compose exec tailscale tailscale --socket=/tmp/tailscaled.sock serve status

echo.
echo [INFO] OpenWebUI GPU status:
docker compose exec openwebui python -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>nul

echo.
echo [INFO] Mnemory status:
docker compose exec mnemory python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8051/health').read().decode())" 2>nul

echo.
echo [INFO] SmolCrawl Pipelines status:
docker compose exec smolcrawl-pipelines curl -s http://localhost:9099/ 2>nul

echo.
echo [INFO] open-notebook API status:
docker compose exec open_notebook python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:5055/api/config').read().decode())" 2>nul

echo.
echo [INFO] Private Search Gateway status:
docker compose exec gateway curl -s http://localhost:8080/healthz 2>nul

echo.
echo [INFO] open-terminal status:
docker compose exec open-terminal curl -s http://localhost:8000/health 2>nul

echo.
echo [INFO] little-coder daemon status:
docker compose exec little-coder curl -s http://localhost:8090/health 2>nul

echo.
echo [INFO] surrealdb running state:
docker compose ps surrealdb --format "table {{.Service}}\t{{.Status}}" 2>nul

echo.
echo [INFO] Memory + coder plane status:
docker compose ps mnemory-cloud-gateway lc-egress --format "table {{.Service}}\t{{.Status}}" 2>nul

echo.
echo [INFO] Backup schedulers status:
docker compose ps mnemory-backup openwebui-backup little-coder-backup smolcrawl-backup tailscale-backup lm-models-backup open-notebook-backup openbrain-db-backup openbrain-wiki-backup --format "table {{.Service}}\t{{.Status}}" 2>nul

echo.
echo [INFO] Open Brain (OB1) status:
if exist "%OB1_COMPOSE%" (
    docker compose -f "%OB1_COMPOSE%" ps --format "table {{.Service}}\t{{.Status}}" 2>nul
) else (
    echo [INFO] Open Brain (OB1) not deployed in this workspace
)

echo.
echo [INFO] agent-org status:
if exist "%AGENTORG_COMPOSE%" (
    docker compose -f "%AGENTORG_COMPOSE%" ps --format "table {{.Service}}\t{{.Status}}" 2>nul
) else (
    echo [INFO] agent-org not deployed in this workspace
)

echo.
echo ========================================
echo EMERGENCY RECOVERY COMPLETED
echo ========================================
echo [INFO] For advanced recovery options, use: emergency-recovery.ps1
pause
