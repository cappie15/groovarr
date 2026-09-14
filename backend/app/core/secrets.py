"""At-rest encryption for credentials stored in the database (Spotify refresh
token today; Jellyfin API key / Plex token from Phase 8 onward).

The encryption key is derived from `GROOVARR_SECRET_KEY` (a single operator-
supplied value, typically injected as a Docker env var). Per §9/§71 of the
architecture doc, this is deliberately *not* presented as equivalent to an
external secrets manager: the key lives on the same host as the encrypted
data. It only raises the bar above "plaintext in SQLite", which is still a
meaningful improvement and the honest, practical option for a single-user
self-hosted app.
"""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _derive_fernet_key(secret: str) -> bytes:
    """Fernet requires a 32-byte urlsafe-base64 key; derive one deterministically
    from the operator-supplied secret so the same GROOVARR_SECRET_KEY always
    decrypts what it previously encrypted.
    """
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache
def _fernet() -> Fernet:
    return Fernet(_derive_fernet_key(get_settings().groovarr_secret_key))


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret for storage. Returns an opaque, ASCII-safe string."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a value previously produced by `encrypt_secret`.

    Raises `ValueError` (not the cryptography-specific exception) on a bad
    token, e.g. GROOVARR_SECRET_KEY changed since the value was encrypted —
    callers should treat this the same as "credential missing/invalid",
    surfacing a re-authorization/reconfiguration prompt rather than crashing.
    """
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Could not decrypt stored secret — GROOVARR_SECRET_KEY may have changed") from exc
