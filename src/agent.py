"""Core agent loop — streaming tool-use with OpenRouter."""
import concurrent.futures
import json
import platform
import sys

from openai import OpenAI, RateLimitError

import ui
from config import MAX_TOKENS, FREE_MODEL_FALLBACKS, setup_logging
from tools import TOOL_SCHEMAS, execute_tool
from tools._exceptions import ToolInterrupted

log = setup_logging()

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

        models_to_try = [self.model] + [m for m in FREE_MODEL_FALLBACKS if m != self.model]
        response = None
        for model in models_to_try:
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=MAX_TOKENS,
                    stream=False,
                )
                break
            except RateLimitError:
                log.warning("compact: rate-limited on %s, trying next", model)

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
        models_to_try = [self.model] + [m for m in FREE_MODEL_FALLBACKS if m != self.model]
        for model in models_to_try:
            try:
                resp = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=512,
                    stream=False,
                )
                return resp.choices[0].message.content or ""
            except RateLimitError:
                log.warning("btw: rate-limited on %s, trying next", model)
        return "Error: all models rate-limited."

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
        try:
            args = json.loads(tc["arguments"])
        except json.JSONDecodeError:
            pass
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
        self.history.append({"role": "user", "content": user_message})

        while True:
            response_text, tool_calls = self._stream_response()

            if not tool_calls:
                if response_text:
                    self._last_response = response_text
                ui.print_gus_done()
                break

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
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(tool_calls)) as executor:
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

    def _stream_response(self) -> tuple[str, list[dict]]:
        models_to_try = [self.model] + [m for m in FREE_MODEL_FALLBACKS if m != self.model]
        last_error = None
        for model in models_to_try:
            try:
                return self._call_model(model)
            except RateLimitError as e:
                last_error = e
                log.warning("rate-limited on %s, trying next model", model)

        raise last_error

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

        if full_text and not tool_calls:
            self.history.append({"role": "assistant", "content": full_text})

        return full_text, tool_calls
