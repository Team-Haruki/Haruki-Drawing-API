import pytest

from src.sekai.misc.drawer import _command_help_bullet, _command_help_heading, _command_help_numbered


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("# Heading", ("Heading", 1)),
        ("  ###### Deep heading  ", ("Deep heading", 6)),
        ("####### Too deep", None),
        ("##", None),
        ("#No separator", None),
    ],
)
def test_command_help_heading(line: str, expected: tuple[str, int] | None) -> None:
    assert _command_help_heading(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("- item", "item"),
        ("  * spaced item  ", "spaced item"),
        ("+\titem", "item"),
        ("-", None),
        ("-no separator", None),
    ],
)
def test_command_help_bullet(line: str, expected: str | None) -> None:
    assert _command_help_bullet(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("1. item", "1. item"),
        ("  42) spaced item  ", "42) spaced item"),
        ("3.\titem", "3. item"),
        ("1.", None),
        ("1.no separator", None),
        ("item", None),
    ],
)
def test_command_help_numbered(line: str, expected: str | None) -> None:
    assert _command_help_numbered(line) == expected
