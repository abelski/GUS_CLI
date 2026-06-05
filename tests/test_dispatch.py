"""Tests for the tool dispatcher and a couple of representative tools."""
from tools import execute_tool


def test_unknown_tool():
    out = execute_tool("does_not_exist", {}, ".")
    assert out.startswith("Error: unknown tool")


def test_invalid_arguments(tmp_path):
    # read_file requires 'path'; omitting it should yield a clean error, not a crash
    out = execute_tool("read_file", {}, str(tmp_path))
    assert out.startswith("Error: invalid arguments")


def test_read_file_roundtrip(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("line1\nline2\n", encoding="utf-8")
    out = execute_tool("read_file", {"path": "hello.txt"}, str(tmp_path))
    assert "line1" in out and "line2" in out


def test_read_file_offset_limit(tmp_path):
    f = tmp_path / "n.txt"
    f.write_text("\n".join(f"row{i}" for i in range(10)) + "\n", encoding="utf-8")
    out = execute_tool("read_file", {"path": "n.txt", "offset": 3, "limit": 2},
                       str(tmp_path))
    assert "row2" in out and "row3" in out
    assert "row0" not in out and "row5" not in out


def test_read_file_outside_sandbox_blocked(tmp_path):
    out = execute_tool("read_file", {"path": "../escape.txt"}, str(tmp_path))
    assert "outside the working directory" in out


def test_write_then_read(tmp_path):
    w = execute_tool("write_file", {"path": "a/b.txt", "content": "hi"}, str(tmp_path))
    assert "Wrote" in w
    assert (tmp_path / "a" / "b.txt").read_text() == "hi"
