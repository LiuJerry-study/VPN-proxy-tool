from __future__ import annotations

import ctypes
import json
import platform
import threading
from typing import Any

from .util import RUNTIME_DIR, atomic_write_json, read_json

BACKUP_FILE = RUNTIME_DIR / "proxy_backup.json"
INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
DEFAULT_BYPASS = "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;172.20.*;172.21.*;172.22.*;172.23.*;172.24.*;172.25.*;172.26.*;172.27.*;172.28.*;172.29.*;172.30.*;172.31.*;192.168.*;<local>"


def _is_windows() -> bool:
    return platform.system().lower() == "windows"


def _notify() -> None:
    if not _is_windows():
        return
    try:
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(None, 39, None, 0)  # SETTINGS_CHANGED
        wininet.InternetSetOptionW(None, 37, None, 0)  # REFRESH
    except Exception:
        pass


def read_current() -> dict[str, Any]:
    if not _is_windows():
        return {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, winreg.KEY_READ) as key:
            result: dict[str, Any] = {}
            for name in ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL"):
                try:
                    value, kind = winreg.QueryValueEx(key, name)
                    result[name] = int(value) if name == "ProxyEnable" else str(value)
                    result[name + "_kind"] = int(kind)
                except FileNotFoundError:
                    result[name] = None
            return result
    except OSError:
        return {}


class SystemProxy:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._enabled = False

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def _backup(self) -> None:
        if BACKUP_FILE.exists():
            return
        current = read_current()
        if not current:
            return
        atomic_write_json(BACKUP_FILE, current)

    def enable(self, port: int) -> bool:
        if not _is_windows():
            return False
        with self._lock:
            try:
                import winreg
                self._backup()
                access = winreg.KEY_READ | winreg.KEY_WRITE
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0, access) as key:
                    winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
                    winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, f"127.0.0.1:{int(port)}")
                    winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, DEFAULT_BYPASS)
                self._enabled = True
                _notify()
                return True
            except OSError:
                return False

    def restore(self) -> bool:
        if not _is_windows():
            self._enabled = False
            return False
        with self._lock:
            backup = read_json(BACKUP_FILE, None)
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, 0,
                                    winreg.KEY_READ | winreg.KEY_WRITE) as key:
                    if isinstance(backup, dict) and backup:
                        for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
                            value = backup.get(name)
                            if value is None:
                                try:
                                    winreg.DeleteValue(key, name)
                                except FileNotFoundError:
                                    pass
                            elif name == "ProxyEnable":
                                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
                            else:
                                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))
                    else:
                        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
                try:
                    BACKUP_FILE.unlink()
                except OSError:
                    pass
                self._enabled = False
                _notify()
                return True
            except OSError:
                return False

    def cleanup_stale(self) -> None:
        """Restore a proxy left behind by a previous crash, but only if it points to us."""
        backup = read_json(BACKUP_FILE, None)
        current = read_current()
        if not backup or not current:
            return
        proxy_server = str(current.get("ProxyServer") or "")
        if "127.0.0.1:" in proxy_server:
            self.restore()

    def public_state(self) -> dict[str, Any]:
        current = read_current()
        return {
            "enabled": self.enabled,
            "system_enable": bool(current.get("ProxyEnable")),
            "proxy_server": current.get("ProxyServer") or "",
        }
