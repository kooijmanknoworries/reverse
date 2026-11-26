# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the NewzCrawl Windows 11 portable build.
#
# Run on a Windows machine (the bundled 7z/par2 tools are Windows
# binaries):
#     pyinstaller NewzCrawl.spec
#
# Produces a self-contained ``NewzCrawl.exe``. All Python code and the
# web UI are frozen in; ``tools/7z`` and ``tools/par2`` are expected to
# sit next to the executable (or be passed via Settings).

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

a = Analysis(
    ['newzcrawl/app.py'],
    pathex=[],
    binaries=[],
    datas=[
        # ship the single-page web UI so it can be served from the
        # frozen package
        ('newzcrawl/web/static', 'newzcrawl/web/static'),
    ],
    hiddenimports=collect_submodules('newzcrawl'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
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
    name='NewzCrawl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # GUI app: no console window on Windows
    icon=None,              # set to 'assets/icon.ico' when available
)
