"""Canonical PBS content store for text/plan artifacts (replaces version_storage)."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from iterthink import config
from iterthink.contract import enums as pbs
from iterthink.contract.version import CANONICAL_TYPE_VERSION
from iterthink.db.content_models import Content, ContentFileLink, FileRecord
from iterthink.persistence import paragraph_user_comments
from iterthink.persistence import store_db

PdfProfile = Literal["text", "plan"]

SnapshotReason = Literal[
    "manual",
    "autosave",
    "pre_switch",
    "ai_apply",
    "ai_staged",
    "ai_proposal",
    "review_edit",
    "before_apply",
    "before_spell_apply",
    "spell_apply",
    "import",
]

SnapshotBucket = Literal["history", "import"]

WORKSPACE_ID = 1
PROJECT_ID = 1


def path_key_for(resolved: Path) -> str:
    return hashlib.sha256(resolved.resolve().as_posix().encode("utf-8")).hexdigest()


def content_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _snapshots_root() -> Path:
    root = config.STORE_DIR / "snapshots"
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_snapshot_file(resolved_doc: Path, body: str) -> tuple[str, str]:
    pk = path_key_for(resolved_doc)
    snap_id = uuid.uuid4().hex
    dir_path = _snapshots_root() / pk
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / f"{snap_id}.md"
    file_path.write_text(body, encoding="utf-8")
    rel = f"snapshots/{pk}/{snap_id}.md"
    return snap_id, rel


def resolve_store_path(relpath: str) -> Path:
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
    p = resolve_store_path(relpath)
    if "pdf_assets" not in p.parts:
        raise ValueError("Not a pdf_assets path")
    return p


def docx_asset_abs_path(relpath: str) -> Path:
    p = resolve_store_path(relpath)
    if "docx_assets" not in p.parts:
        raise ValueError("Not a docx_assets path")
    return p


def content_attrs(row: Content) -> dict[str, Any]:
    return _attrs(row)


def _attrs(row: Content) -> dict[str, Any]:
    if not row.attributes:
        return {}
    try:
        data = json.loads(row.attributes)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _set_attrs(row: Content, data: dict[str, Any]) -> None:
    row.attributes = json.dumps(data, ensure_ascii=False)


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
    lineage_id: str | None = None
    version_no: int | None = None


def _lineage_id_for_path(resolved: Path) -> str:
    """Stable lineage UUID derived from path (deterministic per file)."""
    h = hashlib.sha256(f"lineage:{path_key_for(resolved)}".encode()).hexdigest()
    return str(uuid.UUID(h[:32]))


def _get_or_create_lineage_latest(session: Session, resolved_doc: Path) -> Content | None:
    lid = _lineage_id_for_path(resolved_doc)
    return session.execute(
        select(Content)
        .where(Content.lineage_id == lid)
        .where(Content.is_latest.is_(True))
        .limit(1)
    ).scalar_one_or_none()


def get_lineage_id_for_path(session: Session, resolved_doc: Path) -> str | None:
    row = _get_or_create_lineage_latest(session, resolved_doc)
    return row.lineage_id if row else None


def _lineage_anchor(session: Session, lineage_id: str) -> Content | None:
    return session.execute(
        select(Content)
        .where(Content.lineage_id == lineage_id)
        .where(Content.version_no == 0)
        .limit(1)
    ).scalar_one_or_none()


def _lineage_has_legacy_plan_profile(session: Session, lineage_id: str) -> bool:
    rows = session.scalars(
        select(Content)
        .where(Content.lineage_id == lineage_id)
        .where(Content.version_no > 0)
    ).all()
    for v in rows:
        if str(_attrs(v).get("pdf_profile") or "").strip() == "plan":
            return True
    return False


def get_lineage_artifact_kind(
    session: Session,
    *,
    resolved_doc: Path | None = None,
    lineage_id: str | None = None,
) -> str:
    lid = lineage_id
    if lid is None and resolved_doc is not None:
        row = get_artifact_lineage_by_path(session, resolved_doc)
        lid = row.lineage_id if row else None
    if lid is None:
        return pbs.ARTIFACT_KIND_TEXT_DOCUMENT
    anchor = _lineage_anchor(session, lid)
    if anchor is None:
        return pbs.ARTIFACT_KIND_TEXT_DOCUMENT
    return str(_attrs(anchor).get("artifact_kind") or pbs.ARTIFACT_KIND_TEXT_DOCUMENT)


def _set_lineage_artifact_kind(session: Session, lineage_id: str, kind: str) -> None:
    anchor = _lineage_anchor(session, lineage_id)
    if anchor is None:
        return
    attrs = _attrs(anchor)
    attrs["artifact_kind"] = kind
    _set_attrs(anchor, attrs)
    anchor.updated_at = time.time()
    session.flush()


def is_plan_lineage(session: Session, resolved_doc: Path) -> bool:
    if get_lineage_artifact_kind(session, resolved_doc=resolved_doc) == pbs.ARTIFACT_KIND_PLAN:
        return True
    row = get_artifact_lineage_by_path(session, resolved_doc)
    if row is None:
        return False
    return _lineage_has_legacy_plan_profile(session, row.lineage_id)


def get_or_create_lineage(session: Session, resolved_doc: Path) -> Content:
    existing = _get_or_create_lineage_latest(session, resolved_doc)
    if existing is not None:
        attrs = _attrs(existing)
        attrs["resolved_path"] = str(resolved_doc.resolve())
        attrs["path_key"] = path_key_for(resolved_doc)
        _set_attrs(existing, attrs)
        existing.name = resolved_doc.name
        return existing

    now = time.time()
    lid = _lineage_id_for_path(resolved_doc)
    row = Content(
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        lineage_id=lid,
        version_no=0,
        is_latest=True,
        contract_id=str(uuid.uuid4()),
        content_kind=pbs.CONTENT_KIND_ARTIFACT,
        canonical_type=pbs.CANONICAL_TYPE_ARTIFACT,
        canonical_type_version=CANONICAL_TYPE_VERSION,
        name=resolved_doc.name,
        source_system="iterthink",
        source_id=path_key_for(resolved_doc),
        created_at=now,
        updated_at=now,
    )
    attrs = {
        "artifact_kind": pbs.ARTIFACT_KIND_TEXT_DOCUMENT,
        "resolved_path": str(resolved_doc.resolve()),
        "path_key": path_key_for(resolved_doc),
    }
    _set_attrs(row, attrs)
    session.add(row)
    session.flush()
    return row


def get_artifact_lineage_by_path(session: Session, resolved_doc: Path) -> Content | None:
    return _get_or_create_lineage_latest(session, resolved_doc)


# Back-compat aliases for callers still using document naming
get_document_by_resolved_path = get_artifact_lineage_by_path


def latest_version_id_for_lineage(session: Session, lineage_id: str) -> int | None:
    row = session.execute(
        select(Content.id)
        .where(Content.lineage_id == lineage_id)
        .where(Content.version_no > 0)
        .order_by(Content.version_no.desc(), Content.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return int(row) if row is not None else None


def lineage_id_for_resolved_path(session: Session, resolved_doc: Path) -> str | None:
    row = get_artifact_lineage_by_path(session, resolved_doc)
    return row.lineage_id if row else None


def document_id_for_resolved_path(session: Session, resolved_doc: Path) -> int | None:
    """Return latest content version id (legacy name ``document_id``)."""
    row = get_artifact_lineage_by_path(session, resolved_doc)
    if row is None:
        return None
    vid = latest_version_id_for_lineage(session, row.lineage_id)
    return vid


def update_document_last_disk_state(session: Session, resolved_doc: Path, *, body: str) -> None:
    p = resolved_doc.resolve()
    if not p.is_file():
        return
    row = get_or_create_lineage(session, p)
    st = p.stat()
    row.last_disk_mtime_ns = int(st.st_mtime_ns)
    row.last_disk_size = int(st.st_size)
    row.last_disk_sha256 = content_sha256(body)
    row.updated_at = time.time()


def is_document_disk_stale(session: Session, resolved_doc: Path) -> bool:
    p = resolved_doc.resolve()
    if not p.is_file():
        return False
    row = get_artifact_lineage_by_path(session, p)
    if row is None or row.last_disk_mtime_ns is None:
        return False
    try:
        st = p.stat()
    except OSError:
        return False
    if int(st.st_mtime_ns) != int(row.last_disk_mtime_ns) or int(st.st_size) != int(row.last_disk_size):
        return True
    if row.last_disk_sha256:
        try:
            disk_body = p.read_text(encoding="utf-8")
        except OSError:
            return True
        return content_sha256(disk_body) != row.last_disk_sha256
    return False


def refresh_document_last_disk_state_from_disk(session: Session, resolved_doc: Path) -> None:
    p = resolved_doc.resolve()
    if not p.is_file():
        return
    body = p.read_text(encoding="utf-8")
    update_document_last_disk_state(session, p, body=body)


def update_document_path_after_rename(
    session: Session, old_resolved: Path, new_resolved: Path
) -> Literal["ok", "collision"]:
    old_row = get_artifact_lineage_by_path(session, old_resolved)
    if old_row is None:
        return "ok"
    new_key = path_key_for(new_resolved)
    new_lid = _lineage_id_for_path(new_resolved)
    other = session.execute(
        select(Content).where(Content.lineage_id == new_lid).where(Content.id != old_row.id).limit(1)
    ).scalar_one_or_none()
    if other is not None:
        return "collision"
    attrs = _attrs(old_row)
    attrs["resolved_path"] = str(new_resolved.resolve())
    attrs["path_key"] = new_key
    _set_attrs(old_row, attrs)
    old_row.name = new_resolved.name
    old_row.source_id = new_key
    return "ok"


def update_document_paths_after_dir_rename(
    session: Session, old_dir_resolved: Path, new_dir_resolved: Path
) -> Literal["ok", "collision"]:
    old_b = old_dir_resolved.resolve()
    new_b = new_dir_resolved.resolve()
    rows = list(session.scalars(select(Content).where(Content.is_latest.is_(True))).all())
    planned: list[tuple[Content, Path]] = []
    for row in rows:
        attrs = _attrs(row)
        rp_s = attrs.get("resolved_path")
        if not rp_s:
            continue
        try:
            rp = Path(rp_s).resolve()
            rel = rp.relative_to(old_b)
        except ValueError:
            continue
        planned.append((row, (new_b / rel).resolve()))

    new_lids = {_lineage_id_for_path(np) for _, np in planned}
    for row in rows:
        if row.is_latest and row.lineage_id in new_lids:
            for c, np in planned:
                if c.id != row.id and _lineage_id_for_path(np) == row.lineage_id:
                    if any(c2.id != c.id for c2, np2 in planned if _lineage_id_for_path(np2) == row.lineage_id):
                        return "collision"

    for row, np in planned:
        nk = path_key_for(np)
        nlid = _lineage_id_for_path(np)
        for other in rows:
            if other.id != row.id and other.is_latest and other.lineage_id == nlid:
                return "collision"
        attrs = _attrs(row)
        attrs["resolved_path"] = str(np)
        attrs["path_key"] = nk
        _set_attrs(row, attrs)
        row.name = np.name
        row.source_id = nk
    return "ok"


def _get_or_create_file(session: Session, relpath: str, media_format: str | None = None) -> FileRecord:
    row = session.execute(
        select(FileRecord)
        .where(FileRecord.workspace_id == WORKSPACE_ID)
        .where(FileRecord.project_id == PROJECT_ID)
        .where(FileRecord.storage_relpath == relpath)
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = FileRecord(
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        storage_relpath=relpath,
        media_format=media_format,
    )
    session.add(row)
    session.flush()
    return row


def _link_file(
    session: Session,
    content_id: int,
    file_id: int,
    relation_type: str,
) -> None:
    exists = session.execute(
        select(ContentFileLink)
        .where(ContentFileLink.content_id == content_id)
        .where(ContentFileLink.file_id == file_id)
        .where(ContentFileLink.relation_type == relation_type)
    ).scalar_one_or_none()
    if exists is not None:
        return
    session.add(
        ContentFileLink(
            workspace_id=WORKSPACE_ID,
            project_id=PROJECT_ID,
            content_id=content_id,
            file_id=file_id,
            relation_type=relation_type,
            is_primary=True,
        )
    )


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


def _version_attrs(
    *,
    snapshot_relpath: str,
    snapshot_id: str,
    reason: str,
    display_label: str | None,
    pdf_asset_relpath: str | None,
    docx_asset_relpath: str | None,
    pdf_profile: str | None,
    content_sha: str,
) -> dict[str, Any]:
    return {
        "snapshot_relpath": snapshot_relpath,
        "snapshot_id": snapshot_id,
        "snapshot_reason": reason,
        "display_label": display_label,
        "pdf_asset_relpath": pdf_asset_relpath,
        "docx_asset_relpath": docx_asset_relpath,
        "pdf_profile": pdf_profile,
        "content_sha256": content_sha,
    }


def get_version_pdf_profile(session: Session, version_id: int) -> str | None:
    row = session.get(Content, version_id)
    if row is None:
        return None
    if get_lineage_artifact_kind(session, lineage_id=row.lineage_id) == pbs.ARTIFACT_KIND_PLAN:
        return "plan"
    prof = str(_attrs(row).get("pdf_profile") or "").strip()
    if prof == "plan":
        return "plan"
    return prof or None


def _row_to_snapshot(session: Session, v: Content) -> SnapshotInfo:
    attrs = _attrs(v)
    rel = str(attrs.get("snapshot_relpath") or "")
    name = Path(rel).name if rel else ""
    snap_id = name.replace(".md", "") if name else ""
    return SnapshotInfo(
        version_id=v.id,
        snapshot_id=snap_id,
        created_at=v.created_at,
        reason=str(attrs.get("snapshot_reason") or "manual"),
        content_sha256=str(attrs.get("content_sha256") or ""),
        display_label=attrs.get("display_label"),
        pdf_asset_relpath=attrs.get("pdf_asset_relpath"),
        docx_asset_relpath=attrs.get("docx_asset_relpath"),
        pdf_profile=get_version_pdf_profile(session, v.id),
        lineage_id=v.lineage_id,
        version_no=v.version_no,
    )


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
    rag_conn: Any | None = None,
) -> int | None:
    sha = content_sha256(body)
    lineage = get_or_create_lineage(session, resolved_doc)
    if pdf_profile == "plan":
        _set_lineage_artifact_kind(session, lineage.lineage_id, pbs.ARTIFACT_KIND_PLAN)
    last = session.execute(
        select(Content)
        .where(Content.lineage_id == lineage.lineage_id)
        .where(Content.version_no > 0)
        .order_by(Content.version_no.desc(), Content.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if skip_if_unchanged_sha and last is not None:
        la = _attrs(last)
        if la.get("content_sha256") == sha:
            return None

    if last is not None:
        last.is_latest = False
    elif lineage.version_no == 0:
        lineage.is_latest = False

    _, relpath = write_snapshot_file(resolved_doc, body)
    snap_name = Path(relpath).name
    snap_id = snap_name.replace(".md", "")
    now = time.time()
    next_ver = (last.version_no if last else lineage.version_no) + 1
    parent_id = last.id if last else (lineage.id if lineage.version_no == 0 else None)

    ver = Content(
        workspace_id=WORKSPACE_ID,
        project_id=PROJECT_ID,
        lineage_id=lineage.lineage_id,
        version_no=next_ver,
        is_latest=True,
        supersedes_content_id=parent_id,
        contract_id=str(uuid.uuid4()),
        content_kind=pbs.CONTENT_KIND_ARTIFACT,
        canonical_type=pbs.CANONICAL_TYPE_ARTIFACT,
        canonical_type_version=CANONICAL_TYPE_VERSION,
        name=lineage.name,
        source_system="iterthink",
        source_id=path_key_for(resolved_doc),
        created_at=now,
        updated_at=now,
    )
    pdf_rel: str | None = None
    docx_rel: str | None = None
    _set_attrs(
        ver,
        _version_attrs(
            snapshot_relpath=relpath,
            snapshot_id=snap_id,
            reason=reason,
            display_label=display_label.strip() if display_label and display_label.strip() else None,
            pdf_asset_relpath=None,
            docx_asset_relpath=None,
            pdf_profile=None,
            content_sha=sha,
        ),
    )
    session.add(ver)
    session.flush()

    f = _get_or_create_file(session, relpath, "text/markdown")
    _link_file(session, ver.id, f.id, pbs.FILE_RELATION_SOURCE)

    if parent_id is not None and last is not None:
        try:
            old_body = read_snapshot_body_by_relpath(str(_attrs(last).get("snapshot_relpath")))
        except OSError:
            old_body = ""
        paragraph_user_comments.migrate_comments_to_new_version(
            session,
            parent_version_id=int(parent_id),
            new_version_id=int(ver.id),
            old_body=old_body,
            new_body=body,
        )

    pk = path_key_for(resolved_doc)
    if pdf_source_path is not None and pdf_source_path.is_file():
        dest_dir = _pdf_assets_dir_for_doc(resolved_doc)
        dest = dest_dir / f"{ver.id}.pdf"
        shutil.copy2(pdf_source_path, dest)
        pdf_rel = f"pdf_assets/{pk}/{ver.id}.pdf"
        pf = _get_or_create_file(session, pdf_rel, "application/pdf")
        _link_file(session, ver.id, pf.id, pbs.FILE_RELATION_RENDERED_PDF)
        attrs = _attrs(ver)
        attrs["pdf_asset_relpath"] = pdf_rel
        if pdf_profile is not None and pdf_profile != "plan":
            attrs["pdf_profile"] = pdf_profile
        _set_attrs(ver, attrs)
        session.flush()

    if docx_source_path is not None and docx_source_path.is_file():
        dest_dir = _docx_assets_dir_for_doc(resolved_doc)
        dest = dest_dir / f"{ver.id}.docx"
        shutil.copy2(docx_source_path, dest)
        docx_rel = f"docx_assets/{pk}/{ver.id}.docx"
        df = _get_or_create_file(session, docx_rel, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        _link_file(session, ver.id, df.id, pbs.FILE_RELATION_RENDERED_DOCX)
        attrs = _attrs(ver)
        attrs["docx_asset_relpath"] = docx_rel
        _set_attrs(ver, attrs)

    return ver.id


def latest_version_id_for_document(session: Session, document_id: int) -> int | None:
    """``document_id`` is lineage anchor ``content.id`` (legacy name)."""
    row = session.get(Content, document_id)
    if row is None:
        return None
    return latest_version_id_for_lineage(session, row.lineage_id)


get_or_create_document = get_or_create_lineage


def list_snapshots(session: Session, resolved_doc: Path) -> list[SnapshotInfo]:
    row = get_artifact_lineage_by_path(session, resolved_doc)
    if row is None:
        return []
    rows = session.execute(
        select(Content)
        .where(Content.lineage_id == row.lineage_id)
        .where(Content.version_no > 0)
        .order_by(Content.created_at.desc(), Content.id.desc())
    ).scalars().all()
    return [_row_to_snapshot(session, v) for v in rows]


def snapshot_bucket(sn: SnapshotInfo) -> SnapshotBucket:
    if sn.reason == "import":
        return "import"
    return "history"


def snapshot_dropdown_text(sn: SnapshotInfo) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(sn.created_at))
    label = (sn.display_label or "").strip()
    if label:
        return f"{ts} - {label}"
    return ts


def second_newest_history_autosave_version_id(snaps: list[SnapshotInfo]) -> int | None:
    autos = [s for s in snaps if s.reason == "autosave" and snapshot_bucket(s) == "history"]
    if len(autos) < 2:
        return None
    return autos[1].version_id


def get_version_row(session: Session, version_id: int) -> Content | None:
    return session.get(Content, version_id)


def load_version_body(session: Session, version_id: int) -> str:
    row = session.get(Content, version_id)
    if row is None:
        raise KeyError(version_id)
    rel = str(_attrs(row).get("snapshot_relpath") or "")
    return read_snapshot_body_by_relpath(rel)


def get_version_pdf_relpath(session: Session, version_id: int) -> str | None:
    row = session.get(Content, version_id)
    if row is None:
        return None
    v = _attrs(row).get("pdf_asset_relpath")
    return str(v) if v else None


def get_version_docx_relpath(session: Session, version_id: int) -> str | None:
    row = session.get(Content, version_id)
    if row is None:
        return None
    v = _attrs(row).get("docx_asset_relpath")
    return str(v) if v else None


def _pdf_versions(session: Session, resolved_doc: Path, *, plan_only: bool) -> list[tuple[int, str]]:
    if plan_only and not is_plan_lineage(session, resolved_doc):
        return []
    row = get_artifact_lineage_by_path(session, resolved_doc)
    if row is None:
        return []
    rows = session.execute(
        select(Content)
        .where(Content.lineage_id == row.lineage_id)
        .where(Content.version_no > 0)
        .order_by(Content.created_at.desc(), Content.id.desc())
    ).scalars().all()
    out: list[tuple[int, str]] = []
    for v in rows:
        attrs = _attrs(v)
        pdf_rel = attrs.get("pdf_asset_relpath")
        if not pdf_rel:
            continue
        render_prof = get_version_pdf_profile(session, v.id)
        if render_prof == "docx_layout":
            continue
        ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(v.created_at))
        prof_bit = f" · {render_prof}" if render_prof else ""
        reason = str(attrs.get("snapshot_reason") or "")
        label = f"#{v.id}  {ts}  ({reason})  PDF{prof_bit}"
        out.append((v.id, label))
    return out


def list_pdf_version_options(session: Session, resolved_doc: Path) -> list[tuple[int, str]]:
    return _pdf_versions(session, resolved_doc, plan_only=False)


def list_plan_pdf_version_options(session: Session, resolved_doc: Path) -> list[tuple[int, str]]:
    return _pdf_versions(session, resolved_doc, plan_only=True)


def latest_pdf_version_for_document(session: Session, resolved_doc: Path) -> tuple[int, str] | None:
    opts = list_pdf_version_options(session, resolved_doc)
    if not opts:
        return None
    vid = opts[0][0]
    rel = get_version_pdf_relpath(session, vid)
    return (vid, rel) if rel else None


def document_has_any_pdf(session: Session, resolved_doc: Path) -> bool:
    return latest_pdf_version_for_document(session, resolved_doc) is not None


def latest_docx_version_for_document(session: Session, resolved_doc: Path) -> tuple[int, str] | None:
    row = get_artifact_lineage_by_path(session, resolved_doc)
    if row is None:
        return None
    rows = session.execute(
        select(Content)
        .where(Content.lineage_id == row.lineage_id)
        .where(Content.version_no > 0)
        .order_by(Content.created_at.desc(), Content.id.desc())
    ).scalars().all()
    for v in rows:
        rel = _attrs(v).get("docx_asset_relpath")
        if rel:
            return (v.id, str(rel))
    return None


def document_has_any_docx(session: Session, resolved_doc: Path) -> bool:
    return latest_docx_version_for_document(session, resolved_doc) is not None


def latest_pdf_version_detail(session: Session, resolved_doc: Path) -> tuple[int, str, str | None] | None:
    hit = latest_pdf_version_for_document(session, resolved_doc)
    if hit is None:
        return None
    vid, rel = hit
    return (vid, rel, get_version_pdf_profile(session, vid))


def delete_lineage_if_any(session: Session, resolved_doc: Path, *, rag_conn: Any | None = None) -> None:
    row = get_artifact_lineage_by_path(session, resolved_doc)
    if row is None:
        return
    versions = session.scalars(select(Content).where(Content.lineage_id == row.lineage_id)).all()
    conn = rag_conn
    if conn is None:
        conn = store_db.connect()
        own_rag = True
    else:
        own_rag = False
    try:
        for v in versions:
            if v.version_no > 0:
                store_db.impact_version_chunk_delete_for_version(conn, v.id)
        store_db.impact_version_chunk_delete_for_lineage(conn, row.lineage_id)
    finally:
        if own_rag:
            conn.close()
    for v in versions:
        session.delete(v)


delete_document_row_if_any = delete_lineage_if_any


def purge_document_store_dirs(resolved_doc: Path) -> None:
    pk = path_key_for(resolved_doc.resolve())
    base = config.STORE_DIR.resolve()
    for sub in ("snapshots", "pdf_assets", "docx_assets", "plan_text"):
        d = (config.STORE_DIR / sub / pk).resolve()
        try:
            d.relative_to(base)
        except ValueError:
            continue
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
