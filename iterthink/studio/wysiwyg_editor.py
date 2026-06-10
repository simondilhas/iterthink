"""Flet block preview editor for Focus Area compose (rendered read, click to edit)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable

import flet as ft

from iterthink import config
from iterthink.studio import ui_theme
from iterthink.studio.constants import (
    COMPOSE_EDITOR_CONTENT_PAD_LEFT_PX,
    COMPOSE_EDITOR_CONTENT_PAD_RIGHT_PX,
    COMPOSE_EDITOR_CONTENT_PAD_TOP_PX,
    COMPOSE_EDITOR_LINE_HEIGHT_PX,
    COMPOSE_EDITOR_SCROLLBAR_TRACK_PX,
    COMPOSE_PREVIEW_BLOCK_GAP_PX,
    COMPARE_COL_FONT_SIZE,
    COMPARE_COL_LINE_HEIGHT,
)
from iterthink.studio.markdown_preview import markdown_preview_with_task_checkboxes
from iterthink.studio.markdown_tables import build_table_preview, compute_column_widths_px
from iterthink.studio.wysiwyg_blocks import (
    BlockKind,
    WysiwygBlock,
    global_span_for_block_selection,
    insert_block_at,
    merge_blocks,
    parse_markdown_blocks,
    remove_block_at,
    reorder_blocks,
    serialize_markdown_blocks,
    serialize_single_block,
    set_block_kind,
    split_block_on_enter,
)

OnMarkdownChange = Callable[[str], None]
OnActiveFieldChange = Callable[[], None]
OnFocusEditField = Callable[[], None]
OnSelectionChange = Callable[[ft.TextSelectionChangeEvent], None]
OnBlockComment = Callable[[int], None]

_CHROME_COL_WIDTH = 28
_EDIT_MENU_COL_WIDTH = 24
_ACTION_STRIP_HEIGHT = 18
_BLOCK_BODY_H_PAD = 2
_BLOCK_BODY_V_PAD = 4
_LIST_READ_INDENT_PX = 28
_BLUR_COMMIT_DELAY_SEC = 0.08

_LIST_KINDS = frozenset({"bullet", "ordered", "task"})


def block_gap_after(current: WysiwygBlock, nxt: WysiwygBlock | None) -> float:
    """Inter-block margin after ``current`` (row-level; avoids stacked Markdown padding)."""
    if nxt is None:
        return 0.0
    if current.kind in _LIST_KINDS and nxt.kind in _LIST_KINDS:
        return 0.0
    if current.kind == "paragraph" and nxt.kind == "paragraph":
        return float(COMPOSE_EDITOR_LINE_HEIGHT_PX)
    return float(COMPOSE_PREVIEW_BLOCK_GAP_PX)


_TYPE_MENU: list[tuple[str, BlockKind, int]] = [
    ("Paragraph", "paragraph", 0),
    ("Heading 1", "heading", 1),
    ("Heading 2", "heading", 2),
    ("Heading 3", "heading", 3),
    ("Bullet list", "bullet", 0),
    ("Numbered list", "ordered", 0),
    ("Task item", "task", 0),
    ("Blockquote", "blockquote", 0),
]


@dataclass
class WysiwygEditorController:
    """Rendered block preview; at most one block in edit mode at a time."""

    on_markdown_change: OnMarkdownChange
    on_active_field_change: OnActiveFieldChange | None = None
    on_focus_edit_field: OnFocusEditField | None = None
    on_selection_change: OnSelectionChange | None = None
    on_block_comment: OnBlockComment | None = None
    blocks: list[WysiwygBlock] = field(default_factory=list)
    _editing_block_index: int | None = field(default=None, repr=False)
    _edit_field: ft.TextField | None = field(default=None, repr=False)
    _syncing: bool = field(default=False, repr=False)
    _avail_width: float = field(default=400.0, repr=False)
    _reorder_list: ft.ReorderableListView | None = field(default=None, repr=False)
    _host: ft.Container | None = field(default=None, repr=False)
    _table_cell_fields: dict[int, list[list[ft.TextField]]] = field(
        default_factory=dict, repr=False
    )
    _commit_on_blur: bool = field(default=True, repr=False)
    _blur_commit_gen: int = field(default=0, repr=False)
    _hovered_block_index: int | None = field(default=None, repr=False)
    _menu_open_block_index: int | None = field(default=None, repr=False)
    _row_chrome_refs: dict[int, list[ft.Control]] = field(default_factory=dict, repr=False)
    _block_action_refs: dict[int, tuple[ft.Icon, ft.Icon]] = field(
        default_factory=dict, repr=False
    )

    def build_host(self) -> ft.Container:
        if self._host is None:
            self._reorder_list = ft.ReorderableListView(
                controls=[],
                expand=True,
                spacing=0,
                show_default_drag_handles=False,
                on_reorder=self._on_reorder,
                padding=ft.padding.only(right=COMPOSE_EDITOR_SCROLLBAR_TRACK_PX),
            )
            self._host = ft.Container(
                expand=True,
                padding=ft.padding.only(
                    left=COMPOSE_EDITOR_CONTENT_PAD_LEFT_PX,
                    right=COMPOSE_EDITOR_CONTENT_PAD_RIGHT_PX,
                    top=COMPOSE_EDITOR_CONTENT_PAD_TOP_PX,
                    bottom=0,
                ),
                content=self._reorder_list,
            )
        return self._host

    @property
    def root(self) -> ft.Container:
        return self.build_host()

    @property
    def editing_block_index(self) -> int | None:
        return self._editing_block_index

    def set_avail_width(self, width: float) -> None:
        self._avail_width = max(200.0, float(width or 400.0))

    def sync_from_markdown(self, md: str) -> None:
        self._syncing = True
        try:
            self._editing_block_index = None
            self._edit_field = None
            self.blocks = parse_markdown_blocks(md or "")
            self._rebuild_list()
        finally:
            self._syncing = False

    def get_active_field(self) -> ft.TextField | None:
        return self._edit_field

    def global_span_for_selection(
        self, sel_start: int, sel_end: int
    ) -> tuple[int, int] | None:
        """Map body selection in the active block to global markdown offsets."""
        if self._editing_block_index is None:
            return None
        bi = self._editing_block_index
        if bi < 0 or bi >= len(self.blocks):
            return None
        self._pull_editing_text()
        serialize_markdown_blocks(self.blocks)
        return global_span_for_block_selection(self.blocks[bi], sel_start, sel_end)

    def active_block_global_offset(self) -> int | None:
        """Global markdown offset at the start of the block being edited."""
        if self._editing_block_index is None:
            return None
        return self.block_global_offset(self._editing_block_index)

    def block_global_offset(self, block_index: int) -> int | None:
        """Global markdown offset at the start of ``block_index``."""
        bi = int(block_index)
        if bi < 0 or bi >= len(self.blocks):
            return None
        if bi == self._editing_block_index:
            self._pull_editing_text()
        serialize_markdown_blocks(self.blocks)
        return int(self.blocks[bi].source_span[0])

    def start_edit(self, block_index: int, caret: int = 0) -> None:
        if not self.blocks:
            return
        bi = max(0, min(int(block_index), len(self.blocks) - 1))
        block = self.blocks[bi]
        if block.kind == "horizontal_rule":
            return
        self.commit_edit()
        self._editing_block_index = bi
        self._rebuild_list()
        if self._edit_field is not None:
            c = max(0, min(caret, len(self._edit_field.value or "")))
            self._edit_field.selection = ft.TextSelection(c, c)
        if self.on_active_field_change:
            self.on_active_field_change()
        if self.on_focus_edit_field:
            self.on_focus_edit_field()

    def focus_block(self, block_index: int, caret: int = 0) -> None:
        self.start_edit(block_index, caret)

    def commit_edit(self) -> None:
        if self._editing_block_index is None:
            return
        prev = self._editing_block_index
        self._pull_editing_text()
        self._editing_block_index = None
        self._edit_field = None
        self._emit_change()
        self._rebuild_list()

    def cancel_edit(self) -> None:
        """Discard in-flight edit and restore read view."""
        self._editing_block_index = None
        self._edit_field = None
        self._rebuild_list()

    def current_markdown(self) -> str:
        self._pull_editing_text()
        return serialize_markdown_blocks(self.blocks)

    def handle_enter(self, *, shift: bool = False) -> bool:
        if shift or self._editing_block_index is None or self._edit_field is None:
            return False
        bi = self._editing_block_index
        block = self.blocks[bi]
        tf = self._edit_field
        sel = tf.selection
        caret = int(sel.start) if sel is not None else len(tf.value or "")
        block.text = tf.value or ""
        got = split_block_on_enter(block, caret)
        if got is None:
            return False
        left, right = got
        self.blocks[bi] = left
        self.blocks.insert(bi + 1, right)
        self._emit_change()
        self.start_edit(bi + 1, 0)
        return True

    def handle_backspace_at_start(self) -> bool:
        if self._editing_block_index is None or self._edit_field is None:
            return False
        bi = self._editing_block_index
        if bi <= 0:
            return False
        sel = self._edit_field.selection
        caret = int(sel.start) if sel is not None else 0
        if caret != 0 or (sel is not None and not sel.is_collapsed):
            return False
        prev = self.blocks[bi - 1]
        nxt = self.blocks[bi]
        nxt.text = self._edit_field.value or ""
        merged = merge_blocks(prev, nxt)
        if merged is None:
            return False
        self.blocks[bi - 1] = merged
        del self.blocks[bi]
        merge_caret = len(prev.text or "")
        self._emit_change()
        self.start_edit(bi - 1, merge_caret)
        return True

    def insert_paragraph_after(self, block_index: int) -> None:
        self.insert_paragraph_at(block_index + 1)

    def delete_block(self, block_index: int) -> None:
        if not self.blocks:
            return
        self._cancel_deferred_blur_commit()
        self._pull_editing_text()
        bi = max(0, min(int(block_index), len(self.blocks) - 1))
        edit_ix = self._editing_block_index
        self.blocks = remove_block_at(self.blocks, bi)
        if edit_ix is not None:
            if edit_ix == bi:
                self._editing_block_index = None
            elif edit_ix > bi:
                self._editing_block_index = edit_ix - 1
        self._hovered_block_index = None
        self._emit_change()
        self._rebuild_list()
        self._cancel_deferred_blur_commit()
        page = self._page_ref()
        if page is not None:
            page.run_task(self._post_chrome_action)

    def insert_paragraph_at(self, index: int) -> None:
        self._cancel_deferred_blur_commit()
        self._pull_editing_text()
        new_block = WysiwygBlock(kind="paragraph", text="")
        if not self.blocks:
            self.blocks = [new_block]
            new_ix = 0
        else:
            ix = max(0, min(int(index), len(self.blocks)))
            self.blocks = insert_block_at(self.blocks, ix, new_block)
            new_ix = ix
        self._editing_block_index = new_ix
        self._hovered_block_index = new_ix
        self._emit_change()
        self._rebuild_list()
        self._cancel_deferred_blur_commit()
        page = self._page_ref()
        if page is not None:
            page.run_task(self._post_chrome_action)
        if self.on_active_field_change:
            self.on_active_field_change()
        if self.on_focus_edit_field:
            self.on_focus_edit_field()

    async def _post_chrome_action(self) -> None:
        """Cancel blur commits fired while focus moves to chrome controls."""
        await asyncio.sleep(0.05)
        self._cancel_deferred_blur_commit()

    def set_block_type(self, block_index: int, kind: BlockKind, *, level: int = 1) -> None:
        if block_index < 0 or block_index >= len(self.blocks):
            return
        was_editing = self._editing_block_index == block_index
        if was_editing:
            self._pull_editing_text()
        self.blocks[block_index] = set_block_kind(
            self.blocks[block_index], kind, level=level
        )
        self._menu_open_block_index = None
        self._cancel_deferred_blur_commit()
        self._emit_change()
        if was_editing:
            self._rebuild_list()
            if self.on_focus_edit_field:
                self.on_focus_edit_field()
        else:
            self._rebuild_list()

    def _pull_editing_text(self) -> None:
        bi = self._editing_block_index
        if bi is None or bi >= len(self.blocks):
            return
        block = self.blocks[bi]
        if block.kind == "table":
            if bi in self._table_cell_fields:
                rows = []
                for row_fields in self._table_cell_fields[bi]:
                    rows.append([f.value or "" for f in row_fields])
                block.rows = rows
            return
        if self._edit_field is None:
            return
        val = self._edit_field.value or ""
        if block.kind == "raw":
            block.raw_text = val
        else:
            block.text = val

    def _emit_change(self) -> None:
        if self._syncing:
            return
        md = serialize_markdown_blocks(self.blocks)
        self.on_markdown_change(md)

    def _on_reorder(self, e: ft.OnReorderEvent) -> None:
        if e.old_index is None or e.new_index is None:
            return
        self.commit_edit()
        oi, ni = int(e.old_index), int(e.new_index)
        self.blocks = reorder_blocks(self.blocks, oi, ni)
        self._emit_change()
        self._rebuild_list()

    def _cancel_deferred_blur_commit(self) -> None:
        self._blur_commit_gen += 1

    def _schedule_deferred_blur_commit(self) -> None:
        self._blur_commit_gen += 1
        gen = self._blur_commit_gen
        page = self._page_ref()
        if page is None:
            return
        page.run_task(self._deferred_blur_commit, gen)

    def _page_ref(self) -> ft.Page | None:
        for ctrl in (self._edit_field, self._reorder_list, self._host):
            if ctrl is None:
                continue
            try:
                p = ctrl.page
                if p is not None:
                    return p
            except RuntimeError:
                continue
        return None

    async def _deferred_blur_commit(self, gen: int) -> None:
        await asyncio.sleep(_BLUR_COMMIT_DELAY_SEC)
        if gen != self._blur_commit_gen:
            return
        if self._menu_open_block_index is not None:
            return
        if self._editing_block_index is not None:
            self.commit_edit()

    def _on_edit_blur(self, _e: ft.ControlEvent) -> None:
        if not self._commit_on_blur or self._editing_block_index is None:
            return
        if self._menu_open_block_index is not None:
            return
        self._schedule_deferred_blur_commit()

    def _on_edit_change(self, e: ft.ControlEvent) -> None:
        bi = self._editing_block_index
        if bi is None or bi >= len(self.blocks):
            return
        block = self.blocks[bi]
        val = e.control.value or ""
        if block.kind == "raw":
            block.raw_text = val
        else:
            block.text = val

    def _rebuild_list(self) -> None:
        if self._reorder_list is None:
            self.build_host()
        assert self._reorder_list is not None
        self._table_cell_fields = {}
        self._edit_field = None
        self._row_chrome_refs = {}
        self._block_action_refs = {}
        self._menu_open_block_index = None
        sheet = ui_theme.compose_wysiwyg_block_markdown_style_sheet()
        rows: list[ft.Control] = []
        for bi, block in enumerate(self.blocks):
            nxt = self.blocks[bi + 1] if bi + 1 < len(self.blocks) else None
            rows.append(self._build_block_row(bi, block, sheet, nxt))
        self._reorder_list.controls = rows
        try:
            if self._reorder_list.page is not None:
                self._reorder_list.update()
        except RuntimeError:
            pass

    def _chrome_visible(self, block_index: int) -> bool:
        return (
            block_index == self._hovered_block_index
            or block_index == self._editing_block_index
            or block_index == self._menu_open_block_index
        )

    def _apply_chrome_state(self, block_index: int) -> None:
        refs = self._row_chrome_refs.get(block_index)
        if refs is None:
            return
        show = self._chrome_visible(block_index)
        for ctrl in refs:
            if isinstance(ctrl, ft.PopupMenuButton):
                ctrl.opacity = 1.0 if show else 0.0
            elif isinstance(ctrl, ft.Icon):
                ctrl.opacity = 1.0 if show else 0.0
            elif isinstance(ctrl, ft.Container):
                ctrl.visible = show
        try:
            for ctrl in refs:
                ctrl.update()
        except RuntimeError:
            pass

    def _update_chrome_visibility(self, block_index: int) -> None:
        self._apply_chrome_state(block_index)
        self._apply_block_action_state(block_index)

    def _apply_block_action_state(self, block_index: int) -> None:
        refs = self._block_action_refs.get(block_index)
        if refs is None:
            return
        plus_top, plus_bottom = refs
        show = self._chrome_visible(block_index)
        opacity = 1.0 if show else 0.0
        plus_top.opacity = opacity
        plus_bottom.opacity = opacity
        try:
            plus_top.update()
            plus_bottom.update()
        except RuntimeError:
            pass

    def _set_hovered_block(self, block_index: int | None) -> None:
        prev = self._hovered_block_index
        self._hovered_block_index = block_index
        if prev is not None and prev != block_index:
            self._update_chrome_visibility(prev)
        if block_index is not None:
            self._update_chrome_visibility(block_index)

    def _on_chrome_pointer_down(self, block_index: int) -> None:
        """Run before TextField blur so chrome clicks do not commit the active edit."""
        self._cancel_deferred_blur_commit()
        self._menu_open_block_index = block_index
        self._set_hovered_block(block_index)
        self._update_chrome_visibility(block_index)

    def _on_type_menu_open(self, block_index: int) -> None:
        self._cancel_deferred_blur_commit()
        self._menu_open_block_index = block_index
        self._set_hovered_block(block_index)
        self._update_chrome_visibility(block_index)

    def _on_type_menu_cancel(self, block_index: int) -> None:
        if self._menu_open_block_index == block_index:
            self._menu_open_block_index = None
        self._update_chrome_visibility(block_index)
        if self._editing_block_index == block_index and self.on_focus_edit_field:
            self.on_focus_edit_field()

    def _build_block_row(
        self,
        block_index: int,
        block: WysiwygBlock,
        sheet: ft.MarkdownStyleSheet,
        next_block: WysiwygBlock | None,
    ) -> ft.Control:
        chrome = ft.Container(
            width=_CHROME_COL_WIDTH,
            content=self._build_row_chrome(block_index, block),
            on_hover=lambda e, ix=block_index: self._on_chrome_hover(e, ix),
        )
        if block_index == self._editing_block_index:
            surface = self._build_edit_surface(block_index, block, sheet)
        else:
            surface = self._build_read_surface(block_index, block, sheet)
        content = self._build_block_content(block_index, surface)
        gap = block_gap_after(block, next_block)
        row_body = ft.Container(
            margin=ft.Margin.only(bottom=gap) if gap > 0 else None,
            border_radius=4,
            content=ft.Row(
                [
                    chrome,
                    ft.Container(content=content, expand=True),
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            on_hover=lambda e, ix=block_index: self._on_row_hover(e, ix),
        )
        return row_body

    def _action_icon_button(
        self,
        *,
        icon: str,
        size: int,
        color: str,
        tooltip: str,
        visible: bool,
        on_tap: Callable[[], None],
    ) -> ft.GestureDetector:
        icon_ctrl = ft.Icon(icon, size=size, color=color, opacity=1.0 if visible else 0.0)
        return ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.CLICK,
            on_tap_down=lambda _e: self._cancel_deferred_blur_commit(),
            on_tap=lambda _e: on_tap(),
            content=ft.Container(
                alignment=ft.Alignment.CENTER,
                tooltip=tooltip,
                content=icon_ctrl,
            ),
        ), icon_ctrl

    def _build_block_content(self, block_index: int, surface: ft.Control) -> ft.Column:
        show = self._chrome_visible(block_index)
        plus_top_gd, plus_top = self._action_icon_button(
            icon=ft.Icons.ADD,
            size=14,
            color=config.PRIMARY_COLOR,
            tooltip="Add paragraph above",
            visible=show,
            on_tap=lambda ix=block_index: self._on_plus_tap(ix, before=True),
        )
        plus_bottom_gd, plus_bottom = self._action_icon_button(
            icon=ft.Icons.ADD,
            size=14,
            color=config.PRIMARY_COLOR,
            tooltip="Add paragraph below",
            visible=show,
            on_tap=lambda ix=block_index: self._on_plus_tap(ix, before=False),
        )
        self._block_action_refs[block_index] = (plus_top, plus_bottom)
        return ft.Column(
            [
                ft.Container(
                    height=_ACTION_STRIP_HEIGHT,
                    alignment=ft.Alignment.CENTER,
                    content=plus_top_gd,
                ),
                ft.Container(content=surface, expand=True),
                ft.Container(
                    height=_ACTION_STRIP_HEIGHT,
                    alignment=ft.Alignment.CENTER,
                    content=plus_bottom_gd,
                ),
            ],
            spacing=0,
            tight=True,
        )

    def _on_chrome_hover(self, e: ft.HoverEvent, block_index: int) -> None:
        if e.data == "true":
            self._set_hovered_block(block_index)

    def _on_row_hover(self, e: ft.HoverEvent, block_index: int) -> None:
        if not isinstance(e.control, ft.Container):
            return
        if e.data == "true":
            self._set_hovered_block(block_index)
            e.control.bgcolor = ft.Colors.with_opacity(0.06, config.ON_SURFACE)
        else:
            if self._menu_open_block_index == block_index:
                return
            if self._hovered_block_index == block_index:
                self._set_hovered_block(None)
            e.control.bgcolor = None
        if e.control.page is not None:
            e.control.update()

    def _on_plus_tap(self, block_index: int, *, before: bool) -> None:
        self._cancel_deferred_blur_commit()
        self._set_hovered_block(block_index)
        if before:
            self.insert_paragraph_at(block_index)
        else:
            self.insert_paragraph_after(block_index)

    def _on_edit_menu_delete(self, block_index: int) -> None:
        self._cancel_deferred_blur_commit()
        self._set_hovered_block(block_index)
        self.delete_block(block_index)

    def _on_edit_menu_comment(self, block_index: int) -> None:
        self._cancel_deferred_blur_commit()
        self._set_hovered_block(block_index)
        if self.on_block_comment:
            self.on_block_comment(block_index)

    def _build_edit_overflow_menu(self, block_index: int) -> ft.Control:
        menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            icon_size=18,
            icon_color=config.ON_SURFACE_VARIANT,
            tooltip="Block actions",
            items=[
                ft.PopupMenuItem(
                    content=ft.Text("Delete", size=12),
                    height=32,
                    on_click=lambda _e, ix=block_index: self._on_edit_menu_delete(ix),
                ),
                ft.PopupMenuItem(
                    content=ft.Text("Comment", size=12),
                    height=32,
                    on_click=lambda _e, ix=block_index: self._on_edit_menu_comment(ix),
                ),
            ],
            menu_position=ft.PopupMenuPosition.UNDER,
            padding=0,
            style=ft.ButtonStyle(padding=0, visual_density=ft.VisualDensity.COMPACT),
            on_open=lambda _e, ix=block_index: self._on_type_menu_open(ix),
            on_cancel=lambda _e, ix=block_index: self._on_type_menu_cancel(ix),
        )
        return ft.GestureDetector(
            on_tap_down=lambda _e, ix=block_index: self._on_chrome_pointer_down(ix),
            content=ft.Container(
                width=_EDIT_MENU_COL_WIDTH,
                alignment=ft.Alignment.TOP_CENTER,
                content=menu,
            ),
        )

    def _wrap_edit_surface(self, block_index: int, body: ft.Control) -> ft.Control:
        return ft.Row(
            [
                ft.Container(content=body, expand=True),
                self._build_edit_overflow_menu(block_index),
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    def _build_row_chrome(self, block_index: int, block: WysiwygBlock) -> ft.Control:
        show = self._chrome_visible(block_index)
        drag_icon = ft.Icon(
            ft.Icons.DRAG_HANDLE,
            size=16,
            color=config.ON_SURFACE_VARIANT,
            opacity=1.0 if show else 0.0,
        )
        drag = ft.ReorderableDragHandle(
            content=drag_icon,
            tooltip="Move block",
        )
        if block.kind in ("horizontal_rule", "table", "code_fence", "raw"):
            type_slot = ft.Container(width=_CHROME_COL_WIDTH, height=24, visible=show)
            self._row_chrome_refs[block_index] = [drag_icon, type_slot]
            return ft.Column(
                [drag, type_slot],
                spacing=2,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )

        type_btn = self._block_type_menu(block_index, block, visible=show)
        self._row_chrome_refs[block_index] = [drag_icon, type_btn]
        type_wrap = ft.Container(
            width=_CHROME_COL_WIDTH,
            height=24,
            alignment=ft.Alignment.CENTER,
            content=ft.GestureDetector(
                on_tap_down=lambda _e, ix=block_index: self._on_chrome_pointer_down(ix),
                content=type_btn,
            ),
        )
        return ft.Column(
            [drag, type_wrap],
            spacing=2,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _block_type_menu(
        self, block_index: int, block: WysiwygBlock, *, visible: bool = True
    ) -> ft.Control:
        if block.kind in ("code_fence", "raw"):
            return ft.Container(width=_CHROME_COL_WIDTH, height=24, visible=visible)
        items: list[ft.PopupMenuItem] = []
        for label, kind, level in _TYPE_MENU:
            items.append(
                ft.PopupMenuItem(
                    content=ft.Text(label, size=12),
                    height=32,
                    on_click=lambda _e, ix=block_index, k=kind, lv=level: self.set_block_type(
                        ix, k, level=lv
                    ),
                )
            )
        return ft.PopupMenuButton(
            icon=ft.Icons.TITLE,
            icon_size=16,
            icon_color=config.ON_SURFACE_VARIANT,
            tooltip="Block type",
            items=items,
            menu_position=ft.PopupMenuPosition.UNDER,
            padding=0,
            style=ft.ButtonStyle(padding=0, visual_density=ft.VisualDensity.COMPACT),
            opacity=1.0 if visible else 0.0,
            on_open=lambda _e, ix=block_index: self._on_type_menu_open(ix),
            on_cancel=lambda _e, ix=block_index: self._on_type_menu_cancel(ix),
        )

    def _build_read_surface(
        self, block_index: int, block: WysiwygBlock, sheet: ft.MarkdownStyleSheet
    ) -> ft.Control:
        if block.kind == "horizontal_rule":
            return ft.Container(
                height=1,
                margin=ft.margin.symmetric(vertical=8),
                bgcolor=ui_theme.outline_muted(alpha=0.35),
            )
        if block.kind == "table":
            return self._build_table_read(block_index, block, sheet)
        if block.kind == "paragraph" and not (block.text or ""):
            return ft.GestureDetector(
                mouse_cursor=ft.MouseCursor.CLICK,
                on_tap=lambda _e, ix=block_index: self.start_edit(ix, 0),
                content=ft.Container(
                    height=28,
                    padding=_read_body_padding(),
                    alignment=ft.Alignment.CENTER_LEFT,
                    content=ft.Text(
                        "Empty paragraph",
                        size=COMPARE_COL_FONT_SIZE,
                        color=config.ON_SURFACE_VARIANT,
                        italic=True,
                    ),
                ),
            )
        md = markdown_preview_with_task_checkboxes(serialize_single_block(block))
        md_ctrl = ft.Markdown(
            value=md,
            selectable=False,
            extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
            soft_line_break=True,
            md_style_sheet=sheet,
        )
        return ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.CLICK,
            on_tap=lambda _e, ix=block_index: self.start_edit(ix, 0),
            content=ft.Container(
                padding=_read_body_padding(),
                content=md_ctrl,
            ),
        )

    def _build_table_read(
        self, block_index: int, block: WysiwygBlock, sheet: ft.MarkdownStyleSheet
    ) -> ft.Control:
        rows = block.rows or [[""]]
        widths = compute_column_widths_px(rows, self._avail_width)
        preview = build_table_preview(
            rows, widths, sheet, has_header=block.has_header
        )
        return ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.CLICK,
            on_tap=lambda _e, ix=block_index: self.start_edit(ix, 0),
            content=ft.Container(
                padding=ft.Padding.symmetric(vertical=4),
                content=preview,
            ),
        )

    def _build_edit_surface(
        self, block_index: int, block: WysiwygBlock, sheet: ft.MarkdownStyleSheet
    ) -> ft.Control:
        if block.kind == "table":
            return self._build_table_edit(block_index, block, sheet)
        ec = ui_theme.editor_text_color()
        mono = ft.TextStyle(
            font_family="monospace",
            size=COMPARE_COL_FONT_SIZE,
            height=COMPARE_COL_LINE_HEIGHT,
            color=ec,
        )
        base = sheet.p_text_style or ft.TextStyle(size=COMPARE_COL_FONT_SIZE, color=ec)
        value = block.raw_text if block.kind == "raw" else (block.text or "")
        style = _block_text_style(block, base, sheet, mono)
        tf = ft.TextField(
            value=value,
            multiline=True,
            min_lines=1,
            max_lines=None,
            autofocus=True,
            text_vertical_align=ft.VerticalAlignment.START,
            border=ft.InputBorder.NONE,
            filled=False,
            text_style=style,
            cursor_color=config.PRIMARY_COLOR,
            selection_color=config.SELECTION_OVERLAY,
            content_padding=_edit_content_padding(block),
            expand=True,
            on_change=self._on_edit_change,
            on_blur=self._on_edit_blur,
            on_focus=lambda _e: self._on_edit_focus(),
            on_selection_change=self._on_edit_selection_change,
            enable_interactive_selection=True,
        )
        self._edit_field = tf
        return self._wrap_edit_surface(block_index, ft.Container(expand=True, content=tf))

    def _on_edit_selection_change(self, e: ft.TextSelectionChangeEvent) -> None:
        if self.on_selection_change:
            self.on_selection_change(e)

    def _build_table_edit(
        self, block_index: int, block: WysiwygBlock, sheet: ft.MarkdownStyleSheet
    ) -> ft.Control:
        rows = block.rows or [[""]]
        ncols = max(len(r) for r in rows)
        widths = compute_column_widths_px(rows, self._avail_width)
        if len(widths) < ncols:
            widths.extend([80.0] * (ncols - len(widths)))
        head_style = sheet.table_head_text_style or sheet.p_text_style
        body_style = sheet.table_body_text_style or sheet.p_text_style
        cell_fields: list[list[ft.TextField]] = []
        table_rows: list[ft.Control] = []
        for ri, row in enumerate(rows):
            row_fields: list[ft.TextField] = []
            cells: list[ft.Control] = []
            for ci in range(ncols):
                cell_val = row[ci] if ci < len(row) else ""
                tf = ft.TextField(
                    value=cell_val,
                    border=ft.InputBorder.OUTLINE,
                    dense=True,
                    text_style=body_style if not (block.has_header and ri == 0) else head_style,
                    content_padding=ft.padding.symmetric(horizontal=4, vertical=2),
                    on_change=self._on_edit_change,
                    on_blur=self._on_edit_blur,
                    on_focus=lambda _e: self._on_edit_focus(),
                )
                row_fields.append(tf)
                cells.append(
                    ft.Container(
                        width=widths[ci] if ci < len(widths) else 80.0,
                        content=tf,
                    )
                )
            cell_fields.append(row_fields)
            table_rows.append(ft.Row(cells, spacing=4))
        self._table_cell_fields[block_index] = cell_fields
        self._edit_field = cell_fields[0][0] if cell_fields and cell_fields[0] else None
        table_body = ft.Container(
            padding=ft.padding.only(bottom=8),
            content=ft.Column(table_rows, spacing=4),
        )
        return self._wrap_edit_surface(block_index, table_body)

    def _on_edit_focus(self) -> None:
        if self.on_active_field_change:
            self.on_active_field_change()


def _read_body_padding() -> ft.Padding:
    return ft.Padding.symmetric(
        horizontal=_BLOCK_BODY_H_PAD,
        vertical=_BLOCK_BODY_V_PAD,
    )


def _edit_content_padding(block: WysiwygBlock) -> ft.Padding:
    left = _BLOCK_BODY_H_PAD
    if block.kind in ("bullet", "ordered", "task"):
        left = _BLOCK_BODY_H_PAD + _LIST_READ_INDENT_PX
    elif block.kind == "blockquote":
        left = _BLOCK_BODY_H_PAD + 10
    return ft.Padding.only(
        left=left,
        right=_BLOCK_BODY_H_PAD,
        top=_BLOCK_BODY_V_PAD,
        bottom=_BLOCK_BODY_V_PAD,
    )


def _block_text_style(
    block: WysiwygBlock,
    base: ft.TextStyle,
    sheet: ft.MarkdownStyleSheet,
    mono: ft.TextStyle,
) -> ft.TextStyle:
    if block.kind in ("code_fence", "raw"):
        return mono
    if block.kind == "heading":
        level = max(1, min(6, block.level or 1))
        for attr, lv in (
            ("h1_text_style", 1),
            ("h2_text_style", 2),
            ("h3_text_style", 3),
            ("h4_text_style", 4),
            ("h5_text_style", 5),
            ("h6_text_style", 6),
        ):
            if lv == level:
                st = getattr(sheet, attr, None)
                if st:
                    return st
    return base
