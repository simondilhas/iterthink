"""Read-only markdown preview tweaks (e.g. task lists as visible checkboxes)."""

from __future__ import annotations

import re

# GFM task items: optional indent + list marker + "[ ]" / "[x]" + body (body may start with spaces).
_TASK_ITEM_LINE = re.compile(
    r"^(\s*)(?:[-*+]|\d+\.)\s+\[([ xX])\](.*)$",
    re.MULTILINE,
)


def markdown_preview_with_task_checkboxes(text: str) -> str:
    """Replace task list lines with ``indent + checkbox + body`` (no list marker).

    Unchecked (☐) and checked (☑) use the same body point size so they align visually.
    """

    def _repl(m: re.Match[str]) -> str:
        # Do not strip ``rest``: leading spaces after the checkbox are intentional indent.
        indent, inner, rest = m.group(1), m.group(2), (m.group(3) or "")
        checked = inner.strip().lower() == "x"
        mark = "\u2611" if checked else "\u2610"
        if rest.strip():
            sep = "" if rest.startswith((" ", "\t")) else " "
            return f"{indent}{mark}{sep}{rest}"
        return f"{indent}{mark}"

    return _TASK_ITEM_LINE.sub(_repl, text or "")
