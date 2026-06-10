"""Project-scoped, classification-aware Impact context file selection."""

from __future__ import annotations

from pathlib import Path

from iterthink import config
from iterthink.contract.document_classification import classify_document, functions_match_check
from iterthink.persistence import content_repo
from iterthink.db.session import session_scope
from iterthink.contract.document_function import IMPACT_CHECK_CONTEXT_FUNCTIONS
from iterthink.services.rag.project_scope import project_slug_for_path
from iterthink.studio.tree import build_md_tree


def _iter_md_paths(root: Path) -> list[Path]:
    try:
        tree = build_md_tree(root)
    except OSError:
        return []

    def walk(node: dict) -> list[Path]:
        out: list[Path] = []
        for _name, fpath in node.get("_files", []):
            out.append(fpath.resolve())
        for key, sub in node.items():
            if key != "_files" and isinstance(sub, dict):
                out.extend(walk(sub))
        return out

    return walk(tree)


def _under_project(path: Path, project_slug: str | None) -> bool:
    if project_slug is None:
        return True
    slug = project_slug_for_path(path)
    return slug == project_slug


def _is_shared_norm_library(path: Path) -> bool:
    """Norm corpora often live outside a single project folder."""
    try:
        rel = path.resolve().relative_to(config.DOCUMENTS.resolve())
    except ValueError:
        return False
    if len(rel.parts) < 2:
        return False
    top = rel.parts[0].casefold()
    return any(k in top for k in ("norm", "sia", "din", "vorschrift", "standard"))


def path_is_impact_context_candidate(
    path: Path,
    *,
    check_id: str,
    target_path: Path | None,
    include_shared_norms: bool = True,
) -> bool:
    """Whether *path* should be offered as Impact context for *check_id*."""
    resolved = path.resolve()
    if target_path is not None and resolved == target_path.resolve():
        return False
    if not resolved.is_file() or resolved.suffix.lower() != ".md":
        return False

    allowed = IMPACT_CHECK_CONTEXT_FUNCTIONS.get(check_id)
    if not allowed:
        return True

    stored_attrs: dict | None = None
    try:
        with session_scope() as session:
            row = content_repo.get_artifact_lineage_by_path(session, resolved)
            if row is not None:
                stored_attrs = content_repo.content_attrs(row)
    except Exception:  # noqa: BLE001
        stored_attrs = None
    cl = classify_document(resolved, stored_attrs=stored_attrs)
    if not functions_match_check(cl.document_functions, allowed):
        return False

    project_slug = project_slug_for_path(target_path) if target_path else None
    if check_id == "norm_compliance" and include_shared_norms and _is_shared_norm_library(resolved):
        return True
    return _under_project(resolved, project_slug)


def default_context_paths(
    *,
    check_id: str,
    target_path: Path | None,
) -> list[Path]:
    """Classified, project-scoped default context files for an Impact check."""
    root = config.DOCUMENTS
    if not root.is_dir():
        return []
    candidates = [
        p
        for p in _iter_md_paths(root)
        if path_is_impact_context_candidate(p, check_id=check_id, target_path=target_path)
    ]
    candidates.sort(key=lambda p: str(p).casefold())
    return candidates


def project_scoped_paths(
    *,
    target_path: Path | None,
    exclude_open: bool = True,
) -> list[Path]:
    """All .md files in the same project folder (classification not applied)."""
    root = config.DOCUMENTS
    if not root.is_dir():
        return []
    project_slug = project_slug_for_path(target_path) if target_path else None
    out: list[Path] = []
    for p in _iter_md_paths(root):
        if exclude_open and target_path is not None and p.resolve() == target_path.resolve():
            continue
        if _under_project(p, project_slug):
            out.append(p)
    out.sort(key=lambda x: str(x).casefold())
    return out
