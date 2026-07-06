"""Symmetric encryption for credentials persisted at rest (Fernet).

Secure by default: when ``ENCRYPTION_KEY`` is unset, ``is_encryption_available()`` is False and
callers must decline to persist the secret (re-enter per session) rather than store plaintext.

A valid Fernet key is *derived* from whatever ``ENCRYPTION_KEY`` string is configured
(``base64(sha256(key))``), so operators can set any passphrase without generating a Fernet key by
hand. For production this value belongs in a KMS/secret manager, not a committed ``.env``.
"""

import base64
import hashlib

from cryptography.fernet import Fernet

from src.app.core.common.config import settings


def is_encryption_available() -> bool:
    """True when an encryption key is configured, so secrets may be persisted at rest."""
    return bool(settings.ENCRYPTION_KEY)


def _fernet() -> Fernet:
    """Build the Fernet cipher from a key derived from ``ENCRYPTION_KEY``.

    Not cached, so a key change/rotation takes effect immediately.
    """
    if not settings.ENCRYPTION_KEY:
        raise RuntimeError("ENCRYPTION_KEY is not configured")
    derived = base64.urlsafe_b64encode(hashlib.sha256(settings.ENCRYPTION_KEY.encode()).digest())
    return Fernet(derived)


def encrypt(plaintext: str) -> str:
    """Encrypt a string to an at-rest token. Raises if encryption is unavailable."""
    if not is_encryption_available():
        raise RuntimeError("encryption unavailable: ENCRYPTION_KEY not configured")
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt an at-rest token back to plaintext. Raises if encryption is unavailable."""
    if not is_encryption_available():
        raise RuntimeError("encryption unavailable: ENCRYPTION_KEY not configured")
    return _fernet().decrypt(token.encode()).decode()
