"""Snapshot .md files under STORE_DIR and ORM rows for document_versions."""

from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

PdfProfile = Literal["text", "plan"]

from sqlalchemy import select
from sqlalchemy.orm import Session

from iterthink import config
from iterthink.db.models import Document, DocumentVersion

SnapshotReason = Literal[
    "manual",
    "autosave",
    "pre_switch",
    "ai_apply",
    "ai_staged",
    "ai_proposal",
    "before_apply",
    "import",
]

SnapshotBucket = Literal["history", "import"]


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


def resolve_store_path(relpath: str) -> Path:
    """Resolve a path relative to ``STORE_DIR`` (must stay under store)."""
    p = (config.STORE_DIR / relpath).resolve()
    base = config.STORE_DIR.resolve()
    try:
        p.relative_to(base)
    except ValueError as e:
        raise ValueError("Invalid store-relative path") from e
    return p


def read_snapshot_body_by_relpath(relpath: str) -> str:
    p = (config.STORE_DIR / relpath).resolve()
    if not p.is_file() or "snapshots" not in p.parts:
        raise FileNotFoundError(relpath)
    return p.read_text(encoding="utf-8")


def pdf_asset_abs_path(relpath: str) -> Path:
    """Absolute path to a stored PDF asset (``pdf_assets/...``)."""
    p = resolve_store_path(relpath)
    if "pdf_assets" not in p.parts:
        raise ValueError("Not a pdf_assets path")
    return p


def docx_asset_abs_path(relpath: str) -> Path:
    """Absolute path to a stored DOCX asset (``docx_assets/...``)."""
    p = resolve_store_path(relpath)
    if "docx_assets" not in p.parts:
        raise ValueError("Not a docx_assets path")
    return p


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
    pdf_asset_relpath: str | None = None
    docx_asset_relpath: str | None = None
    pdf_profile: str | None = None


def snapshot_bucket(sn: SnapshotInfo) -> SnapshotBucket:
    """Bucket the snapshot into 'import' (file came from outside) or 'history' (everything else)."""
    if sn.reason == "import":
        return "import"
    return "history"


def snapshot_dropdown_text(sn: SnapshotInfo) -> str:
    """Visible label for dropdown rows: 'YYYY-MM-DD HH:MM' plus optional display_label.

    The bucket prefix (History / Import) is added by the caller so the same row text
    works under either heading.
    """
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(sn.created_at))
    label = (sn.display_label or "").strip()
    if label:
        return f"{ts} - {label}"
    return ts


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


def get_document_by_resolved_path(session: Session, resolved_doc: Path) -> Document | None:
    key = path_key_for(resolved_doc)
    return session.execute(select(Document).where(Document.path_key == key)).scalar_one_or_none()


def latest_version_id_for_document(session: Session, document_id: int) -> int | None:
    row = session.execute(
        select(DocumentVersion.id)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return int(row) if row is not None else None


def document_id_for_resolved_path(session: Session, resolved_doc: Path) -> int | None:
    doc = get_document_by_resolved_path(session, resolved_doc)
    return int(doc.id) if doc is not None else None


def update_document_last_disk_state(session: Session, resolved_doc: Path, *, body: str) -> None:
    """Record canonical .md mtime, size, and content hash after app read or write."""
    p = resolved_doc.resolve()
    if not p.is_file():
        return
    doc = get_or_create_document(session, p)
    st = p.stat()
    doc.last_disk_mtime_ns = int(st.st_mtime_ns)
    doc.last_disk_size = int(st.st_size)
    doc.last_disk_sha256 = content_sha256(body)


def is_document_disk_stale(session: Session, resolved_doc: Path) -> bool:
    """True if the file on disk no longer matches ``last_disk_*`` (e.g. edited outside the app)."""
    p = resolved_doc.resolve()
    if not p.is_file():
        return False
    key = path_key_for(p)
    doc = session.execute(select(Document).where(Document.path_key == key)).scalar_one_or_none()
    if doc is None or doc.last_disk_mtime_ns is None:
        return False
    try:
        st = p.stat()
    except OSError:
        return False
    if int(st.st_mtime_ns) != int(doc.last_disk_mtime_ns) or int(st.st_size) != int(doc.last_disk_size):
        return True
    if doc.last_disk_sha256:
        try:
            disk_body = p.read_text(encoding="utf-8")
        except OSError:
            return True
        return content_sha256(disk_body) != doc.last_disk_sha256
    return False


def refresh_document_last_disk_state_from_disk(session: Session, resolved_doc: Path) -> None:
    """Re-read disk and refresh ``last_disk_*`` without changing the editor (e.g. user chose to keep local edits)."""
    p = resolved_doc.resolve()
    if not p.is_file():
        return
    body = p.read_text(encoding="utf-8")
    update_document_last_disk_state(session, p, body=body)


def _pdf_assets_dir_for_doc(resolved_doc: Path) -> Path:
    pk = path_key_for(resolved_doc)
    d = config.STORE_DIR / "pdf_assets" / pk
    d.mkdir(parents=True, exist_ok=True)
    return d


def _docx_assets_dir_for_doc(resolved_doc: Path) -> Path:
    pk = path_key_for(resolved_doc)
    d = config.STORE_DIR / "docx_assets" / pk
    d.mkdir(parents=True, exist_ok=True)
    return d


def persist_version_snapshot(
    session: Session,
    resolved_doc: Path,
    body: str,
    reason: SnapshotReason,
    *,
    skip_if_unchanged_sha: bool = True,
    display_label: str | None = None,
    pdf_source_path: Path | None = None,
    docx_source_path: Path | None = None,
    pdf_profile: PdfProfile | None = None,
) -> int | None:
    """
    Write snapshot file and insert ``DocumentVersion``.
    If ``skip_if_unchanged_sha`` and the latest version for this document has the same sha, return None.
    If ``pdf_source_path`` is set, copies the file into ``pdf_assets/<path_key>/<version_id>.pdf``.
    If ``docx_source_path`` is set, copies into ``docx_assets/<path_key>/<version_id>.docx``.
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
        pdf_asset_relpath=None,
        docx_asset_relpath=None,
        pdf_profile=None,
    )
    session.add(ver)
    session.flush()
    pk = path_key_for(resolved_doc)
    if pdf_source_path is not None and pdf_source_path.is_file():
        dest_dir = _pdf_assets_dir_for_doc(resolved_doc)
        dest = dest_dir / f"{ver.id}.pdf"
        shutil.copy2(pdf_source_path, dest)
        ver.pdf_asset_relpath = f"pdf_assets/{pk}/{ver.id}.pdf"
        if pdf_profile is not None:
            ver.pdf_profile = pdf_profile
    if docx_source_path is not None and docx_source_path.is_file():
        dest_dir = _docx_assets_dir_for_doc(resolved_doc)
        dest = dest_dir / f"{ver.id}.docx"
        shutil.copy2(docx_source_path, dest)
        ver.docx_asset_relpath = f"docx_assets/{pk}/{ver.id}.docx"
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
                pdf_asset_relpath=v.pdf_asset_relpath,
                docx_asset_relpath=v.docx_asset_relpath,
                pdf_profile=v.pdf_profile,
            )
        )
    return out


def second_newest_history_autosave_version_id(snaps: list[SnapshotInfo]) -> int | None:
    """From ``list_snapshots`` (newest first): second history ``autosave`` row.

    The newest autosave usually matches the current draft; the next row is a useful
    default compare target when opening History.
    """
    autos = [s for s in snaps if s.reason == "autosave" and snapshot_bucket(s) == "history"]
    if len(autos) < 2:
        return None
    return autos[1].version_id


def get_version_pdf_relpath(session: Session, version_id: int) -> str | None:
    row = session.get(DocumentVersion, version_id)
    if row is None:
        return None
    return row.pdf_asset_relpath


def list_pdf_version_options(session: Session, resolved_doc: Path) -> list[tuple[int, str]]:
    """
    Versions that store a PDF asset, newest first.
    Each label is suitable for a dropdown (includes PDF marker and optional profile).
    """
    key = path_key_for(resolved_doc)
    doc = session.execute(select(Document).where(Document.path_key == key)).scalar_one_or_none()
    if doc is None:
        return []
    rows = session.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc.id)
        .where(DocumentVersion.pdf_asset_relpath.isnot(None))
        .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
    ).scalars().all()
    out: list[tuple[int, str]] = []
    for v in rows:
        # Legacy rows from removed Word→PDF layout import path.
        if (v.pdf_profile or "").strip() == "docx_layout":
            continue
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(v.created_at))
        prof = (v.pdf_profile or "").strip()
        prof_bit = f" · {prof}" if prof else ""
        label = f"#{v.id}  {ts}  ({v.reason})  PDF{prof_bit}"
        out.append((v.id, label))
    return out


def list_plan_pdf_version_options(session: Session, resolved_doc: Path) -> list[tuple[int, str]]:
    """
    Like ``list_pdf_version_options`` but only versions with ``pdf_profile == "plan"``.

    Used for the History plan-compare bar (baseline / candidate / overlay), which only
    applies when comparing two drawing-style PDF snapshots.
    """
    key = path_key_for(resolved_doc)
    doc = session.execute(select(Document).where(Document.path_key == key)).scalar_one_or_none()
    if doc is None:
        return []
    rows = session.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc.id)
        .where(DocumentVersion.pdf_asset_relpath.isnot(None))
        .where(DocumentVersion.pdf_profile == "plan")
        .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
    ).scalars().all()
    out: list[tuple[int, str]] = []
    for v in rows:
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(v.created_at))
        prof_bit = " · plan"
        label = f"#{v.id}  {ts}  ({v.reason})  PDF{prof_bit}"
        out.append((v.id, label))
    return out


def latest_pdf_version_for_document(session: Session, resolved_doc: Path) -> tuple[int, str] | None:
    """Most recent version that has a stored PDF asset, or None."""
    key = path_key_for(resolved_doc)
    doc = session.execute(select(Document).where(Document.path_key == key)).scalar_one_or_none()
    if doc is None:
        return None
    row = session.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc.id)
        .where(DocumentVersion.pdf_asset_relpath.isnot(None))
        .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None or not row.pdf_asset_relpath:
        return None
    return (row.id, row.pdf_asset_relpath)


def document_has_any_pdf(session: Session, resolved_doc: Path) -> bool:
    return latest_pdf_version_for_document(session, resolved_doc) is not None


def get_version_docx_relpath(session: Session, version_id: int) -> str | None:
    row = session.get(DocumentVersion, version_id)
    if row is None:
        return None
    return row.docx_asset_relpath


def latest_docx_version_for_document(session: Session, resolved_doc: Path) -> tuple[int, str] | None:
    key = path_key_for(resolved_doc)
    doc = session.execute(select(Document).where(Document.path_key == key)).scalar_one_or_none()
    if doc is None:
        return None
    row = session.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc.id)
        .where(DocumentVersion.docx_asset_relpath.isnot(None))
        .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None or not row.docx_asset_relpath:
        return None
    return (row.id, row.docx_asset_relpath)


def document_has_any_docx(session: Session, resolved_doc: Path) -> bool:
    return latest_docx_version_for_document(session, resolved_doc) is not None


def load_version_body(session: Session, version_id: int) -> str:
    row = session.get(DocumentVersion, version_id)
    if row is None:
        raise KeyError(version_id)
    return read_snapshot_body_by_relpath(row.snapshot_relpath)


def get_version_row(session: Session, version_id: int) -> DocumentVersion | None:
    return session.get(DocumentVersion, version_id)


def latest_pdf_version_detail(session: Session, resolved_doc: Path) -> tuple[int, str, str | None] | None:
    """
    Most recent version with a PDF asset: (version_id, pdf_asset_relpath, pdf_profile).
    """
    hit = latest_pdf_version_for_document(session, resolved_doc)
    if hit is None:
        return None
    vid, rel = hit
    row = session.get(DocumentVersion, vid)
    if row is None:
        return None
    return (vid, rel, row.pdf_profile)


def delete_document_row_if_any(session: Session, resolved_doc: Path) -> None:
    """Remove the ``Document`` row if present (``DocumentVersion`` rows cascade)."""
    key = path_key_for(resolved_doc.resolve())
    doc = session.execute(select(Document).where(Document.path_key == key)).scalar_one_or_none()
    if doc is not None:
        session.delete(doc)


def purge_document_store_dirs(resolved_doc: Path) -> None:
    """Remove snapshot / PDF / DOCX asset directories keyed by this document path."""
    pk = path_key_for(resolved_doc.resolve())
    base = config.STORE_DIR.resolve()
    for sub in ("snapshots", "pdf_assets", "docx_assets"):
        d = (config.STORE_DIR / sub / pk).resolve()
        try:
            d.relative_to(base)
        except ValueError:
            continue
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
