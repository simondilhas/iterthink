"""Classify workspace .md files via KBOB codes and document-function mapping (contract v0.1.0)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from iterthink.contract.document_function import TEC_DOCUMENTS
from iterthink.contract.document_function_catalog import (
    all_function_ids,
    function_label,
    is_valid_function_id,
)

_DATA_PATH = Path(__file__).resolve().parent / "data" / "kbob_to_document_function.json"

_KBOB_IN_NAME = re.compile(r"\b([BVOK])(\d{2,5})\b", re.IGNORECASE)
_FRONTMATTER_KBOB = re.compile(
    r"(?im)^(?:kbob|document[_-]?type|classification[_-]?code)\s*:\s*([BVOK]\d{2,5})\s*$"
)

_PATH_HINTS: tuple[tuple[str, str], ...] = (
    ("norm", "reg_norms"),
    ("sia", "reg_norms"),
    ("din", "reg_norms"),
    ("vorschrift", "reg_norms"),
    ("standard", "reg_norms"),
    ("brief", "req_project_briefs"),
    ("konzept", "req_project_briefs"),
    ("concept", "req_project_briefs"),
    ("ausschreib", "req_tender_documents"),
    ("tender", "req_tender_documents"),
    ("spec", "req_functional_specifications"),
    ("leistung", "req_functional_specifications"),
)

Confidence = Literal["high", "low"]
SuggestSource = Literal["kbob_code", "path_hint", "default", "llm", "stored", "import_manual"]


@dataclass(frozen=True)
class DocumentClassification:
    kbob_code: str | None
    document_functions: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class SuggestResult:
    function_id: str
    source: SuggestSource
    confidence: Confidence


@lru_cache(maxsize=1)
def _kbob_table() -> dict[str, list[str]]:
    if not _DATA_PATH.is_file():
        return {}
    try:
        raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = raw.get("entries") if isinstance(raw, dict) else None
    if not isinstance(entries, dict):
        return {}
    out: dict[str, list[str]] = {}
    for code, targets in entries.items():
        if isinstance(code, str) and isinstance(targets, list):
            out[code.upper()] = [str(t) for t in targets if isinstance(t, str)]
    return out


def _longest_kbob_match(code: str, table: dict[str, list[str]]) -> list[str]:
    upper = code.upper()
    for length in range(len(upper), 0, -1):
        prefix = upper[:length]
        hit = table.get(prefix)
        if hit:
            return hit
    return []


def _extract_kbob_code(path: Path, body: str | None = None) -> str | None:
    name = path.stem
    m = _KBOB_IN_NAME.search(name)
    if m:
        letter = m.group(1).upper()
        digits = m.group(2)
        return f"{letter}{digits}"
    if body:
        for line in body.splitlines()[:40]:
            fm = _FRONTMATTER_KBOB.match(line.strip())
            if fm:
                return fm.group(1).upper()
    return None


def _path_hint_functions(path: Path) -> list[str]:
    parts = "/".join(path.parts).casefold()
    hits: list[str] = []
    for needle, fn in _PATH_HINTS:
        if needle in parts and fn not in hits:
            hits.append(fn)
    return hits


def _primary_function(functions: tuple[str, ...] | list[str]) -> str:
    if functions:
        return str(functions[0])
    return TEC_DOCUMENTS


def _heuristic_classify(path: Path, *, body: str | None = None) -> DocumentClassification:
    table = _kbob_table()
    kbob = _extract_kbob_code(path, body)
    if kbob and table:
        mapped = _longest_kbob_match(kbob, table)
        if mapped:
            return DocumentClassification(
                kbob_code=kbob,
                document_functions=tuple(mapped),
                source="kbob_code",
            )
    hints = _path_hint_functions(path)
    if hints:
        return DocumentClassification(
            kbob_code=kbob,
            document_functions=tuple(hints),
            source="path_hint",
        )
    return DocumentClassification(
        kbob_code=kbob,
        document_functions=(TEC_DOCUMENTS,),
        source="default",
    )


def classify_document(
    path: Path,
    *,
    body: str | None = None,
    stored_attrs: dict[str, Any] | None = None,
) -> DocumentClassification:
    """Return document-function IDs; prefer persisted lineage attrs when present."""
    if stored_attrs:
        stored = stored_attrs.get("document_functions")
        if isinstance(stored, list) and stored:
            fns = tuple(str(x) for x in stored if isinstance(x, str) and x.strip())
            if fns:
                src = str(stored_attrs.get("classification_source") or "stored")
                return DocumentClassification(
                    kbob_code=_nullable_str(stored_attrs.get("kbob_code")),
                    document_functions=fns,
                    source=src,
                )
    return _heuristic_classify(path, body=body)


def _nullable_str(v: Any) -> str | None:
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def suggest_document_function_fast(
    *,
    src_path: Path | None = None,
    dest_md_path: Path,
    body: str | None = None,
) -> SuggestResult:
    """Instant suggestion from path/filename/KBOB (no LLM)."""
    check_path = dest_md_path
    if src_path is not None:
        merged = Path(str(src_path.parent / dest_md_path.name))
        check_path = merged
    cl = _heuristic_classify(check_path, body=body)
    if src_path is not None and cl.source == "default":
        cl_src = _heuristic_classify(src_path, body=body)
        if cl_src.source != "default":
            cl = cl_src
    fid = _primary_function(cl.document_functions)
    if cl.source == "kbob_code":
        return SuggestResult(function_id=fid, source="kbob_code", confidence="high")
    if cl.source == "path_hint":
        conf: Confidence = "low" if len(cl.document_functions) > 1 else "high"
        return SuggestResult(function_id=fid, source="path_hint", confidence=conf)
    return SuggestResult(function_id=fid, source="default", confidence="low")


def build_classification_excerpt(body: str, *, max_chars: int = 1200) -> str:
    text = (body or "").strip()
    if not text:
        return ""
    parts = text.split("\n\n")
    chunks: list[str] = []
    for part in parts:
        p = part.strip()
        if not p:
            continue
        chunks.append(p)
        if len(chunks) >= 3:
            break
    excerpt = "\n\n".join(chunks)
    if len(excerpt) > max_chars:
        excerpt = excerpt[: max_chars - 1] + "…"
    return excerpt


def classification_from_function(
    function_id: str,
    *,
    source: str,
    locale: str = "en",
) -> dict[str, Any]:
    fid = function_id.strip()
    label = function_label(fid, locale=locale)
    return {
        "classification_scheme": "document-function",
        "classification_code": fid,
        "classification_label": label,
        "classification_source": source,
    }


def attrs_from_function(
    function_id: str,
    *,
    source: str,
    kbob_code: str | None = None,
    locale: str = "en",
) -> dict[str, Any]:
    fid = function_id.strip()
    return {
        "kbob_code": kbob_code,
        "document_functions": [fid],
        "classification_source": source,
        "classifications": [classification_from_function(fid, source=source, locale=locale)],
    }


def classification_to_attrs(cl: DocumentClassification) -> dict[str, Any]:
    primary = _primary_function(cl.document_functions)
    return attrs_from_function(primary, source=cl.source, kbob_code=cl.kbob_code)


def functions_match_check(
    document_functions: tuple[str, ...] | list[str],
    allowed: frozenset[str],
) -> bool:
    return bool(allowed.intersection(document_functions))


async def suggest_document_function_llm(
    llm_chat: Any,
    *,
    model: str,
    excerpt: str,
    catalog_ids: tuple[str, ...] | None = None,
) -> SuggestResult | None:
    if not (excerpt or "").strip():
        return None
    ids = catalog_ids or all_function_ids()
    if not ids:
        return None
    allowed = ", ".join(ids)
    system = (
        "Pick one document_function id for this imported document excerpt. "
        f"Allowed ids: {allowed}. JSON only: {{\"document_function\": \"<id>\"}}."
    )
    user = f"Excerpt:\n{excerpt.strip()}"
    try:
        resp = await llm_chat.chat(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            stream=False,
            format="json",
        )
    except BaseException:  # noqa: BLE001
        return None
    from iterthink.ai.ollama_util import chat_response_text

    text = chat_response_text(resp) or ""
    fid = _parse_llm_function_json(text)
    if fid is None or not is_valid_function_id(fid):
        return None
    return SuggestResult(function_id=fid, source="llm", confidence="high")


def _parse_llm_function_json(text: str) -> str | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            obj = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None
    raw = obj.get("document_function")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None
