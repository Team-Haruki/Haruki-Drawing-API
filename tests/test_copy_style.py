"""Drawn Chinese text follows the HarukiBot copy spec (Haruki-Cloud AGENTS.md §12, decisions D and E).

The rules mirror Cloud's catalog lint (``internal/i18n/catalog_lint_test.go``): a full-width colon after
Chinese, full-width parentheses in Chinese, a space between Chinese and Latin letters or digits, and the
glossary's banned words. Every string literal under ``src/sekai`` that contains Chinese is checked, except
docstrings, log calls and exception messages (those never reach an image). f-string placeholders count as
neutral separators, like ``{{.Name}}`` actions in the Cloud catalog.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "sekai"
HAN = "㐀-䶿一-鿿"
SEPARATOR = "⁣"

_HAN_COLON = re.compile(f"[{HAN}]:")
_HAN_PAREN = re.compile(f"[{HAN}][()]|[()][{HAN}]|\\([^()]*[{HAN}][^()]*\\)")
_HAN_LATIN = re.compile(f"[{HAN}][A-Za-z0-9]|[A-Za-z0-9][{HAN}]")
_HAS_HAN = re.compile(f"[{HAN}]")
# The glossary's banned display words (Cloud AGENTS.md §12.5) that a drawer could plausibly write.
_BANNED = (
    "您",
    "请稍后重试",
    "命令",
    "Toolbox",
    "SekaiAPI",
    "Tracker",
    "masterdata",
    "MasterData",
    "Cloud",
    "suite",
    "Suite数据",
    "Mysekai",
    "套装",
    "档线",
    "分数线",
    "体力",
    "协力",
    "主队",
    "队伍",
    "自定义谱面",
    "虚拟LIVE",
    '"',
    "...",
)
_LOG_CALL = re.compile(r"(?:^|\.)(?:logger|log|logging|_perf_logger)\.\w+$|warnings\.warn$")

# (file relative to src/sekai, literal) pairs that are not display text.
_ALLOWED = {
    # Supply labels older Cloud releases sent; matched as input, never drawn.
    ("card/drawer.py", "WL限定"),
    ("card/drawer.py", "Fes限定"),
    ("card/drawer.py", "CFes限定"),
    ("card/drawer.py", "BFes限定"),
    # An exception message (raised through a constant), never drawn.
    ("base/utils.py", "图片路径不能为空(None)"),
    # Durations follow Cloud's FormatDuration ("45秒"): no space between a number and its time unit.
    ("base/utils.py", "0秒"),
    # Characters the custom-profile TMP renderer treats as decorative; a character set, not text.
    ("profile/custom_profile/renderer.py", "●○■█▲△▼▽◣◢◤◥⌒～〜∽︵︶︿()（）【】、，,.-·|^*/\\I丶>〇 "),
}


class _LiteralCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.skip: set[int] = set()
        self.found: list[tuple[int, str]] = []

    def _skip_tree(self, node: ast.AST) -> None:
        self.skip.update(id(child) for child in ast.walk(node))

    def visit_Expr(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            self.skip.add(id(node.value))  # docstrings and bare string statements
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        self._skip_tree(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _LOG_CALL.search(ast.unparse(node.func)):
            self._skip_tree(node)
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        if id(node) in self.skip:
            return
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(value.value)
                self.skip.add(id(value))
            else:
                parts.append(SEPARATOR)
        self.found.append((node.lineno, "".join(parts)))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if id(node) not in self.skip and isinstance(node.value, str):
            self.found.append((node.lineno, node.value))


def copy_findings(text: str) -> list[str]:
    if not _HAS_HAN.search(text):
        return []
    findings = [f"banned {term!r}" for term in _BANNED if term in text]
    if match := _HAN_COLON.search(text):
        findings.append(f"half-width colon {match.group()!r}")
    if match := _HAN_PAREN.search(text.replace("(5v5)", "")):
        findings.append(f"half-width parenthesis {match.group()!r}")
    if match := _HAN_LATIN.search(text):
        findings.append(f"no space between Chinese and Latin {match.group()!r}")
    return findings


def test_copy_findings_apply_the_cloud_rules() -> None:
    assert copy_findings("数据更新于: 刚刚") == ["half-width colon '于:'"]
    assert copy_findings("(付费)") == ["half-width parenthesis '(付'"]
    assert copy_findings("WL活动") == ["no space between Chinese and Latin 'L活'"]
    assert copy_findings("核对体力") == ["banned '体力'"]
    assert copy_findings(f"距离活动结束还有 {SEPARATOR}") == []
    assert copy_findings("欢乐嘉年华(5v5)") == []
    assert copy_findings("plain English: (ok)") == []


def test_drawn_text_follows_the_copy_spec() -> None:
    problems = []
    for path in sorted(SOURCE.rglob("*.py")):
        if path.name.endswith(".real.py"):
            continue
        relative = path.relative_to(SOURCE).as_posix()
        collector = _LiteralCollector()
        collector.visit(ast.parse(path.read_text(encoding="utf-8"), str(path)))
        for line, text in collector.found:
            if (relative, text) in _ALLOWED:
                continue
            for finding in copy_findings(text):
                problems.append(f"src/sekai/{relative}:{line}: {finding} in {text!r}")
    assert not problems, "\n".join(problems)
