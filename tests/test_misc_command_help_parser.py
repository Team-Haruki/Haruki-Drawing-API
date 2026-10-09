import pytest

from src.sekai.misc import drawer
from src.sekai.misc.drawer import (
    _command_help_bullet,
    _command_help_heading,
    _command_help_numbered,
    _compose_command_help_image_sync,
    _layout_command_help_markdown,
    _split_command_help_definition,
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


@pytest.mark.parametrize(
    ("bullet", "expected"),
    [
        ("`event123`、`活动123` 或活动 ID：指定活动", ("event123、活动123 或活动 ID", "指定活动")),
        ("**查询**：查看资料", ("查询", "查看资料")),
        ("参数: 参数说明", ("参数", "参数说明")),
        # A colon inside a code span is input syntax, not a separator.
        ("`/禁止别名提交 qq:123456789`", None),
        ("提交者的写法：`平台:用户 ID`，可以直接使用", ("提交者的写法", "平台:用户 ID，可以直接使用")),
        ("`a：b` 和 c: d", ("a：b 和 c", "d")),
        # A link target is not a separator either.
        ("详见 [文档](https://example.com/help)", None),
        ("：没有标签", None),
        ("没有说明：", None),
        ("普通项目", None),
    ],
)
def test_split_command_help_definition(bullet: str, expected: tuple[str, str] | None) -> None:
    assert _split_command_help_definition(bullet) == expected


def test_wide_definition_label_gets_its_own_line(monkeypatch: pytest.MonkeyPatch) -> None:
    # One em per character, like the CJK glyphs of the production font (tests may run without it).
    monkeypatch.setattr(drawer, "get_text_size", lambda font, text: (len(text) * 21, 21))
    wide = "歌曲、Live 类型、目标、演出能量（火）、卡组来源、固定与排除"
    _, sections = _layout_command_help_markdown(f"## 参数\n- {wide}：和 `/组卡` 相同\n- 短：值\n")

    lines = [line for line in sections[0].lines if line.text]
    label_line, value_line, short_line = lines
    # The wide label is its own bold line, never drawn in the fixed label column over its value.
    assert label_line.text == wide
    assert label_line.label == ""
    assert label_line.label_width == 0
    assert label_line.font_name != value_line.font_name
    # The value follows below, still aligned with the other values.
    assert value_line.text == "和 /组卡 相同"
    assert value_line.label == ""
    assert value_line.label_width == short_line.label_width == 190
    assert short_line.label == "短"
    assert short_line.text == "值"
