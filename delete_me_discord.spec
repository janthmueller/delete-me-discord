# PyInstaller spec for delete-me-discord
# Build with: pyinstaller delete_me_discord.spec

block_cipher = None

a = Analysis(
    ['pyinstaller_runner.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['delete_me_discord'],
    hookspath=[],
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
    [],
    exclude_binaries=True,
    name='delete-me-discord',
    debug=False,                  # no debug bootloader
    bootloader_ignore_signals=False,
    strip=False,                  # keep symbols (can set True to reduce size)
    upx=True,                     # compress with UPX if available
    console=True,                 # console app (shows terminal)
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='delete-me-discord',
)
