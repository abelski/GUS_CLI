"""AskUserQuestion — ask the user a clarifying question.

Disabled when NO_QUESTIONS=1 is set in the environment or .env file.
In that mode the tool returns immediately so the agent proceeds autonomously.
"""
import os

SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "Ask the user a clarifying question when the task is genuinely ambiguous "
            "and proceeding with the wrong assumption would waste significant work. "
            "Disabled when NO_QUESTIONS=1 — in that mode the agent must infer a reasonable "
            "answer and proceed autonomously."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of suggested answers to show the user.",
                },
            },
            "required": ["question"],
        },
    },
}


def _no_questions() -> bool:
    val = os.environ.get("NO_QUESTIONS", "").strip().lower()
    return val in ("1", "true", "yes")


def run(cwd: str, question: str, options: list[str] | None = None) -> str:
    if _no_questions():
        return (
            "NO_QUESTIONS=1 — skip this question and proceed autonomously. "
            "Make a reasonable assumption and continue without asking."
        )

    import ui
    ui.console.print(f"\n[bold cyan]❓ GUS asks:[/bold cyan] {question}")
    if options:
        for i, opt in enumerate(options, 1):
            ui.console.print(f"  [dim]{i}.[/dim] {opt}")
        ui.console.print(f"  [dim](or type your own answer)[/dim]")

    try:
        answer = input("\n  Your answer: ").strip()
    except (EOFError, KeyboardInterrupt):
        return "User did not answer — proceed with a reasonable default."

    return f"User answered: {answer}" if answer else "User provided no answer — proceed with a reasonable default."
