"""Import dialog controls for document-function classification."""

from __future__ import annotations

from typing import Callable

import flet as ft

from iterthink import config
from iterthink.contract.document_function_catalog import (
    is_valid_function_id,
    list_picker_options,
)


def build_document_function_dropdown(
    suggested_id: str,
    *,
    locale: str = "en",
) -> tuple[ft.Dropdown, Callable[[], str]]:
    options = list_picker_options(locale=locale)
    ids = {fid for fid, _ in options}
    initial = suggested_id if suggested_id in ids else (options[0][0] if options else "tec_documents")

    dd = ft.Dropdown(
        label="Document function",
        value=initial,
        options=[ft.dropdown.Option(fid, label) for fid, label in options],
        dense=True,
        expand=True,
    )

    def get_value() -> str:
        v = (dd.value or initial).strip()
        return v if is_valid_function_id(v) else initial

    return dd, get_value


def document_function_section_label() -> ft.Text:
    return ft.Text(
        "Document function",
        size=12,
        weight=ft.FontWeight.W_500,
        color=config.ON_SURFACE,
    )
