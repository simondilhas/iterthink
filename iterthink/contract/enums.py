"""Subset of contract enums for adapter validation (v0.0.7)."""

from __future__ import annotations

CONTENT_KIND_ARTIFACT = "artifact"
CANONICAL_TYPE_ARTIFACT = "Artifact"

ARTIFACT_KIND_TEXT_DOCUMENT = "text_document"
ARTIFACT_KIND_MODEL = "model"
ARTIFACT_KIND_PLAN = "plan"

FILE_RELATION_SOURCE = "source_file"
FILE_RELATION_RENDERED_PDF = "rendered_pdf"
FILE_RELATION_RENDERED_DOCX = "rendered_docx"

CHANGE_CLASS_PROPERTY = "PropertyChange"
CHANGE_CLASS_GEOMETRY = "GeometryChange"

CHANGE_TYPE_PROPERTY = "property_change"
CHANGE_TYPE_GEOMETRY = "geometry_change"

PROPERTY_PATH_KIND_DOCUMENT = "document"

INTENT_VERDICT_STABLE = "STABLE"
INTENT_VERDICT_NEW = "NEW"
