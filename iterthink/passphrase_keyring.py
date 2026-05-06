"""Store the credential-vault passphrase in the OS keyring (cross-platform)."""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError

# Stable identifiers for Secret Service / Windows Credential Manager / macOS Keychain.
KEYRING_SERVICE = "iterthink"
KEYRING_USERNAME = "credential_vault_passphrase"


def _looks_like_missing_password(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name == "PasswordDeleteError":
        return True
    s = str(exc).lower()
    return "not found" in s or "no password" in s or "could not be found" in s


def get_stored_passphrase() -> str | None:
    """Return the stored passphrase, or None if missing or keyring unavailable."""
    try:
        raw = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except Exception:  # noqa: BLE001
        return None
    if raw is None:
        return None
    s = raw.strip()
    return s or None


def set_stored_passphrase(phrase: str) -> tuple[bool, str]:
    p = (phrase or "").strip()
    if not p:
        return False, "Passphrase is empty."
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, p)
    except Exception as exc:  # noqa: BLE001
        return False, f"Keyring: {exc}"
    return True, "Passphrase saved to system keyring."


def delete_stored_passphrase() -> tuple[bool, str]:
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except KeyringError as exc:
        if _looks_like_missing_password(exc):
            return True, "No passphrase was stored in the keyring."
        return False, f"Keyring: {exc}"
    except Exception as exc:  # noqa: BLE001
        if _looks_like_missing_password(exc):
            return True, "No passphrase was stored in the keyring."
        return False, f"Keyring: {exc}"
    return True, "Passphrase removed from system keyring."
