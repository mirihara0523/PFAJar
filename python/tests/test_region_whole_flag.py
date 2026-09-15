"""Tests for region.py parse_whole_flag (argv string truthiness bug)."""

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_DIR = _REPO_ROOT / "py"
if str(_PY_DIR) not in sys.path:
    sys.path.insert(0, str(_PY_DIR))

from intensity_flags import parse_whole_flag  # noqa: E402


@pytest.mark.parametrize(
    "value,expected",
    [
        ("True", True),
        ("False", False),
        ("true", True),
        ("false", False),
        ("1", True),
        ("0", False),
        ("", False),
        (True, True),
        (False, False),
    ],
)
def test_parse_whole_flag(value, expected):
    assert parse_whole_flag(value) is expected


def test_parse_whole_flag_rejects_invalid():
    with pytest.raises(ValueError):
        parse_whole_flag("maybe")
