"""Load agents.md, skills, .gus/commands, and Agent Skills from the working directory."""
import re
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
    skill_warnings: list[str] = field(default_factory=list)  # spec violations


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter block. Returns (meta, body).

    Handles scalar fields and one level of nested mappings (e.g. metadata:).
    Indented lines (starting with whitespace) are collected under the last
    top-level key as a dict; all other lines use key: value semantics.
    """
    meta: dict = {}
    if not text.startswith("---"):
        return meta, text
    end = text.find("\n---", 3)
    if end == -1:
        return meta, text
    front = text[3:end].strip()
    body  = text[end + 4:].strip()
    current_key: str | None = None
    for line in front.splitlines():
        if not line.strip():
            continue
        if line[0] in (" ", "\t"):
            # Indented — belongs to current_key as a nested map entry
            if current_key and ":" in line:
                k, _, v = line.strip().partition(":")
                if not isinstance(meta.get(current_key), dict):
                    meta[current_key] = {}
                meta[current_key][k.strip()] = v.strip()
        elif ":" in line:
            k, _, v = line.partition(":")
            current_key = k.strip()
            meta[current_key] = v.strip()
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


_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$')


def _validate_skill(name: str, meta: dict, folder_name: str) -> list[str]:
    """Return a list of spec-violation messages (empty = valid)."""
    errors: list[str] = []
    # name
    if not name:
        errors.append("name is empty")
    elif len(name) > 64:
        errors.append(f"name exceeds 64 characters ({len(name)})")
    elif not _NAME_RE.match(name):
        errors.append(f"name '{name}' contains invalid characters or leading/trailing/consecutive hyphens")
    elif "--" in name:
        errors.append(f"name '{name}' contains consecutive hyphens")
    if name != folder_name:
        errors.append(f"name '{name}' does not match folder name '{folder_name}'")
    # description
    desc = meta.get("description", "")
    if not desc:
        errors.append("description is missing or empty")
    elif len(desc) > 1024:
        errors.append(f"description exceeds 1024 characters ({len(desc)})")
    # compatibility (optional, max 500)
    compat = meta.get("compatibility", "")
    if compat and len(compat) > 500:
        errors.append(f"compatibility exceeds 500 characters ({len(compat)})")
    return errors


def _load_agent_skills_from_dir(
    directory: Path,
) -> tuple[dict[str, "AgentSkill"], list[str]]:
    """Load Agent Skills (agentskills.io spec) from a directory of skill folders.

    Returns (skills, warnings) where warnings lists any spec violations found.
    """
    skills: dict[str, AgentSkill] = {}
    warnings: list[str] = []
    if not directory.is_dir():
        return skills, warnings
    for skill_dir in sorted(directory.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        raw        = skill_md.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        name       = meta.get("name", skill_dir.name).lower()
        errors     = _validate_skill(name, meta, skill_dir.name)
        for msg in errors:
            warnings.append(f"skill '{skill_dir.name}': {msg}")
        if errors:
            name = skill_dir.name  # fall back to folder name so it's still addressable
        skills[name] = AgentSkill(
            name          = name,
            description   = meta.get("description", f"Agent skill: {name}"),
            path          = skill_md.resolve(),
            compatibility = meta.get("compatibility", ""),
            body          = body,
        )
    return skills, warnings


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

    # Agent Skills (agentskills.io spec) — three layers, each overriding the previous:
    #   1. Bundled (shipped with GUS, next to this file)
    #   2. Global (~/.gus/skills/)
    #   3. Project (<cwd>/.gus/skills/)
    _bundled = Path(__file__).parent.parent / ".gus" / "skills"
    agent_skills: dict[str, AgentSkill] = {}
    all_warnings: list[str] = []
    for _layer in (_bundled, Path.home() / ".gus" / "skills", root / ".gus" / "skills"):
        layer_skills, layer_warnings = _load_agent_skills_from_dir(_layer)
        agent_skills.update(layer_skills)
        all_warnings.extend(layer_warnings)

    return ProjectContext(instructions=instructions, skills=commands,
                         agent_skills=agent_skills, skill_warnings=all_warnings)
