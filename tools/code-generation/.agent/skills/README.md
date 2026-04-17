# Skills Directory

Skills are reusable instruction files that the Code Agent loads automatically.

## Creating a New Skill

1. Create a `.md` file in this directory (e.g., `react-patterns.md`)
2. Start with a `# Title` header
3. Add an `## Applies When` section describing when the skill is relevant
4. List concrete rules, patterns, or examples
5. Keep it concise — the agent loads ALL skills into context

## Example Skill Template

```markdown
# React Component Patterns

## Applies When

Working with React components (.jsx, .tsx files)

## Rules

- Use functional components with hooks, not class components
- Extract custom hooks for reusable logic
- Co-locate styles with components
- Name components with PascalCase, hooks with use\* prefix
```

## Built-in Skills

- `clean-code.md` — SOLID principles, naming conventions, clean code
