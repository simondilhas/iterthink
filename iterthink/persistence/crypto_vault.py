"""Encrypt API key bundles with a user passphrase (PBKDF2 + Fernet)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_VAULT_LABEL = b"iterthink-credential-vault-v1"
_PBKDF2_ITERATIONS = 480_000
_SALT_LEN = 16


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    raw = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def _make_verifier(key_material_b64: bytes) -> bytes:
    return hmac.new(key_material_b64, _VAULT_LABEL, hashlib.sha256).digest()


def new_salt() -> bytes:
    return secrets.token_bytes(_SALT_LEN)


def encrypt_secrets_dict(passphrase: str, salt: bytes, data: dict[str, Any]) -> tuple[bytes, bytes]:
    """
    Returns (ciphertext, verifier).
    ``salt`` should be ``new_salt()`` on first save; reuse stored salt when re-encrypting.
    """
    key_b64 = _derive_key(passphrase, salt)
    verifier = _make_verifier(key_b64)
    f = Fernet(key_b64)
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    ciphertext = f.encrypt(payload)
    return ciphertext, verifier


def decrypt_secrets_dict(passphrase: str, salt: bytes, ciphertext: bytes, verifier: bytes) -> dict[str, Any]:
    key_b64 = _derive_key(passphrase, salt)
    if not hmac.compare_digest(_make_verifier(key_b64), verifier):
        raise ValueError("Incorrect encryption passphrase.")
    f = Fernet(key_b64)
    try:
        raw = f.decrypt(ciphertext)
    except InvalidToken as exc:
        raise ValueError("Could not decrypt credentials (wrong passphrase or corrupt data).") from exc
    obj = json.loads(raw.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("Invalid credential payload.")
    return {str(k): v for k, v in obj.items()}
