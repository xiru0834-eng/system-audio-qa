# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

import tkinterdnd2


tkdnd_folder = Path(tkinterdnd2.__file__).parent / 'tkdnd' / 'win-x64'
tkdnd_files = [str(path) for path in tkdnd_folder.iterdir() if path.is_file()]
tkdnd_binary = [path for path in tkdnd_files if path.lower().endswith('.dll')]
tkdnd_data = [path for path in tkdnd_files if not path.lower().endswith('.dll')]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[(path, 'tkinterdnd2/tkdnd/win-x64') for path in tkdnd_binary],
    datas=[('PILOT_CASES.md', '.')]
          + [(path, 'tkinterdnd2/tkdnd/win-x64') for path in tkdnd_data],
    hiddenimports=[],
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
    name='SystemAudioQA',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
