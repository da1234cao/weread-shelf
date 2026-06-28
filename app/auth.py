"""Password hashing (pbkdf2) and signed-cookie sessions — stdlib only.

A session cookie holds ``base64(username:hmac)``; the HMAC is keyed by the
per-install session secret in ``app_settings``. There is no server-side session
store — the cookie itself is the credential, verified on each request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from fastapi import Request

from . import repository as repo
from . import settings_store
from .db import session_scope
from .models import AppUser

_ROUNDS = 200_000
COOKIE = "wrs_session"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ROUNDS)
    return f"pbkdf2_sha256${_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, rounds, salt_hex, hash_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


def _secret() -> bytes:
    return settings_store.get().session_secret.encode()


def make_token(username: str) -> str:
    sig = hmac.new(_secret(), username.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{username}:{sig}".encode()).decode()


def read_token(token: str) -> str | None:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    username, sep, sig = raw.rpartition(":")
    if not sep or not username:
        return None
    expected = hmac.new(_secret(), username.encode(), hashlib.sha256).hexdigest()
    return username if hmac.compare_digest(sig, expected) else None


def authenticate(username: str, password: str) -> AppUser | None:
    with session_scope() as session:
        user = repo.get_user(session, username)
        if user and verify_password(password, user.password_hash):
            return user
    return None


def current_user(request: Request) -> AppUser | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    username = read_token(token)
    if not username:
        return None
    with session_scope() as session:
        return repo.get_user(session, username)
