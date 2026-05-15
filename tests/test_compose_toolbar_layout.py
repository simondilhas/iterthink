"""Tests for compose selection toolbar layout estimation."""

from iterthink.studio.compose_toolbar_layout import selection_first_line_anchor_px


def test_single_visual_line_center() -> None:
    # stack_w 100, pads 0, char_w 10 -> wrap_cols = 10
    text = "abcdefghij"
    ax, y = selection_first_line_anchor_px(
        text, 0, 4, stack_w=100.0, pad_l=0.0, pad_r=0.0, pad_t=0.0, char_w_px=10.0, line_h_px=22.0
    )
    assert ax == 20.0  # (0 + 4) / 2 * 10
    assert y == 0.0


def test_soft_wrap_second_line() -> None:
    # content_w 40, char_w 10 -> wrap_cols = 4 -> "abcd"|"ef"
    text = "abcdef"
    ax, y = selection_first_line_anchor_px(
        text, 4, 6, stack_w=40.0, pad_l=0.0, pad_r=0.0, pad_t=0.0, char_w_px=10.0, line_h_px=22.0
    )
    assert y == 22.0
    assert ax == 10.0  # "ef" -> cols 0..2 -> center col 1 * 10


def test_multiline_clips_to_first_line_of_selection() -> None:
    text = "abc\ndefghi"
    # First line "abc", newline at 3; select [0, 8) -> first visual line segment [0, 3)
    ax, y = selection_first_line_anchor_px(
        text, 0, 8, stack_w=200.0, pad_l=0.0, pad_r=0.0, pad_t=0.0, char_w_px=10.0, line_h_px=16.0
    )
    assert y == 0.0
    assert ax == 15.0  # "abc" cols 0..3 -> center 1.5 * 10


def test_padding_shifts_anchor() -> None:
    text = "ab"
    ax, y = selection_first_line_anchor_px(
        text,
        0,
        2,
        stack_w=100.0,
        pad_l=4.0,
        pad_r=4.0,
        pad_t=8.0,
        char_w_px=10.0,
        line_h_px=20.0,
    )
    assert y == 8.0
    assert ax == 4.0 + 10.0  # pad_l + center of 2 chars


def test_invalid_returns_none() -> None:
    assert selection_first_line_anchor_px("a", 1, 1, 100, 0, 0, 0, 10, 20) is None
    assert selection_first_line_anchor_px("a", 0, 2, 100, 0, 0, 0, 10, 20) is None


def test_tab_counts_four_columns() -> None:
    text = "a\tb"
    # wrap wide enough for one line
    ax, _ = selection_first_line_anchor_px(
        text, 0, 3, stack_w=500.0, pad_l=0.0, pad_r=0.0, pad_t=0.0, char_w_px=10.0, line_h_px=20.0
    )
    # cols: pos0=0, pos3 after a(1)+tab(4)+b(1)=6 -> center (0+6)/2*10 = 30
    assert ax == 30.0


def test_word_wrap_breaks_at_space() -> None:
    # content_w 40, char_w 10 -> 4 cols; "foo " fits, "foo b" overflows -> break after space; "bar" on second row
    text = "foo bar"
    ax, y = selection_first_line_anchor_px(
        text, 4, 7, stack_w=40.0, pad_l=0.0, pad_r=0.0, pad_t=0.0, char_w_px=10.0, line_h_px=22.0
    )
    assert y == 22.0
    assert ax == 15.0  # center of "bar" on second visual line


def test_wrap_width_reserve_narrows_line() -> None:
    # stack 40, reserve 10 -> effective content 30 -> 3 cols vs 4 without reserve
    text = "abcdef"
    ax, y = selection_first_line_anchor_px(
        text,
        3,
        6,
        stack_w=40.0,
        pad_l=0.0,
        pad_r=0.0,
        pad_t=0.0,
        char_w_px=10.0,
        line_h_px=22.0,
        wrap_width_reserve=10.0,
    )
    assert y == 22.0
    assert ax == 15.0  # "def" on second line, center of three chars
