import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

_client: Client | None = None
_admin_client: Client | None = None


def get_db() -> Client:
    global _client
    if _client is None:
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_ANON_KEY"]
        _client = create_client(
            os.environ["SUPABASE_URL"],
            key,
        )
    return _client


def get_admin_db() -> Client:
    global _admin_client
    if _admin_client is None:
        _admin_client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", os.environ["SUPABASE_ANON_KEY"]),
        )
    return _admin_client
