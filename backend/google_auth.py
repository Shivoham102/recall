import json
import os
import pathlib
from datetime import datetime, timezone
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from security.crypto import decrypt_from_storage, encrypt_for_storage


class GoogleReauthRequired(Exception):
    """Raised when a user's Google connection needs a fresh sign-in: no stored
    credentials, refresh token missing, or the refresh token was rejected by Google
    (revoked, expired, or invalidated by the 50-token-per-client cap)."""

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

_CREDENTIALS_FILE = pathlib.Path(__file__).parent / "credentials.json"
_TOKEN_FILE = pathlib.Path(__file__).parent / "token.json"


def _load_client_secrets() -> tuple[str, str]:
    """Return (client_id, client_secret) from env vars or credentials.json."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if client_id and client_secret:
        return client_id, client_secret
    if not _legacy_file_oauth_enabled():
        raise RuntimeError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required. "
            "Set ALLOW_LEGACY_GOOGLE_FILE_OAUTH=true to read credentials.json in local development."
        )
    with open(_CREDENTIALS_FILE) as f:
        data = json.load(f)
    inner = data.get("installed") or data.get("web")
    return inner["client_id"], inner["client_secret"]


def _legacy_file_oauth_enabled() -> bool:
    return os.environ.get("ALLOW_LEGACY_GOOGLE_FILE_OAUTH", "").lower() in {"1", "true", "yes"}


def _insert_reauth_alert(user_id: str) -> None:
    """One-time notification-center entry, delivered via the same proactive_jobs
    pipeline as any other job (Realtime INSERT + undelivered-backlog fallback)."""
    from db import get_admin_db

    get_admin_db().table("proactive_jobs").insert({
        "user_id": user_id,
        "job_type": "google_reauth_alert",
        "status": "done",
        "delivered": False,
        "result": {
            "text": "Reconnect Google in Profile to keep Recall fully functional.",
            "email_cards": [],
            "calendar_cards": [],
            "task_cards": [],
            "metadata": {},
        },
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


def get_credentials_for_user(user_id: str) -> Credentials:
    """Read Google credentials from Supabase users table and refresh if needed."""
    from db import get_admin_db

    db = get_admin_db()
    res = (
        db.table("users")
        .select("google_access_token, google_refresh_token, google_token_expiry, google_reauth_required")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise GoogleReauthRequired(f"No Google credentials found for user {user_id!r}")

    data = res.data
    if data.get("google_reauth_required"):
        # Already known broken — fail fast, no network call to Google.
        raise GoogleReauthRequired(f"Google reconnect required for user {user_id!r}")

    access_token = decrypt_from_storage(data.get("google_access_token"))
    refresh_token = decrypt_from_storage(data.get("google_refresh_token"))
    if not refresh_token:
        raise GoogleReauthRequired(f"Google reconnect required for user {user_id!r}")

    expiry = None
    if data.get("google_token_expiry"):
        expiry = datetime.fromisoformat(
            data["google_token_expiry"].replace("Z", "+00:00")
        ).replace(tzinfo=None)  # google-auth compares with naive datetime.utcnow()

    client_id, client_secret = _load_client_secrets()

    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
        expiry=expiry,
    )

    if not creds.valid and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            # Terminal: refresh token revoked/expired. Flip the flag with an atomic
            # conditional update so concurrent jobs racing this same user only insert
            # one alert, then mark every caller's attempt as needing reconnect.
            upd = (
                db.table("users")
                .update({
                    "google_reauth_required": True,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
                .eq("id", user_id)
                .eq("google_reauth_required", False)
                .execute()
            )
            if upd.data:
                _insert_reauth_alert(user_id)
            raise GoogleReauthRequired(f"Google reconnect required for user {user_id!r}") from None

        new_expiry = None
        if creds.expiry:
            e = creds.expiry if creds.expiry.tzinfo else creds.expiry.replace(tzinfo=timezone.utc)
            new_expiry = e.isoformat()
        db.table("users").update(
            {
                "google_access_token": encrypt_for_storage(creds.token),
                "google_token_expiry": new_expiry,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", user_id).execute()

    return creds


def get_credentials() -> Credentials:
    """Legacy fallback: read from token.json on disk (used only if no user_id in context)."""
    if not _legacy_file_oauth_enabled():
        raise RuntimeError(
            "Legacy Google file OAuth is disabled. Set ALLOW_LEGACY_GOOGLE_FILE_OAUTH=true for local development."
        )

    creds: Credentials | None = None

    if _TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not _CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    "Google credentials.json not found. Download it from "
                    "Google Cloud Console and place it in the backend/ directory."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(_CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        _TOKEN_FILE.write_text(creds.to_json())

    return creds


if __name__ == "__main__":
    # Run once manually to complete the OAuth2 flow (legacy path):
    #   python google_auth.py
    creds = get_credentials()
    print(f"Auth successful. Token saved to {_TOKEN_FILE}")
