# GUS 🦆

**G**eneral-purpose **U**tility **S**hell — a terminal coding agent powered by [OpenRouter](https://openrouter.ai).

GUS chains tools end-to-end to complete tasks autonomously: reads files, edits code, runs shell commands, searches the web — without stopping to ask questions mid-task. Built for repeatable, scheduled, or looped workflows where no human is present.

---

## Quick start

**Requirements:** Python 3.10+

```bash
git clone https://github.com/abelski/Agent.git
cd Agent
pip install -r requirements.txt
python src/main.py
```

On first run GUS will ask for an [OpenRouter API key](https://openrouter.ai/keys) and save it to `.env`. Free models are available.

On macOS you can also double-click `run.command` to open a terminal session.

---

## How it works

1. You type a task at the `>` prompt
2. GUS calls tools in a loop (read → edit → verify → report) until the task is done
3. Results are printed inline; GUS returns to the prompt when finished

Press **Ctrl+C** at any time to interrupt and return to the prompt immediately.

---

## Slash commands

| Command | What it does |
|---|---|
| `/help` | List all built-in and custom commands |
| `/plan [task]` | Switch to read-only planning mode — analyses without changing anything |
| `/go` | Execute the plan produced by `/plan` |
| `/agent` | Return to normal agent mode |
| `/compact` | Summarise conversation history to free up context |
| `/clear` | Wipe conversation history |
| `/cwd` | Show current working directory |
| `/loop N` | Repeat the next prompt N times |
| `/loop 30m` / `/loop 1h` / `/loop 1d` | Run a prompt on a recurring schedule |
| `/loop every` | Run a prompt before every user message |
| `/loop list` | Show active routines |
| `/loop stop [id]` | Stop a routine by ID |
| `/exit` | Exit GUS |

---

## Custom commands

Add a Markdown file to `.gus/commands/` and GUS will expose it as a slash command.

**`.gus/commands/deploy.md`**
```markdown
---
description: Build and deploy to staging
shell: git diff --stat HEAD
confirm: true
max_iterations: 10
---
Here is the current diff:

$SHELL_OUTPUT

Build the project, run tests, and deploy to staging. $ARGUMENTS
```

| Frontmatter field | Description |
|---|---|
| `description` | Shown in `/help` |
| `shell` | Shell command to run before the prompt; output is injected as `$SHELL_OUTPUT` |
| `confirm` | If `true`, ask the user before executing |
| `max_iterations` | Cap the tool-use loop for this command |

In the prompt body:
- `$ARGUMENTS` — text typed after the command name
- `$SHELL_OUTPUT` — stdout/stderr from the `shell` pre-step

---

## Project instructions

Create `agents.md` in the project root. GUS reads it at startup and injects it into every system prompt — use it to set coding style, project conventions, or domain context.

```markdown
# My Project

## Rules
- Use Python 3.11+ syntax
- All public functions need docstrings
- Tests live in tests/ and use pytest
```

---

## Tools

| Tool | Description |
|---|---|
| `read_file` | Read a file from disk |
| `write_file` | Create or overwrite a file |
| `edit_file` | Targeted string replacement in an existing file |
| `bash` | Run a shell command (Ctrl+C kills the subprocess) |
| `glob` | Find files by pattern |
| `grep` | Search file contents |
| `list_dir` | List a directory |
| `web_search` | DuckDuckGo full-text search |
| `spawn_agent` | Launch a sub-agent to handle an independent workstream in parallel |
| `monitor` | Block until a filesystem event or shell condition is met; Ctrl+C interrupts |

---

## Project layout

```
Agent/
├── src/
│   ├── main.py          # REPL entry point
│   ├── agent.py         # Streaming tool-use loop
│   ├── ui.py            # Rich terminal output
│   ├── config.py        # API client, model config
│   ├── loop.py          # Routine scheduler
│   ├── context.py       # agents.md + custom command loader
│   └── tools/           # One file per tool
│       ├── bash.py
│       ├── monitor.py
│       ├── spawn_agent.py
│       └── ...
├── .gus/
│   └── commands/        # Custom slash commands (*.md)
├── agents.md            # Project-level instructions for GUS
├── requirements.txt
└── run.command          # macOS click-to-run
```

---

## Configuration

| File | Purpose |
|---|---|
| `.env` | `OPENROUTER_API_KEY=sk-or-v1-...` |
| `agents.md` | Project instructions injected into every prompt |
| `.gus/commands/*.md` | Custom slash commands |

GUS is sandboxed to its working directory — tools cannot read or write files outside it.

---

## License

MIT
