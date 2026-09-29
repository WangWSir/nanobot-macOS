const { app, BrowserWindow } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const net = require('net');

const WEBUI_PORT = 8765;
const HEALTH_CHECK_INTERVAL = 500;
const HEALTH_CHECK_TIMEOUT = 30000;

let mainWindow = null;
let nanobotProcess = null;
let healthCheckTimer = null;
let healthCheckStartTime = 0;

function serverDir() {
  const isDev = !app.isPackaged;
  return isDev
    ? path.join(__dirname, '..', 'python', 'dist', 'nanobot-server')
    : path.join(process.resourcesPath, 'nanobot-server');
}

function serverPath() {
  return path.join(serverDir(), 'nanobot-server');
}

function startNanobot() {
  const dir = serverDir();

  nanobotProcess = spawn(serverPath(), [], {
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: false,
    cwd: dir,
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
    // 子进程即网关本身：被主动 kill（退出）时无需关窗；
    // 非 0 异常退出才关闭窗口。
    if (code !== 0 && signal !== 'SIGTERM' && signal !== 'SIGKILL'
        && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.close();
    }
  });
}

// 子进程即网关本身：网关随应用子进程存在，无需独立的 "gateway stop" 调用。

function loadWebUiAuthUrl(callback) {
  const configPath = path.join(os.homedir(), '.nanobot', 'config.json');
  let base = `http://127.0.0.1:${WEBUI_PORT}`;
  try {
    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    const ws = config && config.channels && config.channels.websocket;
    const secret = ws && (ws.tokenIssueSecret || ws.token);
    if (secret) {
      // 服务端 _handle_bootstrap 接受 bootstrapSecret 哈希作为本地引导凭据
      callback(`${base}/#/?bootstrapSecret=${encodeURIComponent(secret)}`);
      return;
    }
  } catch (e) {
    // 配置不存在或解析失败时退回裸地址
  }
  callback(base);
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
      loadWebUiAuthUrl((url) => {
        if (!mainWindow || mainWindow.isDestroyed()) return;
        mainWindow.loadURL(url);
      });
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

  // 子进程即网关本身：SIGTERM 触发 entry 中的网关清理（entry_point 已注册 SIGTERM → SIGINT 转换），
  // 5 秒后强制 SIGKILL 兜底。
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
