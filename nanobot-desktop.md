# nanobot macOS 桌面应用开发规格（Electron + PyInstaller）

> **给 AI 的指令**：根据本规格书直接生成完整项目。所有文件内容均已给出，无需额外推断。按“项目目录结构”创建文件，按“构建流程”执行命令，即可产出可运行的 macOS 应用。

---

## 1. 目标与范围

构建一个 macOS 桌面应用 `nanobot`，实现：

- 打开应用 → 后台启动 nanobot 服务（PyInstaller 打包的独立二进制）
- 应用窗口内嵌 WebUI（加载 `http://127.0.0.1:8765`）
- 关闭应用 → 自动终止 nanobot 子进程
- 支持签名与公证，可分发 `.dmg` / `.zip`

**技术栈**：Electron 33 + electron-builder 25 + PyInstaller 6 + nanobot（Python）

---

## 2. 系统架构

```
┌─────────────────────────────────────────┐
│           Electron 主进程                │
│  ┌───────────────────────────────────┐  │
│  │ 1. spawn() 启动 nanobot-server    │  │
│  │ 2. 健康检查 127.0.0.1:8765        │  │
│  │ 3. BrowserWindow.loadURL(WebUI)   │  │
│  │ 4. before-quit 时 SIGTERM 子进程  │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│      nanobot-server (PyInstaller)        │
│  nanobot webui --background              │
│  监听 127.0.0.1:8765                     │
└─────────────────────────────────────────┘
```

---

## 3. 项目目录结构

```
nanobot-desktop/
├── electron/
│   ├── main.js
│   ├── preload.js
│   ├── loading.html
│   └── error.html
├── python/
│   ├── entry_point.py
│   └── nanobot-server.spec
├── build/
│   ├── icon.icns
│   └── entitlements.mac.plist
├── scripts/
│   └── notarize.js
├── package.json
└── dist/                    # 构建输出（自动生成）
```

---

## 4. 环境要求与依赖

- macOS 12+
- Node.js 18+
- Python 3.10+（用于打包 nanobot，运行时不需要）
- 已安装 nanobot 源码及其依赖（`pip install -e .` 在 nanobot 源码目录）
- Apple Developer ID 证书（用于签名公证，可选）

**安装依赖**：

```bash
# Python 侧
pip install pyinstaller

# Node 侧
npm install
```

---

## 5. 各文件详细规格与代码

### 5.1 `python/entry_point.py`

**作用**：PyInstaller 入口，等价于执行 `nanobot webui --background`。

```python
"""PyInstaller entry point for nanobot-server.
Equivalent to: nanobot webui --background
"""
import sys
import os

if getattr(sys, 'frozen', False):
    bundle_dir = sys._MEIPASS
    os.environ['PATH'] = bundle_dir + os.pathsep + os.environ.get('PATH', '')

def main():
    from nanobot.cli.entry import main as nanobot_main
    sys.argv = ["nanobot", "webui", "--background"]
    nanobot_main()

if __name__ == "__main__":
    main()
```

> **假设**：nanobot 的 CLI 入口为 `nanobot.cli.entry:main`。若实际不同，请替换为正确导入路径。

---

### 5.2 `python/nanobot-server.spec`

**作用**：PyInstaller 打包配置，生成 `onedir` 结构的独立二进制。

```python
# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

hiddenimports = collect_submodules('nanobot')
datas = collect_data_files('nanobot')

a = Analysis(
    ['entry_point.py'],
    pathex=['..'],                    # 指向 nanobot 源码根目录（含 nanobot 包）
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'numpy', 'PyQt5', 'PySide2',
        'IPython', 'pytest', 'setuptools',
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
```

**构建命令**：

```bash
cd python/
pyinstaller nanobot-server.spec
# 输出：python/dist/nanobot-server/nanobot-server
```

---

### 5.3 `electron/main.js`

**作用**：Electron 主进程，启动子进程、健康检查、窗口管理、退出清理。

```javascript
const { app, BrowserWindow } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');
const net = require('net');

const WEBUI_PORT = 8765;
const HEALTH_CHECK_INTERVAL = 500;
const HEALTH_CHECK_TIMEOUT = 30000;

let mainWindow = null;
let nanobotProcess = null;
let healthCheckTimer = null;
let healthCheckStartTime = 0;

function startNanobot() {
  const isDev = !app.isPackaged;
  const serverDir = isDev
    ? path.join(__dirname, '..', 'python', 'dist', 'nanobot-server')
    : path.join(process.resourcesPath, 'nanobot-server');

  const serverPath = path.join(serverDir, 'nanobot-server');

  nanobotProcess = spawn(serverPath, [], {
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: false,
    cwd: serverDir,
    env: {
      ...process.env,
      PATH: `/usr/local/bin:/opt/homebrew/bin:${process.env.PATH || ''}`,
    },
  });

  nanobotProcess.stdout.on('data', (data) => {
    console.log(`[nanobot] ${data.toString().trim()}`);
  });

  nanobotProcess.stderr.on('data', (data) => {
    console.error(`[nanobot:err] ${data.toString().trim()}`);
  });

  nanobotProcess.on('error', (err) => {
    console.error('[nanobot-desktop] Failed to start nanobot:', err);
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadFile(path.join(__dirname, 'error.html'));
    }
  });

  nanobotProcess.on('exit', (code, signal) => {
    console.log(`[nanobot-desktop] nanobot exited (code=${code}, signal=${signal})`);
    nanobotProcess = null;
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.close();
    }
  });
}

function waitForWebUI(callback) {
  healthCheckStartTime = Date.now();

  const check = () => {
    if (Date.now() - healthCheckStartTime > HEALTH_CHECK_TIMEOUT) {
      callback(false);
      return;
    }

    const socket = new net.Socket();
    socket.setTimeout(1000);

    socket.on('connect', () => {
      socket.destroy();
      http.get(`http://127.0.0.1:${WEBUI_PORT}/`, (res) => {
        res.resume(); // 释放连接
        if (res.statusCode >= 200 && res.statusCode < 500) {
          callback(true);
        } else {
          retry();
        }
      }).on('error', () => retry());
    });

    socket.on('error', () => { socket.destroy(); retry(); });
    socket.on('timeout', () => { socket.destroy(); retry(); });

    socket.connect(WEBUI_PORT, '127.0.0.1');

    function retry() {
      if (healthCheckTimer) clearTimeout(healthCheckTimer);
      healthCheckTimer = setTimeout(check, HEALTH_CHECK_INTERVAL);
    }
  };

  check();
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    title: 'nanobot',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'loading.html'));

  waitForWebUI((ready) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (ready) {
      mainWindow.loadURL(`http://127.0.0.1:${WEBUI_PORT}`);
    } else {
      mainWindow.loadFile(path.join(__dirname, 'error.html'));
    }
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

app.whenReady().then(() => {
  startNanobot();
  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on('before-quit', () => {
  if (healthCheckTimer) clearTimeout(healthCheckTimer);

  if (nanobotProcess && !nanobotProcess.killed) {
    nanobotProcess.kill('SIGTERM');
    const forceKillTimer = setTimeout(() => {
      if (nanobotProcess && !nanobotProcess.killed) {
        nanobotProcess.kill('SIGKILL');
      }
    }, 5000);
    nanobotProcess.once('exit', () => clearTimeout(forceKillTimer));
  }
});
```

---

### 5.4 `electron/preload.js`

**作用**：最小预加载脚本，保持上下文隔离。

```javascript
// 当前无需暴露任何 API，保留空文件即可
```

---

### 5.5 `electron/loading.html`

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {
      display: flex; align-items: center; justify-content: center;
      height: 100vh; margin: 0; background: #1a1a2e; color: #eee;
      font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    }
    .loader { text-align: center; }
    .spinner {
      width: 40px; height: 40px; margin: 0 auto 20px;
      border: 3px solid #333; border-top-color: #6c63ff;
      border-radius: 50%; animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    p { opacity: 0.6; font-size: 14px; }
  </style>
</head>
<body>
  <div class="loader">
    <div class="spinner"></div>
    <p>正在启动 nanobot…</p>
  </div>
</body>
</html>
```

---

### 5.6 `electron/error.html`

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {
      display: flex; align-items: center; justify-content: center;
      height: 100vh; margin: 0; background: #1a1a2e; color: #eee;
      font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    }
    .error { text-align: center; max-width: 400px; }
    h2 { color: #ff6b6b; }
    p { opacity: 0.7; font-size: 14px; line-height: 1.6; }
    code {
      background: #2a2a4a; padding: 2px 6px; border-radius: 4px;
      font-size: 13px;
    }
  </style>
</head>
<body>
  <div class="error">
    <h2>nanobot 启动失败</h2>
    <p>WebUI 未能在 30 秒内就绪。请检查：</p>
    <p>
      1. 首次使用请先在终端运行 <code>nanobot webui</code> 完成初始配置<br>
      2. 确认 <code>~/.nanobot/config.json</code> 中已配置有效的 API Key<br>
      3. 检查 8765 端口是否被其他程序占用
    </p>
  </div>
</body>
</html>
```

---

### 5.7 `package.json`

```json
{
  "name": "nanobot-desktop",
  "version": "1.0.0",
  "description": "nanobot Desktop — WebUI in an Electron shell",
  "main": "electron/main.js",
  "scripts": {
    "start": "electron .",
    "build:python": "cd python && pyinstaller nanobot-server.spec",
    "build:mac": "electron-builder --mac",
    "build": "npm run build:python && npm run build:mac"
  },
  "devDependencies": {
    "electron": "^33.0.0",
    "electron-builder": "^25.0.0",
    "@electron/notarize": "^2.3.0"
  },
  "build": {
    "appId": "com.yourname.nanobot-desktop",
    "productName": "nanobot",
    "directories": {
      "output": "dist",
      "buildResources": "build"
    },
    "mac": {
      "target": [
        { "target": "dmg", "arch": ["arm64", "x64"] },
        { "target": "zip", "arch": ["arm64", "x64"] }
      ],
      "category": "public.app-category.productivity",
      "icon": "build/icon.icns",
      "hardenedRuntime": true,
      "entitlements": "build/entitlements.mac.plist",
      "entitlementsInherit": "build/entitlements.mac.plist",
      "gatekeeperAssess": false
    },
    "dmg": {
      "contents": [
        { "x": 130, "y": 220 },
        { "x": 410, "y": 220, "type": "link", "path": "/Applications" }
      ]
    },
    "extraResources": [
      {
        "from": "python/dist/nanobot-server",
        "to": "nanobot-server",
        "filter": ["**/*"]
      }
    ],
    "asar": true,
    "files": [
      "electron/**/*",
      "!python/**/*"
    ],
    "afterSign": "scripts/notarize.js"
  }
}
```

---

### 5.8 `build/entitlements.mac.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.allow-jit</key>
    <true/>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.allow-dyld-environment-variables</key>
    <true/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
</dict>
</plist>
```

---

### 5.9 `scripts/notarize.js`

**作用**：在 electron-builder 签名后，对 nanobot-server 深度签名并提交公证。

```javascript
const { notarize } = require('@electron/notarize');
const { execSync } = require('child_process');
const path = require('path');

exports.default = async function notarizing(context) {
  const { electronPlatformName, appOutDir } = context;
  if (electronPlatformName !== 'darwin') return;

  const appName = context.packager.appInfo.productFilename;
  const appPath = path.join(appOutDir, `${appName}.app`);
  const serverPath = path.join(appPath, 'Contents', 'Resources', 'nanobot-server');

  // 深度签名 nanobot-server 内部所有二进制
  execSync(
    `codesign --deep --force --options runtime --timestamp \
     --entitlements build/entitlements.mac.plist \
     --sign "Developer ID Application: Your Name (TEAMID)" \
     "${serverPath}"`,
    { stdio: 'inherit' }
  );

  // 对最终 .app 签名
  execSync(
    `codesign --deep --force --options runtime --timestamp \
     --entitlements build/entitlements.mac.plist \
     --sign "Developer ID Application: Your Name (TEAMID)" \
     "${appPath}"`,
    { stdio: 'inherit' }
  );

  // 提交公证（需设置环境变量）
  await notarize({
    appPath,
    appleId: process.env.APPLE_ID,
    appleIdPassword: process.env.APPLE_APP_SPECIFIC_PASSWORD,
    teamId: process.env.APPLE_TEAM_ID,
  });
};
```

> 若无需公证，可删除 `afterSign` 配置及此文件。

---

## 6. 构建与打包流程

```bash
# 1. 打包 nanobot（在 nanobot 源码环境中）
cd python/
pyinstaller nanobot-server.spec
cd ..

# 2. 安装 Node 依赖
npm install

# 3. 构建 macOS 应用（含签名公证，需先设置环境变量）
export APPLE_ID="your@email.com"
export APPLE_APP_SPECIFIC_PASSWORD="xxxx-xxxx-xxxx-xxxx"
export APPLE_TEAM_ID="YOURTEAMID"
npm run build
```

输出：`dist/` 下生成 `.dmg` 和 `.zip`。

---

## 7. 运行与调试

- **开发模式**：`npm start`（需先执行 `npm run build:python`）
- **日志**：主进程 stdout 会打印 `[nanobot]` 和 `[nanobot:err]` 前缀日志
- **端口冲突**：若 8765 被占用，修改 `electron/main.js` 中 `WEBUI_PORT`，并同步修改 nanobot 启动参数（在 `entry_point.py` 中添加 `--port`）

---

## 7.1 升级

为了构建一个稳定、可复现的 macOS 应用，建议遵循以下流程：

1.  **强制升级 nanobot 到最新版**：在打包前，明确地更新你的构建环境。
    ```bash
    # 强制安装/升级到 PyPI 上的最新稳定版
    uv tool install --force --upgrade nanobot-ai
    ```
2.  **验证版本**：确认升级成功。
    ```bash
    nanobot --version
    ```
3.  **执行 PyInstaller 打包**：使用更新后的环境，重新生成 `nanobot-server` 二进制文件。
    ```bash
    cd python/
    pyinstaller nanobot-server.spec
    ```
4.  **构建 Electron 应用**：最后，使用 electron-builder 打包整个应用。
    ```bash
    npm run build
    ```

## 8. 已知问题与规避

| 问题 | 规避 |
|---|---|
| 首次运行缺少 `~/.nanobot/config.json` | 应用启动前检查文件是否存在，不存在则显示 error.html 提示先运行 `nanobot webui` |
| WebUI 需要认证 | 在 `session.defaultSession.cookies` 中预置 token，或启动时传入 token 参数 |
| PyInstaller 漏收子模块 | spec 中已用 `collect_submodules('nanobot')`，若仍缺失，手动加入 `hiddenimports` |
| macOS 公证失败 | 确保 `nanobot-server` 已深度签名，且 entitlements 正确 |
| 从 Finder 启动 PATH 不全 | `main.js` 中已补充 `/usr/local/bin` 和 `/opt/homebrew/bin` |

---

## 9. 验收标准

- [ ] `npm run build` 成功生成 `.dmg`
- [ ] 双击 `.app` 后，窗口显示 loading 页，随后自动加载 WebUI
- [ ] 活动监视器中可见 `nanobot-server` 子进程
- [ ] 退出应用后，`nanobot-server` 进程自动终止
- [ ] 无签名错误（若已配置证书）