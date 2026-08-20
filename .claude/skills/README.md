# .claude/skills — provenance

Two kinds of skills live here (established 2026-08-20, CLEANUP-PLAN v3 D-11):

**ai-stack-native** (authored here, edit freely):
`stack-map`, `validate-before-change`, `agent-org-floor`, `agent-org-worker`,
`agent-org-reviewer`.

**Deployed copies of `OB1/skills/`** (the other 15: auto-capture,
autodream-brain-sync, claudeception, competitive-analysis, deal-memo-drafting,
financial-model-review, heavy-file-ingestion, meeting-synthesis,
n-agentic-harnesses, openclaw-agent-memory, panning-for-gold,
research-synthesis, weekly-signal-diff, work-operating-model,
world-model-diagnostic). Claude Code only loads skills from this directory —
`OB1/skills/` is not a skill search path — so these copies are what makes them
usable in ai-stack sessions. **Treat `OB1/skills/` as upstream**: don't edit
the copies here; after an OB1 pull, re-sync and review the diff:

```powershell
robocopy OB1\skills .claude\skills /E /XD stack-map validate-before-change agent-org-floor agent-org-worker agent-org-reviewer /L   # /L = list drift only
```

(Drop `/L` to apply. Review before committing — these files execute as
instructions in every session that triggers them.)
