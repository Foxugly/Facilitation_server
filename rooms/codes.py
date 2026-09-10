"""Room codes and participant tokens (data-model spec §5.1 / §5.2).

Room code: 6 chars, uppercase, ambiguous characters excluded (O/0, I/1/L).
Participant token: a long, non-guessable secret (spec P5) — never exposed to peers.
"""
import secrets

# Excludes O, 0, I, 1, L (scope §8: ambiguous characters excluded).
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 6
TOKEN_BYTES = 32


def generate_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def generate_unique_code(exists):
    """Return a code not already taken. ``exists(code) -> bool`` checks the DB."""
    for _ in range(20):
        code = generate_code()
        if not exists(code):
            return code
    raise RuntimeError("Could not allocate a unique room code")


def generate_token():
    return secrets.token_urlsafe(TOKEN_BYTES)


def normalize_code(raw):
    """Room codes are case-insensitive (scope §8): normalize input to UPPER."""
    return (raw or "").strip().upper()
