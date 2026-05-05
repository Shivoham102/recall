import json
import pathlib
from datetime import datetime, timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]

_CREDENTIALS_FILE = pathlib.Path(__file__).parent / "credentials.json"
_TOKEN_FILE = pathlib.Path(__file__).parent / "token.json"


def _load_client_secrets() -> tuple[str, str]:
    """Return (client_id, client_secret) from credentials.json."""
    with open(_CREDENTIALS_FILE) as f:
        data = json.load(f)
    inner = data.get("installed") or data.get("web")
    return inner["client_id"], inner["client_secret"]


def get_credentials_for_user(user_id: str) -> Credentials:
    """Read Google credentials from Supabase users table and refresh if needed."""
    from db import get_db

    res = (
        get_db()
        .table("users")
        .select("google_access_token, google_refresh_token, google_token_expiry")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise ValueError(f"No Google credentials found for user {user_id!r}")

    data = res.data
    expiry = None
    if data.get("google_token_expiry"):
        expiry = datetime.fromisoformat(
            data["google_token_expiry"].replace("Z", "+00:00")
        )

    client_id, client_secret = _load_client_secrets()

    creds = Credentials(
        token=data["google_access_token"],
        refresh_token=data.get("google_refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
        expiry=expiry,
    )

    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        new_expiry = None
        if creds.expiry:
            e = creds.expiry if creds.expiry.tzinfo else creds.expiry.replace(tzinfo=timezone.utc)
            new_expiry = e.isoformat()
        get_db().table("users").update(
            {
                "google_access_token": creds.token,
                "google_token_expiry": new_expiry,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", user_id).execute()

    return creds


def get_credentials() -> Credentials:
    """Legacy fallback: read from token.json on disk (used only if no user_id in context)."""
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
