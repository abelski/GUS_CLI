"""Load agents.md, skills, .gus/commands, and Agent Skills from the working directory."""
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Command:
    name: str
    description: str
    prompt: str             # prompt template — may contain $ARGUMENTS, $SHELL_OUTPUT
    shell: str | None       # optional shell pre-step, stdout → $SHELL_OUTPUT
    confirm: bool           # ask user before running (default False)
    max_iterations: int     # loop cap when used with /loop (default 1 = no loop)

    def build_prompt(self, args: str = "", shell_output: str = "") -> str:
        return (
            self.prompt
            .replace("$ARGUMENTS",    args)
            .replace("$SHELL_OUTPUT", shell_output)
        )

    def run_shell(self, cwd: str) -> str:
        if not self.shell:
            return ""
        try:
            result = subprocess.run(
                self.shell, shell=True, capture_output=True,
                text=True, timeout=60, cwd=cwd,
            )
            return (result.stdout + result.stderr).strip()
        except subprocess.TimeoutExpired:
            return "[shell timed out]"
        except Exception as e:
            return f"[shell error: {e}]"


# backward-compat alias
Skill = Command


@dataclass
class AgentSkill:
    """An Agent Skills spec-compliant skill loaded from a SKILL.md file."""
    name: str
    description: str
    path: Path              # absolute path to the SKILL.md file
    compatibility: str      # optional compatibility notes from frontmatter
    body: str               # full markdown body (instructions)


@dataclass
class ProjectContext:
    instructions: str
    skills: dict[str, Command] = field(default_factory=dict)
    agent_skills: dict[str, AgentSkill] = field(default_factory=dict)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML-ish frontmatter block. Returns (meta, body)."""
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta, text
    end = text.find("\n---", 3)
    if end == -1:
        return meta, text
    front = text[3:end].strip()
    body  = text[end + 4:].strip()
    for line in front.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, body


def _load_commands_from_dir(directory: Path) -> dict[str, Command]:
    commands: dict[str, Command] = {}
    if not directory.is_dir():
        return commands
    for md_file in sorted(directory.glob("*.md")):
        raw          = md_file.read_text(encoding="utf-8")
        meta, body   = _parse_frontmatter(raw)
        name         = meta.get("name", md_file.stem).lower().replace(" ", "-")
        commands[name] = Command(
            name           = name,
            description    = meta.get("description", f"Run {name}"),
            prompt         = body,
            shell          = meta.get("shell") or None,
            confirm        = meta.get("confirm", "false").lower() == "true",
            max_iterations = int(meta.get("max_iterations", "1")),
        )
    return commands


def _load_agent_skills_from_dir(directory: Path) -> dict[str, "AgentSkill"]:
    """Load Agent Skills (agentskills.io spec) from a directory of skill folders."""
    skills: dict[str, AgentSkill] = {}
    if not directory.is_dir():
        return skills
    for skill_dir in sorted(directory.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        raw          = skill_md.read_text(encoding="utf-8")
        meta, body   = _parse_frontmatter(raw)
        name         = meta.get("name", skill_dir.name).lower()
        skills[name] = AgentSkill(
            name          = name,
            description   = meta.get("description", f"Agent skill: {name}"),
            path          = skill_md.resolve(),
            compatibility = meta.get("compatibility", ""),
            body          = body,
        )
    return skills


def load_context(cwd: str) -> ProjectContext:
    root = Path(cwd)

    # agents.md — project system-prompt instructions
    instructions = ""
    agents_file  = root / "agents.md"
    if agents_file.is_file():
        instructions = agents_file.read_text(encoding="utf-8").strip()

    # skills/   — simple prompt-injection commands (legacy)
    # .gus/commands/ — full-featured commands (shell pre-step, loop cap, confirm)
    commands = _load_commands_from_dir(root / "skills")
    commands.update(_load_commands_from_dir(root / ".gus" / "commands"))

    # Agent Skills (agentskills.io spec): ~/.gus/skills/ and .gus/skills/
    # Global skills load first; project-level overrides them.
    agent_skills = _load_agent_skills_from_dir(Path.home() / ".gus" / "skills")
    agent_skills.update(_load_agent_skills_from_dir(root / ".gus" / "skills"))

    return ProjectContext(instructions=instructions, skills=commands,
                         agent_skills=agent_skills)
