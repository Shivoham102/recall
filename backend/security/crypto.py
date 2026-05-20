import base64
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


_PREFIX = "enc:v1:"


def is_encrypted(value: str | None) -> bool:
    return bool(value and value.startswith(_PREFIX))


def _is_production() -> bool:
    env = (os.environ.get("ENVIRONMENT") or os.environ.get("VERCEL_ENV") or "").lower()
    return env in {"production", "prod"}


@lru_cache
def _active_key() -> bytes | None:
    raw = os.environ.get("DATA_ENCRYPTION_KEY_ACTIVE", "").strip()
    if not raw:
        if _is_production():
            raise RuntimeError("DATA_ENCRYPTION_KEY_ACTIVE is required in production")
        return None
    return raw.encode("utf-8")


@lru_cache
def _previous_keys() -> tuple[bytes, ...]:
    raw = os.environ.get("DATA_ENCRYPTION_KEY_PREVIOUS", "").strip()
    if not raw:
        return ()
    return tuple(k.strip().encode("utf-8") for k in raw.split(",") if k.strip())


def generate_key() -> str:
    """Helper for operators: print this in a REPL to create a new env key."""
    return Fernet.generate_key().decode("utf-8")


def encrypt_for_storage(value: str | None) -> str | None:
    if value is None or value == "":
        return value
    if is_encrypted(value):
        return value
    key = _active_key()
    if key is None:
        raise RuntimeError("DATA_ENCRYPTION_KEY_ACTIVE is required to encrypt sensitive data")
    token = Fernet(key).encrypt(value.encode("utf-8"))
    return _PREFIX + base64.urlsafe_b64encode(token).decode("utf-8")


def decrypt_from_storage(value: str | None) -> str | None:
    if value is None or value == "" or not is_encrypted(value):
        return value

    encoded = value[len(_PREFIX):]
    try:
        token = base64.urlsafe_b64decode(encoded.encode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid encrypted value encoding") from exc

    keys: tuple[bytes | None, ...] = (_active_key(), *_previous_keys())
    for key in keys:
        if key is None:
            continue
        try:
            return Fernet(key).decrypt(token).decode("utf-8")
        except InvalidToken:
            continue
    raise ValueError("Unable to decrypt value with configured keys")
