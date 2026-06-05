"""Tests for the path/bash sandbox guards."""
from tools._sandbox import resolve, sandbox_check, bash_sandbox_check


def test_resolve_relative(tmp_path):
    assert resolve("a/b.txt", str(tmp_path)) == str(tmp_path / "a" / "b.txt")


def test_sandbox_allows_inside(tmp_path):
    inside = str(tmp_path / "sub" / "f.txt")
    assert sandbox_check(inside, str(tmp_path)) is None


def test_sandbox_allows_root_itself(tmp_path):
    assert sandbox_check(str(tmp_path), str(tmp_path)) is None


def test_sandbox_blocks_outside(tmp_path):
    outside = str(tmp_path.parent / "evil.txt")
    assert sandbox_check(outside, str(tmp_path)) is not None


def test_sandbox_blocks_dotdot_escape(tmp_path):
    escaped = resolve("../../etc/passwd", str(tmp_path))
    assert sandbox_check(escaped, str(tmp_path)) is not None


def test_sandbox_prefix_not_fooled(tmp_path):
    # /root vs /root-evil must not be treated as inside
    sibling = str(tmp_path) + "-evil/x"
    assert sandbox_check(sibling, str(tmp_path)) is not None


# ── bash heuristics ─────────────────────────────────────────────────────────

def test_bash_allows_inside_commands(tmp_path):
    cwd = str(tmp_path)
    for cmd in ("ls -la", "echo hi > out.txt", "mkdir sub", "cat ./x", "cd sub && ls"):
        assert bash_sandbox_check(cmd, cwd) is None, cmd


def test_bash_blocks_absolute_redirect(tmp_path):
    assert bash_sandbox_check("echo x > /etc/passwd", str(tmp_path)) is not None


def test_bash_blocks_relative_dotdot_redirect(tmp_path):
    assert bash_sandbox_check("echo x > ../../escape.txt", str(tmp_path)) is not None


def test_bash_blocks_mkdir_outside(tmp_path):
    assert bash_sandbox_check("mkdir -p /tmp/evilgus", str(tmp_path)) is not None


def test_bash_blocks_cp_outside(tmp_path):
    assert bash_sandbox_check("cp ./a.txt /tmp/a.txt", str(tmp_path)) is not None


def test_bash_blocks_cd_outside(tmp_path):
    assert bash_sandbox_check("cd /tmp && touch x", str(tmp_path)) is not None


def test_bash_blocks_cd_dotdot_outside(tmp_path):
    # cd to the parent of cwd escapes the sandbox
    assert bash_sandbox_check("cd .. && rm -rf foo", str(tmp_path)) is not None
