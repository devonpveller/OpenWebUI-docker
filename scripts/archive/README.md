# scripts/archive/

Retired operational code, kept for history and provenance. Nothing in this
directory is referenced by any live path — compose files, scheduled tasks,
hooks, recovery scripts, or modules. Each subdirectory notes what retired it.

| Set | Retired | Why / replaced by |
|---|---|---|
| `lmstudio/` | 2026-08-20 | LM Studio fully retired as an inference target. The 0.11.0 upgrade removed both OWUI connections (`update-owui-to-0-11-0/UPGRADE-PLAN.md`); all inference runs through the LiteLLM gateway → llama.cpp. The four `169.254.83.107` scripts had been dead since 2025; `lmstudio_fix_v2.py` lost its last caller with the retirement. |

Convention: archive (`git mv`) — don't delete — anything with history; explain
the retirement in the commit message and in this table.
