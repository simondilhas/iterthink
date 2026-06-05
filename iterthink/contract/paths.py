"""Canonical property paths for document field changes."""

from __future__ import annotations


def paragraph_property_path(index: int) -> str:
    return f"body:paragraph:{index}"
