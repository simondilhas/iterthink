"""Markdown file tree under a root path."""

from pathlib import Path
from typing import Any

from iterthink import config


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
    except (ValueError, AttributeError):
        if config.STORE_DIR.name in path.parts or ".iterthink" in path.parts:
            return True
    return False


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
