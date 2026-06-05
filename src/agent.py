"""Core agent loop — streaming tool-use with OpenRouter."""
import concurrent.futures
import json
import platform
import time

from openai import (
    OpenAI,
    RateLimitError,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
)

import ui
from config import (
    MAX_TOKENS,
    FREE_MODEL_FALLBACKS,
    MAX_ITERATIONS,
    COMPACT_THRESHOLD_TOKENS,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
    MAX_TOOL_WORKERS,
    setup_logging,
)
from tools import TOOL_SCHEMAS, execute_tool, spawn_agent
from tools._exceptions import ToolInterrupted

log = setup_logging()

# Errors worth retrying with backoff (transient: network blips, 5xx, timeouts).
_TRANSIENT_ERRORS = (APIConnectionError, APITimeoutError, InternalServerError)

_BASE_SYSTEM_PROMPT = """\
You are GUS, a powerful autonomous assistant running in a terminal.
You have access to tools to read, write, and edit files, run shell commands, search codebases, and search the web.

## Autonomy — primary directive
Your main use case is repeatable, automated tasks that run on a schedule or in a loop with no human present.
- Always prefer completing tasks end-to-end using tools rather than asking the user for input.
- When something is ambiguous, make a reasonable decision, act on it, and document what you assumed.
- Never stop mid-task to ask a clarifying question unless the action is irreversible and the risk is high.
- Chain tool calls freely: read → analyse → edit → verify → report in a single turn.
- If a task fails, diagnose and retry with a different approach before reporting the error.
- Prefer writing scripts or shell commands that can be re-run over one-off manual steps.

## Tool use
- Use tools for everything — do not describe what you would do, just do it.
- Before editing a file you have not read in this session, read it first.
- Prefer targeted edits (edit_file) over full rewrites (write_file) for existing files.
- After making changes, verify them (re-read the file, run tests, check output) before reporting done.
- When a task involves multiple files or steps, spawn_agent sub-agents to handle independent workstreams.

## Agent Skills — on-demand discovery
Skills listed under "Available Agent Skills" are those known at startup.
New skills may be added during a session (e.g. after skill-creation runs).
At any point you can scan for available skills by calling list_dir on `.gus/skills/`
and `~/.gus/skills/`, then read_file any SKILL.md you find there.
After loading a SKILL.md, follow its instructions as if the skill had been listed at startup.
This lets you expand your own capabilities mid-conversation without restarting.

## Sandbox — strict file-creation rule
- You are strictly sandboxed to the working directory. NEVER create, write, or move files to any path outside it.
- ALL new files and directories must be created inside the working directory or its subdirectories.
- When running bash commands, use relative paths. Never use absolute paths that point outside the working directory.
- Redirections (`>`, `>>`), `mkdir`, `touch`, `tee`, `cp`, and `mv` that target paths outside the working directory are blocked by the sandbox and will return an error.
- If you need a temporary file, create it inside the working directory (e.g. `./tmp/`).

## Creating commands
When the user asks to "create a command", "add a command", "make a slash command", or
"create a command that/for/to …", they always mean a GUS slash command — a `.gus/commands/<name>.md` file.
Never create a shell script or Python script as the output.

Process — always follow ALL steps in order:

**Step 1 — Plan (think before writing)**
Before touching any file, reason through the command design out loud in your response:
- What is the exact purpose of this command? What should it reliably accomplish?
- What is the best command name (kebab-case, short, memorable)?
- Does it need a `shell:` pre-step to gather live context (e.g. git diff, file list, API output)?
  If yes — what exact shell command? What output does it produce and how will the prompt use it?
- What arguments ($ARGUMENTS) should the user be able to pass?
- Write a detailed, specific prompt body — not vague instructions. The prompt is what GUS receives
  at runtime, so it must be precise enough to produce consistent, high-quality results every time.
  Include: goal, step-by-step instructions for the agent, output format, success criteria.
- Should it require `confirm: true` before running (for destructive or irreversible actions)?

**Step 2 — Write**
Write the final `.gus/commands/<name>.md` file using write_file.

Command file format:
```
---
description: One-line description shown in /help
shell: <optional shell command; output available as $SHELL_OUTPUT>
confirm: true   # optional — prompt user before running
max_iterations: 5  # optional — cap for /loop usage
---
Prompt body. Use $ARGUMENTS for user input. Use $SHELL_OUTPUT if a shell pre-step is defined.
```

**Step 3 — Verify**
Read the file back. Confirm it was written correctly.
Tell the user: "Created `/name` — type `/name [args]` to run it."

## Creating skills
When the user asks to "create a skill", "add a skill", or "make a skill", they mean the agentskills.io format.
Create `.gus/skills/<skill-name>/SKILL.md` with YAML frontmatter (`name`, `description`) and a step-by-step body.
Never use any other format or location.

## Output
- Be concise. Explain what you did and what changed, not what you are about to do.
- End every completed task with a one-line summary of the outcome.
"""

_PLAN_MODE_ADDITION = """
## Current Mode: PLAN
You are in planning mode. Hard rules:
- You may ONLY use read-only tools: read_file, glob, grep, list_dir, web_search
- Do NOT call write_file, edit_file, or bash under any circumstances
- Analyse the codebase and produce a clear, numbered, step-by-step execution plan
- Be specific: name exact files, line ranges, and changes you would make
- End your response with exactly: "---\\nPlan ready. Use /go to execute."
"""


def _platform_note() -> str:
    system = platform.system()
    if system == "Windows":
        return (
            "\n## Environment\n"
            "OS: Windows. Use Windows-native shell commands (dir, type, copy, del, etc.) "
            "instead of Unix commands (ls, cat, cp, rm). "
            "Use backslashes for paths or prefer forward slashes which Python accepts on Windows. "
            "Shell commands run in cmd.exe unless the user specifies PowerShell."
        )
    return f"\n## Environment\nOS: {system}."


def _build_system_prompt(extra_instructions: str = "", mode: str = "agent",
                          agent_skills: dict | None = None) -> str:
    prompt = _BASE_SYSTEM_PROMPT + _platform_note()
    if extra_instructions:
        prompt += "\n\n# Project Instructions\n" + extra_instructions
    if agent_skills:
        lines = [
            "## Available Agent Skills",
            "The following skills are available. "
            "Before starting any task, identify ALL skills relevant to it and read each one with read_file. "
            "A task may require multiple skills — load all of them, then combine their instructions.\n",
        ]
        for skill in agent_skills.values():
            lines.append(f"- **{skill.name}**: {skill.description}  \n  SKILL.md: `{skill.path}`")
        prompt += "\n\n" + "\n".join(lines)
    if mode == "plan":
        prompt += "\n\n" + _PLAN_MODE_ADDITION
    return prompt


class Agent:
    def __init__(self, client: OpenAI, model: str, cwd: str,
                 extra_instructions: str = "", mode: str = "agent",
                 agent_skills: dict | None = None) -> None:
        self.client        = client
        self.model         = model
        self.cwd           = cwd
        self.mode          = mode
        self._extra        = extra_instructions
        self._agent_skills = agent_skills or {}
        self.system_prompt = _build_system_prompt(extra_instructions, mode, self._agent_skills)
        self.history: list[dict] = []

        # session metadata
        self.session_name: str = ""
        self.goal: str | None  = None

        # last assistant text response (for /copy)
        self._last_response: str = ""

        # cumulative token usage
        self.total_input_tokens:  int = 0
        self.total_output_tokens: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_turns: int = 0

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.system_prompt = _build_system_prompt(self._extra, mode, self._agent_skills)

    def fork(self) -> "Agent":
        """Create a sibling agent sharing config but with its own empty history.

        Used for background routines so their turns never pollute (or get
        polluted by) the interactive conversation history.
        """
        return Agent(
            client=self.client, model=self.model, cwd=self.cwd,
            extra_instructions=self._extra, mode=self.mode,
            agent_skills=self._agent_skills,
        )

    def clear(self) -> None:
        self.history = []
        self._last_response = ""
        ui.print_info("Conversation history cleared.")

    def compact(self) -> tuple[str, int]:
        """
        Summarise the conversation history with a non-streaming call, then
        replace the full history with a single context-preserving summary.
        Returns (summary_text, old_message_count).
        """
        if not self.history:
            return "", 0

        messages = (
            [{"role": "system", "content": self.system_prompt}]
            + self.history
            + [{
                "role": "user",
                "content": (
                    "Summarise this conversation for a fresh context window. "
                    "Cover: goals, files read/changed, decisions made, current state, "
                    "open problems, and any key code snippets. Be thorough but concise."
                ),
            }]
        )

        response = None
        for model in self._models_to_try():
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=MAX_TOKENS,
                    stream=False,
                )
                break
            except Exception as e:
                log.warning("compact: error on %s (%s), trying next", model, e)

        if response is None:
            return "", 0

        summary    = response.choices[0].message.content or ""
        old_count  = len(self.history)
        self.history = [{
            "role": "assistant",
            "content": f"[Conversation compacted — summary]\n\n{summary}",
        }]
        log.info("compact: %d messages → 1 summary", old_count)
        return summary, old_count

    def btw(self, question: str) -> str:
        """Ask a side question using session context without adding to history."""
        messages = (
            [{"role": "system", "content": self.system_prompt}]
            + self.history
            + [{"role": "user",
                "content": "[Side question — answer briefly, do not take any action]\n\n" + question}]
        )
        for model in self._models_to_try():
            try:
                resp = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=512,
                    stream=False,
                )
                return resp.choices[0].message.content or ""
            except Exception as e:
                log.warning("btw: error on %s (%s), trying next", model, e)
        return "Error: all models failed."

    def recap(self) -> str:
        """Return a one-sentence summary of this session without modifying history."""
        if not self.history:
            return "Nothing has happened in this session yet."
        return self.btw(
            "Give exactly one sentence summarising what has been accomplished in this session. "
            "Be specific about files changed, tasks done, or conclusions reached."
        )

    def context_stats(self) -> dict:
        """Estimate current context token usage broken down by message category."""
        def _est(text: str) -> int:
            return max(1, len(text) // 4)

        system_tok = _est(self.system_prompt)
        user_tok = assistant_tok = tool_tok = 0

        for msg in self.history:
            role = msg.get("role", "")
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                )
            tool_calls_json = json.dumps(msg["tool_calls"]) if "tool_calls" in msg else ""
            tokens = _est(content + tool_calls_json)
            if role == "user":
                user_tok += tokens
            elif role == "assistant":
                assistant_tok += tokens
            elif role == "tool":
                tool_tok += tokens

        total = system_tok + user_tok + assistant_tok + tool_tok
        return {
            "system":    system_tok,
            "user":      user_tok,
            "assistant": assistant_tok,
            "tool":      tool_tok,
            "total":     total,
        }

    def _exec_tool(self, tc: dict) -> tuple[dict, str, bool]:
        """Execute one tool call; returns (tc, result, was_interrupted)."""
        args = {}
        raw_args = tc.get("arguments") or ""
        if raw_args.strip():
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                # Feed the parse failure back to the model so it can retry with
                # well-formed arguments instead of silently calling with {}.
                msg = (
                    f"Error: could not parse arguments for tool '{tc['name']}' as JSON "
                    f"({exc}). Re-issue the call with valid JSON arguments."
                )
                ui.print_tool_result(tc["name"], msg, error=True)
                log.error("tool %s: bad JSON args: %s", tc["name"], raw_args[:200])
                return tc, msg, False
        log.debug("tool call: %s  args=%s", tc["name"], json.dumps(args))
        # Skill activation — show a clean banner, suppress the raw read_file output
        _is_skill_load = (
            tc["name"] == "read_file"
            and args.get("path", "").endswith("SKILL.md")
        )
        if _is_skill_load:
            import os as _os
            skill_name = _os.path.basename(_os.path.dirname(args["path"]))
            ui.print_skill_load(skill_name)
        else:
            ui.print_tool_call(tc["name"], args)
        try:
            result = execute_tool(tc["name"], args, self.cwd)
        except ToolInterrupted as exc:
            msg = str(exc)
            ui.console.print(f"\n[dim]*{msg}*[/dim]")
            log.debug("tool interrupted: %s", tc["name"])
            return tc, msg, True
        is_error = result.startswith("Error:")
        if not _is_skill_load:
            ui.print_tool_result(tc["name"], result, error=is_error)
        if is_error:
            log.error("tool %s failed: %s", tc["name"], result)
        else:
            log.debug("tool result: %s  → %s", tc["name"], result[:200])
        return tc, result, False

    def run_turn(self, user_message: str) -> None:
        self.total_turns += 1
        self._maybe_compact()
        self.history.append({"role": "user", "content": user_message})

        for iteration in range(1, MAX_ITERATIONS + 1):
            response_text, tool_calls = self._stream_response()

            if not tool_calls:
                if response_text:
                    self._last_response = response_text
                    self.history.append({"role": "assistant", "content": response_text})
                ui.print_gus_done()
                return

            if iteration == MAX_ITERATIONS:
                # Stop runaway loops: record the cap and let the model wrap up
                # on the next turn instead of looping forever.
                self.history.append({
                    "role": "assistant",
                    "content": (
                        f"[Stopped after {MAX_ITERATIONS} tool-use iterations to avoid "
                        "a runaway loop. Summarise progress so far and ask how to proceed.]"
                    ),
                })
                ui.print_warning(
                    f"  Reached the {MAX_ITERATIONS}-iteration limit for this turn — stopping."
                )
                return

            self.history.append({
                "role": "assistant",
                "content": response_text or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                        },
                    }
                    for tc in tool_calls
                ],
            })

            if len(tool_calls) == 1:
                exec_results = [self._exec_tool(tool_calls[0])]
            else:
                workers = min(len(tool_calls), MAX_TOOL_WORKERS)
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = [executor.submit(self._exec_tool, tc) for tc in tool_calls]
                    exec_results = [f.result() for f in futures]

            interrupted = False
            for tc, result, was_interrupted in exec_results:
                self.history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
                if was_interrupted:
                    interrupted = True

            if interrupted:
                return

    def _maybe_compact(self) -> None:
        """Auto-compact history when its estimated size crosses the threshold."""
        if COMPACT_THRESHOLD_TOKENS <= 0 or len(self.history) < 4:
            return
        total = self.context_stats()["total"]
        if total < COMPACT_THRESHOLD_TOKENS:
            return
        ui.print_info(
            f"  Context ~{total:,} tokens ≥ {COMPACT_THRESHOLD_TOKENS:,} — auto-compacting…"
        )
        _, count = self.compact()
        if count:
            ui.print_info(f"  Compacted {count} messages into a summary.")

    def _models_to_try(self) -> list[str]:
        return [self.model] + [m for m in FREE_MODEL_FALLBACKS if m != self.model]

    def _stream_response(self) -> tuple[str, list[dict]]:
        """Stream a model response, falling back across models and retrying
        transient errors (network/5xx/timeout) with exponential backoff."""
        last_error: Exception | None = None
        for model in self._models_to_try():
            for attempt in range(MAX_RETRIES):
                try:
                    return self._call_model(model)
                except RateLimitError as e:
                    last_error = e
                    log.warning("rate-limited on %s, trying next model", model)
                    break  # don't retry same model on 429 — move to the next
                except _TRANSIENT_ERRORS as e:
                    last_error = e
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning("transient error on %s (attempt %d/%d): %s — retrying in %.1fs",
                                model, attempt + 1, MAX_RETRIES, e, delay)
                    if attempt + 1 < MAX_RETRIES:
                        time.sleep(delay)
                except Exception as e:
                    # Non-transient (e.g. model-not-found 404, bad request) —
                    # don't retry this model, try the next fallback instead.
                    last_error = e
                    log.warning("error on %s: %s — trying next model", model, e)
                    break

        raise last_error if last_error else RuntimeError("no model available")

    def _call_model(self, model: str) -> tuple[str, list[dict]]:
        messages = [{"role": "system", "content": self.system_prompt}] + self.history

        # Start the HTTP request before acquiring the console lock so that
        # parallel sub-agents can hit the API concurrently; only rendering is serialised.
        stream = self.client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            max_tokens=MAX_TOKENS,
            stream=True,
            stream_options={"include_usage": True},
        )

        full_text      = ""
        tool_calls_acc: dict[int, dict] = {}
        printed_start  = False

        with ui.console_lock:
            ui.thinking_start()
            try:
                for chunk in stream:
                    # track token usage from the final usage chunk
                    usage = getattr(chunk, "usage", None)
                    if usage:
                        self.total_input_tokens  += getattr(usage, "prompt_tokens",     0) or 0
                        self.total_output_tokens += getattr(usage, "completion_tokens", 0) or 0
                        # OpenAI-format cache: prompt_tokens_details.cached_tokens
                        details = getattr(usage, "prompt_tokens_details", None)
                        self.total_cache_read_tokens += (getattr(details, "cached_tokens", 0) or 0) if details else 0

                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta is None:
                        continue

                    if not printed_start and (delta.content or delta.tool_calls):
                        ui.thinking_stop()
                        printed_start = True
                        if delta.content:
                            ui.print_assistant_start()

                    if delta.content:
                        ui.print_assistant_chunk(delta.content)
                        full_text += delta.content

                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                            acc = tool_calls_acc[idx]
                            if tc_delta.id:
                                acc["id"] = tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    acc["name"] += tc_delta.function.name
                                if tc_delta.function.arguments:
                                    acc["arguments"] += tc_delta.function.arguments

            finally:
                ui.thinking_stop()

            if printed_start and full_text:
                ui.print_assistant_end()

        tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
        # NOTE: history append is owned by run_turn (single source of truth) so
        # a mid-stream retry/fallback never leaves a half-written assistant turn.
        return full_text, tool_calls


def _run_subagent(task: str, cwd: str, context: str = "") -> str:
    """Spawn an isolated sub-agent for one task; return its final summary.

    Registered with the spawn_agent tool at import time so that module never
    has to import this one (keeps agent → tools acyclic).
    """
    from config import get_client, DEFAULT_MODEL

    system_extra = (
        "You are a sub-agent handling one specific task. "
        "Complete it fully, then write a concise summary of every action you took "
        "and every file you changed."
    )
    if context:
        system_extra += f"\n\nContext from parent agent:\n{context}"

    ui.print_subagent_start(task)
    try:
        sub = Agent(client=get_client(), model=DEFAULT_MODEL, cwd=cwd,
                    extra_instructions=system_extra)
        sub.run_turn(task)
    except Exception as e:
        ui.print_subagent_end(failed=True)
        return f"Sub-agent failed: {e}"

    ui.print_subagent_end(failed=False)
    for msg in reversed(sub.history):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"]
    return "Sub-agent completed the task (no text summary produced)."


spawn_agent.register_runner(_run_subagent)
