---
name: diff
description: Show uncommitted git changes and summarise what changed
shell: git diff --stat HEAD 2>&1; echo "---"; git diff HEAD 2>&1 | head -200
---
Here is the current git diff:

$SHELL_OUTPUT

Summarise the changes: which files were modified, what was added or removed, and whether anything looks risky or incomplete. If there is nothing changed, say so.

$ARGUMENTS
