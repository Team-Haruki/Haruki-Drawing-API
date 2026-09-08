import pytest

from src.sekai.misc.drawer import (
    _command_help_bullet,
    _command_help_heading,
    _command_help_numbered,
    _compose_command_help_image_sync,
    _layout_command_help_markdown,
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


def test_layout_command_help_markdown_covers_filtered_and_styled_lines() -> None:
    title, sections = _layout_command_help_markdown(
        """---
title: ignored
---
# 完整帮助
import hidden
const hidden = true
<Component />
## 基础

### 小节
- 普通项目
- 参数: 参数说明
```text
  raw code
```

## 输出
不应出现
## 进阶
> 引用
| 列 | 值 |
1) 步骤
普通文本
"""
    )

    assert title == "完整帮助"
    assert [section.title for section in sections] == ["基础", "进阶"]
    basic = sections[0].lines
    assert any(line.text == "小节" and line.font_name for line in basic)
    assert any(line.text == "普通项目" and line.indent == 34 for line in basic)
    assert any(line.label == "参数" and line.text == "参数说明" for line in basic)
    assert any(line.text == "raw code" and line.bg is not None for line in basic)
    advanced = sections[1].lines
    assert any(line.text == "引用" and line.bg is not None for line in advanced)
    assert any(line.text == "| 列 | 值 |" and line.size == 18 for line in advanced)
    assert any(line.text == "1) 步骤" and line.indent == 36 for line in advanced)
