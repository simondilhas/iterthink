"""IFC (Industry Foundation Classes) compare view for MarkdownStudio.

Status: placeholder — not yet implemented.

-------------------------------------------------------------------------------
How to add a new format renderer (IFC or any future format)
-------------------------------------------------------------------------------
1. Add its member to ``CompareCandidateSource`` in ``iterthink/studio/history/candidate_state.py``:
       ``IFC_ORIGINAL`` (value ``"ifc_original"``).

2. Implement the methods in this mixin (see stubs below).

3. The compare routing in ``iterthink/studio/history/dispatch.py`` (``_rebuild_compare_view()``)
   already dispatches to ``_rebuild_compare_ifc_panes()`` when
   ``_compare_candidate_source == CompareCandidateSource.IFC_ORIGINAL``.

4. Register the mixin in ``markdown_studio.py`` (it is already in the MRO):
       ``class MarkdownStudio(... MarkdownStudioIfcFormat ...)``

5. Wire import detection into ``_select_snapshot_as_candidate`` in
   ``iterthink/studio/history/dropdowns.py`` — add an ``elif ifc_rel:`` branch analogous to the
   existing ``pdf_rel`` and ``docx_rel`` branches.
-------------------------------------------------------------------------------

About IFC
---------
IFC (ISO 16739) is the open BIM (Building Information Modelling) exchange
format. The intended user experience mirrors the PDF plan overlay view:
show two versions of a building model side-by-side (or as a diff overlay)
so the user can review what changed between design iterations.
"""

from __future__ import annotations


class MarkdownStudioIfcFormat:
    """Compare-view mixin for IFC building-model files.

    Intended as a drop-in sibling of ``MarkdownStudioAssetCompare`` (which
    handles PDF/DOCX). All IFC-specific rendering belongs here; the compare
    orchestrator in ``iterthink.studio.history`` stays format-agnostic.
    """

    def _rebuild_compare_ifc_panes(self) -> None:
        """Render an IFC model diff in the History compare pane.

        Not yet implemented. Displays a placeholder message so the app
        degrades gracefully if an IFC snapshot is somehow selected before
        the renderer is written.

        Implementation notes (for the contributor picking this up):
        - Left pane: current markdown text (same as PDF pane left side).
        - Right pane: IFC 3-D diff or property-change table.
        - Consider ``ifcopenshell`` for parsing and ``pythreejs`` or a Flet
          WebView embedding a Three.js scene for visualisation.
        """
        # TODO: implement IFC side-by-side / diff renderer.
        self._snack("IFC compare view is not yet implemented.")  # type: ignore[attr-defined]

    def _open_ifc_file_async(self) -> None:
        """Entry point for importing an IFC file into the version store.

        Not yet implemented. When ready:
        - Use ``document_import`` to copy the .ifc asset into the store.
        - Persist a snapshot row with ``reason="ifc_original"`` and store
          the asset relpath so ``_select_snapshot_as_candidate`` can find it.
        """
        # TODO: import IFC via document_import, persist asset relpath.
        raise NotImplementedError
