# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# 渠道子包目录（websocket/telegram 等）没有顶层被字节码分析引用，
# pkgutil 在 frozen 模式下无法发现它们，需显式加入 hiddenimports 与 datas。
_VENV_SP = os.path.join('.venv', 'lib', 'python3.11', 'site-packages')
_CHANNEL_DIR = os.path.join(_VENV_SP, 'nanobot', 'channels')
channel_datas = []
for _name in sorted(os.listdir(_CHANNEL_DIR)):
    _p = os.path.join(_CHANNEL_DIR, _name)
    if os.path.isdir(_p):
        _dst_dir = 'nanobot/channels/' + _name
        channel_datas.append((_p, _dst_dir))

hiddenimports = (
    collect_submodules('nanobot')
    + [
        'nanobot.channels.websocket',
        'nanobot.channels.websocket.runtime',
        'nanobot.channels.telegram',
        'nanobot.channels.telegram.runtime',
        'nanobot.channels.registry',
    ]
)
datas = collect_data_files('nanobot') + channel_datas

# mcp 包：nanobot mcp_oauth 工具依赖 mcp.client.auth 等子模块。
# 这些模块是 mcp 包的子包（不在 import 链上被 PyInstaller 自动发现），
# 需要显式把整个 mcp 包目录作为 datas 打进 bundle。
mcp_dir = os.path.join(_VENV_SP, 'mcp')
if os.path.isdir(mcp_dir):
    mcp_datas = [(mcp_dir, 'mcp')]
    datas += mcp_datas
    hiddenimports += collect_submodules('mcp')
    print(f'[spec] mcp dir collected as datas: {mcp_dir}')

# -m pip 支持：把 pip 及其全部子模块收集进 bundle（nanobot optional_features
# 在 frozen 模式下会调用 nanobot-server -m pip install ...）
try:
    pip_hiddenimports = collect_submodules('pip')
    pip_datas = collect_data_files('pip')
    hiddenimports += pip_hiddenimports
    datas += pip_datas
    print(f'[spec] pip collected: {len(pip_hiddenimports)} submodules')
except Exception as e:
    print(f'[spec] pip collection skipped: {e}')

# telegram 渠道可选依赖（python-telegram-bot, socksio, python-socks）
# frozen 模式下 _requirement_installed 无法探测系统环境，需要打进 bundle
try:
    telegram_hiddenimports = collect_submodules('telegram')
    hiddenimports += telegram_hiddenimports
    print(f'[spec] telegram collected: {len(telegram_hiddenimports)} submodules')
except Exception as e:
    print(f'[spec] telegram collection skipped: {e}')
for _dep in ['socksio', 'python_socks']:
    try:
        hiddenimports += collect_submodules(_dep)
        datas += collect_data_files(_dep)
        print(f'[spec] {_dep} collected')
    except Exception as e:
        print(f'[spec] {_dep} collection skipped: {e}')

a = Analysis(
    ['entry_point.py'],
    pathex=['.venv/lib/python3.11/site-packages'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['runtime_hook_channels.py'],
    excludes=[
        'tkinter', 'matplotlib', 'PyQt5', 'PySide2',
        'IPython', 'pytest',
    ],
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
    name='nanobot-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='nanobot-server',
)
