"""Single dispatch entry for format-specific compare renderers."""

from __future__ import annotations

from ..constants import TAB_HISTORY
from .candidate_state import CompareCandidateSource


class _HistoryDispatchMixin:
    def _rebuild_compare_view(self) -> None:
        """Rebuild the History compare pane for the current candidate source.

        This is the single dispatch point for format-specific compare renderers.
        Adding a new format requires three steps:
          1. Add its literal to ``CompareCandidateSource`` in ``history/candidate_state.py``.
          2. Implement ``_rebuild_compare_<fmt>_panes()`` in its own mixin.
          3. Add one ``elif`` branch here.
        """
        source = self._compare_candidate_source
        if source == CompareCandidateSource.PDF_ORIGINAL:
            if hasattr(self, "_refresh_plan_compare_bar"):
                self._refresh_plan_compare_bar()
            self._rebuild_compare_pdf_panes()
            self._sync_compare_pdf_layers_visibility()
        elif source == CompareCandidateSource.DOCX_ORIGINAL:
            self._rebuild_compare_docx_panes()
            self._sync_compare_pdf_layers_visibility()
        elif source == CompareCandidateSource.IFC_ORIGINAL:
            # Dispatches to MarkdownStudioIfcFormat (formats.ifc).
            self._rebuild_compare_ifc_panes()
            self._sync_compare_pdf_layers_visibility()
        else:
            self._rebuild_compare_paragraph_ui()
        if self._main_tab_index == TAB_HISTORY:
            self._refresh_tab_toolbar()
