"""PyInstaller entry point for nanobot-server.

Run modes (Electron shell contracts):

- ``nanobot-server gateway`` — run the full gateway in-process as the
  foreground owner, so the app's child process IS the gateway and dies with
  the app. Resides until SIGTERM/SIGINT.
- ``nanobot-server gateway stop`` — stop a persistent background gateway
  (invokes the real CLI, which stops the process recorded in the state file).
- anything else — forward to the real nanobot CLI.
"""
import os
import sys
import signal
from pathlib import Path

def _frozen_setup():
    if getattr(sys, 'frozen', False):
        bundle_dir = sys._MEIPASS
        os.environ['PATH'] = bundle_dir + os.pathsep + os.environ.get('PATH', '')
        # 让 frozen 解释器能 import telegram 等渠道依赖（已打进 bundle）
        internal = os.path.join(bundle_dir, '_internal')
        if os.path.isdir(internal) and internal not in sys.path:
            sys.path.append(internal)

def _patch_optional_features():
    """frozen 模式下：可选依赖（telegram 等）已打进 bundle，
    把 nanobot.optional_features 的关键函数 patch 掉，避免触发
    ``nanobot-server -m pip install ...``（frozen 解释器无法自装）。

    注意：nanobot 各模块在 import 时用 `from nanobot.optional_features import
    extra_installed` 等局部引用了这些函数。为了让这些已经 import 的引用也
    被 patch 掉，这里需要把 patch 应用到所有可能已经持有引用的模块里。
    """
    if not getattr(sys, 'frozen', False):
        return
    try:
        from nanobot import optional_features
    except ImportError:
        return

    # 核心 patch
    optional_features.requirement_installed = lambda raw, extra="": True
    optional_features.extra_installed = lambda extra, deps: True
    # 让 install_extra 永远走"无需安装"分支
    optional_features.install_args_for_extra = lambda extra, deps: ([], f"{extra} support")

    # 传播到已持有局部引用的模块（它们用 `from ... import X` 拿了旧引用）
    import importlib
    for module_name in [
        'nanobot.webui.settings_capabilities',
        'nanobot.webui.nanobot_features_api',
    ]:
        try:
            mod = importlib.import_module(module_name)
        except ImportError:
            continue
        for attr in ('requirement_installed', 'extra_installed', 'install_args_for_extra'):
            if hasattr(mod, attr):
                setattr(mod, attr, getattr(optional_features, attr))

def _uv_python_target() -> str:
    """uv pip 需要明确的目标 Python 环境。frozen 下 sys.executable 是二进制本身，
    不能当 venv 用；按优先级定位：项目构建 venv → 用户全局 uv tool venv → 系统 python3。"""
    import shutil as _sh
    for cand in (
        Path.home() / "nanobot-macOS" / "python" / ".venv" / "bin" / "python",
        Path.home() / ".local" / "share" / "uv" / "tools" / "nanobot-ai" / "bin" / "python",
    ):
        if cand.is_file():
            return str(cand)
    return _sh.which('python3') or '/usr/bin/python3'

def _ensure_exec_path():
    """把 CLI-apps 安装目录加进 nanobot exec 工具的 PATH。

    uv pip install 到项目 venv 后，cli-anything-* 可执行文件落在
    <venv>/bin。nanobot exec 工具默认 PATH 不含该目录，导致安装成功但
    调用时报 "cli-anything-xxx 不在 nanobot 能访问的 PATH 里"。

    通过 config.json 的 tools.exec.pathPrepend 持久化补全 PATH，
    下次网关启动自动生效。
    """
    if not getattr(sys, 'frozen', False):
        return
    cfg_path = Path.home() / ".nanobot" / "config.json"
    if not cfg_path.is_file():
        return
    try:
        import json
        cfg = json.loads(cfg_path.read_text())
        exec_cfg = cfg.setdefault("tools", {}).setdefault("exec", {})
        prepend = exec_cfg.get("pathPrepend", "")
        venv_bin = str(Path(_uv_python_target()).parent)
        if venv_bin not in prepend.split(os.pathsep):
            exec_cfg["pathPrepend"] = (prepend + os.pathsep if prepend else "") + venv_bin
            cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
            print(f"[nanobot-server] exec pathPrepend set to {exec_cfg['pathPrepend']}", flush=True)
    except Exception as e:
        print(f"[nanobot-server] ensure_exec_path failed: {e}", file=sys.stderr, flush=True)

def _patch_cli_apps_pip():
    """frozen 模式下把 CliAppManager 的 pip 安装/卸载路由到 uv，
    避免 `sys.executable -m pip` 走 nanobot CLI 报错。
    安装时取 install_cmd 里的包名（可能是 git URL），卸载时取
    installed_entry 里的 pip_distribution（git 包无此字段则用 entry_point）。"""
    if not getattr(sys, 'frozen', False):
        return
    import shutil as _sh
    import shlex
    uv = _sh.which('uv')
    if not uv:
        return
    try:
        from nanobot.apps.cli import service as cli_service
    except ImportError:
        return

    def _pip_install_argv(self, app, update=False):
        install_cmd = str(app.get('install_cmd') or '')
        pkg = ''
        if install_cmd:
            try:
                tokens = shlex.split(install_cmd)
                if len(tokens) >= 3 and tokens[:2] == ['pip', 'install']:
                    pkg = tokens[2]
                elif len(tokens) >= 5 and tokens[1:4] == ['-m', 'pip', 'install']:
                    pkg = tokens[4]
            except ValueError:
                pass
        if not pkg:
            pkg = str(app.get('pip_package') or app.get('entry_point') or '').strip()
        if not pkg:
            return []
        argv = [uv, 'pip', 'install', '--python', _uv_python_target()]
        if update:
            argv += ['--force-reinstall']
        argv += [pkg]
        return argv

    def _pip_uninstall_argv(self, app, installed_entry=None):
        distribution = str((installed_entry or {}).get('pip_distribution') or '').strip()
        if not distribution:
            # git 安装的包没有 pip_distribution 字段，用 entry_point 兜底
            distribution = str(app.get('entry_point') or '').strip()
        if not distribution:
            return []
        return [uv, 'pip', 'uninstall', '-y', '--python', _uv_python_target(), distribution]

    cli_service.CliAppManager._pip_available = lambda self: True
    cli_service.CliAppManager._pip_install_argv = _pip_install_argv
    cli_service.CliAppManager._pip_uninstall_argv = _pip_uninstall_argv

def _run_cli(args):
    from nanobot.cli.entry import main as nanobot_main
    # CLI-apps 管理词（install/uninstall/update <name>）转发给
    # nanobot.cli.apps 的 CliAppManager（frozen 下 pip 路由到 uv）
    if args[0] in ("install", "uninstall", "update") and len(args) > 1:
        _run_cli_apps(args)
        return
    sys.argv = ["nanobot"] + args
    nanobot_main()

def _run_cli_apps(args):
    """在 frozen bundle 内执行 CliAppManager（pip 路由到 uv，绕过 frozen pip 限制）。"""
    import json
    import shlex
    from nanobot.apps.cli.service import CliAppManager, CliAppsRuntimeConfig, CliAppError
    from nanobot.config.paths import get_workspace_path
    from nanobot.cli.runtime_config import _load_runtime_config
    from pathlib import Path

    # frozen 下 pip 无法自装/自卸，路由到 uv（带明确 --python 目标环境）
    if getattr(sys, 'frozen', False):
        import shutil as _sh
        uv = _sh.which('uv')
        if uv:
            def _pip_install_argv(self, app, update=False):
                install_cmd = str(app.get('install_cmd') or '')
                pkg = ''
                if install_cmd:
                    try:
                        tokens = shlex.split(install_cmd)
                        if len(tokens) >= 3 and tokens[:2] == ['pip', 'install']:
                            pkg = tokens[2]
                        elif len(tokens) >= 5 and tokens[1:4] == ['-m', 'pip', 'install']:
                            pkg = tokens[4]
                    except ValueError:
                        pass
                if not pkg:
                    pkg = str(app.get('pip_package') or app.get('entry_point') or '').strip()
                if not pkg:
                    return []
                argv = [uv, 'pip', 'install', '--python', _uv_python_target()]
                if update:
                    argv += ['--force-reinstall']
                argv += [pkg]
                return argv

            def _pip_uninstall_argv(self, app, installed_entry=None):
                distribution = str((installed_entry or {}).get('pip_distribution') or '').strip()
                if not distribution:
                    distribution = str(app.get('entry_point') or '').strip()
                if not distribution:
                    return []
                return [uv, 'pip', 'uninstall', '-y', '--python', _uv_python_target(), distribution]

            CliAppManager._pip_available = lambda self: True
            CliAppManager._pip_install_argv = _pip_install_argv
            CliAppManager._pip_uninstall_argv = _pip_uninstall_argv

    cfg = _load_runtime_config(str(Path.home() / ".nanobot" / "config.json"), None)
    manager = CliAppManager(
        workspace=Path(get_workspace_path(cfg.workspace_path)),
        data_dir=Path.home() / ".nanobot" / "cli-apps",
        runtime=CliAppsRuntimeConfig(),
    )
    action = args[0]
    name = args[1]
    try:
        if action == "install":
            result = manager.install(name)
        elif action == "update":
            result = manager.update(name)
        else:
            result = manager.uninstall(name)
    except CliAppError as exc:
        print(f"[nanobot-server] {exc.message}", file=sys.stderr)
        sys.exit(exc.status)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("last_action", {}).get("ok", True):
        sys.exit(1)
    # 安装/更新后补全 exec PATH，让 agent 能直接调用新装的 CLI 应用
    if action in ("install", "update"):
        _ensure_exec_path()

def _run_gateway_in_foreground():
    _patch_cli_apps_pip()
    # 补全 exec PATH（把 venv bin 加入 tools.exec.pathPrepend）
    _ensure_exec_path()
    from nanobot.cli.gateway_runtime import _run_gateway
    from nanobot.cli.runtime_config import _load_runtime_config, _provider_setup_error
    from nanobot.cli.webui_support import _prepare_webui_bundle_for_gateway

    config_path = Path.home() / ".nanobot" / "config.json"
    cfg = _load_runtime_config(str(config_path), None)
    _prepare_webui_bundle_for_gateway(cfg, mode="warn")
    provider_error = _provider_setup_error(cfg)

    def handle_shutdown(signum, frame):
        print("[nanobot-server] shutdown signal received", flush=True)
        os.kill(os.getpid(), signal.SIGINT)

    signal.signal(signal.SIGTERM, handle_shutdown)

    _run_gateway(
        cfg,
        port=None,
        webui_bundle_mode="warn",
        unconfigured_provider_error=provider_error,
    )

def main():
    _frozen_setup()
    _patch_optional_features()
    args = sys.argv[1:]
    if args[:2] == ["gateway", "stop"]:
        _run_cli(["gateway", "stop"])
        return
    if args and args[0] == "gateway":
        _run_gateway_in_foreground()
        return
    # 无参数（默认模式）：作为网关常驻运行
    if not args:
        _run_gateway_in_foreground()
        return
    _run_cli(args)

if __name__ == "__main__":
    main()
