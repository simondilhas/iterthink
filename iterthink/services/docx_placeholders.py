"""Replace ``{Titel}``, ``{Date}``, ``{Author}`` in all Word text runs (``w:t``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docx.opc.constants import CONTENT_TYPE as CT

if TYPE_CHECKING:
    from docx.document import Document

_PLACEHOLDER_PART_TYPES = frozenset(
    {
        CT.WML_DOCUMENT_MAIN,
        CT.WML_FOOTNOTES,
        CT.WML_ENDNOTES,
        CT.WML_HEADER,
        CT.WML_FOOTER,
    }
)


def _substitute_in_element(element, mapping: dict[str, str]) -> None:
    from docx.oxml.ns import qn

    xml_ns = "http://www.w3.org/XML/1998/namespace"
    for t in element.iter(qn("w:t")):
        if not t.text:
            continue
        s = t.text
        orig = s
        for key, val in mapping.items():
            if key in s:
                s = s.replace(key, val)
        if s != orig:
            t.text = s
            if s and (s[0].isspace() or s[-1].isspace()):
                t.set(f"{{{xml_ns}}}space", "preserve")


def apply_docx_placeholders(document: Document, mapping: dict[str, str]) -> None:
    """Apply string replacements to every ``w:t`` in main story, headers, footers, foot/endnotes."""
    if not mapping:
        return
    pkg = document.part.package
    for part in pkg.iter_parts():
        ct = getattr(part, "content_type", None)
        if ct not in _PLACEHOLDER_PART_TYPES:
            continue
        el = getattr(part, "element", None)
        if el is None:
            continue
        _substitute_in_element(el, mapping)
