from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Windows 10/11: keep native tray menus and dialogs from being bitmap-scaled.
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4


def enable_process_dpi_awareness() -> bool:
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_bool
        if set_context(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
            return True
    except Exception:
        pass
    try:
        shcore = ctypes.windll.shcore
        set_awareness = shcore.SetProcessDpiAwareness
        set_awareness.argtypes = [ctypes.c_int]
        set_awareness.restype = ctypes.c_int
        if set_awareness(2) == 0:
            return True
    except Exception:
        pass
    try:
        return bool(ctypes.windll.user32.SetProcessDPIAware())
    except Exception:
        return False


def enable_thread_dpi_awareness() -> bool:
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        set_context = user32.SetThreadDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_void_p
        return bool(set_context(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)))
    except Exception:
        return False



def configure_vendor_path() -> Path | None:
    """Configure imports for source mode.

    A PyInstaller build already contains every Python dependency in its archive;
    it must not pick up a sibling ``vendor`` directory. Source mode uses the
    bundled vendor tree only.
    """
    if getattr(sys, "frozen", False):
        return None
    source = APP_DIR / "vendor"
    if not source.exists():
        return None
    for candidate in (APP_DIR, source):
        value = str(candidate)
        while value in sys.path:
            sys.path.remove(value)
        sys.path.insert(0, value)
    return source


configure_vendor_path()

from backend.util import LOG_FILE, RUNTIME_DIR, WEB_DIR, ensure_dirs, migrate_legacy_store
from backend.webapp import Application

ensure_dirs()
migrate_legacy_store()
(RUNTIME_DIR / "app.log").touch(exist_ok=True)
logging.basicConfig(
    filename=str(RUNTIME_DIR / "app.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("qinglian")


def message_box(text: str, title: str = "轻连 VPN") -> None:
    if os.name != "nt":
        print(text, file=sys.stderr)
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:
        print(text, file=sys.stderr)


def find_browser() -> Path | None:
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for path in candidates:
        if str(path) and path.is_file():
            return path
    return None


def activate_window(title: str) -> bool:
    if os.name != "nt":
        return False
    found: list[int] = []
    try:
        EnumWindows = ctypes.windll.user32.EnumWindows
        GetWindowTextW = ctypes.windll.user32.GetWindowTextW
        GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def callback(hwnd, lparam):
            if not IsWindowVisible(hwnd):
                return True
            length = GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buffer, length + 1)
            if title in buffer.value:
                found.append(int(hwnd))
                return False
            return True

        EnumWindows(callback_type(callback), 0)
        if found:
            hwnd = found[0]
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            return True
    except Exception:
        pass
    return False


def is_qinglian_window_open(title: str = "轻连 VPN") -> bool:
    if os.name != "nt":
        return False
    found = [False]
    try:
        EnumWindows = ctypes.windll.user32.EnumWindows
        GetWindowTextW = ctypes.windll.user32.GetWindowTextW
        GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
        IsWindowVisible = ctypes.windll.user32.IsWindowVisible
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def callback(hwnd, lparam):
            if not IsWindowVisible(hwnd):
                return True
            length = GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buffer, length + 1)
            if title in buffer.value:
                found[0] = True
                return False
            return True

        EnumWindows(callback_type(callback), 0)
    except Exception:
        pass
    return found[0]


def stop_browser_profile_processes(profile_dir: Path, fallback_pid: int | None = None) -> int:
    """Stop browser processes that use this app's private profile directory."""
    marker = str(profile_dir.resolve()).lower()
    stopped = 0
    try:
        import psutil  # type: ignore

        targets = []
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                if proc.pid == os.getpid():
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if marker and marker in cmdline:
                    targets.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for proc in targets:
            try:
                proc.kill()
                stopped += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if targets:
            try:
                psutil.wait_procs(targets, timeout=3)
            except Exception:
                pass
        return stopped
    except Exception:
        if fallback_pid and os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(fallback_pid)],
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                return 1
            except Exception:
                pass
    return stopped


class DesktopApp:
    def __init__(self, autostart_launch: bool = False) -> None:
        self.autostart_launch = autostart_launch
        self.tray = None
        self.browser_mode = False
        self.browser_proc: subprocess.Popen | None = None
        self._browser_started_at = 0.0
        self._browser_window_seen = False
        self._browser_lock = threading.RLock()
        self._quitting = False
        self._cleanup_lock = threading.RLock()
        self._cleaned = False
        self._toggle_lock = threading.Lock()
        self.application = Application(on_quit=self.request_quit)
        self.port = self.application.start_server()
        self.url = f"http://127.0.0.1:{self.port}/"
        self.hidden_start = autostart_launch and self._close_to_tray()

    def _close_to_tray(self) -> bool:
        try:
            return bool(self.application.store.settings.get("close_to_tray", True))
        except Exception:
            return True

    def run(self) -> int:
        # A dedicated Edge/Chrome app window gives us a native-looking, chromeless
        # shell without writing runtime data outside the application directory.
        return self.run_browser_mode()

    # ---------- Edge/Chrome app window ----------
    def run_browser_mode(self) -> int:
        self.browser_mode = True
        self.start_tray()
        if not self.hidden_start or self.tray is None:
            if not self.open_browser_window() and self.tray is None:
                self.request_quit()
        log.info("browser mode running at %s", self.url)
        try:
            while not self._quitting:
                with self._browser_lock:
                    proc = self.browser_proc
                if proc is not None:
                    window_open = is_qinglian_window_open()
                    if window_open:
                        self._browser_window_seen = True
                    elif self._browser_window_seen:
                        self._on_browser_window_closed()
                        continue
                    elif time.time() - self._browser_started_at > 25:
                        log.warning("browser window did not appear in time")
                        self._stop_browser_processes()
                        if self.tray is not None and self._close_to_tray():
                            self._notify("界面启动失败，可点击托盘图标重试")
                        else:
                            self.request_quit()
                            break
                time.sleep(0.35)
        finally:
            # Also clean up here: a quit request may arrive from a timer thread
            # and the main interpreter must not exit before Edge children stop.
            self._stop_browser_processes()
            self.shutdown()
        return 0

    def _on_browser_window_closed(self) -> None:
        log.info("browser window closed")
        self._stop_browser_processes()
        self._browser_window_seen = False
        if self._close_to_tray() and self.tray is not None:
            self._notify("轻连仍在后台运行，双击托盘图标可以打开")
        else:
            self.request_quit()

    def _stop_browser_processes(self) -> None:
        with self._browser_lock:
            proc = self.browser_proc
            pid = proc.pid if proc is not None else None
            self.browser_proc = None
            self._browser_started_at = 0.0
        profile_dir = RUNTIME_DIR / "browser-profile"
        stopped = stop_browser_profile_processes(profile_dir, pid)
        if stopped:
            log.info("stopped %s browser processes", stopped)

    def open_browser_window(self) -> bool:
        with self._browser_lock:
            if is_qinglian_window_open():
                activate_window("轻连 VPN")
                return True
            if self.browser_proc is not None:
                self._stop_browser_processes()
            browser = find_browser()
            if browser is None:
                message_box("找不到 Microsoft Edge 或 Google Chrome，无法打开轻连界面。", "轻连 VPN")
                return False
            profile_dir = RUNTIME_DIR / "browser-profile"
            cache_dir = RUNTIME_DIR / "edge-cache"
            crash_dir = RUNTIME_DIR / "edge-crash"
            profile_dir.mkdir(parents=True, exist_ok=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            crash_dir.mkdir(parents=True, exist_ok=True)
            args = [
                str(browser),
                f"--app={self.url}",
                f"--user-data-dir={profile_dir}",
                f"--disk-cache-dir={cache_dir}",
                f"--crash-dumps-dir={crash_dir}",
                "--window-size=1200,800",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-background-mode",
                "--disable-component-update",
                "--no-service-autorun",
                "--disable-session-crashed-bubble",
                "--disable-features=Translate,MediaRouter",
            ]
            try:
                self._browser_window_seen = False
                self._browser_started_at = time.time()
                self.browser_proc = subprocess.Popen(
                    args,
                    cwd=str(APP_DIR),
                    creationflags=CREATE_NO_WINDOW,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("browser window launched pid=%s", self.browser_proc.pid)
                return True
            except Exception:
                log.exception("browser launch failed")
                self.browser_proc = None
                self._browser_started_at = 0.0
                message_box("无法打开轻连界面窗口。", "轻连 VPN")
                return False

    # ---------- tray ----------
    def start_tray(self) -> None:
        if self.tray is not None:
            return
        try:
            import pystray
            from PIL import Image
        except Exception:
            log.exception("tray dependencies unavailable")
            self.tray = None
            return
        try:
            image = Image.open(WEB_DIR / "icon.png").convert("RGBA")
            menu = pystray.Menu(
                pystray.MenuItem("打开轻连", self._tray_show, default=True),
                pystray.MenuItem(lambda item: self._tray_state_text(), None, enabled=False),
                pystray.MenuItem(lambda item: "断开连接" if self._core_active() else "连接到上次节点", self._tray_toggle),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出轻连", self._tray_quit),
            )
            self.tray = pystray.Icon("QingLianVPN", image, "轻连 VPN · 后台运行", menu)
            thread = threading.Thread(target=self._run_tray, name="tray", daemon=True)
            thread.start()
            log.info("tray started")
        except Exception:
            log.exception("tray start failed")
            self.tray = None

    def _run_tray(self) -> None:
        try:
            if not enable_thread_dpi_awareness():
                log.warning("tray thread DPI awareness was not applied")
            self.tray.run()
        except Exception:
            log.exception("tray loop failed")
            self.tray = None

    def _tray_state_text(self) -> str:
        try:
            status = self.application.core.status()
            if status.get("connected"):
                return f"已连接 · {status.get('node_name') or '代理'}"
            if status.get("starting"):
                return "正在连接…"
            if status.get("error"):
                return "连接异常"
            return "未连接"
        except Exception:
            return "轻连"

    def _core_active(self) -> bool:
        try:
            status = self.application.core.status()
            return bool(status.get("connected") or status.get("starting"))
        except Exception:
            return False

    def _tray_show(self, icon=None, item=None) -> None:
        try:
            if is_qinglian_window_open():
                activate_window("轻连 VPN")
            else:
                self.open_browser_window()
        except Exception:
            log.exception("tray show failed")

    def _tray_toggle(self, icon=None, item=None) -> None:
        if not self._toggle_lock.acquire(blocking=False):
            return
        try:
            if self._core_active():
                self.application.core.disconnect()
                self._notify("已断开连接")
            else:
                self.application.core.connect()
                self._notify("已连接")
        except Exception as exc:
            self._notify("连接失败：" + str(exc).splitlines()[0][:120])
        finally:
            self._toggle_lock.release()
            try:
                if self.tray:
                    self.tray.update_menu()
                    self.tray.title = "轻连 VPN · " + self._tray_state_text()
            except Exception:
                pass

    def _notify(self, message: str) -> None:
        try:
            if self.tray:
                self.tray.notify(message, "轻连 VPN")
        except Exception:
            pass

    def _tray_quit(self, icon=None, item=None) -> None:
        self.request_quit()

    # ---------- lifecycle ----------
    def request_quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        log.info("quit requested")
        try:
            if self.tray:
                self.tray.stop()
        except Exception:
            pass
        self.shutdown()
        self._stop_browser_processes()

    def shutdown(self) -> None:
        with self._cleanup_lock:
            if self._cleaned:
                return
            self._cleaned = True
        log.info("shutting down")
        try:
            self.application.shutdown()
        except Exception:
            log.exception("application shutdown failed")
        try:
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 8 * 1024 * 1024:
                LOG_FILE.write_text("", encoding="utf-8")
        except OSError:
            pass


def main() -> int:
    if not enable_process_dpi_awareness():
        log.warning("process DPI awareness was not applied")
    autostart_launch = "--autostart" in sys.argv
    try:
        app = DesktopApp(autostart_launch=autostart_launch)
    except Exception:
        detail = traceback.format_exc()
        log.exception("startup failed")
        message_box("轻连启动失败。\n\n" + detail[-1500:], "轻连 VPN 启动失败")
        return 1
    try:
        return app.run()
    except Exception:
        detail = traceback.format_exc()
        log.exception("unhandled application error")
        message_box("轻连遇到未处理的错误。\n\n" + detail[-1500:], "轻连 VPN")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
