---
name: feature-validation-workflow
description: Step-by-step workflow for validating features against a GitHub repository using GitHub Repo Analyzer, Superpowers delegate_analysis, and FileShed for context management
---

# Feature Validation Workflow

When a user provides a list of features, requirements, or specifications and asks you to validate their implementation in a GitHub repository, follow this workflow. It prevents context overflow and ensures every item gets checked.

## Tools Required

- **GitHub Repo Analyzer** — `get_repo_overview`, `bulk_read_files`, `validate_features`, `search_repo_code`, `get_repo_file`
- **Superpowers** — `delegate_analysis` (for heavy multi-phase reasoning via sub-agents)
- **FileShed** — `shed_create_file`, `shed_exec` (for storing/retrieving intermediate results)

## Workflow

### Step 1: Get the big picture

```
get_repo_overview(repo_url="https://github.com/owner/repo")
```

Skim the file tree and README to understand the project structure. Do NOT start reading individual files yet.

### Step 2: Read key architecture files in bulk

If the overview reveals important config or architecture files (e.g. copilot-instructions.md, ARCHITECTURE.md, package.json, tsconfig.json), read them all at once:

```
bulk_read_files(
    repo_url="https://github.com/owner/repo",
    file_paths=".github/copilot-instructions.md, package.json, tsconfig.json, src/index.ts"
)
```

This is far more efficient than reading files one-by-one. Up to 20 files per call.

### Step 2: Batch-validate all features in ONE call

```
validate_features(
    repo_url="https://github.com/owner/repo",
    features=[
        "JWT authentication",
        "Rate limiting middleware",
        "WebSocket real-time updates",
        "Database migration system",
        "Role-based access control"
    ],
    max_files_per_feature=3
)
```

This returns a structured report with evidence (code snippets + file paths) for every feature. One tool call covers the entire list — no incremental searching.

### Step 3: Store the report in FileShed if it's large

If the validation report is large (many features or lots of evidence), store it immediately to free context:

```
shed_create_file(
    zone="storage",
    path="validation-reports/repo-name-validation.md",
    content="<paste the full validation report here>"
)
```

Then confirm to the user: "I've gathered evidence for all N features. Let me analyze each one."

### Step 4: Delegate heavy analysis to Superpowers (recommended for 10+ features)

For complex analysis with many features, use `delegate_analysis` to break the work into sub-agent phases. Pass ALL gathered data (overview + bulk files + validation report) as context:

```
delegate_analysis(
    context_data="<paste ALL gathered data: overview, architecture files, validation report>",
    analysis_instructions="For each feature in the list, determine implementation status: Implemented, Partially Implemented, or Missing. Cite specific files, classes, and functions as evidence. Flag any TODOs or incomplete implementations.",
    chunk_count=3,
    output_label="feature-validation-myrepo"
)
```

This breaks the analysis into 3 sub-agent phases, synthesizes results, and saves the final report to Fileshed automatically. Use this instead of Steps 4-5 below when dealing with heavy workloads.

### Step 5: Analyze manually in chunks (alternative to Step 4)

If you stored the report, retrieve sections as needed:

```
shed_exec(zone="storage", cmd="sed", args=["-n", "1,50p", "validation-reports/repo-name-validation.md"])
```

Or use grep to find specific features:

```
shed_exec(zone="storage", cmd="grep", args=["-A", "20", "## 3. WebSocket", "validation-reports/repo-name-validation.md"])
```

### Step 6: Drill deeper if needed

For features marked "NOT FOUND" or where evidence is ambiguous, use targeted tools:

```
search_repo_code(repo_url="...", query="specific_function_name")
get_repo_file(repo_url="...", file_path="src/auth/jwt.py")
```

Store any additional findings:

```
shed_patch_text(
    zone="storage",
    path="validation-reports/repo-name-validation.md",
    content="\n\n## Additional findings for Feature 3\n...",
    position="end"
)
```

### Step 7: Present the final report

Deliver a clear, structured response:

```
## Feature Validation Summary

| # | Feature | Status | Evidence |
|----|---------|--------|----------|
| 1 | JWT authentication | ✅ Implemented | Found in src/auth/jwt.py |
| 2 | Rate limiting | ✅ Implemented | middleware/rate_limit.py |
| 3 | WebSocket support | ❌ Not found | No WebSocket code detected |
| 4 | DB migrations | ✅ Implemented | Alembic in migrations/ |
| 5 | RBAC | ⚠️ Partial | Roles defined but not enforced on all routes |

### Detailed findings
(expand each feature with evidence and analysis)
```

## Key Rules

1. **Never search feature-by-feature.** Use `validate_features` to batch all features in one call.
2. **Use `bulk_read_files` for multi-file reads.** Never call `get_repo_file` more than twice in a row.
3. **Delegate heavy analysis to Superpowers.** For 10+ features or complex specs, use `delegate_analysis` — it breaks the work into sub-agent phases automatically.
4. **Store big results in FileShed immediately.** Don't try to hold a 50-feature report in context.
5. **Retrieve selectively.** Use `sed -n` or `grep -A` to pull back only the section you're analyzing.
6. **Drill deeper only for uncertain items.** Don't re-examine features that are clearly found or clearly missing.
7. **Always present a summary table.** Users want a quick overview before the details.

## Handling Large Feature Lists (20+ items)

For very large specs, split into batches:

```
# Batch 1: features 1-15
validate_features(repo_url="...", features=[...first 15...], max_files_per_feature=2)
# Store in FileShed
shed_create_file(zone="storage", path="validation-reports/batch1.md", content="...")

# Batch 2: features 16-30
validate_features(repo_url="...", features=[...next 15...], max_files_per_feature=2)
shed_create_file(zone="storage", path="validation-reports/batch2.md", content="...")

# Combine results
shed_exec(zone="storage", cmd="cat",
    args=["validation-reports/batch1.md", "validation-reports/batch2.md"],
    stdout_file="validation-reports/full-report.md")
```

## Common Pitfalls

- **Don't read every file individually.** Use `bulk_read_files` (up to 20 files per call) and `validate_features` (batch search + read). That's what causes model stalling.
- **Don't keep large evidence in your working context.** Push to FileShed and retrieve excerpts.
- **Don't skip the overview step.** The file tree from `get_repo_overview` helps you understand what's where before diving in.
- **Don't manually reason about 10+ features in one pass.** Use `delegate_analysis` to split into sub-agent phases — each phase gets focused context and returns structured results.
