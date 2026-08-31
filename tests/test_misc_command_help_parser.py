import pytest

from src.sekai.misc.drawer import (
    _command_help_bullet,
    _command_help_heading,
    _command_help_numbered,
    _compose_command_help_image_sync,
)
from src.sekai.misc.model import CommandHelpRenderRequest


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


def test_compose_command_help_image_covers_rich_sections() -> None:
    request = CommandHelpRenderRequest(
        title="自定义标题",
        markdown="""# 文档标题
## 常用指令
- **查询**：查看资料
1. 第一步
> 提示文本
| 参数 | 说明 |
普通说明
""",
    )

    image = _compose_command_help_image_sync(request)

    assert image.mode == "RGBA"
    assert image.width == 1080
    assert image.height > 360


def test_compose_command_help_image_supplies_an_empty_fallback_section() -> None:
    image = _compose_command_help_image_sync(CommandHelpRenderRequest(markdown=""))

    assert image.size == (1080, 360)
