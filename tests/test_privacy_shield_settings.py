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
            enabled_company=False,
            enabled_cloud=False,
        )
    }
    merged = _merge_with_bundled(store)
    assert "phone" in merged
    assert not merged["email"].enabled_company
    assert not merged["email"].enabled_cloud
    assert merged["phone"].enabled_company
    assert merged["phone"].enabled_cloud


def test_build_prompt_lists_enabled_llm_categories() -> None:
    save_categories(
        {
            "email": PrivacyCategory(
                id="email",
                label="Email",
                priority=1,
                placeholder="EMAIL",
                mode="regex",
                enabled_company=True,
                enabled_cloud=True,
            ),
            "person": PrivacyCategory(
                id="person",
                label="Person",
                priority=1,
                placeholder="PERSON",
                mode="llm",
                enabled_company=True,
                enabled_cloud=False,
            ),
        }
    )
    prompt_company = build_redact_system_prompt(tier="company")
    assert "{{PERSON_1}}" in prompt_company or "PERSON_1" in prompt_company
    assert "person names" in prompt_company.lower()

    prompt_cloud = build_redact_system_prompt(tier="cloud")
    assert "{{PERSON_1}}" not in prompt_cloud
    assert "person names" not in prompt_cloud.lower()
