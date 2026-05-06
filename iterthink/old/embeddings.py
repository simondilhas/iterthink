import json
import os
from typing import Optional, List, Tuple
import numpy as np
from sqlalchemy.orm import Session
from datetime import datetime, UTC

from app_server.db.models import TextEmbedding
from app_server.services.services import normalize_whitespace


# Default Infomaniak embeddings model (Sentence-Transformers)
# Can be overridden with INFOMANIAK_EMBEDDINGS_MODEL
DEFAULT_INFOMANIAK_EMBEDDINGS_MODEL = os.getenv(
    "INFOMANIAK_EMBEDDINGS_MODEL",
    # Infomaniak accepted IDs (per validation error): 'mini_lm_l12_v2', 'bge_multilingual_gemma2'
    "mini_lm_l12_v2",
)


def _infomaniak_openai_client():
    """
    Return an OpenAI-compatible client configured for Infomaniak.
    Raises ValueError if required env vars are missing.
    """
    from openai import OpenAI

    api_key = os.getenv("INFOMANIAK_API_KEY", "").strip()
    product_id = os.getenv("INFOMANIAK_PRODUCT_ID", "").strip()
    if not api_key or not product_id:
        raise ValueError("Infomaniak embeddings require INFOMANIAK_API_KEY and INFOMANIAK_PRODUCT_ID")

    # Use the v1 OpenAI-compatible endpoint for embeddings per Infomaniak docs
    # https://developer.infomaniak.com/docs/api/post/1/ai/%7Bproduct_id%7D/openai/v1/embeddings
    base_url = f"https://api.infomaniak.com/1/ai/{product_id}/openai/v1"
    return OpenAI(api_key=api_key, base_url=base_url)


def _hash_text(text: str, document_id: int | None = None) -> str:
    """
    Stable SHA-256 hash for embedding cache keys, salted with document_id
    to avoid cross-document cache sharing.
    """
    import hashlib
    normalized = normalize_whitespace(text or "").lower()
    salt = str(document_id) if document_id is not None else "global"
    payload = f"{salt}|{normalized}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0 or b.size == 0:
        return 0.0
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def fetch_or_create_embedding(
    text: str,
    db: Session,
    *,
    model: str = DEFAULT_INFOMANIAK_EMBEDDINGS_MODEL,
    document_id: int | None = None,
    version_id: int | None = None,
) -> Optional[np.ndarray]:
    """
    Get an embedding vector for the given text, cached in DB by (hash, model).
    Returns None if embedding cannot be computed.
    """
    if not (text and text.strip()):
        return None

    text_hash = _hash_text(text, document_id=document_id)

    # Try cache
    cached = (
        db.query(TextEmbedding)
        .filter(TextEmbedding.text_hash == text_hash, TextEmbedding.model == model)
        .first()
    )
    if cached:
        try:
            vec = np.array(json.loads(cached.vector_json), dtype=np.float32)
            # Debug print (no raw text) to verify embedding usage
            print(
                f"EMBED: cache hit model={model} doc={cached.document_id} ver={cached.version_id} "
                f"dim={cached.dimension} hash={text_hash[:8]}..."
            )
            return vec
        except Exception:
            # If deserialization fails, fall back to re-compute
            pass

    # Normalize model to Infomaniak-supported IDs
    def _normalize_model_name(name: str) -> str:
        if not name:
            return "mini_lm_l12_v2"
        lower = name.strip().lower()
        # Map common aliases to Infomaniak IDs
        alias_map = {
            "sentence-transformers/all-minilm-l12-v2": "mini_lm_l12_v2",
            "all-minilm-l12-v2": "mini_lm_l12_v2",
            "all minilm l12 v2": "mini_lm_l12_v2",
            "minilm-l12-v2": "mini_lm_l12_v2",
            "minilm_l12_v2": "mini_lm_l12_v2",
        }
        return alias_map.get(lower, name)

    # Compute via Infomaniak
    try:
        client = _infomaniak_openai_client()
        # OpenAI-compatible embeddings endpoint
        # Use OpenAI-compatible args only; Infomaniak-specific fields like 'mode' are not accepted by the SDK
        resp = client.embeddings.create(
            model=_normalize_model_name(model),
            input=text,
        )
        vec = resp.data[0].embedding  # type: ignore[attr-defined]
        if not isinstance(vec, list):
            return None
        # Persist
        embedding = TextEmbedding(
            text_hash=text_hash,
            model=model,
            dimension=len(vec),
            vector_json=json.dumps(vec, ensure_ascii=False),
            document_id=document_id,
            version_id=version_id,
            created_at=datetime.now(UTC),
        )
        db.add(embedding)
        db.commit()
        # Debug print for successful compute
        print(
            f"EMBED: computed model={model} doc={document_id} ver={version_id} "
            f"dim={embedding.dimension} hash={text_hash[:8]}..."
        )
        return np.array(vec, dtype=np.float32)
    except Exception as exc:
        # Best-effort: do not break comparison view if embeddings aren't available
        print(f"Embedding error ({model}): {exc}")
        try:
            db.rollback()
        except Exception:
            pass
        return None


def reconcile_reformulated_with_embeddings(
    diffs: List,
    db: Session,
    *,
    proximity: int = 2,
    similarity_threshold: float = 0.86,
    model: str = DEFAULT_INFOMANIAK_EMBEDDINGS_MODEL,
    document_id: int | None = None,
    version_id: int | None = None,
) -> None:
    """
    Relabel nearby (deleted, new) pairs as 'reformulated' when their
    semantic embedding similarity is high. Operates in-place on diffs.
    - proximity: max absolute distance between indices to consider a pair
    - similarity_threshold: cosine similarity to accept as reformulation
    """
    # Collect candidate pairs
    deleted_items = [d for d in diffs if d.status == "deleted"]
    new_items = [d for d in diffs if d.status == "new"]

    if not deleted_items or not new_items:
        return

    # Precompute embeddings lazily with cache
    def get_vec(text: str) -> Optional[np.ndarray]:
        return fetch_or_create_embedding(
            text,
            db,
            model=model,
            document_id=document_id,
            version_id=version_id,
        )

    # Try to pair each new with its closest deleted within proximity
    for new_item in new_items:
        # Skip if already relabeled
        if getattr(new_item, "label", "") == "reformulated":
            continue
        best_match = None
        best_sim = 0.0
        for del_item in deleted_items:
            if getattr(del_item, "label", "") == "reformulated":
                continue
            # Proximity filter: their indices should be close in the flow
            if del_item.old_index < 0 or new_item.new_index < 0:
                continue
            if abs(del_item.old_index - new_item.new_index) > proximity:
                continue
            # Compute similarity
            v_new = get_vec(new_item.new_text or "")
            v_old = get_vec(del_item.old_text or "")
            if v_new is None or v_old is None:
                continue
            sim = _cosine_similarity(v_old, v_new)
            if sim > best_sim:
                best_sim = sim
                best_match = del_item
        if best_match and best_sim >= similarity_threshold:
            # Collapse into a single row on the "new" item, using the deleted item's old_text
            new_item.label = "reformulated"  # keep label for internal hint
            new_item.status = "rewritten"
            new_item.sim_score = best_sim
            # Debug print for successful reformulation
            try:
                print(
                    f"EMBED: rewritten old_idx={best_match.old_index} -> new_idx={new_item.new_index} "
                    f"sim={best_sim:.3f} doc={document_id} ver={version_id}"
                )
            except Exception:
                pass
            # Carry old content into the row so inline diff is meaningful
            new_item.old_text = best_match.old_text
            new_item.old_index = best_match.old_index
            # Prefer the old para_id to preserve comment continuity
            if hasattr(best_match, "para_id") and best_match.para_id:
                new_item.para_id = best_match.para_id
            # Remove the deleted item from the list so only one row remains
            try:
                diffs.remove(best_match)
            except ValueError:
                pass


