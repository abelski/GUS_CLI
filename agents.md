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

When the user asks to "create a command" (or "add a command", "make a slash command"), they mean a `.gus/commands/<name>.md` file.

**Process — always follow this:**
1. **Brainstorm interactively**: ask the user what the command should do, what arguments it takes, whether it needs a shell pre-step, and whether it should ask for confirmation before running.
2. **Draft the frontmatter and prompt body** together with the user before writing.
3. **Write the file** to `.gus/commands/<name>.md` (relative to `cwd`) using `write_file` only once the user confirms the draft. After writing, read the file back to confirm it exists and looks correct.

Command file format:
```markdown
---
description: One-line description shown in /help
shell: <optional shell command; output available as $SHELL_OUTPUT>
confirm: true   # optional — prompt user before executing
max_iterations: 5  # optional — cap for /loop usage
---
Prompt body. Use $ARGUMENTS for text after the command name.
```

The file stem (filename without `.md`) becomes the slash command name.
