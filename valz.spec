# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the valz Windows desktop build.

Build with:
    pyinstaller valz.spec --noconfirm --clean

Outputs ``dist/valz/valz.exe`` (a console-less windowed launcher) plus the
full Python runtime as siblings, ready to be zipped for distribution.

Bundled payload (valz.db, config.yaml) lives in ``desktop/payload/``; the
launcher copies it into the user's ``%LOCALAPPDATA%\\valz\\data`` on first
run. We mark ``data/`` as runtime data via ``datas=`` so PyInstaller
treats it as a non-code resource placed next to the exe.
"""
import os
from pathlib import Path

block_cipher = None

# project root
ROOT = Path(os.path.abspath(SPECPATH))  # SPECPATH injected by PyInstaller
PAYLOAD = ROOT / "desktop" / "payload"

# runtime data we want copied next to the binary (NOT into _internal)
# - payload/* is the first-run snapshot copied into %LOCALAPPDATA%\valz
# - valz/syaria/des_snapshot.json is the on-disk DES fallback loaded
#   by app._load_syaria_default; without it every syaria field is null
# - config.example.yaml + schema.sql are read at runtime by config.py /
#   db.py via __file__-relative paths, so they must sit next to those
#   modules in _internal/
datas = []
if PAYLOAD.is_dir():
    for f in PAYLOAD.iterdir():
        if f.is_file():
            datas.append((str(f), "payload"))
for rel in ("config.example.yaml", "schema.sql", "static"):
    p = ROOT / rel
    if p.exists():
        if p.is_dir():
            datas.append((str(p) + os.sep, "static"))
        else:
            datas.append((str(p), "."))
# DES snapshot: keep the same relative path so _load_syaria_default's
# first candidate resolves inside the bundle.
syaria = ROOT / "valz" / "syaria" / "des_snapshot.json"
if syaria.exists():
    datas.append((str(syaria), "valz/syaria"))

# uvicorn needs a few submodules to be present even when not statically
# referenced; these hidden imports are the canonical list for our config.
hiddenimports = [
    # valz app modules -- uvicorn loads them by string ("app:app",
    # "desktop:make_app") so PyInstaller's static analysis doesn't see
    # the references and skips them without these explicit hints.
    "app", "config", "db", "compute", "zstats", "refresher", "prices",
    "desktop",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.config",
    "uvicorn.server",
    "uvicorn.main",
    "anyio",
    "anyio._backends",
    "anyio._backends._asyncio",
    "h11",
    "yaml",
    "sqlite3",
]


a = Analysis(
    [str(ROOT / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim the obvious bloat; nothing here is referenced by the API
        # path we ship.
        "tkinter", "matplotlib", "numpy", "pandas", "scipy",
        "pytest", "hypothesis",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="valz",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                    # upx is rarely present and trips AV
    console=False,                # GUI launcher; no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                    # no icon yet; can add one for polish
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="valz",
)
