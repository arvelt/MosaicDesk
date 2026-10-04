# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import copy_metadata
from pathlib import Path

project=Path(SPECPATH)

datas = []
datas += collect_data_files('imgutils')
datas += copy_metadata('dghs-imgutils')
datas += copy_metadata('hfutils')
datas += copy_metadata('hbutils')
datas += collect_data_files('tkinterdnd2')
datas += [(str(project/'cache'/'huggingface'/'hub'),'cache/huggingface/hub')]
datas += [(str(project/'LICENSE'),'.'),(str(project/'THIRD_PARTY_NOTICES.txt'),'.')]
datas += [(str(project/'assets'/'mosaicdesk.ico'),'assets')]


a = Analysis(
    ['batch.py'],
    pathex=[],
    binaries=[],
    datas=datas,
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
    name='MosaicDesk',
    icon=str(project/'assets'/'mosaicdesk.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
