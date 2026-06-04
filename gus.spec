# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for GUS.

Build a single-file executable:
    pyinstaller gus.spec

Output: dist/gus  (dist/gus.exe on Windows)
"""

a = Analysis(
    ["src/main.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=[
        # ddgs internals that PyInstaller's static analysis may miss
        "ddgs",
        "ddgs._ddgs_text",
        "ddgs._ddgs_images",
        # certifi / ssl
        "certifi",
        # prompt_toolkit optional backends
        "prompt_toolkit.output.vt100",
        "prompt_toolkit.output.win32",
        "prompt_toolkit.input.win32",
        "prompt_toolkit.input.vt100",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="gus",
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
)
