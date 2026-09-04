import base64
import binascii
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from app.auth.errors import AuthenticationError


PASSWORD_SCRYPT_N = 2**14
PASSWORD_SCRYPT_R = 8
PASSWORD_SCRYPT_P = 1
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "alera-backend"


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password must not be empty.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=PASSWORD_SCRYPT_N,
        r=PASSWORD_SCRYPT_R,
        p=PASSWORD_SCRYPT_P,
    )
    return "$".join(
        (
            "scrypt",
            str(PASSWORD_SCRYPT_N),
            str(PASSWORD_SCRYPT_R),
            str(PASSWORD_SCRYPT_P),
            _encode_base64url(salt),
            _encode_base64url(digest),
        )
    )


def verify_password(password: str, encoded_hash: str | None) -> bool:
    if not encoded_hash:
        return False
    try:
        algorithm, n, r, p, salt_text, expected_text = encoded_hash.split("$")
        if algorithm != "scrypt":
            return False
        expected = _decode_base64url(expected_text)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_decode_base64url(salt_text),
            n=int(n),
            r=int(r),
            p=int(p),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def create_access_token(
    *,
    user_id: UUID,
    household_id: UUID,
    secret: str,
    expires_minutes: int,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    if not secret:
        raise ValueError("ALERA_JWT_SECRET must be configured.")
    if expires_minutes <= 0:
        raise ValueError("ALERA_JWT_ACCESS_TOKEN_MINUTES must be positive.")
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=expires_minutes)
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": str(user_id),
        "household_id": str(household_id),
        "iss": JWT_ISSUER,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    parts = [
        _encode_base64url(
            json.dumps(item, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        for item in (header, payload)
    ]
    signing_input = ".".join(parts).encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{'.'.join(parts)}.{_encode_base64url(signature)}", expires_at


def decode_access_token(
    token: str,
    *,
    secret: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not secret:
        raise AuthenticationError("Bearer authentication is not configured.")
    try:
        header_text, payload_text, signature_text = token.split(".")
        header = json.loads(_decode_base64url(header_text))
        payload = json.loads(_decode_base64url(payload_text))
        signature = _decode_base64url(signature_text)
    except (
        ValueError,
        TypeError,
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise AuthenticationError("Invalid bearer token.") from exc
    if header != {"alg": JWT_ALGORITHM, "typ": "JWT"}:
        raise AuthenticationError("Invalid bearer token.")
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{header_text}.{payload_text}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected):
        raise AuthenticationError("Invalid bearer token.")
    current_time = now or datetime.now(timezone.utc)
    try:
        if payload["iss"] != JWT_ISSUER:
            raise AuthenticationError("Invalid bearer token.")
        if int(payload["exp"]) <= int(current_time.timestamp()):
            raise AuthenticationError("Bearer token has expired.")
        UUID(payload["sub"])
        UUID(payload["household_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthenticationError("Invalid bearer token.") from exc
    return payload
