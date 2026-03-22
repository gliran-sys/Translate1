# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Translate1.
#
# Build command (from the repo root):
#   pip install pyinstaller
#   pyinstaller Translate1.spec
#
# Output: dist/Translate1.exe  (single file, no console window)

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Bundle version.py so updater.py can import it at runtime
        ('version.py', '.'),
    ],
    hiddenimports=[
        # pynput back-ends are detected at runtime — declare them explicitly
        'pynput.keyboard._win32',
        'pynput.mouse._win32',
        # pystray Windows back-end
        'pystray._win32',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='Translate1',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,       # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Optional: embed an icon
    # icon='icon.ico',
)
