为了自己用！！！
# nanobot macOS 桌面应用

Electron 壳 + PyInstaller 打包的 `nanobot-server`，开箱即用。

## 前置条件

- macOS（arm64）
- Node.js 18+
- `uv`（用于创建 Python 打包环境，已在本机全局安装）

## 构建

```bash
# 1. Python 侧（nanobot-ai + PyInstaller 装入 python/.venv）
uv venv --python 3.11 python/.venv
uv pip install -p python/.venv nanobot-ai pyinstaller

# 2. Node 侧
npm install

# 3. 打包（自动先升级 nanobot-ai 并重建二进制）
npm run build
```

产物位于 `dist/`：`nanobot-1.0.0-arm64.dmg`、`nanobot-1.0.0-arm64-mac.zip`。

## 升级 nanobot

```bash
# 一条命令完成：升级 .venv 中 nanobot-ai → 重建 PyInstaller 二进制
npm run upgrade

# 或直接完整重打应用包（自动包含升级步骤）
npm run build
# 或单独打 mac 包
npm run build:mac
```

每次发版改 `package.json` 的 `version`（dmg 文件名带版本号）。

## 开发模式

```bash
npm run build:python
npm start
```

## 运行说明

- 打开应用后自动启动网关（WebUI 监听 `127.0.0.1:8765`），窗口内嵌 WebUI。
- WebUI 认证：主进程读取 `~/.nanobot/config.json` 中 `channels.websocket.tokenIssueSecret`，
  加载 `http://127.0.0.1:8765/#/?bootstrapSecret=<secret>` 完成自动认证。
- 应用退出时 SIGTERM 终止子进程（网关随应用生命周期存在，不再有独立后台网关进程）。

## 签名与公证（可选）

本机未配置 Developer ID 证书时构建自动跳过签名，公证脚本
（`scripts/notarize.js`）在缺失以下环境变量时直接跳过：

```bash
export APPLE_ID="your@email.com"
export APPLE_APP_SPECIFIC_PASSWORD="xxxx-xxxx-xxxx-xxxx"
export APPLE_TEAM_ID="YOURTEAMID"
# 可选：覆盖签名身份
export CSC_NAME="Developer ID Application: Your Name (TEAMID)"
```

## 已知事项

- PyInstaller 环境下 `pkgutil` 无法发现渠道子包，由 `python/runtime_hook_channels.py`
  在运行时以文件系统扫描方式修补（`nanobot-server.spec` 中已配置为 runtime hook）。
- 未签名构建下 Gatekeeper 会提示，请右键「打开」绕过一次。
