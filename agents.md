# GUS

This is a Python CLI coding assistant powered by OpenRouter.

## Behaviour
- Always prefer Python 3.10+ syntax (match statements, `X | Y` unions, walrus operator where appropriate).
- Keep responses concise — one short paragraph max, unless writing code.
- When writing files, use 4-space indentation.

## Creating skills

When the user asks to "create a skill" (or "add a skill", "make a skill"), they mean the **agentskills.io** format:

1. Create `.gus/skills/<skill-name>/SKILL.md` (project-level) or `~/.gus/skills/<skill-name>/SKILL.md` (global).
2. The file must have YAML frontmatter with at minimum `name` and `description`, followed by step-by-step instructions:

```markdown
---
name: skill-name
description: When and why the agent should use this skill.
---
Step-by-step instructions the agent follows when this skill is activated.
```

3. Optional frontmatter fields: `license`, `compatibility`, `metadata`, `allowed-tools`.
4. Extra helper files (`scripts/`, `references/`, `assets/`) can live alongside `SKILL.md` in the same folder.

Never use any other format or location when creating skills.

## Creating commands

When the user asks to "create a command", "add a command", "make a slash command", or "create a command that/for/to …", they **always** mean a GUS slash command — a `.gus/commands/<name>.md` file. Never create a shell script or Python script as the output.

**Process — always follow ALL steps in order:**

**Step 1 — Plan (think before writing)**
Before touching any file, reason through the command design out loud in your response:
- What is the exact purpose? What should it reliably accomplish?
- Best command name (kebab-case, short, memorable)?
- Does it need a `shell:` pre-step to gather live context? If yes — what exact shell command?
- What arguments (`$ARGUMENTS`) should the user be able to pass?
- Write a detailed, specific prompt body — precise enough to produce consistent results every time.
  Include: goal, step-by-step agent instructions, output format, success criteria.
- Should it require `confirm: true` (for destructive or irreversible actions)?

**Step 2 — Write**
Write `.gus/commands/<name>.md` using `write_file`.

```markdown
---
description: One-line description shown in /help
shell: <optional shell command; output available as $SHELL_OUTPUT>
confirm: true   # optional
max_iterations: 5  # optional
---
Prompt body. Use $ARGUMENTS for user input. Use $SHELL_OUTPUT if a shell pre-step is defined.
```

**Step 3 — Verify**
Read the file back. Tell the user: "Created `/name` — type `/name [args]` to run it."
