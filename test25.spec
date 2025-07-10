# -*- mode: python ; coding: utf-8 -*-
# test25.spec

from PyInstaller.utils.hooks import collect_data_files

# Collect model data for faster-whisper
faster_whisper_data = collect_data_files("faster_whisper")

a = Analysis(
    ['test25.py'],
    pathex=[],
    binaries=[],
    datas=faster_whisper_data,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='test25',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Change to False if you want to hide the terminal
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='test25'
)
