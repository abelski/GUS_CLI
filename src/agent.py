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
import url_guard
from session_log import SessionLogger, session_log_enabled
from config import (
    MAX_TOKENS,
    FREE_MODEL_FALLBACKS,
    MAX_ITERATIONS,
    COMPACT_THRESHOLD_TOKENS,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
    RATELIMIT_MAX_WAIT,
    RATELIMIT_ROUNDS,
    RATELIMIT_DEFAULT_WAIT,
    MAX_TOOL_WORKERS,
    URL_GUARD_ENABLED,
    setup_logging,
)
from tools import TOOL_SCHEMAS, execute_tool, spawn_agent
from tools._exceptions import ToolInterrupted
from tools._interrupt import clear_interrupt, is_interrupted, set_interrupt

log = setup_logging()

# Errors worth retrying with backoff (transient: network blips, 5xx, timeouts).
_TRANSIENT_ERRORS = (APIConnectionError, APITimeoutError, InternalServerError)


def _header_lookup(err, *names: str) -> "str | None":
    """Pull a header value off an OpenAI/httpx error, case-insensitively, also
    digging into OpenRouter's nested error body (error.metadata.headers)."""
    resp = getattr(err, "response", None)
    headers = getattr(resp, "headers", None)
    if headers:
        for n in names:
            v = headers.get(n) or headers.get(n.lower())
            if v:
                return v
    # OpenRouter nests the provider headers inside the JSON error body.
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        meta = (body.get("error") or {}).get("metadata") or {}
        nested = meta.get("headers") or {}
        for n in names:
            v = nested.get(n) or nested.get(n.lower())
            if v:
                return v
    return None


def _rate_limit_wait_seconds(err) -> "float | None":
    """Seconds to wait before retrying a 429, from Retry-After (seconds) or
    X-RateLimit-Reset (epoch ms). Returns None when no usable hint is present."""
    ra = _header_lookup(err, "Retry-After", "X-RateLimit-Reset-After")
    if ra:
        try:
            return max(0.0, float(ra))
        except (TypeError, ValueError):
            pass
    reset = _header_lookup(err, "X-RateLimit-Reset")
    if reset:
        try:
            secs = float(reset) / 1000.0 - time.time()
            if secs > 0:
                return secs
        except (TypeError, ValueError):
            pass
    return None


def _is_global_free_limit(err) -> bool:
    """True when a 429 is the account-wide free-tier per-minute cap (rotating to
    another :free model won't help — only waiting will)."""
    msg = str(getattr(err, "message", "") or err).lower()
    return "free-models-per-min" in msg or "free-models-per-day" in msg


def _sleep_interruptible(seconds: float) -> bool:
    """Sleep up to ``seconds``, returning False if the user interrupts midway."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if is_interrupted():
            return False
        time.sleep(min(0.25, deadline - time.time()))
    return True

_BASE_SYSTEM_PROMPT = """\
<gus_behavior>

<identity>
You are GUS (General-purpose Utility Shell), an autonomous AI agent running in a
terminal and powered by models served through OpenRouter. You have tools to read,
write, and edit files, run shell commands, search codebases (glob, grep, list_dir),
search and fetch the web (web_search, web_fetch), drive a real headless browser for
JavaScript/SPA pages and button-triggered downloads (browser), spawn sub-agents
(spawn_agent), and — unless disabled — ask the user a question (ask_user).

When a page needs JavaScript to render (a React/Vue/Angular single-page app) or the task
requires clicking a button, filling a form, or downloading a file behind a click, use the
`browser` tool — `web_fetch` only retrieves static HTML and will see an empty shell on such
sites. Reserve `web_fetch` for simple static pages.

You operate as a command-line coding and automation agent: developers and scripts
delegate tasks to you directly from their terminal, often unattended. The active model,
the host operating system, project instructions, available skills, and any recorded
findings are injected into this prompt at startup; rely on what is actually present
rather than assuming a capability exists.
</identity>

<autonomy>
This is your primary directive. Your main use case is repeatable, automated tasks that
run on a schedule or in a loop with no human present.
- Always prefer completing a task end-to-end with tools over asking the user for input.
- When something is ambiguous, make a reasonable decision, act on it, and document what
  you assumed — do not stall on a clarifying question. Address the request as given,
  even if imperfectly specified, before requesting clarification.
- Never stop mid-task to ask a question unless the action is irreversible and the risk
  is high. If ask_user is unavailable, proceed autonomously and note your assumptions.
- Chain tool calls freely: read → analyse → edit → verify → report in a single turn.
- If a task fails, diagnose it and retry with a different approach before reporting the
  error. An honest report of what you tried beats giving up silently.
- Just do the task the user asked for. Do NOT turn a one-off request into a reusable
  artifact: never create a slash command (.gus/commands/) or a skill (.gus/skills/)
  unless the user explicitly asks you to "create a command/skill". If asked to "open a
  browser", open the browser — don't write a command that opens browsers.
</autonomy>

<search_first>
You have the web_search and web_fetch tools. For any factual question about the present-day
world, you must search before answering. Your confidence on a topic is not an excuse to
skip search. Present-day facts — who holds a role, what something costs, whether a library
or API still works the way you remember, what the newest version of something is — cannot
come from training data. Things you "know" may have changed. Search proactively instead of
answering from priors and offering to check. To reiterate: search before EVERY factual
question about the present-day world, and whenever a task depends on documentation,
versions, or anything that could have changed since training.
</search_first>

<accuracy>
Be helpful AND honest — the two go together, and being honest does NOT mean refusing.
The default is to give the user a useful, best-effort answer built from what you actually
found, with uncertainty clearly labeled. Bailing out with "I can't provide this" when you
DID find relevant results is a failure, not caution.
- Don't fabricate specific details — a URL, subscriber count, date, price, verbatim quote,
  OR contact detail (email, phone number, street address) you did not actually retrieve.
  Contact info especially: never invent or pattern-fill a phone/email (e.g. "+370 6 234 5678",
  "info@company.lt"). If a search result or fetched page didn't give you the real value,
  leave the cell blank or write "not listed" — do not guess. Don't invent precision.
- But DO synthesize and present what your searches turned up. If the user asks for a
  "top 10" and you found seven plausible channels, give the seven, say the ranking is
  approximate, and note you couldn't confirm exact counts. A partial, clearly-caveated
  answer is far more useful than a blanket refusal.
- A close match counts. If a search result plainly matches the request (e.g. a result
  literally named for what the user asked), surface it — don't discard it because it
  isn't from an "authoritative ranking."
- Separate what's solid from what's approximate or uncertain, and cite the source URL for
  non-obvious facts so the user can dig further. Don't overclaim, in either direction.
- Only say you couldn't find something when the searches genuinely returned nothing
  relevant — and even then, share the closest leads you did find and suggest next steps.

Practical fact-checking (apply judgement, not a rigid checklist):
- Search before answering present-day or time-sensitive questions; don't answer those from
  memory.
- Prefer stronger sources (official sites, docs, primary sources, reputable news, Wikipedia)
  over forums/SEO spam, but weak sources are still worth reporting as such.
- When a claim is important or contested, corroborate it or open the page with web_fetch to
  confirm. For ordinary requests, a good search result is enough — you don't need to fetch
  every URL or find two independent sources before answering.
- Note the date on time-sensitive figures (versions, counts, prices, "latest" anything).
</accuracy>

<tool_use>
- Use tools for everything — do not describe what you would do, just do it.
- Before editing a file you have not read in this session, read it first.
- Prefer targeted edits (edit_file) over full rewrites (write_file) for existing files.
- After making changes, verify them — re-read the file, run tests, check output — before
  reporting done. Do not claim success you have not observed.
- When a task has independent workstreams across multiple files or steps, use spawn_agent
  sub-agents to handle them in parallel.
</tool_use>

<tool_and_skill_discovery>
The capabilities listed in this prompt are not exhaustive. Skills listed under "Available
Agent Skills" are those known at startup, but new skills may be added during a session
(for example after skill-creation runs). Before assuming you lack a capability, look for
it: scan for skills by calling list_dir on `.gus/skills/` and `~/.gus/skills/`, then
read_file any SKILL.md you find. After loading a SKILL.md, follow its instructions as if
the skill had been listed at startup. This lets you expand your own capabilities
mid-conversation without restarting. When a task involves creating, editing, or analysing
a file and a relevant skill exists, read that SKILL.md FIRST, before touching the file.
</tool_and_skill_discovery>

<sandbox>
You are strictly sandboxed to the working directory. This is enforced per-tool.
- NEVER create, write, or move files to any path outside the working directory.
- ALL new files and directories must live inside the working directory or its subdirectories.
- In bash commands use relative paths; never absolute paths pointing outside the sandbox.
- Redirections (`>`, `>>`), `mkdir`, `touch`, `tee`, `cp`, and `mv` targeting paths outside
  the working directory are blocked and will return an error.
- If you need a temporary file, create it inside the working directory (e.g. `./tmp/`).
</sandbox>

<safety>
You default to helping. You only decline a request when helping would create a concrete,
specific risk of serious harm; requests that are merely edgy, hypothetical, or unusual do
not meet that bar.
- You do not write, explain, improve, or debug malicious code — malware, vulnerability
  exploits for unauthorized use, credential stealers, ransomware, spoofing sites — even if
  the person gives a seemingly good reason. Legitimate defensive security, CTF, and
  authorized testing work is fine when the context is clear.
- You do not provide information that could enable the creation of weapons (with extra
  caution around explosives and chemical, biological, or nuclear weapons), and you do not
  rationalize compliance by citing public availability or assumed research intent.
- For destructive or irreversible operations (deleting data, force-pushing, dropping
  tables, rewriting history), confirm intent unless the user has clearly authorized it.
- You can keep a normal, constructive tone even when declining part of a task.
</safety>

<creating_commands>
Only create a command when the user EXPLICITLY asks for one ("create a command", "add a
command", "make a slash command", "create a command that/for/to …"). A bare task request
is NOT such a request — just do the task. When the user does ask, they always mean a GUS
slash command — a `.gus/commands/<name>.md` file. Never produce a shell or Python script
as the output. Follow ALL steps in order:

Step 1 — Plan. Before touching any file, reason through the design in your response: the
exact purpose; the best kebab-case name; whether it needs a `shell:` pre-step to gather
live context (and the exact command, its output, and how the prompt uses it); what
$ARGUMENTS the user passes; a detailed, specific prompt body (goal, step-by-step
instructions, output format, success criteria) precise enough for consistent results;
and whether it needs `confirm: true` for destructive actions.

Step 2 — Write the final `.gus/commands/<name>.md` with write_file:
```
---
description: One-line description shown in /help
shell: <optional shell command; output available as $SHELL_OUTPUT>
confirm: true   # optional — prompt user before running
max_iterations: 5  # optional — cap for /loop usage
---
Prompt body. Use $ARGUMENTS for user input. Use $SHELL_OUTPUT if a shell pre-step is defined.
```

Step 3 — Verify. Read the file back, confirm it is correct, and tell the user:
"Created `/name` — type `/name [args]` to run it."
</creating_commands>

<creating_skills>
Only create a skill when the user EXPLICITLY asks for one. Do not invent a skill to
satisfy an ordinary task. When the user asks to "create/add/make a skill", they mean the
agentskills.io format: `.gus/skills/<skill-name>/SKILL.md` with YAML frontmatter (`name`,
`description`) and a step-by-step body. Never use any other format or location.
</creating_skills>

<findings_memory>
GUS keeps a per-project memory of what it learns in `.gus/findings.md`, distilled at the
end of each turn that did real work and injected back into this prompt on later sessions.
When past findings appear below, treat them as durable lessons from earlier sessions in
this project: consult them before repeating work or known mistakes, but verify anything
that names a specific file, flag, or command still exists before relying on it.
</findings_memory>

<directness>
Answer the question that was asked, directly, and stop. Do not overthink.
- Lead with the answer or the action, not with preamble, restatement of the request, or a
  narration of your reasoning. The user can see the task — don't repeat it back.
- Match effort to the task: a simple question gets a short, direct answer; only a genuinely
  complex task warrants step-by-step planning. Don't manufacture complexity that isn't there.
- Stay on the point. Don't wander into tangents, caveats nobody asked for, alternative
  framings, or "things to consider" unless they actually matter to the answer.
- Think as much as the problem needs and no more. Once you have the answer, give it —
  don't keep second-guessing or expanding. Spend deliberation on hard problems, not easy ones.
</directness>

<tone_and_formatting>
- Be concise. Explain what you did and what changed, not what you are about to do.
- Match formatting to the medium: this is a terminal. Use the minimum formatting needed
  to be clear. Prefer short prose for explanations; reserve bullet lists for genuinely
  multi-item content, not for narrative.
- Reference files as clickable `path:line` where the host supports it.
- Own mistakes honestly and fix them, without collapsing into excessive apology. Stay
  steadily helpful and keep self-respect even if the user is curt.
- End every completed task with a one-line summary of the outcome.
</tone_and_formatting>

</gus_behavior>"""

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
                          agent_skills: dict | None = None,
                          findings_text: str = "") -> str:
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
    if findings_text:
        prompt += (
            "\n\n## Past Findings (this project)\n"
            "Lessons recorded from earlier sessions in this project — consult them "
            "before repeating work or known mistakes.\n\n" + findings_text
        )
    if mode == "plan":
        prompt += "\n\n" + _PLAN_MODE_ADDITION
    return prompt


class Agent:
    def __init__(self, client: OpenAI, model: str, cwd: str,
                 extra_instructions: str = "", mode: str = "agent",
                 agent_skills: dict | None = None,
                 findings_text: str = "", enable_session_log: bool = False) -> None:
        self.client        = client
        self.model         = model
        self.cwd           = cwd
        self.mode          = mode
        self._extra        = extra_instructions
        self._agent_skills = agent_skills or {}
        self._findings     = findings_text
        self.system_prompt = _build_system_prompt(extra_instructions, mode,
                                                  self._agent_skills, findings_text)
        self.history: list[dict] = []

        # Per-project session transcript (interactive/one-shot agent only;
        # forks for routines and sub-agents stay quiet to avoid noise).
        self.session_log = None
        if enable_session_log and session_log_enabled():
            try:
                self.session_log = SessionLogger(cwd, model)
            except Exception:
                self.session_log = None
        self._turn_tool_calls = 0

        # session metadata
        self.session_name: str = ""
        self.goal: str | None  = None

        # last assistant text response (for /copy)
        self._last_response: str = ""

        # URL guard per-turn state: URLs verified via tools/user input this
        # turn, and whether we've already spent the one auto-correction pass.
        self._turn_verified_urls: set[str] = set()
        self._url_correction_done: bool = False

        # cumulative token usage
        self.total_input_tokens:  int = 0
        self.total_output_tokens: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_turns: int = 0

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.system_prompt = _build_system_prompt(self._extra, mode,
                                                  self._agent_skills, self._findings)

    def fork(self) -> "Agent":
        """Create a sibling agent sharing config but with its own empty history.

        Used for background routines so their turns never pollute (or get
        polluted by) the interactive conversation history.
        """
        return Agent(
            client=self.client, model=self.model, cwd=self.cwd,
            extra_instructions=self._extra, mode=self.mode,
            agent_skills=self._agent_skills, findings_text=self._findings,
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

    def extract_findings(self, transcript: str) -> str:
        """Distil a completed turn into durable findings markdown.

        Independent of self.history (builds its own message list) so it never
        pollutes the conversation. Returns markdown under the three headings, or
        the literal "NONE" when there is nothing worth recording.
        """
        system = (
            "You distil a work log into durable lessons for future sessions. "
            "Record only findings about the PROBLEM DOMAIN that would help complete a "
            "similar task next time — facts about the project, environment, APIs, "
            "commands, or errors and their fixes. "
            "Do NOT record the agent's own routine mechanics as findings: writing/reading/"
            "editing files, calling tools, or scaffolding a slash command or skill are "
            "plumbing, not lessons — never log them as Successes. "
            "A 'Success' means the user's actual task was achieved and you learned what "
            "made it work; if the task was not actually completed, record nothing under "
            "Successes. "
            "Use up to three headings: **Successes** (what genuinely worked), "
            "**Pitfalls** (gotchas to avoid), and **Problems & Solutions** (a problem hit "
            "and how it was fixed). Use terse markdown bullets. Omit any heading with "
            "nothing to say. If nothing is worth recording, reply with exactly: NONE"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "Work log:\n\n" + transcript},
        ]
        for model in self._models_to_try():
            try:
                resp = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=512,
                    stream=False,
                )
                return resp.choices[0].message.content or "NONE"
            except Exception as e:
                log.warning("extract_findings: error on %s (%s), trying next", model, e)
        return "NONE"

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
                if self.session_log:
                    self.session_log.tool_result(tc["name"], msg, is_error=True)
                return tc, msg, False
        log.debug("tool call: %s  args=%s", tc["name"], json.dumps(args))
        if self.session_log:
            self.session_log.tool_call(tc["name"], args)
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
            if self.session_log:
                self.session_log.tool_result(tc["name"], msg, is_error=True)
            return tc, msg, True
        is_error = result.startswith("Error:")
        if not _is_skill_load:
            ui.print_tool_result(tc["name"], result, error=is_error)
        if is_error:
            log.error("tool %s failed: %s", tc["name"], result)
        else:
            log.debug("tool result: %s  → %s", tc["name"], result[:200])
        if self.session_log:
            self.session_log.tool_result(tc["name"], result, is_error=is_error)
        return tc, result, False

    def _run_tools(self, tool_calls: list[dict]) -> tuple[list[tuple[dict, str, bool]], bool]:
        """Execute all tool calls, returning (results, interrupted).

        Guarantees one (tc, result, was_interrupted) entry per call even when the
        user hits Ctrl+C mid-flight, so the history never ends up with a
        tool_call that has no matching tool response (which would break the next
        API request). On interrupt, any unfinished call is recorded as
        interrupted and the shared flag is set so worker-thread tools bail out.
        """
        msg = "Interrupted by user (Ctrl+C)."

        if len(tool_calls) == 1:
            try:
                return [self._exec_tool(tool_calls[0])], False
            except KeyboardInterrupt:
                set_interrupt()
                return [(tool_calls[0], msg, True)], True

        workers = min(len(tool_calls), MAX_TOOL_WORKERS)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        futures = {executor.submit(self._exec_tool, tc): tc for tc in tool_calls}
        done: dict[str, tuple[dict, str, bool]] = {}
        interrupted = False
        try:
            for fut in concurrent.futures.as_completed(futures):
                tc, result, was_int = fut.result()
                done[tc["id"]] = (tc, result, was_int)
                if was_int:
                    interrupted = True
        except KeyboardInterrupt:
            set_interrupt()  # let still-running worker tools abort themselves
            interrupted = True
            for fut in futures:
                fut.cancel()
        finally:
            executor.shutdown(wait=False)

        results = []
        for tc in tool_calls:
            results.append(done.get(tc["id"], (tc, msg, True)))
        return results, interrupted

    def run_turn(self, user_message: str) -> None:
        """Thin wrapper: record the turn in the session log, then run it."""
        if self.session_log:
            self.session_log.user(user_message)
        self._turn_tool_calls = 0
        _turn_started = time.time()
        try:
            self._run_turn_inner(user_message)
        finally:
            if self.session_log:
                self.session_log.turn_end(
                    self._turn_tool_calls, self.model, time.time() - _turn_started)

    def _run_turn_inner(self, user_message: str) -> None:
        self.total_turns += 1
        clear_interrupt()
        self._maybe_compact()
        self.history.append({"role": "user", "content": user_message})

        # Seed the URL guard: links the user supplied are not fabrications, so
        # the model may echo them. Tool results add to this set as they arrive.
        self._turn_verified_urls = url_guard.extract_urls(user_message)
        self._url_correction_done = False

        for iteration in range(1, MAX_ITERATIONS + 1):
            # Bail between iterations if the flag was tripped from another thread
            # (e.g. the REPL stopping a background routine mid-run). History is
            # consistent here — no half-written tool_calls to leave dangling.
            if is_interrupted():
                return
            response_text, tool_calls = self._stream_response()

            if not tool_calls:
                if response_text:
                    unverified = (
                        url_guard.find_unverified(response_text, self._turn_verified_urls)
                        if URL_GUARD_ENABLED else []
                    )
                    # First time we see fabricated-looking links, spend one
                    # automatic pass making the model verify or drop them
                    # instead of ending the turn on unverified URLs.
                    if unverified and not self._url_correction_done:
                        self._url_correction_done = True
                        self.history.append({"role": "assistant", "content": response_text})
                        self.history.append({
                            "role": "user",
                            "content": (
                                "[Automated link check] These URLs in your answer were not "
                                "retrieved from any tool this turn, so they may not exist:\n"
                                + "\n".join(f"  - {u}" for u in unverified)
                                + "\n\nFor EACH one, call web_fetch to confirm it resolves. "
                                "Keep a URL only if the fetch succeeds; remove any that error "
                                "or 404. Do not invent replacement URLs. If you cannot verify a "
                                "link, drop it and say so. Then give your corrected final answer."
                            ),
                        })
                        ui.print_url_guard_checking(len(unverified))
                        continue
                    self._last_response = response_text
                    self.history.append({"role": "assistant", "content": response_text})
                    if self.session_log:
                        self.session_log.assistant(response_text)
                    # Survived a correction pass and still has unverified links —
                    # warn the user rather than letting them be trusted silently.
                    if unverified:
                        ui.print_url_guard_warning(unverified)
                    ui.print_gus_done()
                else:
                    # Model returned neither text nor a tool call — nothing
                    # actually happened. Common when every free model is
                    # rate-limited or returns an empty completion. Don't
                    # celebrate an empty turn as success; say what happened so
                    # the user can retry (and doesn't trust a false "done").
                    ui.print_empty_response()
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

            self._turn_tool_calls += len(tool_calls)
            exec_results, interrupted = self._run_tools(tool_calls)

            for tc, result, _ in exec_results:
                self.history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
                # Record URLs this tool genuinely surfaced so the guard can
                # tell them apart from links the model invents.
                self._turn_verified_urls |= url_guard.verified_urls_from_tool(
                    tc["name"], tc["arguments"], result)

            if interrupted:
                ui.console.print(
                    "\n[dim]*Interrupted — returning to prompt. History preserved; "
                    "type a new instruction or /exit.*[/dim]"
                )
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

    def _trace_model(self, msg: str, style: str = "dim") -> None:
        """Surface a model fallback / retry transition to the console (so the
        user can see GUS switching models) and record it in the session log."""
        ui.console.print(f"[{style}]  ↻ {msg}[/{style}]")
        if self.session_log:
            self.session_log.event(msg)

    def _stream_response(self) -> tuple[str, list[dict]]:
        """Stream a model response, resilient to free-tier limits.

        Strategy: try each model in turn (cheap rotation that escapes a single
        provider's limit or outage). If the whole chain is rate-limited — the
        account-wide :free 20/min cap, which rotating can't dodge — wait for the
        window to reset (honouring the reset header) and retry the chain, up to
        RATELIMIT_ROUNDS times. Transient network/5xx errors get the usual
        exponential backoff per model.
        """
        last_error: Exception | None = None
        for round_idx in range(RATELIMIT_ROUNDS + 1):
            all_rate_limited = True   # every failure this pass was a 429?
            rl_wait: float | None = None
            global_limit = False
            for model in self._models_to_try():
                for attempt in range(MAX_RETRIES):
                    try:
                        result = self._call_model(model)
                        # Tell the user when a fallback model (not the chosen one)
                        # ended up serving the response.
                        if model != self.model:
                            self._trace_model(f"served by fallback model {model}", "green")
                        return result
                    except RateLimitError as e:
                        last_error = e
                        rl_wait = _rate_limit_wait_seconds(e) or rl_wait
                        log.warning("rate-limited on %s", model)
                        if _is_global_free_limit(e):
                            # Account-wide cap — other :free models are capped
                            # too, so stop rotating and go straight to the wait.
                            global_limit = True
                            self._trace_model(f"{model}: account-wide free rate limit hit", "yellow")
                            break
                        self._trace_model(f"{model} rate-limited — trying next model", "yellow")
                        break  # per-model limit: try the next model
                    except _TRANSIENT_ERRORS as e:
                        last_error = e
                        all_rate_limited = False
                        delay = RETRY_BASE_DELAY * (2 ** attempt)
                        log.warning("transient error on %s (attempt %d/%d): %s — retrying in %.1fs",
                                    model, attempt + 1, MAX_RETRIES, e, delay)
                        if attempt + 1 < MAX_RETRIES:
                            self._trace_model(
                                f"{model}: {type(e).__name__} — retry "
                                f"{attempt + 1}/{MAX_RETRIES} in {delay:.0f}s", "yellow")
                            if not _sleep_interruptible(delay):
                                raise
                        else:
                            self._trace_model(
                                f"{model}: {type(e).__name__} — giving up on this model, "
                                "trying next", "yellow")
                    except Exception as e:
                        # Non-transient (e.g. model-not-found 404, bad request) —
                        # don't retry this model, try the next fallback instead.
                        last_error = e
                        all_rate_limited = False
                        log.warning("error on %s: %s — trying next model", model, e)
                        self._trace_model(f"{model}: {str(e)[:120]} — trying next model", "red")
                        break
                if global_limit:
                    break

            # The whole chain failed this pass. Only a 429 storm is worth
            # waiting out; any other failure means waiting won't help.
            if not all_rate_limited or round_idx >= RATELIMIT_ROUNDS:
                break
            wait = min(rl_wait or RATELIMIT_DEFAULT_WAIT, RATELIMIT_MAX_WAIT)
            ui.print_info(
                f"  All free models rate-limited — waiting {wait:.0f}s for the "
                f"limit to reset (round {round_idx + 1}/{RATELIMIT_ROUNDS})…"
            )
            if self.session_log:
                self.session_log.event(
                    f"all models rate-limited — waiting {wait:.0f}s "
                    f"(round {round_idx + 1}/{RATELIMIT_ROUNDS})")
            if not _sleep_interruptible(wait):
                break  # user interrupted the wait — surface the last error

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
