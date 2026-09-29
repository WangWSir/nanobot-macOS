"""PyInstaller runtime hook: patch channel plugin discovery.

Under PyInstaller, pkgutil.iter_modules() over the nanobot.channels path
returns no subpackages (the frozen importer's state), so
nanobot.channels.registry._channel_package_names() finds nothing and no
channels ever enable. This hook monkeypatches discovery with a
filesystem-based scan of the bundled channel directories.
"""
import os
import sys


def _install_discovery_patch():
    if not getattr(sys, 'frozen', False):
        return
    try:
        import nanobot.channels as channels_pkg
        from nanobot.channels import registry as reg
    except Exception:
        return

    base = None
    for p in channels_pkg.__path__:
        if os.path.isdir(p):
            base = p
            break
    if base is None:
        return

    def _fs_package_names():
        names = []
        for name in sorted(os.listdir(base)):
            sub = os.path.join(base, name)
            if not os.path.isdir(sub):
                continue
            if not name.startswith('_'):
                names.append(name)
        return [n for n in names if reg.has_channel_package(n)]

    reg._channel_package_names = _fs_package_names
    print("[nanobot-server] channel discovery patched (filesystem mode)", flush=True)


_install_discovery_patch()
