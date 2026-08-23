"""Argon2 password hashing helpers for local production authentication."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_HASHER = PasswordHasher()
_DUMMY_HASH = _HASHER.hash("eurskem-dummy-password-never-used")


def hash_password(password: str) -> str:
    """Hash the password.

    Args:
        password (str): Password value.

    Returns:
        str: The password.
    """
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    if len(password) > 1_024:
        raise ValueError("Password is too long")
    return _HASHER.hash(password)


def verify_password(password: str, encoded: str | None) -> bool:
    """Verify the password.

    Args:
        password (str): Password value.
        encoded (str | None): The encoded.

    Returns:
        bool: The password.
    """
    candidate = encoded or _DUMMY_HASH
    try:
        return _HASHER.verify(candidate, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False
