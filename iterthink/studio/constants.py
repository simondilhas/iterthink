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

COMPARE_COL_LINE_HEIGHT = 1.6
# Compose main editor: line height for rail Y math (must match TextField text_style size × height).
COMPOSE_EDITOR_LINE_HEIGHT_PX = float(COMPARE_COL_FONT_SIZE * COMPARE_COL_LINE_HEIGHT)
# Compose main editor content insets (keep in sync with studio/__init__.py TextField content_padding).
COMPOSE_EDITOR_CONTENT_PAD_LEFT_PX = 4
COMPOSE_EDITOR_CONTENT_PAD_RIGHT_PX = 4
COMPOSE_EDITOR_CONTENT_PAD_TOP_PX = 0
COMPOSE_MARGIN_COL_W = 104
COMPARE_PILL_COL_W = 100

RESULT_CARD_W = 380
RESULT_CARD_MAX_H = 360

COMPARE_ACTION_GRID_CELL = 40
COMPARE_ACTION_INNER_W = COMPARE_ACTION_GRID_CELL * 2
COMPARE_ACTION_H_PAD = 5
COMPARE_ACTION_V_PAD = 2
COMPARE_ACTION_COL_W = COMPARE_ACTION_INNER_W + 2 * COMPARE_ACTION_H_PAD
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

# Review tab: no DB proposal loaded; candidate mirrors compose so Accept still persists.
REVIEW_MANUAL_CANDIDATE_ACTION_ID = "manual_review"
# Review candidate dropdown: compare editor draft against an editable copy (no AI snapshot).
REVIEW_KEY_DRAFT_MIRROR = "__review_draft_mirror__"

# History toolbar: horizontal gap between Older vs Newer version dropdown columns.
# At ~96 dpi, 76 px ≈ 2 cm total; with equal flex children each dropdown column is ~1 cm narrower.
HISTORY_COMPARE_DROPDOWN_COLUMNS_GAP_PX = 76
