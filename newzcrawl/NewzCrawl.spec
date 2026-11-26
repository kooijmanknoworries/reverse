# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the NewzCrawl Windows 11 build (portable exe and
# installer payload).
#
# Run on a Windows machine (the bundled 7z/par2 tools are Windows
# binaries):
#     pyinstaller NewzCrawl.spec
#
# Produces a self-contained ``NewzCrawl.exe``. All Python code and the
# web UI are frozen in; ``tools/`` (7z, par2, Unpack) sits next to the
# executable. The dashboard is shown in a native window (pywebview /
# Edge WebView2), so no browser is required to use the app.

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
    hiddenimports=(collect_submodules('newzcrawl')
                  # native desktop window (imported lazily at runtime);
                  # the hook-webview.py hook ships the WebView2 loader +
                  # interop assemblies, and the clr/clr_loader hooks ship
                  # the pythonnet .NET bridge used by the WinForms backend
                  + collect_submodules('webview')
                  + ['pythonnet', 'clr', 'clr_loader']),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas', 'PyQt5', 'PySide2', 'PySide6',
              'PyQt6', 'tkinter'],
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
    icon='assets/icon.ico',  # embedded so shortcuts/uninstaller inherit it
)
