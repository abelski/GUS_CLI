"""Tests for frontmatter parsing, value coercion, and context loading."""
from context import (
    _parse_frontmatter, _as_bool, _as_int,
    _load_commands_from_dir, load_context, context_fingerprint,
)


def test_frontmatter_basic():
    meta, body = _parse_frontmatter("---\ndescription: Hello\nshell: ls\n---\nBody text")
    assert meta["description"] == "Hello"
    assert meta["shell"] == "ls"
    assert body == "Body text"


def test_frontmatter_no_frontmatter():
    meta, body = _parse_frontmatter("Just a body")
    assert meta == {}
    assert body == "Just a body"


def test_frontmatter_list_field():
    # Real YAML parses lists; the old hand-rolled parser could not.
    meta, _ = _parse_frontmatter(
        "---\nname: t\nallowed-tools:\n  - read_file\n  - bash\n---\nx"
    )
    assert meta["allowed-tools"] == ["read_file", "bash"]


def test_as_bool():
    assert _as_bool(True) is True
    assert _as_bool("true") is True
    assert _as_bool("yes") is True
    assert _as_bool("false") is False
    assert _as_bool(None) is False


def test_as_int():
    assert _as_int(5, 1) == 5
    assert _as_int("3", 1) == 3
    assert _as_int(None, 1) == 1
    assert _as_int("oops", 7) == 7


def test_load_command_coerces_yaml_types(tmp_path):
    d = tmp_path / "commands"
    d.mkdir()
    (d / "deploy.md").write_text(
        "---\ndescription: Deploy\nconfirm: true\nmax_iterations: 3\n---\nRun deploy",
        encoding="utf-8",
    )
    cmds = _load_commands_from_dir(d)
    assert "deploy" in cmds
    assert cmds["deploy"].confirm is True
    assert cmds["deploy"].max_iterations == 3
    assert cmds["deploy"].prompt == "Run deploy"


def test_fingerprint_changes_on_edit(tmp_path):
    (tmp_path / "agents.md").write_text("v1", encoding="utf-8")
    fp1 = context_fingerprint(str(tmp_path))
    (tmp_path / "agents.md").write_text("v2 longer content", encoding="utf-8")
    fp2 = context_fingerprint(str(tmp_path))
    assert fp1 != fp2


def test_load_context_sets_fingerprint(tmp_path):
    ctx = load_context(str(tmp_path))
    assert ctx.fingerprint == context_fingerprint(str(tmp_path))
