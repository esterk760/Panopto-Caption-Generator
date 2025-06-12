# -*- mode: python ; coding: utf-8 -*-
# test5.spec

from PyInstaller.utils.hooks import collect_data_files

# Collect model data for spaCy and faster-whisper
spacy_model_data = collect_data_files("en_core_web_sm")
faster_whisper_data = collect_data_files("faster_whisper")

a = Analysis(
    ['test5.py'],
    pathex=[],
    binaries=[],
    datas=spacy_model_data + faster_whisper_data,
    hiddenimports=['en_core_web_sm'],
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
    name='test5',
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
    name='test5'
)
