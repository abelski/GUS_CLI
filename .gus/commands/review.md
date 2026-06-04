---
name: review
description: Review a pull request or branch — fetch PR details and give a thorough code review
shell: git log --oneline main..HEAD 2>/dev/null | head -20; echo "---"; git diff main...HEAD --stat 2>/dev/null | head -40
---
Review the following branch changes:

$SHELL_OUTPUT

$ARGUMENTS

Do a thorough pull request review:
1. Understand the purpose of the change from the commit messages
2. Read the changed files with read_file to understand the full context
3. Evaluate: correctness, test coverage, API design, backwards compatibility, documentation
4. Check for bugs, security issues, and performance concerns
5. Write a structured review with: Summary, What's good, Issues (blocking / non-blocking), and Suggestions
