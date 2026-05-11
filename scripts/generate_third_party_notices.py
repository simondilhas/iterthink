#!/usr/bin/env python3
"""Regenerate THIRD_PARTY_NOTICES.md from the current environment and pyproject.toml."""
from __future__ import annotations

import re
import sys
import tomllib
from datetime import date
from pathlib import Path

import importlib.metadata as im

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "THIRD_PARTY_NOTICES.md"
OUT_PACKAGE = ROOT / "iterthink" / "THIRD_PARTY_NOTICES.md"
PYPROJECT = ROOT / "pyproject.toml"


def norm(name: str) -> str:
    return name.lower().replace("_", "-")


def site_packages() -> Path:
    import site

    for p in site.getsitepackages():
        sp = Path(p)
        if sp.is_dir():
            return sp
    raise RuntimeError("Could not locate site-packages")


def read_if(p: Path | None, max_bytes: int = 600_000) -> str | None:
    if not p or not p.is_file():
        return None
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if len(data) > max_bytes:
        return None
    return data.decode("utf-8", errors="replace")


def root_names() -> list[str]:
    with open(PYPROJECT, "rb") as f:
        deps = tomllib.load(f)["project"]["dependencies"]
    out: list[str] = []
    for line in deps:
        line = line.strip()
        if not line:
            continue
        base = line.split(",")[0].split(";")[0].strip()
        for sep in ("<", ">", "=", "!", "~", "["):
            if sep in base:
                base = base.split(sep)[0].strip()
        out.append(norm(base))
    return sorted(set(out))


def parse_req_name(req: str) -> str | None:
    req = req.split(";")[0].strip()
    if not req:
        return None
    m = re.match(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)", req)
    if not m:
        return None
    return norm(m.group(1))


def walk_deps(seeds: list[str]) -> dict[str, im.Distribution]:
    seen: set[str] = set()
    queue: list[str] = list(seeds)
    resolved: dict[str, im.Distribution] = {}
    while queue:
        key = queue.pop(0)
        if key in seen:
            continue
        seen.add(key)
        try:
            d = im.distribution(key)
        except im.PackageNotFoundError:
            continue
        resolved[d.metadata["Name"]] = d
        for req in d.requires or []:
            if "extra ==" in req:
                continue
            n = parse_req_name(req)
            if n and n not in seen:
                queue.append(n)
    return dict(sorted(resolved.items(), key=lambda x: x[0].lower()))


def project_url(md: im.Message, *keywords: str) -> str:
    for u in md.get_all("Project-URL") or []:
        low = u.lower()
        if any(k in low for k in keywords):
            return u.split(",", 1)[-1].strip()
    return ""


def license_summary(md: im.Message) -> str:
    le = (md.get("License-Expression") or "").strip()
    if le:
        return le
    lf = (md.get("License") or "").strip().replace("\n", " ")
    if len(lf) > 180:
        lf = lf[:177] + "..."
    if lf:
        return lf
    cls = [c for c in (md.get_all("Classifier") or []) if c.startswith("License ::")]
    if cls:
        return cls[0].split("::")[-1].strip()
    return "See distribution metadata / bundled LICENSE in site-packages"


def home_url(md: im.Message) -> str:
    h = md.get("Home-page") or md.get("Home-Page") or ""
    if h:
        return h.strip()
    return project_url(md, "homepage", "source", "repository", "github")


def first_existing(sp: Path, patterns: list[str]) -> Path | None:
    for pattern in patterns:
        for p in sorted(sp.glob(pattern)):
            if p.is_file():
                return p
    return None


def main() -> int:
    if not PYPROJECT.is_file():
        print("Missing pyproject.toml", file=sys.stderr)
        return 1

    rows: list[tuple[str, str, str, str]] = []
    for _name, d in walk_deps(root_names()).items():
        md = d.metadata
        rows.append(
            (
                md["Name"],
                md.get("Version", ""),
                license_summary(md),
                home_url(md),
            )
        )

    sp = site_packages()
    apache = first_existing(sp, ["huggingface_hub-*.dist-info/licenses/LICENSE"])
    mit = first_existing(sp, ["ollama-*.dist-info/licenses/LICENSE"])
    mit_yaml = first_existing(sp, ["pyyaml-*.dist-info/licenses/LICENSE"])
    bsd_httpx = first_existing(sp, ["httpx-*.dist-info/licenses/LICENSE.md"])
    mpl_certifi = first_existing(sp, ["certifi-*.dist-info/licenses/LICENSE"])
    pdfium_apache = first_existing(
        sp, ["pypdfium2-*.dist-info/licenses/LICENSES/Apache-2.0.txt"]
    )
    pdfium_bsd = first_existing(
        sp, ["pypdfium2-*.dist-info/licenses/LICENSES/BSD-3-Clause.txt"]
    )
    pdfium_ccby = first_existing(
        sp, ["pypdfium2-*.dist-info/licenses/LICENSES/CC-BY-4.0.txt"]
    )

    lines: list[str] = []
    lines.append("# Third-party notices")
    lines.append("")
    lines.append(
        "This file lists third-party software typically present when installing "
        "**iterthink** with the runtime dependencies declared in `pyproject.toml`, "
        "including transitive requirements, using the Python environment that was "
        "active when this file was generated."
    )
    lines.append("")
    lines.append(f"**Generated:** {date.today().isoformat()}  ")
    lines.append(
        "**Regenerate before each release:** run `python scripts/generate_third_party_notices.py` "
        "from the repository root with the same interpreter / lockfile you ship."
    )
    lines.append("")
    lines.append(
        "The **iterthink** application is licensed under the Business Source License 1.1 "
        "(see the repository `LICENSE` file). That license is separate from the third-party "
        "components listed below."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Package index")
    lines.append("")
    lines.append("| Package | Version | License (summary) | Project URL |")
    lines.append("|---------|---------|-------------------|-------------|")
    for pkg, ver, lic, url in rows:
        lic_esc = lic.replace("|", "\\|")
        url_esc = (url or "—").replace("|", "\\|")
        lines.append(f"| {pkg} | {ver} | {lic_esc} | {url_esc} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## License texts and bundled notices")
    lines.append("")
    lines.append(
        "The subsections below reproduce common license texts as shipped in this environment. "
        "For Apache-2.0, also retain any `NOTICE` files bundled inside the corresponding wheels "
        "when you redistribute."
    )
    lines.append("")

    def section(title: str, path: Path | None, body: str | None) -> None:
        lines.append(f"### {title}")
        lines.append("")
        if path:
            lines.append(f"*Source in this environment:* `{path}`")
            lines.append("")
        if not body:
            lines.append("*Full text not embedded; see the path above or the project repository.*")
            lines.append("")
            return
        lines.append("```")
        lines.append(body.rstrip())
        lines.append("```")
        lines.append("")

    section("Apache License, Version 2.0 (representative)", apache, read_if(apache))
    section("MIT License (representative — Ollama Python client)", mit, read_if(mit))
    if mit_yaml and read_if(mit_yaml) != read_if(mit):
        section("MIT License (PyYAML)", mit_yaml, read_if(mit_yaml))
    section("BSD 3-Clause (representative — httpx)", bsd_httpx, read_if(bsd_httpx))
    section("Mozilla Public License 2.0 (representative — certifi)", mpl_certifi, read_if(mpl_certifi))
    lines.append("### pypdfium2 / PDFium bundled texts")
    lines.append("")
    lines.append("The `pypdfium2` wheel bundles third-party license files. In this environment:")
    lines.append("")
    for label, p in (
        ("Apache-2.0", pdfium_apache),
        ("BSD-3-Clause", pdfium_bsd),
        ("CC-BY-4.0", pdfium_ccby),
    ):
        lines.append(f"- **{label}:** `{p}`" if p else f"- **{label}:** *(not found)*")
    lines.append("")
    section("pypdfium2 — Apache-2.0.txt", pdfium_apache, read_if(pdfium_apache))
    section("pypdfium2 — BSD-3-Clause.txt", pdfium_bsd, read_if(pdfium_bsd))
    section("pypdfium2 — CC-BY-4.0.txt", pdfium_ccby, read_if(pdfium_ccby))

    lines.append("---")
    lines.append("")
    lines.append(
        "*This document is not legal advice. Confirm compliance—including BUSL-1.1 and "
        "model or binary redistribution terms—with qualified counsel.*"
    )
    lines.append("")

    text = "\n".join(lines)
    OUT_ROOT.write_text(text, encoding="utf-8")
    OUT_PACKAGE.parent.mkdir(parents=True, exist_ok=True)
    OUT_PACKAGE.write_text(text, encoding="utf-8")
    print(f"Wrote {OUT_ROOT} and {OUT_PACKAGE} ({len(rows)} packages); site-packages: {sp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
