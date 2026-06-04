---
name: code-review
description: Review the current git diff for bugs, correctness, and cleanup opportunities
shell: git diff HEAD 2>&1
---
Review the following diff for:

1. **Correctness bugs** — logic errors, off-by-ones, null/undefined, type mismatches, race conditions
2. **Security issues** — injection, XSS, hardcoded secrets, insecure defaults
3. **Simplification** — dead code, duplicate logic, unnecessary complexity
4. **Missing edge cases** — inputs not handled, error paths not covered

Diff to review:

```
$SHELL_OUTPUT
```

For each finding: state the file and line, describe the issue, and suggest a fix.
If no issues found, say "LGTM" with a brief summary of what was reviewed.

$ARGUMENTS
