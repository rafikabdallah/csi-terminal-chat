"""Password hashing and verification.

Uses PBKDF2-HMAC-SHA256 with a per-user random salt.
No plaintext password is ever stored or logged.
"""

import os
import hashlib
import hmac

ITERATIONS = 200_000
SALT_BYTES = 16


def hash_password(password):
    """Return (salt_hex, hash_hex) for a new password."""
    salt = os.urandom(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return salt.hex(), dk.hex()


def verify_password(password, salt_hex, expected_hash_hex):
    """Check a password against a stored salt and hash."""
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False

    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return hmac.compare_digest(dk.hex(), expected_hash_hex)
