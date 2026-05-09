"""Markdown file tree under a root path."""

from pathlib import Path
from typing import Any

from iterthink import config

PROJECT_CONTEXT_BASENAME = "PROJECT_CONTEXT.md"


def project_context_markdown(project_display_name: str) -> str:
    """Default body for a new project's context note (Markdown)."""
    name = project_display_name.strip() or "Untitled"
    return f"""# Project Context: {name}

## 1. Project Parameters
* **Scope:** What are the physical or conceptual boundaries of this project?
* **Location/Jurisdiction:** (e.g., Basel, Switzerland) — This triggers the correct regional norms.
* **Classification:** (e.g., Residential, Industrial, High-Stakes, Prototype).

## 2. Hard Constraints (Non-Negotiable)
* **Budget Ceiling:** Total financial limit or "Cost per Unit" target.
* **Regulatory Framework:** Specific laws, building codes, or safety standards (e.g., SIA, DIN, Eurocode).
* **Sustainability Requirements:** (e.g., Net Zero, specific CO₂ limits, Minergie-P).

## 3. Technical Strategy
* **Materiality:** Mandatory or forbidden materials/technologies (e.g., "Circular materials only").
* **Performance Targets:** Specific engineering or functional benchmarks (e.g., "Fire resistance class R90").
* **Methodology:** (e.g., Modular construction, Agile development, Lean management).

## 4. Organizational Context
* **Stakeholder Priorities:** (e.g., "Investor focus is on longevity over initial cost").
* **Legacy/Existing Conditions:** What pre-existing data or structures must be respected?

---
"""


def add_md_file(tree: dict[str, Any], rel_parts: tuple[str, ...], full_path: Path) -> None:
    if not rel_parts:
        return
    node = tree
    for part in rel_parts[:-1]:
        node = node.setdefault(part, {})
    leaf = rel_parts[-1]
    node.setdefault("_files", []).append((leaf, full_path))


def add_dir_branch(tree: dict[str, Any], rel_parts: tuple[str, ...]) -> None:
    if not rel_parts:
        return
    node = tree
    for part in rel_parts:
        node = node.setdefault(part, {})


def _scan_excluded(path: Path) -> bool:
    try:
        if path.is_relative_to(config.STORE_DIR):
            return True
        if path.is_relative_to(config.IMPORT_ASSETS_DIR):
            return True
    except (ValueError, AttributeError):
        if config.STORE_DIR.name in path.parts or ".iterthink" in path.parts:
            return True
        if config.IMPORT_ASSETS_DIR.name in path.parts:
            return True
    return False


def is_excluded_from_doc_tree(path: Path) -> bool:
    """True for store paths and other dirs hidden from the documents file tree."""
    return _scan_excluded(path)


def filter_md_tree(node: dict[str, Any], query: str) -> dict[str, Any]:
    """Return a copy of the tree subtree containing only paths that match ``query``."""
    q = query.strip().lower()
    if not q:
        return node
    out: dict[str, Any] = {}
    for dirname in sorted(k for k in node if k != "_files"):
        sub = node[dirname]
        if q in dirname.lower():
            out[dirname] = sub
            continue
        filtered = filter_md_tree(sub, query)
        if filtered:
            out[dirname] = filtered
    hit_files = [(fn, fp) for fn, fp in node.get("_files", []) if q in fn.lower()]
    if hit_files:
        out["_files"] = hit_files
    return out


def build_md_tree(root: Path) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    if not root.is_dir():
        return tree
    for p in sorted(root.rglob("*.md")):
        if _scan_excluded(p):
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        add_md_file(tree, rel.parts, p)
    for d in sorted(root.rglob("*")):
        if not d.is_dir() or _scan_excluded(d):
            continue
        try:
            rel = d.relative_to(root)
        except ValueError:
            continue
        if rel.parts:
            add_dir_branch(tree, rel.parts)
    return tree
