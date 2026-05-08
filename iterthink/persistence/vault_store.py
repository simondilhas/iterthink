"""Persist encrypted API credentials (singleton row id=1)."""

from __future__ import annotations

from iterthink.db.models import CredentialVault
from iterthink.db.session import session_scope

VAULT_ROW_ID = 1


def vault_exists() -> bool:
    with session_scope() as sess:
        return sess.get(CredentialVault, VAULT_ROW_ID) is not None


def vault_read() -> tuple[bytes, bytes, bytes] | None:
    """Return (salt, ciphertext, verifier) or None if no vault row."""
    with session_scope() as sess:
        row = sess.get(CredentialVault, VAULT_ROW_ID)
        if row is None:
            return None
        return (row.kdf_salt, row.ciphertext, row.verifier)


def vault_write(*, kdf_salt: bytes, ciphertext: bytes, verifier: bytes) -> None:
    with session_scope() as sess:
        row = sess.get(CredentialVault, VAULT_ROW_ID)
        if row is None:
            sess.add(
                CredentialVault(
                    id=VAULT_ROW_ID,
                    kdf_salt=kdf_salt,
                    ciphertext=ciphertext,
                    verifier=verifier,
                )
            )
        else:
            row.kdf_salt = kdf_salt
            row.ciphertext = ciphertext
            row.verifier = verifier
