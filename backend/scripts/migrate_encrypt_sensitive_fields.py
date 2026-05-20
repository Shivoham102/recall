import argparse
import pathlib
import sys

from dotenv import load_dotenv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
load_dotenv(pathlib.Path(__file__).resolve().parents[2] / ".env")

from db import get_admin_db  # noqa: E402
from security.crypto import decrypt_from_storage, encrypt_for_storage, is_encrypted  # noqa: E402


def _encrypt_users(write: bool, verify: bool) -> tuple[int, int]:
    db = get_admin_db()
    res = db.table("users").select("id, google_access_token, google_refresh_token").execute()
    rows = res.data or []
    changed = 0
    checked = 0

    for row in rows:
        checked += 1
        update: dict[str, str] = {}
        for field in ("google_access_token", "google_refresh_token"):
            value = row.get(field)
            if not value:
                continue
            if verify:
                decrypt_from_storage(value)
            if not is_encrypted(value):
                update[field] = encrypt_for_storage(value)

        if update:
            changed += 1
            if write:
                db.table("users").update(update).eq("id", row["id"]).execute()

    return checked, changed


def _encrypt_style_profiles(write: bool, verify: bool) -> tuple[int, int]:
    db = get_admin_db()
    res = db.table("email_style_profiles").select("user_id, samples_preview").execute()
    rows = res.data or []
    changed = 0
    checked = 0

    for row in rows:
        checked += 1
        value = row.get("samples_preview")
        if not value:
            continue
        if verify:
            decrypt_from_storage(value)
        if not is_encrypted(value):
            changed += 1
            if write:
                db.table("email_style_profiles").update(
                    {"samples_preview": encrypt_for_storage(value)}
                ).eq("user_id", row["user_id"]).execute()

    return checked, changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Encrypt existing token/style-profile fields.")
    parser.add_argument("--write", action="store_true", help="Write updates. Default is dry run.")
    parser.add_argument("--verify", action="store_true", help="Verify existing encrypted rows decrypt.")
    args = parser.parse_args()

    user_checked, user_changed = _encrypt_users(args.write, args.verify)
    style_checked, style_changed = _encrypt_style_profiles(args.write, args.verify)
    mode = "write" if args.write else "dry-run"
    print(
        f"{mode}: users checked={user_checked} rows_to_encrypt={user_changed}; "
        f"style_profiles checked={style_checked} rows_to_encrypt={style_changed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
