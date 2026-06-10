#!/usr/bin/env python3
"""Download pragmatic-bim-data-contract v0.1.0 classification data and emit JSON for iterthink."""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = "simondilhas/pragmatic-bim-data-contract"
TAG = "v0.1.0"
BASE = f"https://raw.githubusercontent.com/{REPO}/{TAG}"
MAPPING_URL = f"{BASE}/classification/mapping/kbob-document-types-to-document-function.mapping.ttl"
DOCFN_URL = f"{BASE}/classification/abstract-document-function/document-function.skos.ttl"
DATA_DIR = Path(__file__).resolve().parent.parent / "iterthink" / "contract" / "data"
KBOB_OUT = DATA_DIR / "kbob_to_document_function.json"
DOCFN_OUT = DATA_DIR / "document_functions.json"

_KBOB_LINE = re.compile(
    r"^kbob:(?P<code>[A-Z]\d{5})\s+skos:(?P<match>closeMatch|relatedMatch)\s+"
    r"(?P<targets>docfn:[\w_]+(?:\s*,\s*docfn:[\w_]+)*)\s*\.\s*$"
)
_CONCEPT_START = re.compile(r"^:([a-z][a-z0-9_]*) a skos:Concept\s*;?\s*$", re.I)
_NOTATION = re.compile(r'skos:notation\s+"([^"]+)"')
_PREF_EN = re.compile(r'skos:prefLabel\s+"([^"]+)"@en')
_PREF_DE = re.compile(r'skos:prefLabel\s+"([^"]+)"@de')
_BROADER = re.compile(r"skos:broader\s+:([a-z][a-z0-9_]*)")
_TOP = re.compile(r"skos:topConceptOf\s+:scheme")


def _parse_kbob_ttl(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("kbob:"):
            continue
        m = _KBOB_LINE.match(line)
        if not m:
            continue
        code = m.group("code")
        targets = [
            t.replace("docfn:", "")
            for t in re.findall(r"docfn:[\w_]+", m.group("targets"))
        ]
        if not targets:
            continue
        existing = out.setdefault(code, [])
        for t in targets:
            if t not in existing:
                existing.append(t)
    return out


def _parse_document_functions_ttl(text: str) -> list[dict[str, str]]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if _CONCEPT_START.match(line.strip()):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
            if line.strip().endswith(".") and not line.strip().endswith(";"):
                blocks.append("\n".join(current))
                current = []
    if current:
        blocks.append("\n".join(current))

    functions: list[dict[str, str]] = []
    for block in blocks:
        start = _CONCEPT_START.match(block.strip().splitlines()[0].strip())
        if not start:
            continue
        cid = start.group(1)
        notation_m = _NOTATION.search(block)
        en_m = _PREF_EN.search(block)
        de_m = _PREF_DE.search(block)
        broader_m = _BROADER.search(block)
        is_top = _TOP.search(block) is not None
        parent = broader_m.group(1) if broader_m else (cid if is_top else "")
        functions.append(
            {
                "id": cid,
                "notation": notation_m.group(1) if notation_m else "",
                "label_en": en_m.group(1) if en_m else cid,
                "label_de": de_m.group(1) if de_m else (en_m.group(1) if en_m else cid),
                "parent": parent,
            }
        )
    return functions


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=120) as resp:
        return resp.read().decode("utf-8")


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {MAPPING_URL}")
    kbob_ttl = _fetch(MAPPING_URL)
    mapping = _parse_kbob_ttl(kbob_ttl)
    if not mapping:
        print("No KBOB mappings parsed.", file=sys.stderr)
        return 1
    kbob_payload = {
        "contract_version": "0.1.0",
        "source": MAPPING_URL,
        "scheme": "kbob-document-types-2016",
        "target_scheme": "document-function",
        "entries": mapping,
    }
    KBOB_OUT.write_text(json.dumps(kbob_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(mapping)} KBOB codes to {KBOB_OUT}")

    print(f"Fetching {DOCFN_URL}")
    docfn_ttl = _fetch(DOCFN_URL)
    functions = _parse_document_functions_ttl(docfn_ttl)
    if not functions:
        print("No document functions parsed.", file=sys.stderr)
        return 1
    docfn_payload = {
        "contract_version": "0.1.0",
        "source": DOCFN_URL,
        "scheme": "document-function",
        "functions": functions,
    }
    DOCFN_OUT.write_text(json.dumps(docfn_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(functions)} document functions to {DOCFN_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
