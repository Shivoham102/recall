import os
import pathlib
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]

_CREDENTIALS_FILE = pathlib.Path(__file__).parent / "credentials.json"
_TOKEN_FILE = pathlib.Path(__file__).parent / "token.json"


def get_credentials() -> Credentials:
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
    # Run this file once manually to complete the OAuth2 flow:
    #   python google_auth.py
    creds = get_credentials()
    print(f"Auth successful. Token saved to {_TOKEN_FILE}")
