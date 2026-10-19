from __future__ import annotations

import concurrent.futures
import ctypes
import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .nodes import protocol_label
from .singbox_config import build_config, build_test_config, build_outbound, write_config
from .system_proxy import SystemProxy
from .traffic import TrafficMonitor
from .util import CORE_DIR, LOG_FILE, RUNTIME_DIR, free_port, port_is_open, read_json, wait_until

CORE_EXE = CORE_DIR / "sing-box.exe"
CONFIG_FILE = RUNTIME_DIR / "config.json"
TEST_CONFIG_FILE = RUNTIME_DIR / "test-config.json"
TEST_LOG_FILE = RUNTIME_DIR / "test-core.log"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# 测速参数。移动热点/远距离链路上 RTT 常在 300ms 以上还带丢包，TLS/QUIC 握手
# 要好几轮往返，6 秒的预算会把本来能用的节点误判成「连接超时」；并发数过高时
# 探测之间还会互相抢带宽，所以这里放宽超时、压低并发并允许重试一次。
TEST_DELAY_TIMEOUT_MS = 12000      # sing-box 单次探测的服务端超时
TEST_DELAY_CLIENT_TIMEOUT = 16.0   # 本机等 Clash API 应答的超时，需大于上面
TEST_DELAY_CONCURRENCY = 2         # 同时探测的节点数（实测：并发 5 时 0/5 通过，并发 2 时 5/5 通过）
TEST_DELAY_RETRIES = 1             # 失败后的重试次数


def is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _hide_flags() -> int:
    return CREATE_NO_WINDOW


def _describe_delay_error(raw: str) -> str:
    """把 sing-box / urllib 的原始报错收敛成一句能给用户看的话。"""
    text = (raw or "").strip()
    try:
        payload = json.loads(text)
    except Exception:
        payload = None
    if isinstance(payload, dict):
        text = str(payload.get("message") or "").strip() or text
    lowered = text.lower()
    if "context deadline exceeded" in lowered or "timed out" in lowered or "timeout" in lowered:
        return "连接超时"
    return (text or "测速失败")[:300]


class CoreManager:
    def __init__(self, store, system_proxy: SystemProxy, traffic: TrafficMonitor) -> None:
        self.store = store
        self.system_proxy = system_proxy
        self.traffic = traffic
        self._lock = threading.RLock()
        self._op_lock = threading.RLock()
        self._test_lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._state = "disconnected"
        self._error = ""
        self._warning = ""
        self._node: dict[str, Any] | None = None
        self._started_at: float | None = None
        self._api_port = 0
        self._api_secret = ""
        self._log_path = LOG_FILE
        self._core_version = ""
        self._admin = is_admin()
        self.system_proxy.cleanup_stale()
        self._monitor_stop = threading.Event()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, name="core-monitor", daemon=True)
        self._monitor_thread.start()
        self._core_version = self._query_core_version()

    # ---------- helpers ----------
    def _query_core_version(self) -> str:
        if not CORE_EXE.exists():
            return "核心缺失"
        try:
            result = subprocess.run(
                [str(CORE_EXE), "version"], capture_output=True, text=True, timeout=5,
                creationflags=_hide_flags(), encoding="utf-8", errors="replace",
            )
            first = (result.stdout or result.stderr or "").splitlines()
            return first[0].strip() if first else "未知"
        except Exception:
            return "未知"

    def _api_url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._api_port}{path}"

    def _api_request(self, path: str, timeout: float = 2.0) -> dict[str, Any]:
        request = urllib.request.Request(
            self._api_url(path),
            headers={"Authorization": f"Bearer {self._api_secret}"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))

    def _api_ready(self) -> bool:
        try:
            payload = self._api_request("/proxies", timeout=0.6)
            return "proxy" in (payload.get("proxies") or {})
        except Exception:
            return False

    def _append_log_header(self, title: str) -> None:
        try:
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2 * 1024 * 1024:
                LOG_FILE.write_text("", encoding="utf-8")
            with LOG_FILE.open("ab", buffering=0) as fh:
                fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {title} =====\n".encode("utf-8"))
        except OSError:
            pass

    def _check_config(self, path: Path) -> None:
        if not CORE_EXE.exists():
            raise RuntimeError(f"找不到代理核心：{CORE_EXE}")
        result = subprocess.run(
            [str(CORE_EXE), "check", "-c", str(path)],
            capture_output=True, text=True, timeout=15,
            creationflags=_hide_flags(), encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            detail = self._sanitize_text((result.stderr or result.stdout or "配置校验失败").strip())
            raise RuntimeError(detail[-1200:])

    def _read_log_tail(self, lines: int = 20, path: Path | None = None) -> str:
        target = path or LOG_FILE
        try:
            if not target.exists():
                return ""
            data = target.read_bytes()
            text = data[-160000:].decode("utf-8", errors="replace")
            clean = ANSI_RE.sub("", text)
            return self._sanitize_text("\n".join(clean.splitlines()[-lines:]))
        except OSError:
            return ""

    def _sanitize_text(self, text: str) -> str:
        secrets_to_hide: list[str] = []
        for node in self.store.nodes:
            for key in ("password", "uuid", "username", "obfs_password", "reality_public_key"):
                value = str(node.get(key) or "")
                if len(value) >= 3:
                    secrets_to_hide.append(value)
        secrets_to_hide.sort(key=len, reverse=True)
        result = text
        for value in secrets_to_hide:
            result = result.replace(value, "••••")
        result = re.sub(r"(?i)authorization:\s*bearer\s+\S+", "Authorization: Bearer ••••", result)
        return result

    def _log_snapshot(self) -> str:
        return self._read_log_tail(200)

    def _port_available(self, port: int) -> bool:
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False

    def _set_error(self, message: str) -> None:
        self._error = self._sanitize_text(message)
        self._state = "error"

    def _clear_runtime_refs(self) -> None:
        self._proc = None
        self._api_port = 0
        self._api_secret = ""
        self._node = None
        self._started_at = None

    def _terminate_process(self, proc: subprocess.Popen, timeout: float = 3.0) -> None:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        except Exception:
            pass

    def _stop_internal(self) -> None:
        self.traffic.stop()
        try:
            if self.system_proxy.enabled:
                self.system_proxy.restore()
        except Exception:
            pass
        proc = self._proc
        if proc:
            self._terminate_process(proc)
        self._clear_runtime_refs()

    # ---------- public operations ----------
    def connect(self) -> dict[str, Any]:
        with self._op_lock:
            with self._lock:
                if self._state == "connected" and self._proc and self._proc.poll() is None:
                    return self.status()
            node = self.store.selected_node()
            if not node:
                raise ValueError("还没有节点，请先导入节点")
            settings = self.store.settings
            if settings.get("tun_mode") and not self._admin:
                raise ValueError("TUN 模式需要管理员权限。请关闭 TUN，或右键使用「以管理员身份运行」启动轻连。")
            with self._lock:
                self._stop_internal()
                self._error = ""
                self._warning = ""
                self._state = "starting"
                self._node = node
                self._started_at = None
                local_port = int(settings.get("local_port") or 2080)
                if not self._port_available(local_port):
                    self._set_error(f"本地端口 {local_port} 已被占用，请在设置中换一个端口")
                    raise RuntimeError(self._error)
                self._api_port = free_port()
                self._api_secret = secrets.token_urlsafe(24)
                try:
                    config = build_config(node, settings, self._api_port, self._api_secret)
                    write_config(CONFIG_FILE, config)
                    self._check_config(CONFIG_FILE)
                    self._append_log_header(f"start {node.get('name', 'node')}")
                    with LOG_FILE.open("ab", buffering=0) as log_handle:
                        proc = subprocess.Popen(
                            [str(CORE_EXE), "run", "-c", str(CONFIG_FILE)],
                            cwd=str(CORE_DIR),
                            stdout=log_handle,
                            stderr=subprocess.STDOUT,
                            creationflags=_hide_flags(),
                            stdin=subprocess.DEVNULL,
                        )
                    self._proc = proc
                    if not wait_until(self._api_ready, timeout=10.0, interval=0.12):
                        code = proc.poll()
                        tail = self._read_log_tail(30)
                        self._stop_internal()
                        self._set_error(
                            (f"代理核心启动失败（退出码 {code}）。\n{tail}" if code is not None else f"代理核心启动超时。\n{tail}").strip()
                        )
                        raise RuntimeError(self._error)
                    self._state = "connected"
                    self._started_at = time.time()
                    self.traffic.start(self._api_port, self._api_secret)
                    if settings.get("system_proxy") and not settings.get("tun_mode"):
                        if not self.system_proxy.enable(local_port):
                            self._warning = "已连接，但系统代理设置失败。可以手动将系统代理设为 127.0.0.1:%d" % local_port
                    return self.status()
                except Exception as exc:
                    if self._state != "error":
                        self._stop_internal()
                        self._set_error(str(exc))
                    raise RuntimeError(self._error or str(exc)) from exc

    def disconnect(self) -> dict[str, Any]:
        with self._op_lock:
            with self._lock:
                self._state = "stopping"
                self._stop_internal()
                self._state = "disconnected"
                self._error = ""
                self._warning = ""
                return self.status()

    def restart_if_connected(self) -> None:
        with self._op_lock:
            with self._lock:
                active = self._state == "connected" or (self._proc is not None and self._proc.poll() is None)
            if active:
                self.disconnect()
                self.connect()

    def select_node(self, node_id: str) -> None:
        with self._lock:
            active = self._state == "connected" or (self._proc is not None and self._proc.poll() is None)
        self.store.set_selected(node_id)
        if active:
            with self._op_lock:
                self.disconnect()
                self.connect()

    def update_runtime_settings(self, old: dict[str, Any], new: dict[str, Any]) -> None:
        active = self._state in {"connected", "starting"}
        restart_keys = {"mode", "local_port", "tun_mode"}
        if active and any(old.get(k) != new.get(k) for k in restart_keys):
            self.restart_if_connected()
            return
        if active and self._state == "connected":
            if not new.get("tun_mode"):
                if new.get("system_proxy") and not self.system_proxy.enabled:
                    self.system_proxy.enable(int(new.get("local_port") or 2080))
                elif not new.get("system_proxy") and self.system_proxy.enabled:
                    self.system_proxy.restore()
            elif self.system_proxy.enabled:
                self.system_proxy.restore()

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._proc is not None and self._proc.poll() is not None and self._state in {"connected", "starting"}:
                tail = self._read_log_tail(20)
                self._stop_internal()
                self._set_error("代理核心已退出。" + (("\n" + tail) if tail else ""))
            node = self._node or self.store.selected_node()
            settings = self.store.settings
            uptime = int(time.time() - self._started_at) if self._started_at and self._state == "connected" else 0
            return {
                "state": self._state,
                "connected": self._state == "connected" and self._proc is not None and self._proc.poll() is None,
                "starting": self._state == "starting",
                "error": self._error,
                "warning": self._warning,
                "node_id": node.get("id") if node else None,
                "node_name": node.get("name") if node else None,
                "node_protocol": protocol_label(str(node.get("type") or "")) if node else None,
                "mode": settings.get("mode"),
                "tun_mode": bool(settings.get("tun_mode")),
                "local_port": int(settings.get("local_port") or 2080),
                "system_proxy": self.system_proxy.enabled,
                "uptime": uptime,
                "is_admin": self._admin,
                "core_version": getattr(self, "_core_version", "未知"),
                "core_ready": CORE_EXE.exists(),
                "traffic": self.traffic.snapshot(),
            }

    def logs(self, lines: int = 200) -> str:
        return self._sanitize_text(self._read_log_tail(max(20, min(int(lines or 200), 1000))))

    def test_nodes(self, node_ids: list[str] | None = None) -> list[dict[str, Any]]:
        with self._test_lock:
            selected = self.store.nodes
            if node_ids:
                wanted = set(node_ids)
                selected = [n for n in selected if n.get("id") in wanted]
            if not selected:
                raise ValueError("没有可测试的节点")
            settings = self.store.settings
            test_url = str(settings.get("test_url") or "https://www.gstatic.com/generate_204")
            valid_nodes: list[dict[str, Any]] = []
            results_by_id: dict[str, dict[str, Any]] = {}
            for node in selected:
                try:
                    build_outbound(node, f"node-{len(valid_nodes)}")
                    valid_nodes.append(node)
                except Exception as exc:
                    results_by_id[str(node.get("id"))] = {
                        "id": node.get("id"), "name": node.get("name"), "delay": None,
                        "error": f"不支持：{exc}",
                    }
            if not valid_nodes:
                return list(results_by_id.values())
            api_port = free_port()
            test_secret = secrets.token_urlsafe(24)
            inbound_port = free_port()
            config = build_test_config(valid_nodes, api_port, test_secret, inbound_port)
            write_config(TEST_CONFIG_FILE, config)
            self._check_config(TEST_CONFIG_FILE)
            header = f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} test latency =====\n"
            with TEST_LOG_FILE.open("wb") as test_log:
                test_log.write(header.encode("utf-8"))
                try:
                    proc = subprocess.Popen(
                        [str(CORE_EXE), "run", "-c", str(TEST_CONFIG_FILE)],
                        cwd=str(CORE_DIR), stdout=test_log, stderr=subprocess.STDOUT,
                        creationflags=_hide_flags(), stdin=subprocess.DEVNULL,
                    )
                except Exception as exc:
                    raise RuntimeError(f"测速核心启动失败：{exc}") from exc
            try:
                expected_tags = {f"node-{index}" for index in range(len(valid_nodes))}

                def api_ready() -> bool:
                    try:
                        req = urllib.request.Request(
                            f"http://127.0.0.1:{api_port}/proxies",
                            headers={"Authorization": f"Bearer {test_secret}"},
                        )
                        with urllib.request.urlopen(req, timeout=0.5) as response:
                            payload = json.loads(response.read().decode("utf-8", errors="replace"))
                        return expected_tags.issubset(set((payload.get("proxies") or {}).keys()))
                    except Exception:
                        return False
                if not wait_until(api_ready, timeout=10.0, interval=0.15):
                    tail = self._read_log_tail(25, TEST_LOG_FILE)
                    raise RuntimeError("测速核心启动失败。" + (("\n" + tail) if tail else ""))
                delay_base = (
                    f"http://127.0.0.1:{api_port}/proxies/"
                    "{tag}/delay?url=" + urllib.parse.quote(test_url, safe="")
                    + f"&timeout={TEST_DELAY_TIMEOUT_MS}"
                )

                def probe_once(index: int) -> tuple[int | None, str | None]:
                    req = urllib.request.Request(
                        delay_base.format(tag=f"node-{index}"),
                        headers={"Authorization": f"Bearer {test_secret}"},
                    )
                    try:
                        with urllib.request.urlopen(req, timeout=TEST_DELAY_CLIENT_TIMEOUT) as response:
                            payload = json.loads(response.read().decode("utf-8", errors="replace"))
                        delay = payload.get("delay")
                        return (int(delay) if isinstance(delay, (int, float)) else None), None
                    except urllib.error.HTTPError as exc:
                        # 节点确实连不通时，sing-box 会带着 {"message": "..."} 返回 5xx
                        try:
                            body = exc.read().decode("utf-8", errors="replace")
                        except Exception:
                            body = ""
                        return None, _describe_delay_error(body or str(exc))
                    except Exception as exc:
                        # Clash API 自己没应答：这是本地测速核心的问题，不该记到节点头上
                        return None, f"测速核心无响应（{type(exc).__name__}）"

                def probe(index: int, node: dict[str, Any]) -> tuple[dict[str, Any], int | None, str | None]:
                    error: str | None = None
                    for attempt in range(TEST_DELAY_RETRIES + 1):
                        delay, error = probe_once(index)
                        if delay is not None:
                            return node, delay, None
                        if attempt < TEST_DELAY_RETRIES:
                            time.sleep(0.4)
                    return node, None, error

                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(TEST_DELAY_CONCURRENCY, len(valid_nodes))
                ) as executor:
                    for node, delay, error in executor.map(lambda pair: probe(*pair), list(enumerate(valid_nodes))):
                        now = int(time.time())
                        if delay is not None:
                            self.store.update_node(str(node.get("id")), {"delay": delay, "tested_at": now})
                        else:
                            self.store.update_node(str(node.get("id")), {"delay": -1, "tested_at": now})
                        results_by_id[str(node.get("id"))] = {
                            "id": node.get("id"), "name": node.get("name"),
                            "delay": delay, "error": error,
                        }
                return [results_by_id[n["id"]] for n in selected if n.get("id") in results_by_id]
            finally:
                proc = locals().get("proc")
                if proc is not None:
                    self._terminate_process(proc)

    def shutdown(self) -> None:
        self._monitor_stop.set()
        with self._op_lock:
            with self._lock:
                self._stop_internal()
                self._state = "disconnected"
        try:
            self.system_proxy.restore()
        except Exception:
            pass

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.wait(1.0):
            try:
                with self._lock:
                    proc = self._proc
                    if proc is not None and proc.poll() is not None and self._state in {"connected", "starting"}:
                        tail = self._read_log_tail(20)
                        self._stop_internal()
                        self._set_error("代理核心已退出。" + (("\n" + tail) if tail else ""))
            except Exception:
                pass
