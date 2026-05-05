# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Recall FastAPI backend sidecar.
# Build: cd backend && pyinstaller recall-backend.spec
# Output: backend/dist/recall-backend[.exe]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('.env', '.'),
        ('credentials.json', '.'),
    ],
    hiddenimports=[
        # uvicorn internals not auto-detected
        'uvicorn.lifespan.on',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.wsproto_impl',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.logging',
        # faster-whisper / ctranslate2 backends
        'faster_whisper',
        'ctranslate2',
        # google api discovery
        'googleapiclient.discovery',
        'googleapiclient._helpers',
        'google.auth.transport.requests',
        'google.oauth2.credentials',
        'google_auth_oauthlib.flow',
        # other
        'httpx',
        'httpcore',
        'anyio',
        'anyio._backends._asyncio',
        'anyio._backends._trio',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='recall-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=True,
)
