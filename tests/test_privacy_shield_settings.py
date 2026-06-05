"""Tests for privacy shield category config."""

from __future__ import annotations

from iterthink.privacy_shield_settings import (
    CATEGORY_DISPLAY_ORDER,
    PrivacyCategory,
    _merge_with_bundled,
    _bundled_categories,
    build_redact_system_prompt,
    format_placeholder,
    load_categories,
    save_categories,
)


def test_bundled_has_all_sixteen_categories() -> None:
    bundled = _bundled_categories()
    assert len(bundled) >= 16
    for cid in CATEGORY_DISPLAY_ORDER:
        assert cid in bundled


def test_merge_adds_missing_categories_from_bundled() -> None:
    bundled = _bundled_categories()
    store = {
        "email": PrivacyCategory(
            id="email",
            label="Email",
            priority=1,
            placeholder="EMAIL",
            mode="regex",
            enabled=False,
        )
    }
    merged = _merge_with_bundled(store)
    assert "phone" in merged
    assert merged["email"].enabled is False
    assert merged["phone"].enabled is True


def test_build_prompt_lists_enabled_llm_categories() -> None:
    save_categories(
        {
            "email": PrivacyCategory(
                id="email",
                label="Email",
                priority=1,
                placeholder="EMAIL",
                mode="regex",
                enabled=True,
            ),
            "person": PrivacyCategory(
                id="person",
                label="Person",
                priority=1,
                placeholder="PERSON",
                mode="llm",
                enabled=True,
            ),
        }
    )
    prompt = build_redact_system_prompt()
    assert "{{PERSON_1}}" in prompt or "PERSON_1" in prompt
    assert "person names" in prompt.lower()
