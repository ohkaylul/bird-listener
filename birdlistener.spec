# PyInstaller spec for Bird Listener.
# Build with: venv\Scripts\pyinstaller birdlistener.spec --noconfirm

import birdnetlib
import os

from PyInstaller.utils.hooks import collect_all

block_cipher = None

birdnetlib_dir = os.path.dirname(birdnetlib.__file__)
models_dir = os.path.join(birdnetlib_dir, "models")

datas = [(models_dir, "birdnetlib/models")]
binaries = []
hiddenimports = [
    "plyer.platforms.win.notification",
    "pystray._win32",
]

# Pull in the full tensorflow / librosa trees, since PyInstaller's static
# analysis misses their dynamically-loaded submodules.
for pkg in ["tensorflow", "librosa", "numba"]:
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="BirdListener",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
