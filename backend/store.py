from __future__ import annotations

import copy
import hashlib
import threading
import uuid
from typing import Any

from .util import ROOT, STORE_FILE, atomic_write_json, normalize_domains, read_json

APP_VERSION = "1.2.6"

DEFAULT_SETTINGS: dict[str, Any] = {
    "local_port": 2080,
    "mode": "rule",
    "system_proxy": True,
    "tun_mode": False,
    "close_to_tray": True,
    "autostart": False,
    "auto_connect": False,
    "test_url": "https://www.gstatic.com/generate_204",
    "v2rayn_path": str(ROOT.parent / "v2rayN-windows-64"),
    "direct_domains": [],
    "proxy_domains": [],
}

DEFAULT_STORE: dict[str, Any] = {
    "version": 1,
    "settings": copy.deepcopy(DEFAULT_SETTINGS),
    "nodes": [],
    "subscriptions": [],
    "selected_id": None,
    "imported_v2rayn_at": None,
}


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        data = read_json(STORE_FILE, {})
        if not isinstance(data, dict):
            data = {}
        merged = copy.deepcopy(DEFAULT_STORE)
        merged.update({k: v for k, v in data.items() if k in merged})
        if not isinstance(merged.get("settings"), dict):
            merged["settings"] = {}
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings.update(merged["settings"])
        merged["settings"] = settings
        if not isinstance(merged.get("nodes"), list):
            merged["nodes"] = []
        if not isinstance(merged.get("subscriptions"), list):
            merged["subscriptions"] = []
        return merged

    def save(self) -> None:
        with self._lock:
            atomic_write_json(STORE_FILE, self._data)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    @property
    def settings(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data["settings"])

    @property
    def nodes(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._data["nodes"])

    @property
    def subscriptions(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._data["subscriptions"])

    @property
    def selected_id(self) -> str | None:
        with self._lock:
            return self._data.get("selected_id")

    def selected_node(self) -> dict[str, Any] | None:
        with self._lock:
            selected_id = self._data.get("selected_id")
            for node in self._data["nodes"]:
                if node.get("id") == selected_id:
                    return copy.deepcopy(node)
            return copy.deepcopy(self._data["nodes"][0]) if self._data["nodes"] else None

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        with self._lock:
            for node in self._data["nodes"]:
                if node.get("id") == node_id:
                    return copy.deepcopy(node)
            return None

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_SETTINGS)
        with self._lock:
            for key, value in values.items():
                if key not in allowed:
                    continue
                if key in {"local_port"}:
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        raise ValueError("端口必须是数字")
                    if not 1024 <= value <= 65535:
                        raise ValueError("端口需要在 1024 到 65535 之间")
                elif key in {"system_proxy", "tun_mode", "close_to_tray", "autostart", "auto_connect"}:
                    value = bool(value)
                elif key == "mode":
                    if value not in {"rule", "global", "direct"}:
                        raise ValueError("未知代理模式")
                elif key in {"direct_domains", "proxy_domains"}:
                    value = normalize_domains(value)
                elif key in {"test_url", "v2rayn_path"}:
                    value = str(value).strip()
                self._data["settings"][key] = value
            self.save()
            return copy.deepcopy(self._data["settings"])

    def set_selected(self, node_id: str | None) -> None:
        with self._lock:
            if node_id and not any(n.get("id") == node_id for n in self._data["nodes"]):
                raise ValueError("节点不存在")
            self._data["selected_id"] = node_id
            self.save()

    def add_node(self, node: dict[str, Any]) -> dict[str, Any]:
        node = copy.deepcopy(node)
        node.setdefault("id", uuid.uuid4().hex)
        node.setdefault("name", "未命名节点")
        with self._lock:
            existing_names = {n.get("name") for n in self._data["nodes"]}
            base_name = str(node["name"])
            name = base_name
            suffix = 2
            while name in existing_names:
                name = f"{base_name} ({suffix})"
                suffix += 1
            node["name"] = name
            node["created_at"] = int(__import__("time").time())
            self._data["nodes"].append(node)
            if not self._data.get("selected_id"):
                self._data["selected_id"] = node["id"]
            self.save()
            return copy.deepcopy(node)

    def update_node(self, node_id: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            for node in self._data["nodes"]:
                if node.get("id") == node_id:
                    for key, value in values.items():
                        if key in {"name", "delay", "tested_at"}:
                            node[key] = value
                    self.save()
                    return copy.deepcopy(node)
            raise ValueError("节点不存在")

    def delete_node(self, node_id: str) -> None:
        with self._lock:
            before = len(self._data["nodes"])
            self._data["nodes"] = [n for n in self._data["nodes"] if n.get("id") != node_id]
            if len(self._data["nodes"]) == before:
                raise ValueError("节点不存在")
            if self._data.get("selected_id") == node_id:
                self._data["selected_id"] = self._data["nodes"][0]["id"] if self._data["nodes"] else None
            self.save()

    def replace_source_nodes(self, nodes: list[dict[str, Any]], source: str, subscription_id: str | None = None,
                             default_selected: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            old_selected = self._data.get("selected_id")
            previous = {
                str(n.get("id")): n for n in self._data["nodes"]
                if (n.get("subscription_id") == subscription_id if subscription_id else n.get("source") == source)
            }
            if subscription_id:
                self._data["nodes"] = [n for n in self._data["nodes"] if n.get("subscription_id") != subscription_id]
            else:
                self._data["nodes"] = [n for n in self._data["nodes"] if n.get("source") != source]
            added: list[dict[str, Any]] = []
            existing_names = {n.get("name") for n in self._data["nodes"]}
            import time
            for incoming in nodes:
                node = copy.deepcopy(incoming)
                if subscription_id:
                    identity = "|".join(str(node.get(k) or "") for k in ("type", "server", "port", "name", "uuid", "password"))
                    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:18]
                    node["id"] = f"sub-{subscription_id[:8]}-{digest}"
                else:
                    node.setdefault("id", uuid.uuid4().hex)
                old_node = previous.get(str(node.get("id")))
                if old_node:
                    for carry_key in ("delay", "tested_at"):
                        if carry_key in old_node:
                            node[carry_key] = old_node[carry_key]
                node["source"] = source
                if subscription_id:
                    node["subscription_id"] = subscription_id
                base_name = str(node.get("name") or "未命名节点")
                name = base_name
                suffix = 2
                while name in existing_names:
                    name = f"{base_name} ({suffix})"
                    suffix += 1
                node["name"] = name
                existing_names.add(name)
                node.setdefault("created_at", int(time.time()))
                self._data["nodes"].append(node)
                added.append(copy.deepcopy(node))
            all_ids = {n.get("id") for n in self._data["nodes"]}
            if old_selected in all_ids:
                self._data["selected_id"] = old_selected
            elif default_selected and added:
                self._data["selected_id"] = added[0]["id"]
            else:
                self._data["selected_id"] = self._data["nodes"][0]["id"] if self._data["nodes"] else None
            self.save()
            return added

    def upsert_subscription(self, sub: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            for item in self._data["subscriptions"]:
                if item.get("id") == sub.get("id"):
                    item.update(sub)
                    self.save()
                    return copy.deepcopy(item)
            sub = copy.deepcopy(sub)
            sub.setdefault("id", uuid.uuid4().hex)
            self._data["subscriptions"].append(sub)
            self.save()
            return copy.deepcopy(sub)

    def delete_subscription(self, sub_id: str) -> None:
        with self._lock:
            self._data["subscriptions"] = [s for s in self._data["subscriptions"] if s.get("id") != sub_id]
            self._data["nodes"] = [n for n in self._data["nodes"] if n.get("subscription_id") != sub_id]
            all_ids = {n.get("id") for n in self._data["nodes"]}
            if self._data.get("selected_id") not in all_ids:
                self._data["selected_id"] = self._data["nodes"][0]["id"] if self._data["nodes"] else None
            self.save()

    def set_v2rayn_imported(self, timestamp: int) -> None:
        with self._lock:
            self._data["imported_v2rayn_at"] = timestamp
            self.save()
