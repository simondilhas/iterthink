"""Background workspace-wide RAG indexing."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

IndexOutcome = Literal["updated", "unchanged", "no_body", "empty", "missing"]

from iterthink import config
from iterthink.ai.local_embedding import LOCAL_EMBEDDING_MODEL_ID
from iterthink.compare.paragraph_semantics import embed_texts_cached, text_hash
from iterthink.persistence import content_repo, store_db
from iterthink.services.rag.chunking import build_parent_child_chunks, document_title
from iterthink.services.rag.enrichment import enrich_child, enrichment_allowed_for_tier
from iterthink.services.rag.project_scope import project_scope_from_lineage
from iterthink.studio.tree import is_excluded_from_doc_tree

ProgressCb = Callable[[int, int, str], Awaitable[None] | None]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexAllResult:
    updated: int
    scanned: int
    skipped_empty: int
    skipped_unchanged: int
    skipped_no_body: int


def _doc_embed_key(lineage_id: str, content_version_id: int) -> str:
    return f"rag_idx::{lineage_id}::{content_version_id}"


def _read_disk_body(resolved: Path) -> str | None:
    try:
        return resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _load_body_for_index(
    session: Any,
    resolved: Path,
    lineage: Any,
) -> tuple[str, int, str] | None:
    """Load text for indexing: PBS latest version, else on-disk + snapshot."""
    lid = lineage.lineage_id
    vid = content_repo.latest_version_id_for_lineage(session, lid)
    if vid is not None:
        body = content_repo.load_version_body(session, vid)
        sha = content_repo.content_sha256(body)
        return body, int(vid), sha

    body = _read_disk_body(resolved)
    if body is None:
        return None
    sha = content_repo.content_sha256(body)
    new_vid = content_repo.persist_version_snapshot(
        session, resolved, body, "manual", skip_if_unchanged_sha=False
    )
    if new_vid is not None:
        return body, int(new_vid), sha
    vid = content_repo.latest_version_id_for_lineage(session, lid)
    if vid is not None:
        body = content_repo.load_version_body(session, vid)
        sha = content_repo.content_sha256(body)
        return body, int(vid), sha
    return None


def iter_workspace_markdown_paths() -> list[Path]:
    root = config.DOCUMENTS.resolve()
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(root.rglob("*.md")):
        if is_excluded_from_doc_tree(p):
            continue
        out.append(p.resolve())
    return out


async def index_document_path(
    session: Any,
    conn: Any,
    resolved: Path,
    *,
    enrichment_mode: str = "skip",
    ki_tier: str = "local",
    llm: Any | None = None,
    llm_model: str | None = None,
    embed_model_id: str = LOCAL_EMBEDDING_MODEL_ID,
    latest_version_only: bool = True,
    force_reindex: bool = False,
) -> IndexOutcome:
    """Index one markdown file. Outcome describes whether the RAG index changed."""
    del latest_version_only  # search-only; indexing always uses PBS latest or on-disk fallback
    resolved = resolved.resolve()
    if not resolved.is_file():
        return "missing"

    lineage = content_repo.get_or_create_lineage(session, resolved)
    lid = lineage.lineage_id
    project_id, project_slug = project_scope_from_lineage(session, resolved, lineage)
    loaded = _load_body_for_index(session, resolved, lineage)
    if loaded is None:
        return "no_body"
    body, vid, sha = loaded
    if not body.strip():
        store_db.rag_delete_for_lineage(conn, lid)
        conn.commit()
        return "empty"

    existing = store_db.rag_lineage_index_get(conn, lid)
    if existing is not None and not force_reindex:
        if (
            str(existing["content_sha"]) == sha
            and int(existing["content_version_id"]) == int(vid)
        ):
            session.commit()
            return "unchanged"

    session.commit()

    store_db.rag_delete_for_version(conn, int(vid))
    title = document_title(body, resolved.name)
    overlap = int(getattr(config, "RAG_OVERLAP_CHARS", 200))
    parents = build_parent_child_chunks(body, doc_title=title, overlap_chars=overlap)
    if not parents:
        store_db.rag_lineage_index_put(
            conn,
            lineage_id=lid,
            content_version_id=int(vid),
            content_sha=sha,
            enrichment_mode="skip",
            project_id=project_id,
            project_slug=project_slug,
        )
        conn.commit()
        return "updated"

    do_enrich = enrichment_allowed_for_tier(ki_tier, enrichment_mode) and llm is not None
    mode_record = "local" if do_enrich else "skip"

    flat: list[tuple[Any, Any]] = []
    embed_inputs: list[str] = []

    for parent in parents:
        for child in parent.children:
            enriched = child
            if do_enrich and llm_model:
                summary, questions = await enrich_child(
                    raw=child.raw_text,
                    header=child.section_header,
                    doc_title=title,
                    llm=llm,
                    model=llm_model,
                )
                enriched = replace(child, summary=summary, questions=questions)
            embed_text = enriched.build_embed_text(doc_title=title, project_label=project_slug)
            embed_inputs.append(embed_text)
            flat.append((parent, enriched))

    dk = _doc_embed_key(lid, int(vid))
    await embed_texts_cached(conn, dk, embed_inputs)

    parent_ids: dict[int, int] = {}
    for (parent, child), embed_text in zip(flat, embed_inputs, strict=True):
        h = text_hash(embed_text)
        row_cache = conn.execute(
            """SELECT vec_rowid FROM paragraph_embedding_cache
               WHERE lineage_id = ? AND input_hash = ? AND embed_model_id = ?""",
            (dk, h, embed_model_id),
        ).fetchone()
        if row_cache is None:
            continue
        rid = int(row_cache[0])
        if parent.parent_index not in parent_ids:
            parent_ids[parent.parent_index] = store_db.rag_parent_insert(
                conn,
                lineage_id=lid,
                content_version_id=int(vid),
                parent_index=parent.parent_index,
                doc_title=title,
                section_header=parent.section_header,
                parent_text=parent.parent_text,
                content_sha=sha,
            )
        parent_id = parent_ids[parent.parent_index]
        store_db.rag_child_insert(
            conn,
            parent_id=parent_id,
            lineage_id=lid,
            content_version_id=int(vid),
            slot_index=child.slot_index,
            raw_text=child.raw_text,
            summary=child.summary,
            questions_json=json.dumps(list(child.questions)),
            embed_text=embed_text,
            overlap_text=child.overlap_text,
            input_hash=h,
            vec_rowid=rid,
            embed_model_id=embed_model_id,
        )

    store_db.rag_lineage_index_put(
        conn,
        lineage_id=lid,
        content_version_id=int(vid),
        content_sha=sha,
        enrichment_mode=mode_record,
        project_id=project_id,
        project_slug=project_slug,
    )
    conn.commit()
    return "updated"


async def index_all_documents(
    session: Any,
    conn: Any,
    *,
    enrichment_mode: str = "skip",
    ki_tier: str = "local",
    llm: Any | None = None,
    llm_model: str | None = None,
    progress_cb: ProgressCb | None = None,
    latest_version_only: bool = True,
    force_reindex: bool = False,
) -> IndexAllResult:
    """Index all workspace markdown files."""
    paths = iter_workspace_markdown_paths()
    total = len(paths)
    updated = 0
    skipped_empty = 0
    skipped_unchanged = 0
    skipped_no_body = 0
    for i, path in enumerate(paths):
        if progress_cb is not None:
            maybe = progress_cb(i + 1, total, path.name)
            if maybe is not None:
                await maybe
        try:
            outcome = await index_document_path(
                session,
                conn,
                path,
                enrichment_mode=enrichment_mode,
                ki_tier=ki_tier,
                llm=llm,
                llm_model=llm_model,
                latest_version_only=latest_version_only,
                force_reindex=force_reindex,
            )
            session.commit()
        except asyncio.CancelledError:
            session.rollback()
            raise
        except BaseException:
            session.rollback()
            _log.warning("RAG index failed for %s", path, exc_info=True)
            continue
        if outcome == "updated":
            updated += 1
        elif outcome == "unchanged":
            skipped_unchanged += 1
        elif outcome == "empty":
            skipped_empty += 1
        elif outcome == "no_body":
            skipped_no_body += 1
    return IndexAllResult(
        updated=updated,
        scanned=total,
        skipped_empty=skipped_empty,
        skipped_unchanged=skipped_unchanged,
        skipped_no_body=skipped_no_body,
    )
