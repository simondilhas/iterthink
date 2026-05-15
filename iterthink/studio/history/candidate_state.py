"""Typed compare candidate source (runtime values match persisted / UI literals)."""

from __future__ import annotations

from enum import StrEnum


class CompareCandidateSource(StrEnum):
    """Discriminant for which compare renderer and persistence path is active."""

    DRAFT = "draft"
    AI_PREVIEW = "ai_preview"
    SPELL_PREVIEW = "spell_preview"
    SNAPSHOT = "snapshot"
    PDF_ORIGINAL = "pdf_original"
    DOCX_ORIGINAL = "docx_original"
    IFC_ORIGINAL = "ifc_original"
