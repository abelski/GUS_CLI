"""Sub-agent tool — delegate a self-contained task to a fresh agent instance."""

SCHEMA = {
    "type": "function",
    "function": {
        "name": "spawn_agent",
        "description": (
            "Spawn an independent sub-agent to handle a well-defined subtask. "
            "The sub-agent has the same tools (read, write, edit, bash, search) and "
            "the same working directory. Use this to parallelise distinct work streams, "
            "isolate risky operations, or delegate research. "
            "Returns a summary of everything the sub-agent did."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Clear, self-contained description of what the sub-agent should accomplish.",
                },
                "context": {
                    "type": "string",
                    "description": "Extra context, constraints, or files the sub-agent should know about.",
                },
            },
            "required": ["task"],
        },
    },
}


def run(task: str, cwd: str, context: str = "") -> str:
    # Lazy imports to avoid circular dependency (agent → tools → agent)
    from config import get_client, DEFAULT_MODEL
    from agent import Agent
    import ui

    system_extra = (
        "You are a sub-agent handling one specific task. "
        "Complete it fully, then write a concise summary of every action you took and every file you changed."
    )
    if context:
        system_extra += f"\n\nContext from parent agent:\n{context}"

    ui.print_subagent_start(task)
    try:
        client    = get_client()
        sub       = Agent(client=client, model=DEFAULT_MODEL, cwd=cwd,
                          extra_instructions=system_extra)
        sub.run_turn(task)
    except Exception as e:
        ui.print_subagent_end(failed=True)
        return f"Sub-agent failed: {e}"

    ui.print_subagent_end(failed=False)

    # Return the last assistant text as the result
    for msg in reversed(sub.history):
        if msg.get("role") == "assistant" and msg.get("content"):
            return msg["content"]
    return "Sub-agent completed the task (no text summary produced)."
