"""Tests for privacy shield redaction and LlmChatBackend gating."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from iterthink.ai.llm_router import LlmChatBackend
from iterthink.ai.privacy_shield import (
    RedactionMap,
    _extract_json_object,
    privacy_shield_applies_to_tier,
    reinject_response,
    reinject_text,
    redact_messages,
    redact_text_via_local_llm,
    should_show_masked_in_chat,
    split_redaction_chunks,
)
from iterthink.privacy_shield_settings import format_placeholder, regex_redact_configured


def test_should_show_masked_in_chat(monkeypatch) -> None:
    from iterthink import config

    monkeypatch.setattr(config, "PRIVACY_SHIELD_COMPANY_ENABLED", True)
    monkeypatch.setattr(config, "PRIVACY_SHIELD_CLOUD_ENABLED", True)
    monkeypatch.setattr(config, "PRIVACY_SHIELD_ENABLED", True)
    monkeypatch.setattr(config, "PRIVACY_SHIELD_SHOW_MASKED_IN_CHAT", True)
    assert privacy_shield_applies_to_tier("company")
    assert should_show_masked_in_chat("cloud")
    assert not should_show_masked_in_chat("local")
    monkeypatch.setattr(config, "PRIVACY_SHIELD_SHOW_MASKED_IN_CHAT", False)
    assert not should_show_masked_in_chat("company")


def test_privacy_shield_per_tier_master_switch(monkeypatch) -> None:
    from iterthink import config

    monkeypatch.setattr(config, "PRIVACY_SHIELD_COMPANY_ENABLED", True)
    monkeypatch.setattr(config, "PRIVACY_SHIELD_CLOUD_ENABLED", False)
    assert privacy_shield_applies_to_tier("company")
    assert not privacy_shield_applies_to_tier("cloud")


def test_format_placeholder() -> None:
    assert format_placeholder("EMAIL", 1) == "{{EMAIL_1}}"


def test_regex_redact_email() -> None:
    text = "Contact me at alice@example.com please."
    redacted, mapping = regex_redact_configured(text)
    assert "alice@example.com" not in redacted
    assert "{{EMAIL_1}}" in redacted
    assert mapping["{{EMAIL_1}}"] == "alice@example.com"


def test_reinject_longest_first() -> None:
    rmap = RedactionMap()
    rmap.add("{{PERSON_1}}", "Ann")
    rmap.add("{{PERSON_10}}", "Bob")
    out = reinject_text("Hello {{PERSON_10}} and {{PERSON_1}}", rmap)
    assert out == "Hello Bob and Ann"


def test_reinject_legacy_angle_brackets() -> None:
    rmap = RedactionMap()
    rmap.add("{{PERSON_1}}", "Ann")
    out = reinject_text("Hello <<PERSON_1>>", rmap)
    assert out == "Hello Ann"


def test_reinject_response_dict() -> None:
    rmap = RedactionMap()
    rmap.add("{{ORG_1}}", "Acme Corp")
    resp = {"message": {"content": "Contact {{ORG_1}} for details."}}
    out = reinject_response(resp, rmap)
    assert out["message"]["content"] == "Contact Acme Corp for details."


def test_redact_text_via_local_llm_normalizes_generic_redacted() -> None:
    async def _run() -> None:
        payload = {
            "redacted_text": (
                "abstract ag and {{REDACTED}} is a genius. "
                "His email is {{EMAIL_1}}. His adress is {{REDACTED}},"
            ),
            "entities": [
                {"placeholder": "{{REDACTED}}", "value": "Simon Dilhas", "type": "person"},
                {"placeholder": "{{REDACTED}}", "value": "Bahnhofstrasse 1", "type": "address"},
            ],
        }

        with patch(
            "iterthink.ai.privacy_shield.complete_redaction_json",
            new_callable=AsyncMock,
            return_value=json.dumps(payload),
        ):
            text = (
                "abstract ag and Simon Dilhas is a genius. "
                "His email is simon@test.com. His adress is Bahnhofstrasse 1,"
            )
            redacted, rmap = await redact_text_via_local_llm(text)

        assert "{{PERSON_1}}" in redacted
        assert "{{ADDRESS_1}}" in redacted
        assert "{{EMAIL_1}}" in redacted
        assert "{{REDACTED}}" not in redacted
        assert rmap._entries["{{PERSON_1}}"] == "Simon Dilhas"
        assert rmap._entries["{{ADDRESS_1}}"] == "Bahnhofstrasse 1"
        assert rmap._entries["{{EMAIL_1}}"] == "simon@test.com"

    asyncio.run(_run())


def test_redact_text_via_local_llm_merges_maps() -> None:
    async def _run() -> None:
        payload = {
            "redacted_text": "Hi {{PERSON_1}} at {{API_KEY_1}}",
            "entities": [{"placeholder": "{{PERSON_1}}", "value": "Jane Doe", "type": "person"}],
        }

        with patch(
            "iterthink.ai.privacy_shield.complete_redaction_json",
            new_callable=AsyncMock,
            return_value=json.dumps(payload),
        ):
            text = "Hi Jane Doe at sk-abcdefghijklmnopqrstuvwxyz1234567890"
            redacted, rmap = await redact_text_via_local_llm(text)
        assert "{{PERSON_1}}" in redacted
        assert "{{API_KEY_1}}" in redacted
        assert "Jane Doe" in rmap._entries.values()
        assert any(v.startswith("sk-") for v in rmap._entries.values())

    asyncio.run(_run())


def test_redact_messages_all_roles() -> None:
    async def _run() -> None:
        with patch(
            "iterthink.ai.privacy_shield.redact_text_via_local_llm",
            new_callable=AsyncMock,
            return_value=("redacted", RedactionMap()),
        ):
            msgs = [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "user text"},
            ]
            out, rmap = await redact_messages(msgs)
        assert len(out) == 2
        assert out[0]["content"] == "redacted"
        assert isinstance(rmap, RedactionMap)

    asyncio.run(_run())


def test_extract_json_object_from_markdown_fence() -> None:
    raw = (
        "Here are the entities:\n\n"
        "```json\n"
        '{"redacted_text": "Hi {{PERSON_1}}", "entities": []}\n'
        "```"
    )
    data = _extract_json_object(raw)
    assert data["redacted_text"] == "Hi {{PERSON_1}}"


def test_redact_simon_dilhas_sample_uses_redacted_text_fallback() -> None:
    async def _run() -> None:
        text = (
            "test pricacy shield abstract ag and Simon Dilhas is a genius. "
            "His email is simon.dilhas@abstract.build. His adress is Engelgasse 45,"
        )
        payload = {
            "redacted_text": (
                "test pricacy shield abstract ag and {{PERSON_1}} is a genius. "
                "His email is {{EMAIL_1}}. His adress is {{ADDRESS_1}},"
            ),
            "entities": [
                {"placeholder": "{{PERSON_1}}", "value": "Simon Dilhas", "type": "person"},
                {"placeholder": "{{ADDRESS_1}}", "value": "Engelgasse 45", "type": "address"},
            ],
        }

        with patch(
            "iterthink.ai.privacy_shield.complete_redaction_json",
            new_callable=AsyncMock,
            return_value=json.dumps(payload),
        ):
            redacted, rmap = await redact_text_via_local_llm(text)

        assert "Simon Dilhas" not in redacted
        assert "Engelgasse 45" not in redacted
        assert "simon.dilhas@abstract.build" not in redacted
        assert "{{PERSON_1}}" in redacted
        assert "{{ADDRESS_1}}" in redacted
        assert "{{EMAIL_1}}" in redacted
        assert rmap._entries["{{PERSON_1}}"] == "Simon Dilhas"

    asyncio.run(_run())


def test_split_redaction_chunks_packs_and_overlaps() -> None:
    short = "Para one.\n\nPara two.\n\nPara three."
    one = split_redaction_chunks(short, max_chars=500, overlap_paragraphs=1)
    assert one == [short]

    paras = [f"Paragraph {i} with some text.\n\n" for i in range(12)]
    long_text = "".join(paras)
    chunks = split_redaction_chunks(long_text, max_chars=80, overlap_paragraphs=1)
    assert len(chunks) > 1
    for ch in chunks:
        assert len(ch) <= 80
    if len(chunks) >= 2:
        assert chunks[0][-20:] in chunks[1] or "Paragraph" in chunks[1]


def test_split_redaction_chunks_oversized_paragraph() -> None:
    huge = "x" * 10_000
    chunks = split_redaction_chunks(huge, max_chars=3000, overlap_paragraphs=0)
    assert len(chunks) > 1
    for ch in chunks:
        assert len(ch) <= 3000
    assert chunks[0].startswith("x")
    assert chunks[-1].endswith("x")


def test_redact_text_chunked_merges_entities() -> None:
    async def _run() -> None:
        from iterthink import config

        calls: list[str] = []

        async def _fake_complete(system: str, user: str) -> str:
            calls.append(user)
            entities: list[dict[str, str]] = []
            if "Alice Smith" in user:
                entities.append(
                    {"placeholder": "{{PERSON_1}}", "value": "Alice Smith", "type": "person"},
                )
            if "Bob Jones" in user:
                entities.append(
                    {"placeholder": "{{PERSON_1}}", "value": "Bob Jones", "type": "person"},
                )
            return json.dumps({"redacted_text": "", "entities": entities})

        filler = "Neutral filler text without names. " * 8
        text = (
            "Block A: Alice Smith works here.\n\n"
            + filler
            + "\n\nBlock B: Bob Jones works there.\n\n"
            + filler
        )

        with (
            patch.object(config, "PRIVACY_SHIELD_CHUNK_MAX_CHARS", 100),
            patch.object(config, "PRIVACY_SHIELD_CHUNK_OVERLAP_PARAGRAPHS", 0),
            patch(
                "iterthink.ai.privacy_shield.complete_redaction_json",
                side_effect=_fake_complete,
            ),
        ):
            redacted, rmap = await redact_text_via_local_llm(text)

        assert len(calls) > 1
        assert "Alice Smith" not in redacted
        assert "Bob Jones" not in redacted
        assert "{{PERSON_1}}" in redacted
        assert "{{PERSON_2}}" in redacted
        assert "Alice Smith" in rmap._entries.values()
        assert "Bob Jones" in rmap._entries.values()

    asyncio.run(_run())


def test_redact_text_short_single_llm_call() -> None:
    async def _run() -> None:
        from iterthink import config

        mock = AsyncMock(return_value=json.dumps({"redacted_text": "", "entities": []}))

        with (
            patch.object(config, "PRIVACY_SHIELD_CHUNK_MAX_CHARS", 50_000),
            patch("iterthink.ai.privacy_shield.complete_redaction_json", mock),
        ):
            await redact_text_via_local_llm("Short note from Jane Doe.")

        mock.assert_awaited_once()

    asyncio.run(_run())


def test_llm_backend_skips_shield_on_local_tier() -> None:
    async def _run() -> None:
        ollama = AsyncMock()
        ollama.chat.return_value = {"message": {"content": "ok"}}

        backend = LlmChatBackend(
            ollama,
            tier="local",
            cloud_vendor="openai",
            local_model="llama3:8B",
            company_openai_model="gpt-4o-mini",
            company_openai_base_url="https://api.openai.com/v1",
            cloud_anthropic_model="",
            cloud_openai_model="",
            cloud_google_model="",
            secrets={},
        )

        with patch("iterthink.ai.llm_router.redact_messages", new_callable=AsyncMock) as mock_redact:
            await backend.chat(
                messages=[{"role": "user", "content": "Secret John at Acme"}],
                stream=False,
            )
            mock_redact.assert_not_called()
        ollama.chat.assert_awaited_once()

    asyncio.run(_run())


def test_llm_backend_skips_reinject_when_disabled() -> None:
    async def _run() -> None:
        ollama = AsyncMock()
        rmap = RedactionMap()
        rmap.add("{{PERSON_1}}", "John")
        redacted_msgs = [{"role": "user", "content": "Hello {{PERSON_1}}"}]
        mock_redact = AsyncMock(return_value=(redacted_msgs, rmap))

        async def fake_openai_nonstream(client, **kwargs):
            return {"message": {"content": "Reply about {{PERSON_1}}"}}, None

        backend = LlmChatBackend(
            ollama,
            tier="company",
            cloud_vendor="openai",
            local_model="llama3:8B",
            company_openai_model="gpt-4o-mini",
            company_openai_base_url="https://api.openai.com/v1",
            cloud_anthropic_model="",
            cloud_openai_model="",
            cloud_google_model="",
            secrets={"company_openai": "sk-test"},
            privacy_shield_reinject=False,
        )

        with (
            patch("iterthink.ai.llm_router.redact_messages", mock_redact),
            patch("iterthink.ai.llm_router._openai_nonstream", fake_openai_nonstream),
            patch("iterthink.ai.llm_router.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            resp = await backend.chat(
                messages=[{"role": "user", "content": "Hello John"}],
                stream=False,
            )

        assert resp["message"]["content"] == "Reply about {{PERSON_1}}"

    asyncio.run(_run())


def test_llm_backend_redacts_before_company_call() -> None:
    async def _run() -> None:
        ollama = AsyncMock()
        rmap = RedactionMap()
        rmap.add("{{PERSON_1}}", "John")

        redacted_msgs = [{"role": "user", "content": "Hello {{PERSON_1}}"}]
        mock_redact = AsyncMock(return_value=(redacted_msgs, rmap))

        captured_messages: list[list[dict[str, str]]] = []

        async def fake_openai_nonstream(client, **kwargs):
            captured_messages.append(list(kwargs["messages"]))
            return {"message": {"content": "Reply about {{PERSON_1}}"}}, None

        backend = LlmChatBackend(
            ollama,
            tier="company",
            cloud_vendor="openai",
            local_model="llama3:8B",
            company_openai_model="gpt-4o-mini",
            company_openai_base_url="https://api.openai.com/v1",
            cloud_anthropic_model="",
            cloud_openai_model="",
            cloud_google_model="",
            secrets={"company_openai": "sk-test"},
            privacy_shield_reinject=True,
        )

        with (
            patch("iterthink.ai.llm_router.redact_messages", mock_redact),
            patch("iterthink.ai.llm_router._openai_nonstream", fake_openai_nonstream),
            patch("iterthink.ai.llm_router.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            resp = await backend.chat(
                messages=[{"role": "user", "content": "Hello John"}],
                stream=False,
            )

        mock_redact.assert_awaited_once()
        assert captured_messages
        assert "{{PERSON_1}}" in captured_messages[0][0]["content"]
        assert "John" not in captured_messages[0][0]["content"]
        assert resp["message"]["content"] == "Reply about John"

    asyncio.run(_run())


def test_download_gguf_streams_with_progress(tmp_path, monkeypatch) -> None:
    from iterthink.ai import privacy_shield_gguf as gguf_mod

    monkeypatch.setattr(gguf_mod.config, "STORE_DIR", tmp_path)
    monkeypatch.setattr(gguf_mod.config, "PRIVACY_SHIELD_CACHE_NAME", "qwen-2.5-1.5b.gguf")
    monkeypatch.setattr(gguf_mod.config, "PRIVACY_SHIELD_HF_REPO", "Qwen/Qwen2.5-1.5B-Instruct-GGUF")
    monkeypatch.setattr(gguf_mod.config, "PRIVACY_SHIELD_HF_FILE", "qwen2.5-1.5b-instruct-q4_k_m.gguf")
    monkeypatch.setattr(gguf_mod, "_hf_download_url", lambda: "https://hf.example/model.gguf")

    body = b"x" * 2048
    progress: list[float] = []

    class _FakeResp:
        headers = {"content-length": str(len(body))}

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, chunk_size: int = 0):
            yield body[:1024]
            yield body[1024:]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with patch("iterthink.ai.privacy_shield_gguf.httpx.stream", return_value=_FakeResp()):
        dest = gguf_mod.download_privacy_shield_gguf_sync(progress.append, force=True)

    assert dest == tmp_path / "privacy_shield" / "qwen-2.5-1.5b.gguf"
    assert dest.read_bytes() == body
    assert progress[-1] == 1.0
    assert any(0 < p < 1 for p in progress)


def test_is_gguf_ready_skips_download(tmp_path, monkeypatch) -> None:
    from iterthink.ai.privacy_shield_gguf import ensure_privacy_shield_gguf_sync, is_gguf_ready

    from iterthink.ai import privacy_shield_gguf as gguf_mod

    monkeypatch.setattr(gguf_mod.config, "STORE_DIR", tmp_path)
    monkeypatch.setattr(gguf_mod.config, "PRIVACY_SHIELD_CACHE_NAME", "qwen-2.5-1.5b.gguf")
    cache = tmp_path / "privacy_shield" / "qwen-2.5-1.5b.gguf"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"ok")
    assert is_gguf_ready()

    with patch("iterthink.ai.privacy_shield_gguf.httpx.stream") as mock_stream:
        ensure_privacy_shield_gguf_sync()
        mock_stream.assert_not_called()


def test_load_categories_from_store(tmp_path, monkeypatch) -> None:
    from iterthink import privacy_shield_settings as pss

    monkeypatch.setattr(pss.config, "STORE_DIR", tmp_path)
    pss._cached = None
    cats = pss.load_categories(reload=True)
    assert "email" in cats
    assert cats["email"].example_token(1) == "{{EMAIL_1}}"
    assert "project_name" in cats
    assert "govt_id" in cats
    ordered = [c.id for c in pss.categories_for_ui()]
    assert ordered.index("email") < ordered.index("password")
    assert ordered.index("password") < ordered.index("credit_card")
