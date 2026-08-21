# Clean Code & Software Engineering Principles

## Always Applies

These principles apply to ALL code you write or modify, in any language.

## SOLID Principles

- **Single Responsibility**: Each function/class/module does ONE thing well. If you can't describe what it does in one sentence without "and", split it.
- **Open/Closed**: Design for extension without modification. Use interfaces, callbacks, configuration — not editing existing working code to add features.
- **Liskov Substitution**: Subtypes must be substitutable for their base types. Don't override methods to throw "not supported" — that violates the contract.
- **Interface Segregation**: Don't force consumers to depend on methods they don't use. Prefer small, focused interfaces over large ones.
- **Dependency Inversion**: Depend on abstractions, not concretions. Pass dependencies in (constructor, function params) rather than creating them internally.

## Naming Conventions

- **Variables**: Descriptive nouns that reveal intent. `userCount` not `n`, `isVisible` not `flag`, `maxRetries` not `num`.
- **Functions**: Verb phrases that describe the action. `calculateTotal()` not `doStuff()`, `validateEmail()` not `check()`.
- **Booleans**: Prefix with `is`, `has`, `can`, `should`. `isValid` not `valid`, `hasPermission` not `permission`.
- **Constants**: UPPER_SNAKE_CASE with meaningful names. `MAX_RETRY_COUNT` not `MAX` or `CONST_3`.
- **Classes**: PascalCase nouns. `UserRepository` not `UR` or `UserRepoManagerHelper`.
- **Files**: Match the primary export. `user-service.js` contains `UserService`.
- **No abbreviations** unless universally understood (`id`, `url`, `html`). Write `configuration` not `cfg`, `message` not `msg`.

## Code Readability

- **Functions under 20 lines** — if longer, extract sub-functions with descriptive names.
- **Max 3 parameters** per function. Use an options/config object for more.
- **No magic numbers** — use named constants. `if (attempts > MAX_RETRIES)` not `if (attempts > 3)`.
- **Early returns** over deep nesting. Guard clauses at the top, happy path at the bottom.
- **Consistent formatting** — match the existing project style exactly.
- **Self-documenting code** — if you need a comment to explain _what_ the code does, rename things instead. Comments explain _why_, not _what_.

## Encapsulation

- **Hide implementation details** — expose only what consumers need.
- **Private by default** — make fields/methods public only when there's a reason.
- **Immutable when possible** — prefer `const`/`readonly`/`final`. Mutate only when necessary.
- **Data + behavior together** — don't create "data bags" with separate "manager" classes. Put behavior on the objects that own the data.

## Error Handling

- **Fail fast** — validate inputs at boundaries, not deep inside.
- **Specific errors** — catch specific exceptions, not bare `catch`. Never swallow errors silently.
- **Error messages that help** — include what went wrong, what was expected, and how to fix it.
- **No error handling for impossible states** — if your types prevent it, don't check for it.

## DRY (Don't Repeat Yourself)

- **Extract on the third occurrence** — first time: write it. Second time: note the duplication. Third time: extract.
- **Don't over-abstract** — premature abstraction is worse than duplication. Wait for patterns to emerge.
- **Shared logic in utilities** — but only when truly shared. Don't create `utils.js` dumping grounds.

## YAGNI (You Aren't Gonna Need It)

- **Build what's needed now** — don't add features "just in case".
- **No speculative generalization** — if there's only one implementation, you don't need an interface.
- **Delete dead code** — commented-out code and unused functions are noise.

## Testing Mindset

- **Write testable code** — if it's hard to test, the design needs improvement.
- **Test behavior, not implementation** — tests should pass even if internals change.
- **One assert per concept** — each test verifies one thing.
- **Descriptive test names** — `test_expired_coupon_returns_zero_discount` not `test_coupon_3`.
