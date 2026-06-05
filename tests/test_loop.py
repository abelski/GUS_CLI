"""Tests for interval parsing/formatting."""
import pytest
from loop import parse_interval, interval_label


@pytest.mark.parametrize("token,expected", [
    ("5s", 5.0),
    ("30m", 1800.0),
    ("1h", 3600.0),
    ("1d", 86400.0),
    ("1.5h", 5400.0),
])
def test_parse_interval_valid(token, expected):
    assert parse_interval(token) == expected


@pytest.mark.parametrize("token", ["", "abc", "5x", "h", "5", "-5s"])
def test_parse_interval_invalid(token):
    assert parse_interval(token) is None


@pytest.mark.parametrize("seconds,label", [
    (3600.0, "1h"),
    (86400.0, "1d"),
    (90.0, "90s"),     # not a whole minute -> seconds
    (45.0, "45s"),
    (1800.0, "30m"),
])
def test_interval_label(seconds, label):
    assert interval_label(seconds) == label
