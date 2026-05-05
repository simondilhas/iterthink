"""Snapshot .md files under STORE_DIR and ORM rows for document_versions."""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from iterthink import config
from iterthink.db.models import Document, DocumentVersion

SnapshotReason = Literal["manual", "autosave", "pre_switch", "ai_apply"]


def path_key_for(resolved: Path) -> str:
    return hashlib.sha256(resolved.resolve().as_posix().encode("utf-8")).hexdigest()


def _snapshots_root() -> Path:
    root = config.STORE_DIR / "snapshots"
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_snapshot_file(resolved_doc: Path, body: str) -> tuple[str, str]:
    """
    Write body to ``snapshots/<path_key>/<id>.md``.
    Returns ``(snapshot_id, relative_path_from_store_dir)``.
    """
    pk = path_key_for(resolved_doc)
    snap_id = uuid.uuid4().hex
    dir_path = _snapshots_root() / pk
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / f"{snap_id}.md"
    file_path.write_text(body, encoding="utf-8")
    rel = f"snapshots/{pk}/{snap_id}.md"
    return snap_id, rel


def read_snapshot_body_by_relpath(relpath: str) -> str:
    p = (config.STORE_DIR / relpath).resolve()
    if not p.is_file() or "snapshots" not in p.parts:
        raise FileNotFoundError(relpath)
    return p.read_text(encoding="utf-8")


def content_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass
class SnapshotInfo:
    version_id: int
    snapshot_id: str
    created_at: float
    reason: str
    content_sha256: str
    display_label: str | None = None


def snapshot_display_text(sn: SnapshotInfo) -> str:
    """Label for UI selectors (tab Comparison, menus)."""
    if sn.display_label and sn.display_label.strip():
        return sn.display_label.strip()
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(sn.created_at))
    return f"{ts}  ({sn.reason})"


def update_document_path_after_rename(
    session: Session, old_resolved: Path, new_resolved: Path
) -> Literal["ok", "collision"]:
    """
    After ``old_resolved`` was renamed on disk to ``new_resolved``, move the
    ``Document`` row from the old path key to the new one so version history
    stays attached. Snapshot blob paths on disk are unchanged.
    """
    old_key = path_key_for(old_resolved)
    new_key = path_key_for(new_resolved)
    doc = session.execute(select(Document).where(Document.path_key == old_key)).scalar_one_or_none()
    if doc is None:
        return "ok"
    other = session.execute(select(Document).where(Document.path_key == new_key)).scalar_one_or_none()
    if other is not None and other.id != doc.id:
        return "collision"
    doc.path_key = new_key
    doc.resolved_path = str(new_resolved.resolve())
    return "ok"


def update_document_paths_after_dir_rename(
    session: Session, old_dir_resolved: Path, new_dir_resolved: Path
) -> Literal["ok", "collision"]:
    """
    After ``old_dir_resolved`` was renamed on disk to ``new_dir_resolved``, update
    every ``Document`` whose stored path was under the old directory so version
    history stays attached.
    """
    old_b = old_dir_resolved.resolve()
    new_b = new_dir_resolved.resolve()
    all_docs = list(session.scalars(select(Document)).all())
    planned: list[tuple[Document, Path]] = []
    for doc in all_docs:
        try:
            rp = Path(doc.resolved_path).resolve()
            rel = rp.relative_to(old_b)
        except ValueError:
            continue
        planned.append((doc, (new_b / rel).resolve()))

    updating_ids = {doc.id for doc, _ in planned}
    for doc, np in planned:
        nk = path_key_for(np)
        for other in all_docs:
            if other.id == doc.id or other.id in updating_ids:
                continue
            if other.path_key == nk:
                return "collision"

    for doc, np in planned:
        doc.path_key = path_key_for(np)
        doc.resolved_path = str(np.resolve())
    return "ok"


def get_or_create_document(session: Session, resolved_doc: Path) -> Document:
    key = path_key_for(resolved_doc)
    row = session.execute(select(Document).where(Document.path_key == key)).scalar_one_or_none()
    if row is not None:
        row.resolved_path = str(resolved_doc.resolve())
        return row
    now = time.time()
    doc = Document(path_key=key, resolved_path=str(resolved_doc.resolve()), created_at=now)
    session.add(doc)
    session.flush()
    return doc


def persist_version_snapshot(
    session: Session,
    resolved_doc: Path,
    body: str,
    reason: SnapshotReason,
    *,
    skip_if_unchanged_sha: bool = True,
    display_label: str | None = None,
) -> int | None:
    """
    Write snapshot file and insert ``DocumentVersion``.
    If ``skip_if_unchanged_sha`` and the latest version for this document has the same sha, return None.
    Returns new version id or None.
    """
    sha = content_sha256(body)
    doc = get_or_create_document(session, resolved_doc)
    last = (
        session.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
            .limit(1)
        )
        .scalar_one_or_none()
    )
    if skip_if_unchanged_sha and last is not None and last.content_sha256 == sha:
        return None

    _, relpath = write_snapshot_file(resolved_doc, body)
    now = time.time()
    parent_id = last.id if last else None
    ver = DocumentVersion(
        document_id=doc.id,
        snapshot_relpath=relpath,
        content_sha256=sha,
        created_at=now,
        reason=reason,
        parent_version_id=parent_id,
        display_label=display_label.strip() if display_label and display_label.strip() else None,
    )
    session.add(ver)
    session.flush()
    # snapshot_id from filename
    return ver.id


def list_snapshots(session: Session, resolved_doc: Path) -> list[SnapshotInfo]:
    key = path_key_for(resolved_doc)
    doc = session.execute(select(Document).where(Document.path_key == key)).scalar_one_or_none()
    if doc is None:
        return []
    rows = session.execute(
        select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.created_at.desc())
    ).scalars().all()
    out: list[SnapshotInfo] = []
    for v in rows:
        name = Path(v.snapshot_relpath).name
        snap_id = name.replace(".md", "")
        out.append(
            SnapshotInfo(
                version_id=v.id,
                snapshot_id=snap_id,
                created_at=v.created_at,
                reason=v.reason,
                content_sha256=v.content_sha256,
                display_label=v.display_label,
            )
        )
    return out


def load_version_body(session: Session, version_id: int) -> str:
    row = session.get(DocumentVersion, version_id)
    if row is None:
        raise KeyError(version_id)
    return read_snapshot_body_by_relpath(row.snapshot_relpath)


def get_version_row(session: Session, version_id: int) -> DocumentVersion | None:
    return session.get(DocumentVersion, version_id)
