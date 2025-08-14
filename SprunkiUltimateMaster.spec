# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

# --- PHẦN QUAN TRỌNG NHẤT: Analysis ---
a = Analysis(['launch.py'], # <-- GIỮ NGUYÊN: Đúng là file khởi động của bạn.
             pathex=['I:\\VS-Project\\Sprunki'],
             binaries=[],
             datas=[
                 ('config.json', '.'),
                 ('image_assets', 'image_assets'),
                 ('extensions', 'extensions')
             ],
             hiddenimports=[
                 'win32com.client',
                 'psutil',
                 'skimage.metrics',
                 'skimage.metrics._structural_similarity' 
             ],
             hookspath=[],
             hooksconfig={},
             runtime_hooks=['runtime_hook.py'],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)

pyz = PYZ(a.pure, a.zipped_data,
             cipher=block_cipher)

exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          [],
          name='SprunkiUltimateMaster',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          upx_exclude=[],
          runtime_tmpdir=None,
          console=False,
          disable_windowed_traceback=False,
          target_arch=None,
          codesign_identity=None,
          entitlements_file=None,

          icon=None # <-- THAY ĐỔI (TÙY CHỌN): Nếu bạn có file icon, hãy thay None bằng 'path/to/your/icon.ico'
                    # Ví dụ: icon='I:\\VS-Project\\Sprunki\\my_icon.ico'
                    # Nếu không có, cứ để là None.
          )