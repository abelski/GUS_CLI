---
name: commit
description: Stage all changes and write a commit message
shell: git diff --stat HEAD 2>&1; git status --short 2>&1
confirm: true
---
Here is the current git diff summary:

$SHELL_OUTPUT

Write a concise conventional-commit message for these changes and run:
  git add -A && git commit -m "<message>"

$ARGUMENTS
