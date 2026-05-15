"""File tree, rename/import dialogs, and open_file."""

from __future__ import annotations

import asyncio
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any, Literal

import flet as ft

from iterthink import config
from iterthink.persistence import store_db, version_storage
from iterthink.services import document_import
from iterthink.db.session import session_scope
from .constants import TAB_FUTURE, TAB_PRESENT
from .util import ctrl_on_page as _ctrl_on_page
from .tree import (
    PROJECT_CONTEXT_BASENAME,
    build_search_md_tree,
    is_excluded_from_doc_tree,
    list_visible_children,
    project_context_markdown,
)

ExplorerTreeSortMode = Literal["name_az", "name_za", "mtime_newest", "mtime_oldest"]


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _sorted_dirnames(node: dict[str, Any], parent_path: Path, mode: str) -> list[str]:
    names = [k for k in node if k != "_files"]
    if mode == "name_az":
        return sorted(names, key=str.lower)
    if mode == "name_za":
        return sorted(names, key=str.lower, reverse=True)
    if mode == "mtime_newest":
        return sorted(names, key=lambda n: (-_safe_mtime(parent_path / n), n.lower()))
    if mode == "mtime_oldest":
        return sorted(names, key=lambda n: (_safe_mtime(parent_path / n), n.lower()))
    return sorted(names, key=str.lower)


def _sorted_file_entries(
    entries: list[tuple[str, Path]], mode: str
) -> list[tuple[str, Path]]:
    if mode == "name_az":
        return sorted(entries, key=lambda x: x[0].lower())
    if mode == "name_za":
        return sorted(entries, key=lambda x: x[0].lower(), reverse=True)
    if mode == "mtime_newest":
        return sorted(entries, key=lambda x: (-_safe_mtime(x[1]), x[0].lower()))
    if mode == "mtime_oldest":
        return sorted(entries, key=lambda x: (_safe_mtime(x[1]), x[0].lower()))
    return sorted(entries, key=lambda x: x[0].lower())


def _sorted_child_dir_paths(dirs: list[Path], mode: str) -> list[Path]:
    if mode == "name_az":
        return sorted(dirs, key=lambda p: p.name.lower())
    if mode == "name_za":
        return sorted(dirs, key=lambda p: p.name.lower(), reverse=True)
    if mode == "mtime_newest":
        return sorted(dirs, key=lambda p: (-_safe_mtime(p), p.name.lower()))
    if mode == "mtime_oldest":
        return sorted(dirs, key=lambda p: (_safe_mtime(p), p.name.lower()))
    return sorted(dirs, key=lambda p: p.name.lower())


def _sorted_child_md_paths(paths: list[Path], mode: str) -> list[Path]:
    if mode == "name_az":
        return sorted(paths, key=lambda p: p.name.lower())
    if mode == "name_za":
        return sorted(paths, key=lambda p: p.name.lower(), reverse=True)
    if mode == "mtime_newest":
        return sorted(paths, key=lambda p: (-_safe_mtime(p), p.name.lower()))
    if mode == "mtime_oldest":
        return sorted(paths, key=lambda p: (_safe_mtime(p), p.name.lower()))
    return sorted(paths, key=lambda p: p.name.lower())


def first_markdown_in_tree(root: Path, sort_mode: ExplorerTreeSortMode = "name_az") -> Path | None:
    """First ``.md`` path in DFS order matching the lazy sidebar (``list_visible_children`` + sort mode)."""
    if not root.is_dir():
        return None
    mode = sort_mode if sort_mode in ("name_az", "name_za", "mtime_newest", "mtime_oldest") else "name_az"
    root_res = root.resolve()

    def walk(parent: Path) -> Path | None:
        dirs, files = list_visible_children(parent)
        for d in _sorted_child_dir_paths(dirs, mode):
            hit = walk(d)
            if hit is not None:
                return hit
        md_sorted = _sorted_child_md_paths(files, mode)
        if md_sorted:
            return md_sorted[0]
        return None

    return walk(root_res)


class MarkdownStudioExplorer:
    def _on_tree_search_change(self, _e: ft.ControlEvent | None = None) -> None:
        self._rebuild_tree_ui()
        if _ctrl_on_page(self.tree_column):
            self.tree_column.update()

    def _on_tree_sort_selected(self, mode: ExplorerTreeSortMode, _e: ft.ControlEvent | None = None) -> None:
        self._tree_sort_mode = mode
        self._rebuild_tree_ui()
        if _ctrl_on_page(self.tree_column):
            self.tree_column.update()

    async def _apply_rename_path(
        self,
        path: Path,
        *,
        is_dir: bool,
        raw: str,
    ) -> Literal["noop", "renamed", "blocked"]:
        """Validate, optionally flush dirty buffer, rename on disk and in DB."""
        root = config.DOCUMENTS.resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError:
            self._snack("Cannot rename outside the documents folder.")
            return "blocked"

        is_md_file = not is_dir and path.suffix.lower() == ".md"
        raw = (raw or "").strip()
        if is_md_file:
            if raw.lower().endswith(".md"):
                raw = raw[: -len(".md")].strip()
            new_name = f"{raw}.md" if raw else ""
        else:
            new_name = raw
        if not new_name or new_name in (".", ".."):
            self._snack("Invalid name.")
            return "blocked"
        if "/" in new_name or "\\" in new_name:
            self._snack("Name cannot contain path separators.")
            return "blocked"

        new_path = (path.parent / new_name).resolve()
        try:
            new_path.relative_to(root)
        except ValueError:
            self._snack("Invalid target path.")
            return "blocked"

        if new_path == path.resolve():
            return "noop"
        if new_path.exists():
            self._snack("A file or folder with that name already exists.")
            return "blocked"

        old_resolved = path.resolve()
        if self.current_path and self._is_dirty():
            cur = self.current_path.resolve()
            if not is_dir and cur == old_resolved:
                await self.save_file(silent=True, snapshot_reason="pre_switch")
            elif is_dir:
                try:
                    cur.relative_to(old_resolved)
                    await self.save_file(silent=True, snapshot_reason="pre_switch")
                except ValueError:
                    pass

        try:
            path.rename(new_path)
        except OSError as ex:
            self._snack(f"Rename failed: {ex}")
            return "blocked"

        new_resolved = new_path.resolve()
        _db_collision = "iterthink_rename_db_collision"
        try:
            with session_scope() as s:
                if is_dir:
                    st = version_storage.update_document_paths_after_dir_rename(s, old_resolved, new_resolved)
                else:
                    st = version_storage.update_document_path_after_rename(s, old_resolved, new_resolved)
                if st == "collision":
                    raise RuntimeError(_db_collision)
        except RuntimeError as ex:
            if ex.args and ex.args[0] == _db_collision:
                try:
                    new_path.rename(path)
                except OSError:
                    self._snack("Rename rolled back with a database conflict; check document paths in settings.")
                    return "blocked"
                self._snack("That name conflicts with the version library database.")
                return "blocked"
            raise
        except Exception:
            try:
                new_path.rename(path)
            except OSError:
                pass
            raise

        if self.current_path:
            cur = self.current_path.resolve()
            if not is_dir and cur == old_resolved:
                self.current_path = new_resolved
            elif is_dir:
                try:
                    rel = cur.relative_to(old_resolved)
                    self.current_path = new_resolved / rel
                except ValueError:
                    pass

        return "renamed"

    def _show_rename_path_dialog(self, path: Path, *, is_dir: bool) -> None:
        root = config.DOCUMENTS.resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError:
            self._snack("Cannot rename outside the documents folder.")
            return

        is_md_file = not is_dir and path.suffix.lower() == ".md"
        name_field = ft.TextField(
            value=path.stem if is_md_file else path.name,
            autofocus=True,
            dense=True,
            width=280 if is_md_file else 360,
        )
        dialog_content: ft.Control
        if is_md_file:
            dialog_content = ft.Row(
                [name_field, ft.Text(".md", size=14, color=config.ON_SURFACE_VARIANT)],
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        else:
            dialog_content = name_field

        async def apply_async() -> None:
            raw = (name_field.value or "").strip()
            r = await self._apply_rename_path(path, is_dir=is_dir, raw=raw)
            if r == "blocked":
                return
            self.page.pop_dialog()
            if r == "renamed":
                self._rebuild_tree_ui()
                if _ctrl_on_page(self.tree_column):
                    self.tree_column.update()
                self._refresh_compare_tab_candidate_ui()
                self._refresh_title_bar()
                self._snack("Renamed.")

        def on_ok(_e: ft.ControlEvent | None = None) -> None:
            self.page.run_task(apply_async)

        name_field.on_submit = lambda _e: self.page.run_task(apply_async)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Rename folder" if is_dir else "Rename file", weight=ft.FontWeight.W_600),
                content=dialog_content,
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                    ft.TextButton("OK", on_click=on_ok),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    def _show_new_folder_dialog(self, parent: Path) -> None:
        root = config.DOCUMENTS.resolve()
        parent = parent.resolve()
        try:
            parent.relative_to(root)
        except ValueError:
            self._snack("Invalid parent folder.")
            return
        if parent != root and not parent.is_dir():
            self._snack("Parent is not a folder.")
            return
        if parent == root:
            self._snack("Create a project from + instead of a root-level folder.")
            return
        config.DOCUMENTS.mkdir(parents=True, exist_ok=True)
        name_field = ft.TextField(
            label="Folder name",
            autofocus=True,
            dense=True,
            width=360,
        )

        async def apply_async() -> None:
            raw = (name_field.value or "").strip()
            if not raw or raw in (".", ".."):
                self._snack("Invalid name.")
                return
            if "/" in raw or "\\" in raw:
                self._snack("Name cannot contain path separators.")
                return
            new_path = (parent / raw).resolve()
            try:
                new_path.relative_to(root)
            except ValueError:
                self._snack("Invalid target path.")
                return
            if new_path.exists():
                self._snack("A file or folder with that name already exists.")
                return
            try:
                new_path.mkdir(parents=False)
            except OSError as ex:
                self._snack(f"Could not create folder: {ex}")
                return
            self.page.pop_dialog()
            self._rebuild_tree_ui()
            if _ctrl_on_page(self.tree_column):
                self.tree_column.update()
            self._snack(f'Created folder "{new_path.name}".')

        def on_ok(_e: ft.ControlEvent | None = None) -> None:
            self.page.run_task(apply_async)

        name_field.on_submit = lambda _e: self.page.run_task(apply_async)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("New subfolder", weight=ft.FontWeight.W_600),
                content=name_field,
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                    ft.TextButton("Create", on_click=on_ok),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    def _show_create_project_dialog(self) -> None:
        root = config.DOCUMENTS.resolve()
        config.DOCUMENTS.mkdir(parents=True, exist_ok=True)
        name_field = ft.TextField(
            label="Project name (new folder)",
            autofocus=True,
            dense=True,
            width=360,
        )

        async def apply_async() -> None:
            raw = (name_field.value or "").strip()
            if not raw or raw in (".", ".."):
                self._snack("Invalid name.")
                return
            if "/" in raw or "\\" in raw:
                self._snack("Name cannot contain path separators.")
                return
            project_dir = (config.DOCUMENTS / raw).resolve()
            try:
                project_dir.relative_to(root)
            except ValueError:
                self._snack("Invalid target path.")
                return
            if project_dir.exists():
                self._snack("A file or folder with that name already exists.")
                return
            ctx_path = project_dir / PROJECT_CONTEXT_BASENAME
            try:
                project_dir.mkdir(parents=False)
                ctx_path.write_text(project_context_markdown(raw), encoding="utf-8")
            except OSError as ex:
                self._snack(f"Could not create project: {ex}")
                try:
                    if project_dir.is_dir() and not any(project_dir.iterdir()):
                        project_dir.rmdir()
                except OSError:
                    pass
                return
            self.page.pop_dialog()
            self._rebuild_tree_ui()
            if _ctrl_on_page(self.tree_column):
                self.tree_column.update()
            self._snack(f'Created project "{project_dir.name}".')
            await self.open_file(ctx_path)

        def on_ok(_e: ft.ControlEvent | None = None) -> None:
            self.page.run_task(apply_async)

        name_field.on_submit = lambda _e: self.page.run_task(apply_async)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Create project", weight=ft.FontWeight.W_600),
                content=name_field,
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                    ft.TextButton("Create", on_click=on_ok),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    def _show_new_markdown_in_folder_dialog(self, parent: Path) -> None:
        root = config.DOCUMENTS.resolve()
        parent = parent.resolve()
        try:
            parent.relative_to(root)
        except ValueError:
            self._snack("Invalid folder.")
            return
        if parent == root:
            self._snack("Use the top + menu for a new markdown file at the documents root.")
            return
        if not parent.is_dir():
            self._snack("Not a folder.")
            return
        name_tf = ft.TextField(
            label="File name (without .md)",
            autofocus=True,
            dense=True,
            width=360,
        )

        async def apply_async() -> None:
            name = (name_tf.value or "").strip()
            if not name:
                self._snack("Enter a file name.")
                return
            safe = "".join(c for c in name if c.isalnum() or c in " ._-")[:200].strip()
            if not safe:
                self._snack("Invalid file name.")
                return
            dest = (parent / f"{safe}.md").resolve()
            try:
                dest.relative_to(root)
            except ValueError:
                self._snack("Invalid target path.")
                return
            if dest.exists():
                self._snack("A file with that name already exists.")
                return
            try:
                dest.write_text("", encoding="utf-8")
            except OSError as ex:
                self._snack(f"Could not create file: {ex}")
                return
            self.page.pop_dialog()
            self._rebuild_tree_ui()
            if _ctrl_on_page(self.tree_column):
                self.tree_column.update()
            await self.open_file(dest)
            self._snack(f'Created "{dest.name}".')

        def on_ok(_e: ft.ControlEvent | None = None) -> None:
            self.page.run_task(apply_async)

        name_tf.on_submit = lambda _e: self.page.run_task(apply_async)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("New markdown in folder", weight=ft.FontWeight.W_600),
                content=name_tf,
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                    ft.TextButton("Create", on_click=on_ok),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    async def _defer_show_delete_file_dialog(self, path: Path) -> None:
        """Open delete confirmation after the tree popup menu has closed (Flet event ordering)."""
        await asyncio.sleep(0)
        self._show_delete_file_dialog(path)

    async def _defer_show_delete_folder_dialog(self, path: Path) -> None:
        await asyncio.sleep(0)
        self._show_delete_folder_dialog(path)

    async def _defer_show_move_file_dialog(self, path: Path) -> None:
        """Open move dialog after the tree popup menu has closed (Flet event / dialog stack ordering)."""
        await asyncio.sleep(0)
        self._show_move_file_dialog(path)

    def _tree_delete_target_is_open_note(self, rp: Path) -> bool:
        """True when ``rp`` is the note currently open in the editor (symlink-safe)."""
        cur = self.current_path
        if cur is None:
            return False
        try:
            if cur.exists() and rp.exists():
                return cur.samefile(rp)
        except OSError:
            pass
        try:
            return cur.resolve() == rp.resolve()
        except OSError:
            return False

    async def _apply_delete_file_confirmed_async(self, path: Path) -> None:
        """Confirm-handler: remove DB row, store assets, and the ``.md`` file on disk."""
        try:
            await asyncio.sleep(0)
            root = config.DOCUMENTS.resolve()
            try:
                rp = path.resolve()
                rp.relative_to(root)
            except (ValueError, OSError):
                self._snack("Cannot delete outside the documents folder.")
                return
            if rp.suffix.lower() != ".md":
                self._snack("Only markdown files can be deleted here.")
                return

            open_here = self._tree_delete_target_is_open_note(rp)
            if open_here:
                self._flush_review_edits_if_changed()

            doc_id: int | None = None
            try:
                with session_scope() as s:
                    doc_id = version_storage.document_id_for_resolved_path(s, rp)
                    version_storage.delete_document_row_if_any(s, rp)
            except BaseException as ex:
                print(traceback.format_exc(), file=sys.stderr, flush=True)
                self._snack(f"Could not update library: {ex}")
                return

            if open_here:
                self._detach_pdf_import_ui_for_store_delete()
                await asyncio.sleep(0.06)

            if doc_id is not None:
                try:
                    store_db.impact_version_chunk_delete_for_document(self._db, doc_id)
                except BaseException:
                    print(traceback.format_exc(), file=sys.stderr, flush=True)

            try:
                version_storage.purge_document_store_dirs(rp)
            except BaseException as ex:
                print(traceback.format_exc(), file=sys.stderr, flush=True)
                self._snack(f"Could not remove stored assets: {ex}")
                return
            try:
                rp.unlink(missing_ok=True)
            except OSError as ex:
                print(traceback.format_exc(), file=sys.stderr, flush=True)
                self._snack(f"Could not delete file: {ex}")
                return

            self.page.pop_dialog()
            if open_here:
                self._clear_open_document_ui()
            self._rebuild_tree_ui()
            if _ctrl_on_page(self.tree_column):
                self.tree_column.update()
            self._refresh_compare_tab_candidate_ui()
            self._refresh_title_bar()
            self._snack("File deleted.")
        except BaseException as ex:
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            try:
                self._snack(f"Delete failed: {ex}")
            except BaseException:
                pass

    async def _apply_delete_folder_confirmed_async(self, folder: Path) -> None:
        """Confirm-handler: remove all ``.md`` notes under ``folder`` and the folder itself."""
        try:
            await asyncio.sleep(0)
            folder_resolved = folder.resolve()
            cur_resolved = self.current_path.resolve() if self.current_path else None
            opened_under = False
            if cur_resolved:
                try:
                    cur_resolved.relative_to(folder_resolved)
                    opened_under = True
                except ValueError:
                    pass
            if opened_under:
                self._flush_review_edits_if_changed()
                self._detach_pdf_import_ui_for_store_delete()
                await asyncio.sleep(0.06)

            md_paths: list[Path] = []
            try:
                for p in folder.rglob("*.md"):
                    if is_excluded_from_doc_tree(p):
                        continue
                    md_paths.append(p.resolve())
            except OSError as ex:
                print(traceback.format_exc(), file=sys.stderr, flush=True)
                self._snack(f"Could not scan folder: {ex}")
                return

            try:
                for rp in md_paths:
                    doc_id: int | None = None
                    try:
                        with session_scope() as s:
                            doc_id = version_storage.document_id_for_resolved_path(s, rp)
                            version_storage.delete_document_row_if_any(s, rp)
                    except BaseException as ex:
                        print(traceback.format_exc(), file=sys.stderr, flush=True)
                        self._snack(f"Could not update library: {ex}")
                        return
                    if doc_id is not None:
                        try:
                            store_db.impact_version_chunk_delete_for_document(self._db, doc_id)
                        except BaseException:
                            print(traceback.format_exc(), file=sys.stderr, flush=True)
                    try:
                        version_storage.purge_document_store_dirs(rp)
                    except BaseException as ex:
                        print(traceback.format_exc(), file=sys.stderr, flush=True)
                        self._snack(f"Could not remove stored assets: {ex}")
                        return
                    try:
                        rp.unlink(missing_ok=True)
                    except OSError as ex:
                        print(traceback.format_exc(), file=sys.stderr, flush=True)
                        self._snack(f"Could not delete file: {ex}")
                        return
                shutil.rmtree(folder_resolved, ignore_errors=False)
            except OSError as ex:
                print(traceback.format_exc(), file=sys.stderr, flush=True)
                self._snack(f"Could not delete folder: {ex}")
                return

            self.page.pop_dialog()
            if opened_under:
                self._clear_open_document_ui()
            self._rebuild_tree_ui()
            if _ctrl_on_page(self.tree_column):
                self.tree_column.update()
            self._refresh_compare_tab_candidate_ui()
            self._refresh_title_bar()
            self._snack("Folder deleted.")
        except BaseException as ex:
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            try:
                self._snack(f"Delete failed: {ex}")
            except BaseException:
                pass

    def _clear_open_document_ui(self) -> None:
        """Reset editor and compare state when no file is open (e.g. after delete)."""
        self._cancel_autosave_timers()
        self._compare_candidate_source = "draft"
        self._compare_snapshot_version_id = None
        self._compare_newer_version_id = None
        self._compare_newer_cached_body = ""
        self._pending_ai_accept_action_id = None
        self._compare_pdf_peer_snapshot_id = None
        self._latest_ai_proposal_vid = None
        self._ai_proposal_action_ids.clear()
        self._loaded_proposal_sha = None
        self.current_path = None
        self.last_saved_text = ""
        self.editor.value = ""
        self._editor_prev_for_list_continue = ""
        self._compare_editor.value = ""
        self._compare_baseline_snapshot = ""
        if _ctrl_on_page(self.editor):
            self.editor.update()
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()
        self._sync_version_toolbar_state()
        self._refresh_compare_tab_candidate_ui()
        self._refresh_compare_diff_immediate()
        self._refresh_compare_bulk_buttons()
        self._refresh_title_bar()
        self._refresh_compose_plan_surface()

    def _show_delete_file_dialog(self, path: Path) -> None:
        root = config.DOCUMENTS.resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError:
            self._snack("Cannot delete outside the documents folder.")
            return
        if path.suffix.lower() != ".md":
            self._snack("Only markdown files can be deleted here.")
            return

        body = ft.Text(
            f"Delete “{path.name}”? The file, its version snapshots, and stored import assets "
            "for this note will be removed. This cannot be undone.",
            size=13,
        )

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Delete file", weight=ft.FontWeight.W_600),
                content=body,
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                    ft.TextButton(
                        "Delete",
                        style=ft.ButtonStyle(color=ft.Colors.RED_400),
                        on_click=lambda _e, p=path: self.page.run_task(self._apply_delete_file_confirmed_async, p),
                    ),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    def _show_delete_folder_dialog(self, path: Path) -> None:
        root = config.DOCUMENTS.resolve()
        folder = path.resolve()
        try:
            folder.relative_to(root)
        except ValueError:
            self._snack("Cannot delete outside the documents folder.")
            return
        if folder == root:
            self._snack("Cannot delete the documents root folder.")
            return
        if not folder.is_dir():
            self._snack("Not a folder.")
            return

        body = ft.Text(
            f"Delete folder “{path.name}” and everything inside? All notes under this folder, "
            "their version snapshots, and stored import assets will be removed. This cannot be undone.",
            size=13,
        )

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Delete folder", weight=ft.FontWeight.W_600),
                content=body,
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                    ft.TextButton(
                        "Delete",
                        style=ft.ButtonStyle(color=ft.Colors.RED_400),
                        on_click=lambda _e, fd=folder: self.page.run_task(
                            self._apply_delete_folder_confirmed_async, fd
                        ),
                    ),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    def _show_move_file_dialog(self, path: Path) -> None:
        root = config.DOCUMENTS.resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError:
            self._snack("Cannot move outside the documents folder.")
            return
        if path.suffix.lower() != ".md":
            self._snack("Only markdown files can be moved here.")
            return

        folder_entries: list[tuple[str, str]] = [("", "Documents (root)")]
        for p in sorted(root.rglob("*"), key=lambda x: str(x).lower()):
            if not p.is_dir() or is_excluded_from_doc_tree(p):
                continue
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if not rel.parts:
                continue
            posix = rel.as_posix()
            folder_entries.append((posix, posix))

        cur_parent = path.parent.resolve()
        try:
            cur_rel = cur_parent.relative_to(root)
            default_key = cur_rel.as_posix() if cur_rel.parts else ""
        except ValueError:
            default_key = ""
        keys = {k for k, _ in folder_entries}
        if default_key not in keys:
            default_key = ""

        folder_dd = ft.Dropdown(
            label="Move into folder",
            width=360,
            options=[ft.dropdown.Option(key=k, text=lab) for k, lab in folder_entries],
            value=default_key,
        )

        async def apply_move(_e: ft.ControlEvent | None = None) -> None:
            await asyncio.sleep(0)
            raw_key = folder_dd.value
            key = (raw_key or "").strip()
            if key not in keys:
                self._snack("Pick a folder.")
                return
            dest_dir = root if not key else (root / key).resolve()
            try:
                dest_dir.relative_to(root)
            except ValueError:
                self._snack("Invalid folder.")
                return
            old_resolved = path.resolve()
            new_path = (dest_dir / path.name).resolve()
            try:
                new_path.relative_to(root)
            except ValueError:
                self._snack("Invalid target path.")
                return
            if new_path == old_resolved:
                self.page.pop_dialog()
                if _ctrl_on_page(self.page):
                    self.page.update()
                return
            if new_path.exists():
                self._snack("A file with that name already exists in that folder.")
                return

            if self.current_path and self._is_dirty():
                cur = self.current_path.resolve()
                if cur == old_resolved:
                    await self.save_file(silent=True, snapshot_reason="pre_switch")

            try:
                path.rename(new_path)
            except OSError as ex:
                self._snack(f"Move failed: {ex}")
                return

            new_resolved = new_path.resolve()
            _db_collision = "iterthink_move_db_collision"
            try:
                with session_scope() as s:
                    st = version_storage.update_document_path_after_rename(s, old_resolved, new_resolved)
                    if st == "collision":
                        raise RuntimeError(_db_collision)
            except RuntimeError as ex:
                if ex.args and ex.args[0] == _db_collision:
                    try:
                        new_path.rename(path)
                    except OSError:
                        self._snack("Move rolled back: library path conflict.")
                        return
                    self._snack("That folder already has this file in the version library.")
                    return
                raise
            except BaseException:
                try:
                    new_path.rename(path)
                except OSError:
                    pass
                raise

            if self.current_path:
                cur = self.current_path.resolve()
                if cur == old_resolved:
                    self.current_path = new_resolved

            self.page.pop_dialog()
            if _ctrl_on_page(self.page):
                self.page.update()
            self._rebuild_tree_ui()
            if _ctrl_on_page(self.tree_column):
                self.tree_column.update()
            self._refresh_compare_tab_candidate_ui()
            self._refresh_title_bar()
            self._snack("File moved.")

        def on_move_click(_e: ft.ControlEvent | None = None) -> None:
            self.page.run_task(apply_move)

        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Move file", weight=ft.FontWeight.W_600),
                content=folder_dd,
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                    ft.TextButton("Move", on_click=on_move_click),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    async def _tree_import_new_clicked(self, _e: ft.ControlEvent | None = None) -> None:
        await self._run_import_pick(new_document=True, target_md=None, dest_parent=None)

    async def _tree_import_new_into_folder(self, folder_path: Path) -> None:
        await self._run_import_pick(new_document=True, target_md=None, dest_parent=folder_path)

    async def _tree_import_version_clicked(self, fp: Path) -> None:
        await self._run_import_pick(new_document=False, target_md=fp)

    async def _run_import_pick(
        self,
        *,
        new_document: bool,
        target_md: Path | None,
        dest_parent: Path | None = None,
    ) -> None:
        if not new_document and target_md is None:
            self._snack("No target file.")
            return
        self.ensure_file_pickers()
        pick_initial: str | None = None
        if config.DOCUMENTS.is_dir():
            if dest_parent is not None:
                cand = dest_parent.resolve()
                try:
                    cand.relative_to(config.DOCUMENTS.resolve())
                except ValueError:
                    cand = config.DOCUMENTS.resolve()
                pick_initial = str(cand if cand.is_dir() else config.DOCUMENTS)
            else:
                pick_initial = str(config.DOCUMENTS)
        try:
            files = await self._fp_import.pick_files(
                dialog_title="Import Word or PDF",
                initial_directory=pick_initial,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["docx", "pdf"],
            )
        except BaseException as ex:
            self._snack(f"Picker failed: {ex}")
            return
        if not files or not getattr(files[0], "path", None):
            return
        src = Path(files[0].path)
        if document_import.validate_extension(src) is None:
            self._snack("Unsupported file. Choose a .docx or .pdf file.")
            return
        if new_document:
            await self._import_finish_new_document_dialog(src, dest_parent=dest_parent)
        else:
            await self._write_import_result(src, target_md.resolve())

    async def _import_finish_new_document_dialog(
        self, src: Path, *, dest_parent: Path | None = None
    ) -> None:
        root = config.DOCUMENTS.resolve()
        base = (dest_parent if dest_parent is not None else config.DOCUMENTS).resolve()
        try:
            base.relative_to(root)
        except ValueError:
            self._snack("Cannot import outside the documents folder.")
            return
        stem = src.stem
        name_tf = ft.TextField(label="Save as (name without .md)", value=stem, dense=True, autofocus=True)
        warn_color = ft.Colors.AMBER_700 if config.IS_LIGHT else ft.Colors.AMBER_400
        warning_txt = ft.Text(
            "A file with that name already exists in that folder.",
            size=12,
            color=warn_color,
            visible=False,
        )

        async def apply(_e: ft.ControlEvent | None = None) -> None:
            name = (name_tf.value or "").strip()
            if not name:
                self._snack("Enter a file name.")
                return
            safe = "".join(c for c in name if c.isalnum() or c in " ._-")[:200].strip()
            if not safe:
                self._snack("Invalid file name.")
                return
            dest = base / f"{safe}.md"
            if dest.exists():
                self._snack("A file with that name already exists in that folder.")
                return
            self.page.pop_dialog()
            await self._write_import_result(src, dest)

        import_btn = ft.TextButton("Import", on_click=lambda _e: self.page.run_task(apply))

        def sync_name_warning(_e: ft.ControlEvent | None = None) -> None:
            name = (name_tf.value or "").strip()
            if not name:
                warning_txt.visible = False
                import_btn.disabled = False
            else:
                safe = "".join(c for c in name if c.isalnum() or c in " ._-")[:200].strip()
                if not safe:
                    warning_txt.visible = False
                    import_btn.disabled = False
                else:
                    dest = base / f"{safe}.md"
                    conflict = dest.exists()
                    warning_txt.visible = conflict
                    import_btn.disabled = conflict
            if _ctrl_on_page(warning_txt):
                warning_txt.update()
            if _ctrl_on_page(import_btn):
                import_btn.update()

        name_tf.on_change = sync_name_warning
        name_tf.on_submit = lambda _e: self.page.run_task(apply)
        sync_name_warning()
        dialog_col = ft.Column([name_tf, warning_txt], tight=True, spacing=4)
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Save imported markdown"),
                content=dialog_col,
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _e: self.page.pop_dialog()),
                    import_btn,
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
        )

    async def _write_import_result(self, src: Path, dest: Path) -> None:
        try:
            md = document_import.import_file_to_markdown(src, dest)
        except BaseException as ex:
            self._snack(f"Import failed: {ex}")
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            dest.write_text(md, encoding="utf-8")
        except OSError as ex:
            self._snack(f"Could not write file: {ex}")
            return
        ext = document_import.validate_extension(src)
        pdf_src = src if ext == "pdf" else None
        docx_src = src if ext == "docx" else None
        pdf_prof: version_storage.PdfProfile | None = None
        if pdf_src is not None:
            pdf_prof = document_import.classify_pdf_profile(src)
        new_vid: int | None = None
        try:
            with session_scope() as s:
                new_vid = version_storage.persist_version_snapshot(
                    s,
                    dest.resolve(),
                    md,
                    "import",
                    skip_if_unchanged_sha=False,
                    pdf_source_path=pdf_src,
                    docx_source_path=docx_src,
                    pdf_profile=pdf_prof,
                )
        except BaseException as ex:
            self._snack(f"Could not record version: {ex}")
            return
        self._rebuild_tree_ui()
        if _ctrl_on_page(self.tree_column):
            self.tree_column.update()
        # PDF import: open Review on the import snapshot. Word: land on Focus (Present).
        select_vid = new_vid if ext == "pdf" else None
        await self.open_file(dest, after_import_vid=select_vid)
        self._snack("Imported.")

    def _on_tree_file_row_hover(self, e: ft.ControlEvent, menu_wrap: ft.Container) -> None:
        menu_wrap.opacity = 1.0 if e.data else 0.0
        if _ctrl_on_page(menu_wrap):
            menu_wrap.update()

    def _tree_file_inline_rename_active(self, fpath: Path) -> bool:
        t = getattr(self, "_tree_file_rename_target", None)
        return t is not None and fpath.resolve() == t

    def _begin_tree_file_inline_rename(self, path: Path) -> None:
        root = config.DOCUMENTS.resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError:
            self._snack("Cannot rename outside the documents folder.")
            return
        self._tree_file_rename_target = path.resolve()
        self._rebuild_tree_ui()
        if _ctrl_on_page(self.tree_column):
            self.tree_column.update()
        self.page.run_task(self._focus_tree_file_rename_field_async)

    async def _focus_tree_file_rename_field_async(self) -> None:
        await asyncio.sleep(0.05)
        tf = getattr(self, "_tree_file_rename_field", None)
        if tf is not None and _ctrl_on_page(tf):
            await tf.focus()

    def _on_tree_file_rename_field_submit(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._commit_tree_file_inline_rename_async)

    def _on_tree_file_rename_field_blur(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._commit_tree_file_inline_rename_async)

    async def _commit_tree_file_inline_rename_async(self) -> None:
        lock = getattr(self, "_tree_file_rename_lock", None)
        if lock is None:
            return
        async with lock:
            path = getattr(self, "_tree_file_rename_target", None)
            tf = getattr(self, "_tree_file_rename_field", None)
            if path is None or tf is None:
                self._tree_file_rename_target = None
                self._tree_file_rename_field = None
                return
            raw = (tf.value or "").strip()
            r = await self._apply_rename_path(path, is_dir=False, raw=raw)
            self._tree_file_rename_target = None
            self._tree_file_rename_field = None
            self._rebuild_tree_ui()
            if _ctrl_on_page(self.tree_column):
                self.tree_column.update()
            if r == "renamed":
                self._refresh_compare_tab_candidate_ui()
                self._refresh_title_bar()
                self._snack("Renamed.")

    def _make_tree_file_row(self, fname: str, fpath: Path) -> ft.Control:
        fp = fpath
        menu_btn = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            icon_size=18,
            icon_color=config.ON_SURFACE_VARIANT,
            tooltip="File actions",
            items=[
                ft.PopupMenuItem(
                    content=ft.Text("Export…", size=13),
                    on_click=lambda _e, p=fp: self.page.run_task(self.begin_export_to_word, p),
                ),
                ft.PopupMenuItem(
                    content=ft.Text("Rename…", size=13),
                    on_click=lambda _e, p=fp: self._begin_tree_file_inline_rename(p),
                ),
                ft.PopupMenuItem(
                    content=ft.Text("Import new version…", size=13),
                    on_click=lambda _e, p=fp: self.page.run_task(self._tree_import_version_clicked, p),
                ),
                ft.PopupMenuItem(
                    content=ft.Text("Move to folder…", size=13),
                    on_click=lambda _e, p=fp: self.page.run_task(self._defer_show_move_file_dialog, p),
                ),
                ft.PopupMenuItem(
                    content=ft.Text("Delete file…", size=13),
                    on_click=lambda _e, p=fp: self.page.run_task(self._defer_show_delete_file_dialog, p),
                ),
            ],
        )
        menu_wrap = ft.Container(content=menu_btn, opacity=0.0, animate_opacity=150)
        if self._tree_file_inline_rename_active(fp):
            stem_tf = ft.TextField(
                value=fp.stem,
                dense=True,
                text_size=12,
                max_lines=1,
                filled=False,
                bgcolor=ft.Colors.TRANSPARENT,
                border=ft.InputBorder.UNDERLINE,
                border_width=1,
                border_color=config.OUTLINE,
                focused_border_color=config.PRIMARY_COLOR,
                cursor_color=config.PRIMARY_COLOR,
                selection_color=config.SELECTION_OVERLAY,
                content_padding=ft.padding.only(left=8, right=4, bottom=2, top=2),
                expand=True,
                on_submit=self._on_tree_file_rename_field_submit,
                on_blur=self._on_tree_file_rename_field_blur,
            )
            self._tree_file_rename_field = stem_tf
            name_block = ft.Row(
                [
                    stem_tf,
                    ft.Text(".md", size=12, font_family="monospace", color=config.ON_SURFACE_VARIANT),
                ],
                tight=True,
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
            )
            name_hit = ft.Container(expand=True, content=name_block)
        else:
            name_hit = ft.Container(
                expand=True,
                content=ft.GestureDetector(
                    mouse_cursor=ft.MouseCursor.CLICK,
                    on_tap=lambda _e, p=fp: self.page.run_task(self.open_file, p),
                    content=ft.Container(
                        content=ft.Text(fname, size=12, font_family="monospace"),
                        padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                    ),
                ),
            )
        row_inner = ft.Row(
            [name_hit, menu_wrap],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )
        return ft.Container(
            content=row_inner,
            padding=ft.padding.only(right=2),
            on_hover=lambda e: self._on_tree_file_row_hover(e, menu_wrap),
        )

    def _make_tree_folder_title_row(self, dirname: str, folder_path: Path) -> ft.Control:
        fp = folder_path
        menu_btn = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            icon_size=18,
            icon_color=config.ON_SURFACE_VARIANT,
            tooltip="Folder actions",
            items=[
                ft.PopupMenuItem(
                    content=ft.Text("New markdown…", size=13),
                    on_click=lambda _e, p=fp: self._show_new_markdown_in_folder_dialog(p),
                ),
                ft.PopupMenuItem(
                    content=ft.Text("New subfolder…", size=13),
                    on_click=lambda _e, p=fp: self._show_new_folder_dialog(p),
                ),
                ft.PopupMenuItem(
                    content=ft.Text("Import…", size=13),
                    on_click=lambda _e, p=fp: self.page.run_task(self._tree_import_new_into_folder, p),
                ),
                ft.PopupMenuItem(),
                ft.PopupMenuItem(
                    content=ft.Text("Rename…", size=13),
                    on_click=lambda _e, p=fp: self._show_rename_path_dialog(p, is_dir=True),
                ),
                ft.PopupMenuItem(
                    content=ft.Text("Delete folder…", size=13),
                    on_click=lambda _e, p=fp: self.page.run_task(self._defer_show_delete_folder_dialog, p),
                ),
            ],
        )
        actions_wrap = ft.Container(
            content=menu_btn,
            opacity=0.0,
            animate_opacity=150,
        )
        name_hit = ft.Container(
            expand=True,
            content=ft.GestureDetector(
                mouse_cursor=ft.MouseCursor.CLICK,
                on_double_tap=lambda _e, p=fp: self._show_rename_path_dialog(p, is_dir=True),
                content=ft.Container(
                    content=ft.Text(dirname, size=13, color=config.ON_SURFACE),
                    padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                ),
            ),
        )
        row_inner = ft.Row(
            [name_hit, actions_wrap],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )
        return ft.Container(
            content=row_inner,
            padding=ft.padding.only(right=2),
            on_hover=lambda e: self._on_tree_file_row_hover(e, actions_wrap),
        )

    def _render_lazy_folder_children(self, folder_path: Path, depth: int) -> list[ft.Control]:
        sort_mode = getattr(self, "_tree_sort_mode", "name_az")
        dirs, files = list_visible_children(folder_path)
        ctrls: list[ft.Control] = []
        for d in _sorted_child_dir_paths(dirs, sort_mode):
            ctrls.append(self._make_lazy_folder_expansion_tile(d, depth + 1))
        for fp in _sorted_child_md_paths(files, sort_mode):
            ctrls.append(self._make_tree_file_row(fp.name, fp))
        return ctrls

    def _make_lazy_folder_expansion_tile(self, folder_path: Path, depth: int) -> ft.Control:
        inner_col = ft.Column([], tight=True, spacing=0)
        pad = ft.Container(
            content=inner_col,
            padding=ft.Padding.only(left=8),
        )

        def on_folder_change(e: ft.ControlEvent) -> None:
            if not e.data:
                return
            if inner_col.controls:
                return
            inner_col.controls.extend(self._render_lazy_folder_children(folder_path, depth))
            if _ctrl_on_page(inner_col):
                inner_col.update()

        return ft.ExpansionTile(
            title=self._make_tree_folder_title_row(folder_path.name, folder_path),
            controls=[pad],
            expanded=False,
            maintain_state=True,
            dense=True,
            affinity=ft.TileAffinity.LEADING,
            show_trailing_icon=True,
            leading=None,
            icon_color=config.ON_SURFACE_VARIANT,
            collapsed_icon_color=config.ON_SURFACE_VARIANT,
            on_change=on_folder_change,
        )

    def _rebuild_tree_ui(self) -> None:
        self.tree_column.controls.clear()
        self._tree_file_rename_field = None
        root = config.DOCUMENTS
        if not root.is_dir():
            self.tree_column.controls.append(
                ft.Text(f"Missing folder: {root}", size=12, color=ft.Colors.ORANGE_200)
            )
            return

        q = (self.tree_search_field.value or "").strip()
        sort_mode = getattr(self, "_tree_sort_mode", "name_az")
        root_res = root.resolve()

        if q:
            tree = build_search_md_tree(root_res, q)

            def render_level(node: dict[str, Any], parent_path: Path, depth: int = 0) -> list[ft.Control]:
                ctrls: list[ft.Control] = []
                for dirname in _sorted_dirnames(node, parent_path, sort_mode):
                    sub = node[dirname]
                    folder_path = parent_path / dirname
                    inner = render_level(sub, folder_path, depth + 1)
                    ctrls.append(
                        ft.ExpansionTile(
                            title=self._make_tree_folder_title_row(dirname, folder_path),
                            controls=[
                                ft.Container(
                                    content=ft.Column(inner, tight=True, spacing=0),
                                    padding=ft.Padding.only(left=8),
                                )
                            ],
                            expanded=False,
                            maintain_state=True,
                            dense=True,
                            affinity=ft.TileAffinity.LEADING,
                            show_trailing_icon=True,
                            leading=None,
                            icon_color=config.ON_SURFACE_VARIANT,
                            collapsed_icon_color=config.ON_SURFACE_VARIANT,
                        )
                    )
                files = node.get("_files", [])
                for fname, fpath in _sorted_file_entries(list(files), sort_mode):
                    ctrls.append(self._make_tree_file_row(fname, fpath))
                return ctrls

            if not tree:
                self.tree_column.controls.append(
                    ft.Text("No matching files.", size=12, color=config.ON_SURFACE_VARIANT)
                )
            else:
                self.tree_column.controls.extend(render_level(tree, root_res))
            return

        dirs, files = list_visible_children(root_res)
        dirs_s = _sorted_child_dir_paths(dirs, sort_mode)
        files_s = _sorted_child_md_paths(files, sort_mode)
        top: list[ft.Control] = []
        for d in dirs_s:
            top.append(self._make_lazy_folder_expansion_tile(d, 0))
        for fp in files_s:
            top.append(self._make_tree_file_row(fp.name, fp))

        if not top:
            self.tree_column.controls.append(
                ft.Text(
                    "No projects yet. Use the explorer menu → Create project…",
                    size=12,
                    color=config.ON_SURFACE_VARIANT,
                )
            )
        else:
            self.tree_column.controls.extend(top)

    async def open_file(
        self,
        path: Path,
        *,
        after_import_vid: int | None = None,
    ) -> None:
        self._tree_file_rename_target = None
        self._tree_file_rename_field = None
        if self.current_path and path != self.current_path and self._is_dirty():
            await self.save_file(silent=True, snapshot_reason="pre_switch")
        # Persist any in-flight Review proposal edits for the previous file before switching.
        if self.current_path and path != self.current_path:
            self._flush_review_edits_if_changed()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as ex:
            self._snack(f"Could not open: {ex}")
            return

        # Reset all compare-side state for the incoming document.
        self._reset_compare_state()
        self.current_path = path
        self.last_saved_text = text
        self.editor.value = text
        self._editor_prev_for_list_continue = text
        self._compare_editor.value = text
        self._compare_baseline_snapshot = text
        self._refresh_compare_tab_candidate_ui()
        self._sync_version_toolbar_state()
        if _ctrl_on_page(self.editor):
            self.editor.update()
        if _ctrl_on_page(self._compare_editor):
            self._compare_editor.update()

        if after_import_vid is not None:
            # PDF import: Review tab with PDF + editable markdown (see _rebuild_future_pdf_import_ui).
            self._select_snapshot_as_candidate(after_import_vid)
            self._refresh_compare_tab_candidate_ui()
            await self._request_tab_switch_async(TAB_FUTURE)
            self._refresh_compare_diff_immediate()
        else:
            # Normal open: land on the Compose (Present) tab.
            was_present = self._main_tab_index == TAB_PRESENT
            await self._request_tab_switch_async(TAB_PRESENT)
            if was_present:
                self._margin_gen += 1
                await self._debounced_compose_rebuild(self._margin_gen)
            self._refresh_compare_diff_immediate()

        self._refresh_compare_bulk_buttons()
        self._refresh_title_bar()
        self._refresh_compose_plan_surface()

        if getattr(self, "_impact_tab_initialized", False) and hasattr(self, "_rebuild_impact_context_tree"):
            self._rebuild_impact_context_tree()

        try:
            with session_scope() as s:
                version_storage.update_document_last_disk_state(s, path, body=text)
        except BaseException:
            pass
