# .agent — Code Agent Project Context

This folder is automatically loaded by the Code Agent pipe at the start of every conversation. It provides persistent project context and reusable skills.

## Structure

```
.agent/
  README.md          ← You are here
  context.md         ← Auto-updated: what was last done, current state
  skills/            ← Reusable instruction files loaded into system prompt
    clean-code.md    ← SOLID, naming conventions, clean code principles
    README.md        ← How to create new skills
```

## How It Works

1. **On conversation start**: The agent reads `context.md` and all `skills/*.md` files
2. **During work**: The agent follows skill instructions alongside its core system prompt
3. **On conversation end**: The agent updates `context.md` with what was accomplished
4. **Self-creating**: The agent can create new skills by writing to `skills/new-skill.md`

## context.md

Auto-maintained by the agent. Contains:

- What was last worked on
- Current project state and file structure
- Known issues or TODOs
- Key decisions made

## Skills

Markdown files in `skills/` that augment the agent's behavior. Each skill should:

- Have a clear `# Title` header
- Describe **when** it applies
- List concrete rules or patterns to follow
- Be concise — bullet points, not essays

The agent can create new skills when it discovers reusable patterns.
