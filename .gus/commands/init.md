---
name: init
description: Initialise agents.md with project instructions for GUS
---
Analyse this project and create an `agents.md` file in the project root.

Steps:
1. Use glob to discover the project structure (source files, config, tests, docs)
2. Read key files: package.json / pyproject.toml / Makefile / README.md / existing agents.md
3. Understand: language, framework, test runner, build system, coding conventions

Then write `agents.md` with these sections:
- **Project overview** — what it is and what it does
- **Tech stack** — language, frameworks, key dependencies
- **Project structure** — key directories and their purpose
- **How to run** — dev server, tests, build commands
- **Coding conventions** — style, patterns, things to avoid
- **GUS guidance** — task types this agent will help with, important constraints

Keep it concise (under 300 lines). This file is injected into every GUS prompt, so make it actionable.

$ARGUMENTS
