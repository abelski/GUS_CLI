# GUS 🦆

A terminal-based autonomous coding agent powered by [OpenRouter](https://openrouter.ai).

GUS chains tools end-to-end — reading, writing, editing files, running shell commands, searching the web — without stopping to ask questions. Designed for repeatable, automated tasks that run on a schedule or in a loop.

## Features

- **Autonomous tool-chaining** — reads, edits, runs, verifies in a single turn
- **Custom commands** — define slash commands in `.gus/commands/*.md`
- **Loop & routines** — `/loop 3` (fixed iterations), `/loop 1h` (hourly), `/loop every` (every prompt)
- **Plan / Agent modes** — `/plan` to analyse, `/go` to execute
- **Sub-agents** — `spawn_agent` tool for parallel workstreams
- **Monitor tool** — watch folders/files or poll a shell condition until an event fires
- **Compact** — `/compact` summarises conversation to free context window
- **Sandbox** — tools are restricted to the project working directory

## Setup

**1. Clone**
```bash
git clone https://github.com/<you>/gus.git
cd gus
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Add your OpenRouter API key**

Create a `.env` file (or let GUS create it on first run):
```
OPENROUTER_API_KEY=sk-or-v1-...
```

Get a key at [openrouter.ai/keys](https://openrouter.ai/keys). Free models are available.

**4. Run**
```bash
python src/main.py
```

On macOS you can also double-click `run.command`.

## Slash commands

| Command | Description |
|---|---|
| `/help` | Show all commands |
| `/plan [task]` | Switch to read-only planning mode |
| `/go` | Execute the current plan |
| `/agent` | Return to normal agent mode |
| `/loop N` | Run next prompt N times |
| `/loop 1h` / `/loop 30m` | Repeat every N hours/minutes |
| `/loop every` | Run before every prompt |
| `/loop list` | Show active routines |
| `/loop stop [id]` | Stop a routine |
| `/compact` | Summarise and compress history |
| `/clear` | Clear conversation history |
| `/cwd` | Show working directory |
| `/exit` | Exit GUS |

## Custom commands

Create `.gus/commands/yourcommand.md`:

```markdown
---
description: Short description shown in /help
shell: git status --short   # optional pre-step
---
$ARGUMENTS refers to anything typed after the command.
$SHELL_OUTPUT is the output of the shell pre-step.
Describe what GUS should do here.
```

Use it with `/yourcommand [arguments]`.

## Project instructions

Create `agents.md` in the project root. GUS reads it at startup and includes it in every system prompt.

## Tools

| Tool | Description |
|---|---|
| `read_file` | Read file contents |
| `write_file` | Create or overwrite a file |
| `edit_file` | Targeted string replacement |
| `bash` | Run a shell command |
| `glob` | Find files by pattern |
| `grep` | Search file contents |
| `list_dir` | List directory |
| `web_search` | DuckDuckGo search |
| `spawn_agent` | Launch a sub-agent for a parallel task |
| `monitor` | Watch a path or poll a condition until an event |
