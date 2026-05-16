"""Shared UI constants for studio modules."""

import flet as ft

COMPARE_COL_FONT_SIZE = 14

# KI sidebar / analyse pills
KI_PILL_TEXT_SIZE = 10

# Floating Analyse result card timing
RESULT_CARD_HIDE_DELAY_SEC = 0.18

# Autosave: disk flush (no DB row) vs snapshot row (idle checkpoint)
AUTOSAVE_DISK_IDLE_SEC = 30.0
AUTOSAVE_SNAPSHOT_IDLE_SEC = 300.0

# Side rails
COLLAPSED_RAIL_WIDTH_PX = 36
SIDEBAR_EXPANDED_WIDTH_PX = 280
SIDEBAR_TOOLBAR_ROW_H_PX = 36
# Inner well (explorer tree + KI topic/chat) inside SIDEBAR_SURFACE card
SIDEBAR_INNER_BORDER_RADIUS_PX = 8
SIDEBAR_INNER_PAD_PX = 4

# Pane split handle: same width collapsed/expanded; pill + bar height (~2 cm @ 96dpi); strip = hit pad
PANE_HANDLE_HEIGHT_PX = 76
PANE_HANDLE_WIDTH_PX = 10
PANE_HANDLE_STRIP_W_PX = 12

KI_TAB_BODY_MIN_HEIGHT_PX = 96
KI_TAB_PAGE_PAD_V_PX = 8
KI_TAB_BAR_TO_PILLS_GAP_PX = 12
KI_TAB_ICON_PX = 18
# KI tier strip in chat composer: lighter than topic tabs (~15% smaller outlined glyphs).
KI_TIER_TAB_ICON_PX = 15

READING_MAX_PX = 720
COMPOSE_READING_WIDTH_FRAC = 0.92

DIFF_SPAN_CHAR_CAP = 120_000

# Review (Future) tab: TF-IDF paragraph alignment cost explodes with block count; above this
# we pair baseline vs candidate by index only (still editable; gap deletes not mapped).
REVIEW_ALIGN_PARAGRAPH_CAP = 320

COMPARE_COL_LINE_HEIGHT = 1.6
# Compose main editor: line height for rail Y math (must match TextField text_style size × height).
COMPOSE_EDITOR_LINE_HEIGHT_PX = float(COMPARE_COL_FONT_SIZE * COMPARE_COL_LINE_HEIGHT)
# Compose main editor content insets (keep in sync with studio/__init__.py TextField content_padding).
COMPOSE_EDITOR_CONTENT_PAD_LEFT_PX = 4
COMPOSE_EDITOR_CONTENT_PAD_RIGHT_PX = 4
COMPOSE_EDITOR_CONTENT_PAD_TOP_PX = 0
# Subtracted from editor content width before wrap column count (caret / scrollbar slop heuristic).
COMPOSE_EDITOR_WRAP_WIDTH_RESERVE_PX = 3
# Compose TextField uses monospace; heuristic advance for wrap / toolbar X (tuned below generic 0.6em).
COMPOSE_EDITOR_MONO_CHAR_WIDTH_EST_PX = float(COMPARE_COL_FONT_SIZE) * 0.58
COMPOSE_MARGIN_COL_W = 104
COMPARE_PILL_COL_W = 100

RESULT_CARD_W = 380
RESULT_CARD_MAX_H = 360

COMPARE_ACTION_GRID_CELL = 40
COMPARE_ACTION_INNER_W = COMPARE_ACTION_GRID_CELL * 2
COMPARE_ACTION_H_PAD = 5
COMPARE_ACTION_V_PAD = 2
COMPARE_ACTION_COL_W = COMPARE_ACTION_INNER_W + 2 * COMPARE_ACTION_H_PAD
# Vertical size of the 2×2 compare action rectangle (grid + vertical padding + 1px border top+bottom).
COMPARE_ACTION_RECTANGLE_OUTER_MIN_H = (
    2 * COMPARE_ACTION_GRID_CELL + 2 * COMPARE_ACTION_V_PAD + 2
)
# Top padding on the hover wrapper around that rectangle (see action_chrome.wrap_workspace_action_chrome).
COMPARE_ACTION_RAIL_CHROME_TOP_PAD = 4
COMPARE_ACTION_RAIL_HOVER_WRAP_MIN_H = COMPARE_ACTION_RAIL_CHROME_TOP_PAD + COMPARE_ACTION_RECTANGLE_OUTER_MIN_H
# ``build_action_rectangle`` uses ``ft.border.all(1, …)``; center of bottom-left (comment) cell for overlay alignment.
_COMPARE_ACTION_CARD_BORDER_PX = 1
COMPARE_ACTION_COMMENT_ICON_CX = float(
    _COMPARE_ACTION_CARD_BORDER_PX + COMPARE_ACTION_H_PAD + COMPARE_ACTION_GRID_CELL / 2
)
COMPARE_ACTION_COMMENT_ICON_CY = float(
    COMPARE_ACTION_RAIL_CHROME_TOP_PAD
    + _COMPARE_ACTION_CARD_BORDER_PX
    + COMPARE_ACTION_V_PAD
    + COMPARE_ACTION_GRID_CELL
    + COMPARE_ACTION_GRID_CELL / 2
)
# Eval column on Compare/Review rows mirrors the action column width so the row stays symmetric.
COMPARE_EVAL_COL_W = COMPARE_ACTION_COL_W
# Wider eval column when showing symbol + truncated paragraph summary.
COMPARE_EVAL_COL_W_WIDE = 176

PROJECT_PAGE_URL = "https://www.yourcompanyos.io"
PROJECT_PAGE_TOOLTIP = "Start workflow on {yourcompany}os."

COMPARE_KEY_CURRENT = "__current__"
COMPARE_KEY_CANDIDATE = "__candidate__"

# Main tab indices
TAB_HISTORY = 0
TAB_PRESENT = 1
TAB_FUTURE = 2

# KI right strip: Comments first, then Discuss / Change / Analyse / Act (must match strip order).
KI_TOPIC_COMMENTS = 0
KI_TOPIC_DISCUSS = 1
KI_TOPIC_CHANGE = 2
KI_TOPIC_ANALYSE = 3
KI_TOPIC_ACT = 4

# Review tab: no DB proposal loaded; candidate mirrors compose so Accept still persists.
REVIEW_MANUAL_CANDIDATE_ACTION_ID = "manual_review"
# Review candidate dropdown: compare editor draft against an editable copy (no AI snapshot).
REVIEW_KEY_DRAFT_MIRROR = "__review_draft_mirror__"
# Review candidate dropdown: draft vs pyspellchecker-suggested body (SPELL_PREVIEW source).
REVIEW_KEY_SPELL_CHECK = "__review_spell_check__"
# Accept / snapshot label when applying spelling suggestions from Review.
REVIEW_SPELL_CANDIDATE_ACTION_ID = "spell_review"

# History toolbar: horizontal gap between Older vs Newer version dropdown columns.
# At ~96 dpi, 76 px ≈ 2 cm total; with equal flex children each dropdown column is ~1 cm narrower.
HISTORY_COMPARE_DROPDOWN_COLUMNS_GAP_PX = 76
