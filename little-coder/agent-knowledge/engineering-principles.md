# Engineering principles

Apply these to all code you write or modify, in any language. They are not
optional polish — they are how code survives review and change.

## SOLID

- **Single Responsibility** — each function / class / module does ONE thing.
  If describing it needs the word "and", split it.
- **Open/Closed** — extend behaviour by ADDING code (a new function,
  parameter, or subtype), not by editing code that already works.
- **Liskov Substitution** — a subtype must honour its base type's contract.
  Never override a method just to throw "not supported".
- **Interface Segregation** — keep interfaces small and focused. Don't force
  a caller to depend on methods it never uses.
- **Dependency Inversion** — depend on abstractions. Pass dependencies in
  (constructor / parameters); don't construct them deep inside.

## Encapsulation

- Hide implementation detail; expose the minimum surface.
- Private by default — make something public only when a caller needs it.
- Prefer immutability (`const` / `final` / `readonly`); mutate only when you
  must.
- Keep data and the behaviour that owns it together — avoid a "data bag"
  plus a separate "manager" class.

## Naming & readability

- Variables — descriptive nouns that reveal intent: `userCount`, not `n`.
- Functions — verb phrases: `calculateTotal()`, not `doStuff()`.
- Booleans — `is` / `has` / `can` / `should` prefixes: `isValid`.
- Constants — `UPPER_SNAKE_CASE`, meaningful: `MAX_RETRIES`, not `MAX`.
- No magic numbers — name them.
- Early returns over deep nesting; guard clauses first.
- Functions short (~20 lines); ≤ 3 parameters (use an options object beyond).
- Self-documenting code — if a comment explains *what*, rename instead.
  Comments explain *why*.

## Patterns & standardization

- Use a well-known pattern when it genuinely fits — Strategy for swappable
  algorithms, Adapter at a boundary, Factory when construction is complex —
  and name it so the next reader recognizes the shape.
- Do NOT reach for a pattern speculatively. A pattern that doesn't match the
  problem is worse than no pattern.
- Match the codebase you are in. Its existing conventions, structure, and
  style outrank your personal preference — consistency is a feature.

## DRY, YAGNI, errors

- DRY — extract on the *third* occurrence, not the first. Premature
  abstraction is worse than a little duplication.
- YAGNI — build only what the task needs now. Delete dead code.
- Errors — validate inputs at boundaries; catch specific exceptions, never a
  bare catch; an error message states what failed, what was expected, and
  how to fix it.

## Verify

Write testable code. Test behaviour, not implementation. After a change, run
the relevant test or a syntax check before claiming it works.
