"""Tests for the GUS system-prompt builder in agent.py."""
import agent


class _FakeSkill:
    def __init__(self, name, description, path):
        self.name = name
        self.description = description
        self.path = path


def test_base_prompt_has_core_sections():
    p = agent._build_system_prompt()
    for tag in ("<gus_behavior>", "<autonomy>", "<accuracy>", "<tool_use>",
                "<sandbox>", "<safety>"):
        assert tag in p, f"missing section {tag}"
    assert "You are GUS" in p


def test_accuracy_is_best_effort_not_strict():
    """Regression for the 'GUS too strict' feedback: the accuracy block must
    favour best-effort answers, not all-or-nothing refusals."""
    p = agent._build_system_prompt()
    assert "best-effort" in p
    assert "does NOT mean refusing" in p
    # the old strict mandates that caused blanket refusals must be gone
    assert "overrides the urge to be helpful" not in p
    assert "TWO independent" not in p
    assert "Validate every URL before you show it" not in p


def test_no_fabricated_contact_details():
    """Regression: GUS invented placeholder phones/emails on the AK-ceramic
    query. The accuracy block must explicitly forbid fabricating contact info."""
    p = agent._build_system_prompt()
    assert "phone" in p and "email" in p
    assert "not listed" in p  # the prescribed fallback instead of guessing


def test_prompt_pushes_directness():
    """Regression for 'GUS wanders / overthinks': the prompt must tell it to
    answer directly and match effort to the task."""
    p = agent._build_system_prompt()
    assert "<directness>" in p
    assert "Do not overthink" in p
    assert "Stay on the point" in p


def test_plan_mode_is_read_only():
    p = agent._build_system_prompt(mode="plan")
    assert "PLAN" in p
    assert "read-only" in p.lower()
    # agent-mode prompt should NOT carry the plan addition
    assert "PLAN" not in agent._build_system_prompt(mode="agent")


def test_findings_injected_when_present():
    p = agent._build_system_prompt(findings_text="REMEMBER_THIS_FINDING")
    assert "Past Findings" in p
    assert "REMEMBER_THIS_FINDING" in p
    # ...and omitted when empty
    assert "Past Findings" not in agent._build_system_prompt(findings_text="")


def test_skills_injected_when_present():
    skills = {"demo": _FakeSkill("demo-skill", "does demo things", "/x/SKILL.md")}
    p = agent._build_system_prompt(agent_skills=skills)
    assert "Available Agent Skills" in p
    assert "demo-skill" in p
    assert "does demo things" in p
    assert "/x/SKILL.md" in p


def test_project_instructions_injected():
    p = agent._build_system_prompt(extra_instructions="PROJECT_RULE_XYZ")
    assert "Project Instructions" in p
    assert "PROJECT_RULE_XYZ" in p
