import base64
import hashlib
import hmac
import secrets


ACCESS_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def generate_access_code() -> str:
    raw = "".join(secrets.choice(ACCESS_CODE_ALPHABET) for _ in range(12))
    return "-".join(raw[index : index + 4] for index in range(0, 12, 4))


def normalize_access_code(value: str) -> str | None:
    """Return the canonical presentation form without doing expensive hashing."""
    if not isinstance(value, str):
        return None
    compact = value.strip().upper().replace("-", "")
    if len(compact) != 12 or any(char not in ACCESS_CODE_ALPHABET for char in compact):
        return None
    return "-".join(compact[index : index + 4] for index in range(0, 12, 4))


def access_code_selector(code: str) -> str:
    return code[:4]


def hash_access_code(code: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        code.encode("ascii"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
    )
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_access_code(code: str, encoded_hash: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, expected_text = encoded_hash.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(expected_text)
        actual = hashlib.scrypt(
            code.encode("ascii"), salt=salt, n=int(n), r=int(r), p=int(p)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)
