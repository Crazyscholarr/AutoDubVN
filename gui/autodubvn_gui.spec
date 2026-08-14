# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec cho app desktop AutoDubVN.
#
# App .exe này là LỚP VỎ GIAO DIỆN gọn nhẹ: nó mở cửa sổ desktop và điều khiển
# pipeline; phần lồng tiếng nặng (torch/funasr/edge-tts…) vẫn chạy bằng Python
# trong venv của project (xem gui/app.py -> _pipeline_python). Nhờ vậy .exe nhỏ,
# build nhanh, và không phải nhồi vài GB thư viện ML + model vào một file.
#
# Build (chạy TRONG venv, ở thư mục gốc AutoDubVN):
#     venv\Scripts\pyinstaller gui\autodubvn_gui.spec
# Kết quả: dist\AutoDubVN\AutoDubVN.exe  (đặt cả thư mục dist\AutoDubVN cạnh
# main.py, hoặc chép AutoDubVN.exe vào thư mục gốc project rồi chạy).

import os

block_cipher = None
GUI_DIR = os.path.abspath(os.path.join(SPECPATH))   # thư mục gui/

a = Analysis(
    [os.path.join(GUI_DIR, 'app.py')],
    pathex=[],
    binaries=[],
    datas=[(os.path.join(GUI_DIR, 'web'), 'web')],   # đóng gói kèm giao diện
    hiddenimports=['webview', 'yaml'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'funasr', 'faster_whisper', 'whisperx', 'edge_tts',
              'playwright', 'modelscope'],   # để pipeline dùng venv, KHÔNG nhồi vào exe
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='AutoDubVN',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # app cửa sổ, KHÔNG hiện console đen
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(GUI_DIR, 'icon.ico') if os.path.exists(os.path.join(GUI_DIR, 'icon.ico')) else None,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, upx_exclude=[], name='AutoDubVN',
)
