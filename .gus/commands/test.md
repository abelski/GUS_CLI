---
name: test
description: Run tests, fix any failures, loop until green
shell: python -m pytest --tb=short -q 2>&1 || echo "(no pytest found)"
max_iterations: 5
---
Test output:

$SHELL_OUTPUT

If all tests pass, say so and stop. Otherwise fix every failing test, then I will re-run them. $ARGUMENTS
