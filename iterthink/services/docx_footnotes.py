"""Minimal Word footnote support for python-docx (no upstream high-level API)."""

from __future__ import annotations

import xml.sax.saxutils as xml_esc

from docx.document import Document
from docx.opc.constants import CONTENT_TYPE as CT
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import XmlPart
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.parser import parse_xml
from docx.text.paragraph import Paragraph
from docx.text.run import Run

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _default_footnotes_blob() -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="{_W_NS}">
  <w:footnote w:type="separator" w:id="-1">
    <w:p><w:r><w:separator/></w:r></w:p>
  </w:footnote>
  <w:footnote w:type="continuationSeparator" w:id="0">
    <w:p><w:r><w:continuationSeparator/></w:r></w:p>
  </w:footnote>
</w:footnotes>
""".encode("utf-8")


class FootnotesPart(XmlPart):
    """Package part ``/word/footnotes.xml``."""

    @classmethod
    def new(cls, package) -> FootnotesPart:
        element = parse_xml(_default_footnotes_blob())
        return cls(PackURI("/word/footnotes.xml"), CT.WML_FOOTNOTES, element, package)


def ensure_footnotes_part(document: Document) -> XmlPart:
    """Return the document's footnotes part, creating a minimal one if missing."""
    dpart = document.part
    try:
        return dpart.part_related_by(RT.FOOTNOTES)
    except KeyError:
        fn = FootnotesPart.new(dpart.package)
        dpart.relate_to(fn, RT.FOOTNOTES)
        return fn


def max_footnote_id(fn_part: XmlPart) -> int:
    """Largest numeric ``w:footnote/@w:id`` in the part (ignores -1 and 0)."""
    m = 0
    for fn in fn_part.element.findall(qn("w:footnote")):
        raw = fn.get(qn("w:id"))
        if raw is None:
            continue
        try:
            n = int(raw)
        except ValueError:
            continue
        if n > m:
            m = n
    return m


def append_footnote_xml(fn_part: XmlPart, footnote_id: int, inner_xml_parts: list[bytes]) -> None:
    """Append ``w:footnote`` with ``w:id`` containing ``w:p`` fragments from ``inner_xml_parts``."""
    fn_el = OxmlElement("w:footnote")
    fn_el.set(qn("w:id"), str(footnote_id))
    for blob in inner_xml_parts:
        el = parse_xml(blob)
        fn_el.append(el)
    fn_part.element.append(fn_el)


def paragraph_xml_plain(text: str) -> bytes:
    esc = xml_esc.escape(text)
    return (
        f'<w:p xmlns:w="{_W_NS}"><w:r><w:t xml:space="preserve">{esc}</w:t></w:r></w:p>'.encode("utf-8")
    )


def add_footnote_reference(paragraph: Paragraph, footnote_id: int) -> Run:
    """Append a run containing only ``w:footnoteReference``."""
    run = paragraph.add_run()
    r = run._r
    for child in list(r):
        r.remove(child)
    ref = OxmlElement("w:footnoteReference")
    ref.set(qn("w:id"), str(footnote_id))
    r.append(ref)
    return run
