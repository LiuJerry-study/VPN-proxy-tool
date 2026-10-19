from __future__ import annotations

import ctypes
import json
import mimetypes
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import autostart, clipboard
from .core_manager import CoreManager, is_admin
from .nodes import fetch_subscription, import_v2rayn, node_to_share_link, parse_import_text, public_node
from .store import APP_VERSION, Store
from .system_proxy import SystemProxy
from .traffic import TrafficMonitor
from .util import DATA_DIR, ROOT, RUNTIME_DIR, WEB_DIR, atomic_write_json, read_json


class QingLianServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: "Application") -> None:
        self.app = app
        super().__init__(address, ApiHandler)


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "QingLian/1.0"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        try:
            self.server.app.handle_get(self)
        except Exception as exc:
            self.server.app.handle_exception(self, exc)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self.server.app.handle_post(self)
        except Exception as exc:
            self.server.app.handle_exception(self, exc)

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()


class Application:
    def __init__(self, on_quit=None) -> None:
        self.on_quit = on_quit
        self.store = Store()
        self.system_proxy = SystemProxy()
        self.traffic = TrafficMonitor()
        self.core = CoreManager(self.store, self.system_proxy, self.traffic)
        self.httpd: QingLianServer | None = None
        self.port = 0
        self._shutdown = threading.Event()
        self._bootstrap()

    # ---------- lifecycle ----------
    def _bootstrap(self) -> None:
        settings = self.store.settings
        configured = Path(str(settings.get("v2rayn_path") or ""))
        if not configured.exists():
            sibling = ROOT.parent / "v2rayN-windows-64"
            if sibling.exists():
                try:
                    self.store.update_settings({"v2rayn_path": str(sibling)})
                except Exception:
                    pass
        if not self.store.nodes and not self.store.snapshot().get("imported_v2rayn_at"):
            self._auto_import_v2rayn(silent=True)

    def _auto_import_v2rayn(self, silent: bool = False) -> int:
        settings = self.store.settings
        path = str(settings.get("v2rayn_path") or "")
        if not path or not Path(path).exists():
            if not silent:
                raise ValueError("找不到 v2rayN 文件夹，请检查路径")
            return 0
        nodes, _ = import_v2rayn(path)
        if not nodes:
            if not silent:
                raise ValueError("v2rayN 中没有找到受支持的节点")
            return 0
        self.store.replace_source_nodes(nodes, "v2rayn")
        self.store.set_v2rayn_imported(int(time.time()))
        if not silent:
            self.store.update_settings({"v2rayn_path": path})
        return len(nodes)

    def start_server(self) -> int:
        self.httpd = QingLianServer(("127.0.0.1", 0), self)
        self.port = int(self.httpd.server_address[1])
        thread = threading.Thread(target=self.httpd.serve_forever, name="http-server", daemon=True)
        thread.start()
        relaunch = read_json(RUNTIME_DIR / "relaunch.json", {})
        connect_after_start = bool(isinstance(relaunch, dict) and relaunch.get("connect_after_start"))
        try:
            (RUNTIME_DIR / "relaunch.json").unlink()
        except OSError:
            pass
        if (connect_after_start or self.store.settings.get("auto_connect")) and self.store.nodes:
            threading.Timer(0.8, self._auto_connect).start()
        return self.port

    def _auto_connect(self) -> None:
        try:
            self.core.connect()
        except Exception:
            pass

    def shutdown(self) -> None:
        if self._shutdown.is_set():
            return
        self._shutdown.set()
        try:
            self.core.shutdown()
        except Exception:
            pass
        if self.httpd:
            try:
                self.httpd.shutdown()
            except Exception:
                pass
            try:
                self.httpd.server_close()
            except Exception:
                pass

    # ---------- HTTP helpers ----------
    def _send_json(self, handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        handler.wfile.write(body)

    def _ok(self, handler: BaseHTTPRequestHandler, **payload: Any) -> None:
        payload.setdefault("ok", True)
        self._send_json(handler, 200, payload)

    def _error(self, handler: BaseHTTPRequestHandler, message: str, status: int = 400) -> None:
        self._send_json(handler, status, {"ok": False, "error": message})

    def _read_json(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        try:
            length = int(handler.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > 2 * 1024 * 1024:
            raise ValueError("请求体过大")
        if length <= 0:
            return {}
        raw = handler.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise ValueError("请求数据格式错误") from exc
        if not isinstance(data, dict):
            raise ValueError("请求数据格式错误")
        return data

    def handle_exception(self, handler: BaseHTTPRequestHandler, exc: Exception) -> None:
        if isinstance(exc, (ValueError, FileNotFoundError)):
            message = str(exc)
            status = 400
        elif isinstance(exc, RuntimeError):
            message = str(exc)
            status = 400
        else:
            message = "操作失败，请查看运行日志"
            status = 500
        try:
            self._error(handler, message, status)
        except Exception:
            pass

    # ---------- state ----------
    def _state_payload(self) -> dict[str, Any]:
        settings = self.store.settings
        nodes = self.store.nodes
        subs = self.store.subscriptions
        for sub in subs:
            sub["node_count"] = sum(1 for n in nodes if n.get("subscription_id") == sub.get("id"))
        v2rayn_path = str(settings.get("v2rayn_path") or "")
        state = {
            "version": APP_VERSION,
            "nodes": [public_node(n) for n in nodes],
            "selected_id": self.store.selected_id,
            "settings": settings,
            "subscriptions": subs,
            "status": self.core.status(),
            "is_admin": is_admin(),
            "autostart_enabled": autostart.is_enabled(),
            "v2rayn_available": bool(v2rayn_path and Path(v2rayn_path).exists()),
            "imported_v2rayn_at": self.store.snapshot().get("imported_v2rayn_at"),
            "data_dir": str(DATA_DIR),
        }
        return state

    def handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urllib.parse.urlsplit(handler.path)
        path = parsed.path
        if path == "/api/health":
            self._ok(handler, app="qinglian", version=APP_VERSION)
            return
        if path == "/api/state":
            self._ok(handler, **self._state_payload())
            return
        if path == "/api/status":
            self._ok(handler, status=self.core.status())
            return
        if path == "/api/logs":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                lines = int((query.get("lines") or ["200"])[0])
            except ValueError:
                lines = 200
            self._ok(handler, logs=self.core.logs(lines))
            return
        if path.startswith("/api/"):
            self._error(handler, "接口不存在", 404)
            return
        self.serve_static(handler, path)

    def serve_static(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        if path in {"", "/"}:
            relative = "index.html"
        else:
            relative = urllib.parse.unquote(path).lstrip("/")
        base = WEB_DIR.resolve()
        target = (base / relative).resolve()
        if not str(target).startswith(str(base) + os.sep):
            self._error(handler, "文件不存在", 404)
            return
        if not target.is_file():
            self._error(handler, "文件不存在", 404)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        data = target.read_bytes()
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        if target.name == "index.html":
            handler.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self';",
            )
        handler.end_headers()
        handler.wfile.write(data)

    # ---------- API actions ----------
    def handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        path = urllib.parse.urlsplit(handler.path).path
        data = self._read_json(handler)
        if path == "/api/connect":
            status = self.core.connect()
            self._ok(handler, status=status)
            return
        if path == "/api/disconnect":
            status = self.core.disconnect()
            self._ok(handler, status=status)
            return
        if path == "/api/node/select":
            node_id = str(data.get("id") or "")
            if not node_id:
                raise ValueError("缺少节点 ID")
            self.core.select_node(node_id)
            self._ok(handler, state=self._state_payload())
            return
        if path == "/api/node/rename":
            node_id = str(data.get("id") or "")
            name = str(data.get("name") or "").strip()
            if not node_id:
                raise ValueError("缺少节点 ID")
            if not name or len(name) > 80:
                raise ValueError("名称不能为空，且不能超过 80 个字符")
            self.store.update_node(node_id, {"name": name})
            self._ok(handler, state=self._state_payload())
            return
        if path == "/api/node/delete":
            node_id = str(data.get("id") or "")
            if not node_id:
                raise ValueError("缺少节点 ID")
            current = self.core.status()
            if current.get("node_id") == node_id and current.get("connected"):
                self.core.disconnect()
            self.store.delete_node(node_id)
            self._ok(handler, state=self._state_payload())
            return
        if path == "/api/nodes/test":
            ids = data.get("ids")
            if ids is not None and not isinstance(ids, list):
                raise ValueError("节点列表格式错误")
            results = self.core.test_nodes([str(x) for x in ids] if ids else None)
            self._ok(handler, results=results, state=self._state_payload())
            return
        if path == "/api/nodes/import":
            text = str(data.get("text") or "")
            nodes = parse_import_text(text)
            if not nodes:
                raise ValueError("没有识别到受支持的节点链接。支持 VLESS、VMess、Trojan、Hysteria2、TUIC、Shadowsocks、SOCKS 和 HTTP。")
            added = [self.store.add_node(n) for n in nodes]
            self._ok(handler, count=len(added), state=self._state_payload())
            return
        if path == "/api/clipboard/import":
            self._import_from_clipboard(handler)
            return
        if path == "/api/clipboard/export":
            self._export_to_clipboard(handler)
            return
        if path == "/api/import/v2rayn":
            path_value = str(data.get("path") or self.store.settings.get("v2rayn_path") or "").strip()
            if path_value:
                self.store.update_settings({"v2rayn_path": path_value})
            count = self._auto_import_v2rayn(silent=False)
            self._ok(handler, count=count, state=self._state_payload())
            return
        if path == "/api/subscriptions/add":
            self._add_subscription(handler, data)
            return
        if path == "/api/subscriptions/update":
            self._update_subscription(handler, data)
            return
        if path == "/api/subscriptions/delete":
            self._delete_subscription(handler, data)
            return
        if path == "/api/settings":
            self._update_settings(handler, data)
            return
        if path == "/api/logs/clear":
            try:
                from .util import LOG_FILE
                LOG_FILE.write_text("", encoding="utf-8")
            except OSError:
                pass
            self._ok(handler)
            return
        if path == "/api/relaunch-admin":
            self._relaunch_admin(handler, data)
            return
        if path == "/api/data/open":
            if os.name != "nt":
                raise RuntimeError("打开数据目录仅支持 Windows")
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                os.startfile(str(DATA_DIR))
            except OSError as exc:
                raise RuntimeError(f"无法打开数据目录：{exc}") from exc
            self._ok(handler)
            return
        if path == "/api/quit":
            self._ok(handler)
            if self.on_quit:
                timer = threading.Timer(0.25, self.on_quit)
                timer.daemon = False
                timer.start()
            return
        self._error(handler, "接口不存在", 404)

    @staticmethod
    def _node_identity(node: dict[str, Any]) -> tuple[str, ...]:
        fields = ("type", "server", "port", "uuid", "username", "password", "method",
                  "network", "ws_host", "ws_path", "grpc_service", "sni", "flow")
        return tuple(str(node.get(key) or "").strip().lower() for key in fields)

    def _import_from_clipboard(self, handler: BaseHTTPRequestHandler) -> None:
        text = clipboard.read_text().strip()
        if not text:
            raise ValueError("剪贴板为空")
        if re.fullmatch(r"https?://\S+", text, re.I):
            nodes, _ = fetch_subscription(text)
            if not nodes:
                raise ValueError("剪贴板中的订阅没有识别到受支持的节点")
            existing = next((s for s in self.store.subscriptions if s.get("url") == text), None)
            if existing:
                was_connected = bool(self.core.status().get("connected"))
                self.store.replace_source_nodes(nodes, f"subscription:{existing['id']}", subscription_id=existing["id"])
                existing.update({"node_count": len(nodes), "updated_at": int(time.time())})
                self.store.upsert_subscription(existing)
                if was_connected:
                    self.core.restart_if_connected()
                self._ok(handler, mode="subscription", updated=True, count=len(nodes), state=self._state_payload())
                return
            sub_id = uuid.uuid4().hex
            sub = {
                "id": sub_id,
                "name": f"剪贴板订阅 {len(self.store.subscriptions) + 1}",
                "url": text,
                "node_count": len(nodes),
                "updated_at": int(time.time()),
            }
            self.store.upsert_subscription(sub)
            self.store.replace_source_nodes(nodes, f"subscription:{sub_id}", subscription_id=sub_id)
            self._ok(handler, mode="subscription", updated=False, count=len(nodes), state=self._state_payload())
            return

        parsed = parse_import_text(text)
        if not parsed:
            raise ValueError("剪贴板中没有识别到受支持的节点链接或订阅地址")
        existing_keys = {self._node_identity(node) for node in self.store.nodes}
        added = 0
        skipped = 0
        for node in parsed:
            key = self._node_identity(node)
            if key in existing_keys:
                skipped += 1
                continue
            self.store.add_node(node)
            existing_keys.add(key)
            added += 1
        self._ok(handler, mode="nodes", count=added, skipped=skipped, state=self._state_payload())

    def _export_to_clipboard(self, handler: BaseHTTPRequestHandler) -> None:
        nodes = self.store.nodes
        if not nodes:
            raise ValueError("当前没有可导出的节点")
        links: list[str] = []
        skipped = 0
        for node in nodes:
            try:
                link = node_to_share_link(node)
            except Exception:
                link = ""
            if link:
                links.append(link)
            else:
                skipped += 1
        unique_links = list(dict.fromkeys(links))
        skipped += len(links) - len(unique_links)
        if not unique_links:
            raise ValueError("当前节点都不支持导出分享链接")
        clipboard.write_text("\n".join(unique_links))
        self._ok(handler, count=len(unique_links), skipped=skipped)

    def _add_subscription(self, handler: BaseHTTPRequestHandler, data: dict[str, Any]) -> None:
        url = str(data.get("url") or "").strip()
        name = str(data.get("name") or "").strip()
        if not url:
            raise ValueError("请输入订阅地址")
        nodes, _ = fetch_subscription(url)
        if not nodes:
            raise ValueError("订阅中没有识别到受支持的节点")
        sub_id = uuid.uuid4().hex
        sub = {
            "id": sub_id,
            "name": name or f"订阅 {len(self.store.subscriptions) + 1}",
            "url": url,
            "node_count": len(nodes),
            "updated_at": int(time.time()),
        }
        self.store.upsert_subscription(sub)
        self.store.replace_source_nodes(nodes, f"subscription:{sub_id}", subscription_id=sub_id)
        self._ok(handler, count=len(nodes), state=self._state_payload())

    def _update_subscription(self, handler: BaseHTTPRequestHandler, data: dict[str, Any]) -> None:
        sub_id = str(data.get("id") or "")
        if not sub_id:
            raise ValueError("缺少订阅 ID")
        sub = next((s for s in self.store.subscriptions if s.get("id") == sub_id), None)
        if not sub:
            raise ValueError("订阅不存在")
        url = str(data.get("url") or sub.get("url") or "").strip()
        name = str(data.get("name") or sub.get("name") or "订阅").strip()
        nodes, _ = fetch_subscription(url)
        if not nodes:
            raise ValueError("订阅中没有识别到受支持的节点")
        was_connected = bool(self.core.status().get("connected"))
        self.store.replace_source_nodes(nodes, f"subscription:{sub_id}", subscription_id=sub_id)
        sub.update({"name": name, "url": url, "node_count": len(nodes), "updated_at": int(time.time())})
        self.store.upsert_subscription(sub)
        if was_connected:
            self.core.restart_if_connected()
        self._ok(handler, count=len(nodes), state=self._state_payload())

    def _delete_subscription(self, handler: BaseHTTPRequestHandler, data: dict[str, Any]) -> None:
        sub_id = str(data.get("id") or "")
        if not sub_id:
            raise ValueError("缺少订阅 ID")
        current = self.core.status()
        nodes = [n for n in self.store.nodes if n.get("subscription_id") == sub_id]
        if current.get("connected") and any(n.get("id") == current.get("node_id") for n in nodes):
            self.core.disconnect()
        self.store.delete_subscription(sub_id)
        self._ok(handler, state=self._state_payload())

    def _update_settings(self, handler: BaseHTTPRequestHandler, data: dict[str, Any]) -> None:
        old = self.store.settings
        values = dict(data)
        if "autostart" in values:
            enabled = bool(values["autostart"])
            if not autostart.set_enabled(enabled):
                raise RuntimeError("无法写入开机启动项")
        new = self.store.update_settings(values)
        if new.get("tun_mode") and not is_admin():
            # The preference is saved for the next elevated launch. Restarting the core here
            # would fail, so disconnect cleanly and let the UI request elevation.
            if self.core.status().get("connected"):
                try:
                    self.core.disconnect()
                except Exception:
                    pass
            self._ok(handler, settings=new, requires_admin=True, state=self._state_payload())
            return
        try:
            self.core.update_runtime_settings(old, new)
        except Exception:
            pass
        self._ok(handler, settings=new, state=self._state_payload())

    def _relaunch_admin(self, handler: BaseHTTPRequestHandler, data: dict[str, Any]) -> None:
        if is_admin():
            raise ValueError("当前已经是管理员权限")
        connect_after_start = bool(data.get("connect_after_start"))
        if getattr(sys, "frozen", False):
            exe = str(Path(sys.executable).resolve())
            params = ""
        else:
            python = Path(sys.executable)
            pythonw = python.with_name("pythonw.exe")
            exe = str((pythonw if pythonw.exists() else python).resolve())
            params = f'"{Path(ROOT / "app.py").resolve()}"'
        if connect_after_start:
            try:
                self.core.disconnect()
            except Exception:
                pass
        relaunch_file = RUNTIME_DIR / "relaunch.json"
        atomic_write_json(relaunch_file, {"connect_after_start": connect_after_start})
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, str(ROOT), 1)
        if int(result) <= 32:
            try:
                relaunch_file.unlink()
            except OSError:
                pass
            raise RuntimeError("无法请求管理员权限")
        self._ok(handler)
        if self.on_quit:
            timer = threading.Timer(0.4, self.on_quit)
            timer.daemon = False
            timer.start()
