"""Privacy shield model download progress dialog."""

from __future__ import annotations

import asyncio
from typing import Any

import flet as ft

from iterthink import config
from iterthink.studio.util import ctrl_on_page as _ctrl_on_page


class PrivacyShieldDownloadHandle:
    def __init__(self, studio: Any, dialog: ft.AlertDialog, message: ft.Text, bar: ft.ProgressBar, pct: ft.Text) -> None:
        self._studio = studio
        self._dialog = dialog
        self._message = message
        self._bar = bar
        self._pct = pct

    async def set_progress(self, fraction: float, message: str | None = None) -> None:
        frac = max(0.0, min(1.0, float(fraction)))
        self._bar.value = frac
        self._pct.value = f"{int(round(frac * 100))}%"
        if message is not None:
            self._message.value = message
        pg = self._studio.page
        for ctrl in (self._bar, self._pct, self._message):
            if _ctrl_on_page(ctrl):
                ctrl.update()
        pg.update()
        await asyncio.sleep(0)

    async def close(self) -> None:
        try:
            self._studio.page.pop_dialog()
        except BaseException:
            pass
        await asyncio.sleep(0)
        self._studio.page.update()


async def begin_privacy_shield_download(studio: Any) -> PrivacyShieldDownloadHandle:
    msg = ft.Text(
        "Downloading privacy shield model from Hugging Face…",
        size=13,
        color=config.ON_SURFACE_VARIANT,
    )
    pct = ft.Text("0%", size=12, weight=ft.FontWeight.W_600, color=config.ON_SURFACE)
    bar = ft.ProgressBar(
        value=0,
        width=360,
        color=config.PRIMARY_COLOR,
        bgcolor=ft.Colors.with_opacity(0.2, config.OUTLINE),
    )
    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Privacy shield"),
        content=ft.Column(
            [msg, bar, pct],
            tight=True,
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
    studio.page.show_dialog(dlg)
    await asyncio.sleep(0)
    studio.page.update()
    return PrivacyShieldDownloadHandle(studio, dlg, msg, bar, pct)
