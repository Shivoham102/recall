import os
import fnmatch
import httpx
from fastapi import APIRouter
from fastapi.responses import Response, JSONResponse

router = APIRouter()

REPO = "Shivoham102/recall"
GITHUB_API = "https://api.github.com"

# Asset patterns per (target, arch) — installer and updater artifacts differ on macOS
_ASSET_PATTERNS = {
    ("windows", "x86_64"): {
        "installer": "Recall_*_x64-setup.exe",
        "sig": "Recall_*_x64-setup.exe.sig",
    },
    ("darwin", "aarch64"): {
        "installer": "Recall_*.app.tar.gz",
        "sig": "Recall_*.app.tar.gz.sig",
    },
    ("darwin", "x86_64"): {
        "installer": "Recall_*.app.tar.gz",
        "sig": "Recall_*.app.tar.gz.sig",
    },
    ("linux", "x86_64"): {
        "installer": "Recall_*.AppImage",
        "sig": "Recall_*.AppImage.sig",
    },
}


def _parse_version(tag: str) -> tuple[int, ...]:
    v = tag.lstrip("v")
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def _github_headers() -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "recall-updater/1.0",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


@router.get("/updates/{target}/{arch}/{current_version}")
async def check_update(target: str, arch: str, current_version: str):
    patterns = _ASSET_PATTERNS.get((target, arch))
    if patterns is None:
        return Response(status_code=204)

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{GITHUB_API}/repos/{REPO}/releases/latest",
            headers=_github_headers(),
        )
        if resp.status_code != 200:
            return Response(status_code=204)

        release = resp.json()

    latest_tag: str = release.get("tag_name", "")
    latest_version = _parse_version(latest_tag)
    current_parsed = _parse_version(current_version)

    if current_parsed >= latest_version:
        return Response(status_code=204)

    assets: list[dict] = release.get("assets", [])
    asset_map = {a["name"]: a["browser_download_url"] for a in assets}

    installer_url = next(
        (url for name, url in asset_map.items() if fnmatch.fnmatch(name, patterns["installer"])),
        None,
    )
    sig_url = next(
        (url for name, url in asset_map.items() if fnmatch.fnmatch(name, patterns["sig"])),
        None,
    )

    if not installer_url or not sig_url:
        return Response(status_code=204)

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        sig_resp = await client.get(sig_url, headers=_github_headers())
        if sig_resp.status_code != 200:
            return Response(status_code=204)
        signature = sig_resp.text.strip()

    platform_key = f"{target}-{arch}"
    body = {
        "version": latest_tag.lstrip("v"),
        "notes": release.get("body") or "",
        "pub_date": release.get("published_at") or "",
        "platforms": {
            platform_key: {
                "url": installer_url,
                "signature": signature,
            }
        },
    }

    return JSONResponse(
        content=body,
        headers={"Cache-Control": "s-maxage=300"},
    )
