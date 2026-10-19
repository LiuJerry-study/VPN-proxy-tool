from __future__ import annotations

import base64
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .util import as_list, b64decode_flexible, parse_bool, split_host_port

PROTOCOL_LABELS = {
    "vmess": "VMess",
    "vless": "VLESS",
    "trojan": "Trojan",
    "shadowsocks": "Shadowsocks",
    "hysteria2": "Hysteria2",
    "tuic": "TUIC",
    "socks": "SOCKS",
    "http": "HTTP",
    "anytls": "AnyTLS",
    "wireguard": "WireGuard",
    "naive": "Naive",
}

PUBLIC_NODE_FIELDS = {
    "id", "name", "type", "server", "port", "source", "subscription_id", "delay", "tested_at",
    "created_at", "network", "tls", "sni",
}


def protocol_label(node_type: str) -> str:
    return PROTOCOL_LABELS.get(node_type, (node_type or "未知").upper())


def public_node(node: dict[str, Any]) -> dict[str, Any]:
    result = {key: node.get(key) for key in PUBLIC_NODE_FIELDS if key in node}
    result["protocol"] = protocol_label(str(node.get("type") or ""))
    result["tls"] = bool(node.get("tls"))
    if not result.get("name"):
        result["name"] = f"{node.get('server', '')}:{node.get('port', '')}"
    return result


def _new_node(node_type: str, **kwargs: Any) -> dict[str, Any]:
    node = {
        "type": node_type,
        "name": "",
        "server": "",
        "port": 443,
        "source": "manual",
        "network": "tcp",
        "tls": False,
        "sni": "",
        "alpn": [],
        "allow_insecure": False,
        "fingerprint": "",
        "ws_path": "",
        "ws_host": "",
        "grpc_service": "",
        "http_path": "",
        "http_host": "",
        "flow": "",
        "uuid": "",
        "password": "",
        "username": "",
        "method": "",
        "security": "",
        "alter_id": 0,
        "plugin": "",
        "plugin_opts": "",
        "obfs": "",
        "obfs_password": "",
        "congestion_control": "",
        "udp_relay_mode": "",
        "up_mbps": 0,
        "down_mbps": 0,
        "reality_public_key": "",
        "reality_short_id": "",
        "reality_spider_x": "",
        "delay": None,
        "tested_at": None,
    }
    node.update(kwargs)
    node["port"] = int(node.get("port") or 443)
    node["alpn"] = as_list(node.get("alpn"))
    node["tls"] = bool(node.get("tls"))
    node["allow_insecure"] = bool(node.get("allow_insecure"))
    if not node["name"]:
        node["name"] = f"{node['server']}:{node['port']}"
    return node


def _q(query: dict[str, list[str]], *keys: str, default: str = "") -> str:
    for key in keys:
        values = query.get(key)
        if values:
            return str(values[0])
    return default


def _bool_q(query: dict[str, list[str]], *keys: str) -> bool:
    return any(parse_bool(_q(query, key)) for key in keys)


def _raw_userinfo(parsed: urllib.parse.SplitResult) -> str:
    netloc = parsed.netloc
    if "@" not in netloc:
        return ""
    return urllib.parse.unquote(netloc.rsplit("@", 1)[0])


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _transport_from_query(query: dict[str, list[str]], node: dict[str, Any]) -> dict[str, Any]:
    network = _q(query, "type", default="tcp").lower()
    if network in {"raw", "tcp", ""}:
        network = "tcp"
    elif network in {"grpc", "gun"}:
        network = "grpc"
    elif network in {"http", "h2"}:
        network = "http"
    elif network == "xhttp":
        network = "httpupgrade"
    node["network"] = network
    if network in {"ws", "httpupgrade"}:
        node["ws_host"] = _q(query, "host")
        node["ws_path"] = _q(query, "path", default="/")
    elif network == "grpc":
        node["grpc_service"] = _q(query, "serviceName", "service_name", "path")
        node["ws_host"] = _q(query, "authority", "host")
    elif network == "http":
        node["http_host"] = _q(query, "host")
        node["http_path"] = _q(query, "path", default="/")
    return node


def _tls_from_query(query: dict[str, list[str]], node: dict[str, Any]) -> dict[str, Any]:
    security = _q(query, "security").lower()
    sni = _q(query, "sni", "peer", "servername")
    node["sni"] = sni
    node["alpn"] = as_list(_q(query, "alpn"))
    node["fingerprint"] = _q(query, "fp")
    node["allow_insecure"] = _bool_q(query, "allowInsecure", "insecure", "allow_insecure", "skip-cert-verify")
    node["reality_public_key"] = _q(query, "pbk", "public_key")
    node["reality_short_id"] = _q(query, "sid", "short_id")
    node["reality_spider_x"] = _q(query, "spx", "spider_x")
    node["tls"] = bool(security in {"tls", "reality", "xtls"} or node["reality_public_key"])
    node["security_mode"] = security
    return node


def parse_share_link(link: str) -> dict[str, Any] | None:
    link = link.strip()
    if not link or "://" not in link:
        return None
    try:
        parsed = urllib.parse.urlsplit(link)
        scheme = parsed.scheme.lower()
        name = urllib.parse.unquote(parsed.fragment or "").strip()
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if scheme == "vmess":
            return _parse_vmess(link, name)
        if scheme == "ss":
            return _parse_ss(link, name)
        host = parsed.hostname or ""
        port = parsed.port or (443 if scheme in {"vless", "trojan", "hy2", "hysteria2", "tuic", "anytls"} else 1080)
        if not host:
            return None
        if scheme == "vless":
            user = _raw_userinfo(parsed)
            node = _new_node("vless", name=name, server=host, port=port, uuid=user)
            node["flow"] = _q(query, "flow")
            node["encryption"] = _q(query, "encryption", default="none")
            _tls_from_query(query, node)
            _transport_from_query(query, node)
            return node
        if scheme == "trojan":
            user = _raw_userinfo(parsed)
            node = _new_node("trojan", name=name, server=host, port=port, password=user)
            node["flow"] = _q(query, "flow")
            _tls_from_query(query, node)
            _transport_from_query(query, node)
            return node
        if scheme in {"hysteria2", "hy2"}:
            password = _raw_userinfo(parsed)
            node = _new_node("hysteria2", name=name, server=host, port=port or 443, password=password)
            _tls_from_query(query, node)
            node["tls"] = True
            node["obfs"] = _q(query, "obfs")
            node["obfs_password"] = _q(query, "obfs-password", "obfs_password")
            try:
                node["up_mbps"] = int(_q(query, "upmbps", "up", default="0") or 0)
                node["down_mbps"] = int(_q(query, "downmbps", "down", default="0") or 0)
            except ValueError:
                pass
            return node
        if scheme == "tuic":
            raw_user = _raw_userinfo(parsed)
            raw_user, _, raw_password = raw_user.partition(":")
            node = _new_node("tuic", name=name, server=host, port=port or 443, uuid=raw_user, password=raw_password)
            _tls_from_query(query, node)
            node["tls"] = True
            node["congestion_control"] = _q(query, "congestion_control", "congestion-controller")
            node["udp_relay_mode"] = _q(query, "udp_relay_mode", "udp-relay-mode")
            return node
        if scheme == "anytls":
            password = _raw_userinfo(parsed)
            node = _new_node("anytls", name=name, server=host, port=port or 443, password=password)
            _tls_from_query(query, node)
            node["tls"] = True
            return node
        if scheme in {"socks", "socks5", "http", "https"}:
            node_type = "socks" if scheme.startswith("socks") else "http"
            username = urllib.parse.unquote(parsed.username or "")
            password = urllib.parse.unquote(parsed.password or "")
            node = _new_node(node_type, name=name, server=host, port=port or (1080 if node_type == "socks" else 8080),
                             username=username, password=password)
            node["tls"] = scheme == "https" or _bool_q(query, "tls", "security")
            node["sni"] = _q(query, "sni")
            return node
    except Exception:
        return None
    return None


def _parse_vmess(link: str, fallback_name: str = "") -> dict[str, Any] | None:
    try:
        raw = link.split("://", 1)[1]
        query: dict[str, list[str]] = {}
        if "@" in raw.split("#", 1)[0]:
            parsed = urllib.parse.urlsplit(link)
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            payload = {
                "add": parsed.hostname,
                "port": parsed.port,
                "id": _raw_userinfo(parsed),
                "aid": 0,
                "scy": "auto",
                "net": _q(query, "type", default="tcp"),
                "host": _q(query, "host"),
                "path": _q(query, "path"),
                "tls": _q(query, "security"),
                "sni": _q(query, "sni"),
                "fp": _q(query, "fp"),
                "alpn": _q(query, "alpn"),
                "ps": urllib.parse.unquote(parsed.fragment or fallback_name),
            }
        else:
            decoded = b64decode_flexible(raw).decode("utf-8", errors="replace")
            payload = json.loads(decoded)
        network = str(payload.get("net") or "tcp").lower()
        if network == "raw":
            network = "tcp"
        if network in {"http", "h2"}:
            network = "http"
        elif network in {"grpc", "gun"}:
            network = "grpc"
        elif network == "xhttp":
            network = "httpupgrade"
        node = _new_node(
            "vmess",
            name=str(payload.get("ps") or fallback_name or ""),
            server=str(payload.get("add") or ""),
            port=int(payload.get("port") or 443),
            uuid=str(payload.get("id") or ""),
            alter_id=int(payload.get("aid") or 0),
            security=str(payload.get("scy") or "auto"),
        )
        node["network"] = network
        host = str(payload.get("host") or _q(query, "host") or "")
        path = str(payload.get("path") or _q(query, "path") or "")
        if network in {"ws", "httpupgrade"}:
            node["ws_host"] = host
            node["ws_path"] = path or "/"
        elif network == "grpc":
            node["grpc_service"] = path
            node["ws_host"] = host
        elif network == "http":
            node["http_host"] = host
            node["http_path"] = path or "/"
        security = str(payload.get("tls") or _q(query, "security") or "").lower()
        node["tls"] = security in {"tls", "reality"}
        node["sni"] = str(payload.get("sni") or _q(query, "sni") or "")
        node["alpn"] = as_list(payload.get("alpn") or _q(query, "alpn"))
        node["fingerprint"] = str(payload.get("fp") or _q(query, "fp") or "")
        node["allow_insecure"] = str(payload.get("insecure") or "") == "1" or _bool_q(query, "allowInsecure", "insecure")
        return node
    except Exception:
        return None


def _parse_ss(link: str, fallback_name: str = "") -> dict[str, Any] | None:
    try:
        raw_link = link.split("://", 1)[1]
        fragment = ""
        if "#" in raw_link:
            raw_link, fragment = raw_link.split("#", 1)
            fragment = urllib.parse.unquote(fragment)
        if "?" in raw_link:
            raw_link, query_text = raw_link.split("?", 1)
            query = urllib.parse.parse_qs(query_text)
        else:
            query = {}
        if "@" in raw_link:
            userinfo, endpoint = raw_link.rsplit("@", 1)
            userinfo = urllib.parse.unquote(userinfo)
            host, port = split_host_port(endpoint, 8388)
            if ":" in userinfo:
                method, password = userinfo.split(":", 1)
            else:
                decoded_user = b64decode_flexible(userinfo).decode("utf-8", errors="replace")
                method, password = decoded_user.split(":", 1)
        else:
            decoded = b64decode_flexible(raw_link).decode("utf-8", errors="replace")
            if "@" not in decoded or ":" not in decoded:
                raise ValueError("invalid shadowsocks link")
            userinfo, endpoint = decoded.rsplit("@", 1)
            method, password = userinfo.split(":", 1)
            host, port = split_host_port(endpoint, 8388)
        plugin_raw = _q(query, "plugin")
        plugin = plugin_raw.split(";", 1)[0] if plugin_raw else ""
        plugin_opts = plugin_raw.split(";", 1)[1] if ";" in plugin_raw else ""
        return _new_node("shadowsocks", name=fragment or fallback_name, server=host, port=port,
                         method=urllib.parse.unquote(method), password=urllib.parse.unquote(password),
                         plugin=plugin, plugin_opts=plugin_opts)
    except Exception:
        return None


def from_v2rayn_row(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        config_type = int(row.get("ConfigType") or 0)
        transport = _json_obj(row.get("TransportExtra"))
        extra = _json_obj(row.get("ProtoExtra"))
        node_type = {
            1: "vmess", 3: "shadowsocks", 4: "socks", 5: "vless", 6: "trojan",
            7: "hysteria2", 8: "tuic", 9: "wireguard", 10: "http", 11: "anytls", 12: "naive",
        }.get(config_type)
        if not node_type:
            return None
        server = str(row.get("Address") or "").strip()
        port = int(row.get("Port") or 0)
        if not server or not 0 < port < 65536:
            return None
        node = _new_node(
            node_type,
            name=str(row.get("Remarks") or ""),
            server=server,
            port=port,
            network=str(row.get("Network") or "tcp").lower(),
            tls=str(row.get("StreamSecurity") or "").lower() in {"tls", "reality", "xtls"},
            sni=str(row.get("Sni") or ""),
            alpn=as_list(row.get("Alpn")),
            allow_insecure=parse_bool(row.get("AllowInsecure")),
            fingerprint=str(row.get("Fingerprint") or ""),
            reality_public_key=str(row.get("PublicKey") or ""),
            reality_short_id=str(row.get("ShortId") or ""),
            reality_spider_x=str(row.get("SpiderX") or ""),
            flow=str(extra.get("Flow") or ""),
            password=str(row.get("Password") or ""),
            username=str(row.get("Username") or ""),
            method=str(extra.get("SsMethod") or ""),
            security=str(extra.get("VmessSecurity") or ""),
            alter_id=int(extra.get("AlterId") or row.get("AlterId") or 0),
            obfs=str(extra.get("Obfs") or ""),
            obfs_password=str(extra.get("SalamanderPass") or ""),
            congestion_control=str(extra.get("CongestionControl") or ""),
            up_mbps=int(extra.get("UpMbps") or 0),
            down_mbps=int(extra.get("DownMbps") or 0),
        )
        if node_type == "tuic":
            node["uuid"] = str(row.get("Username") or row.get("Password") or "")
        elif node_type in {"vless", "vmess"}:
            node["uuid"] = str(row.get("Password") or "")
        if node_type in {"hysteria2", "tuic", "anytls"}:
            node["tls"] = True
        if str(row.get("StreamSecurity") or "").lower() in {"tls", "reality", "xtls"}:
            node["security_mode"] = str(row.get("StreamSecurity") or "").lower()
        network = node["network"]
        if network in {"raw", ""}:
            network = "tcp"
        elif network in {"http", "h2"}:
            network = "http"
        elif network in {"grpc", "gun"}:
            network = "grpc"
        elif network == "xhttp":
            network = "httpupgrade"
        node["network"] = network
        if network == "ws":
            node["ws_host"] = str(transport.get("Host") or "")
            node["ws_path"] = str(transport.get("Path") or "/")
        elif network == "grpc":
            node["grpc_service"] = str(transport.get("GrpcServiceName") or transport.get("Path") or "")
            node["ws_host"] = str(transport.get("GrpcAuthority") or transport.get("Host") or "")
        elif network == "httpupgrade":
            node["ws_host"] = str(transport.get("Host") or "")
            node["ws_path"] = str(transport.get("Path") or "/")
        elif network == "http":
            node["http_host"] = str(transport.get("Host") or "")
            node["http_path"] = str(transport.get("Path") or "/")
        if node_type == "shadowsocks":
            node["method"] = str(extra.get("SsMethod") or node.get("method") or "")
            plugin_parts = []
            if network == "ws":
                plugin = "v2ray-plugin"
                plugin_parts.extend(["mode=websocket", f"host={transport.get('Host') or ''}", f"path={transport.get('Path') or '/'}"])
                if node["tls"]:
                    plugin_parts.append("tls")
                node["plugin"] = plugin
                node["plugin_opts"] = ";".join(plugin_parts)
            elif node["tls"]:
                node["plugin"] = "v2ray-plugin"
                plugin_parts.extend(["tls", f"host={transport.get('Host') or ''}", f"path={transport.get('Path') or '/'}"])
                node["plugin_opts"] = ";".join(plugin_parts)
        if node_type == "vmess":
            node["security"] = str(extra.get("VmessSecurity") or "auto")
        if node_type == "hysteria2" and str(extra.get("SalamanderPass") or ""):
            node["obfs"] = "salamander"
        node["source"] = "v2rayn"
        return node
    except Exception:
        return None


def import_v2rayn(v2rayn_dir: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import sqlite3
    base = Path(v2rayn_dir)
    db_path = base / "guiConfigs" / "guiNDB.db"
    if not db_path.exists():
        raise FileNotFoundError(f"找不到 v2rayN 数据库：{db_path}")
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in con.execute("select * from ProfileItem order by IndexId")]
    finally:
        con.close()
    nodes: list[dict[str, Any]] = []
    for row in rows:
        node = from_v2rayn_row(row)
        if node:
            node["source_index_id"] = str(row.get("IndexId") or "")
            node["id"] = f"v2rayn-{row.get('IndexId')}"
            nodes.append(node)
    name_counts: dict[str, int] = {}
    for node in nodes:
        name_counts[node["name"]] = name_counts.get(node["name"], 0) + 1
    for node in nodes:
        if name_counts.get(node["name"], 0) > 1:
            node["name"] = f"{node['name']} · {protocol_label(node['type'])}"
    return nodes, rows


def _decode_possible_base64(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 16:
        return None
    if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact):
        return None
    try:
        decoded = b64decode_flexible(compact)
        result = decoded.decode("utf-8", errors="strict")
    except Exception:
        return None
    if "://" in result or "proxies:" in result or result.lstrip().startswith("{"):
        return result
    return None


def _clash_yaml_to_nodes(text: str) -> list[dict[str, Any]]:
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    proxies = data.get("proxies")
    if not isinstance(proxies, list):
        return []
    nodes: list[dict[str, Any]] = []
    for proxy in proxies:
        if not isinstance(proxy, dict):
            continue
        node = _clash_proxy_to_node(proxy)
        if node:
            nodes.append(node)
    return nodes


def _clash_proxy_to_node(p: dict[str, Any]) -> dict[str, Any] | None:
    try:
        kind = str(p.get("type") or "").lower()
        type_map = {
            "ss": "shadowsocks", "shadowsocks": "shadowsocks", "vmess": "vmess", "vless": "vless",
            "trojan": "trojan", "hysteria2": "hysteria2", "hy2": "hysteria2", "tuic": "tuic",
            "socks5": "socks", "socks": "socks", "http": "http", "anytls": "anytls",
        }
        node_type = type_map.get(kind)
        if not node_type:
            return None
        name = str(p.get("name") or "")
        server = str(p.get("server") or "")
        port = int(p.get("port") or 0)
        if not server or not 0 < port < 65536:
            return None
        node = _new_node(node_type, name=name, server=server, port=port)
        node["uuid"] = str(p.get("uuid") or p.get("id") or "")
        node["password"] = str(p.get("password") or p.get("auth") or "")
        node["username"] = str(p.get("username") or "")
        node["method"] = str(p.get("cipher") or p.get("method") or "")
        node["alter_id"] = int(p.get("alterId") or p.get("alter-id") or 0)
        node["security"] = str(p.get("cipher") or "auto")
        node["flow"] = str(p.get("flow") or "")
        node["udp_relay_mode"] = str(p.get("udp-relay-mode") or "")
        node["congestion_control"] = str(p.get("congestion-controller") or p.get("congestion_control") or "")
        node["obfs"] = str(p.get("obfs") or "")
        node["obfs_password"] = str(p.get("obfs-password") or "")
        node["up_mbps"] = int(p.get("up") or 0)
        node["down_mbps"] = int(p.get("down") or 0)
        tls_enabled = bool(p.get("tls")) or str(p.get("security") or "").lower() in {"tls", "reality"} or node_type in {"hysteria2", "tuic", "anytls"}
        node["tls"] = tls_enabled
        node["sni"] = str(p.get("servername") or p.get("sni") or p.get("peer") or "")
        node["network"] = str(p.get("network") or "tcp").lower()
        if node["network"] in {"http", "h2"}:
            node["network"] = "http"
        node["alpn"] = as_list(p.get("alpn"))
        node["allow_insecure"] = bool(p.get("skip-cert-verify") or p.get("allow-insecure") or False)
        node["fingerprint"] = str(p.get("client-fingerprint") or "")
        reality = p.get("reality-opts") if isinstance(p.get("reality-opts"), dict) else {}
        node["reality_public_key"] = str(reality.get("public-key") or "")
        node["reality_short_id"] = str(reality.get("short-id") or "")
        if node_type == "vless" and node["reality_public_key"]:
            node["security_mode"] = "reality"
        if node["network"] == "ws":
            ws = p.get("ws-opts") if isinstance(p.get("ws-opts"), dict) else {}
            headers = ws.get("headers") if isinstance(ws.get("headers"), dict) else {}
            node["ws_path"] = str(ws.get("path") or "/")
            node["ws_host"] = str(headers.get("Host") or headers.get("host") or p.get("ws-host") or "")
        elif node["network"] == "grpc":
            grpc = p.get("grpc-opts") if isinstance(p.get("grpc-opts"), dict) else {}
            node["grpc_service"] = str(grpc.get("grpc-service-name") or "")
        elif node["network"] == "http":
            opts = p.get("h2-opts") if isinstance(p.get("h2-opts"), dict) else p.get("http-opts", {})
            if isinstance(opts, dict):
                host = opts.get("host")
                node["http_host"] = ",".join(host) if isinstance(host, list) else str(host or "")
                path = opts.get("path")
                node["http_path"] = (path[0] if isinstance(path, list) and path else str(path or "/"))
        elif node["network"] == "httpupgrade":
            opts = p.get("http-opts") if isinstance(p.get("http-opts"), dict) else {}
            node["ws_path"] = str(opts.get("path") or "/")
            host = opts.get("host")
            node["ws_host"] = ",".join(host) if isinstance(host, list) else str(host or "")
        plugin = str(p.get("plugin") or "")
        if plugin:
            plugin_opts = p.get("plugin-opts") if isinstance(p.get("plugin-opts"), dict) else {}
            if plugin == "obfs":
                node["plugin"] = "obfs-local"
                node["plugin_opts"] = f"obfs={plugin_opts.get('mode', 'http')};obfs-host={plugin_opts.get('host', '')}"
            elif plugin == "v2ray-plugin":
                parts = [f"mode={plugin_opts.get('mode', 'websocket')}"]
                if plugin_opts.get("host"):
                    parts.append(f"host={plugin_opts['host']}")
                if plugin_opts.get("path"):
                    parts.append(f"path={plugin_opts['path']}")
                if plugin_opts.get("tls"):
                    parts.append("tls")
                node["plugin"] = "v2ray-plugin"
                node["plugin_opts"] = ";".join(parts)
            else:
                node["plugin"] = plugin
                node["plugin_opts"] = ""
        return node
    except Exception:
        return None


def parse_import_text(text: str) -> list[dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return []
    decoded = _decode_possible_base64(text)
    if decoded:
        text = decoded.strip()
    if text.startswith("{") or text.startswith("proxies:") or "\nproxies:" in text:
        yaml_nodes = _clash_yaml_to_nodes(text)
        if yaml_nodes:
            return yaml_nodes
        try:
            data = json.loads(text)
            if isinstance(data, dict) and isinstance(data.get("proxies"), list):
                return [n for p in data["proxies"] if isinstance(p, dict) and (n := _clash_proxy_to_node(p))]
        except ValueError:
            pass
    nodes: list[dict[str, Any]] = []
    for line in re.split(r"[\r\n]+", text):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        node = parse_share_link(line)
        if node:
            nodes.append(node)
    return nodes


def fetch_subscription(url: str, timeout: int = 20) -> tuple[list[dict[str, Any]], str]:
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("订阅地址必须以 http:// 或 https:// 开头")
    headers = {
        "User-Agent": "v2rayN/7.14.3",
        "Accept": "*/*",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("订阅内容超过 8 MB")
        charset = response.headers.get_content_charset() or "utf-8"
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    nodes = parse_import_text(text)
    if not nodes and "clash" not in text[:2000].lower() and "proxies:" not in text[:2000].lower():
        # Some providers return different formats based on the client user agent.
        req = urllib.request.Request(url, headers={"User-Agent": "clash-verge/v2.0.0", "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            charset = response.headers.get_content_charset() or "utf-8"
        try:
            text = raw.decode(charset, errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        nodes = parse_import_text(text)
    return nodes, text[:500]


def _uri_host(server: str) -> str:
    value = str(server or "").strip()
    if ":" in value and not (value.startswith("[") and value.endswith("]")):
        return f"[{value}]"
    return value


def _uri_fragment(node: dict[str, Any]) -> str:
    name = str(node.get("name") or f"{node.get('server', '')}:{node.get('port', '')}")
    return "#" + urllib.parse.quote(name, safe="")


def _encode_query(params: list[tuple[str, Any]]) -> str:
    pairs = [(key, str(value)) for key, value in params if value not in (None, "")]
    if not pairs:
        return ""
    return "?" + urllib.parse.urlencode(pairs, quote_via=urllib.parse.quote, safe="")


def _tls_params(node: dict[str, Any]) -> list[tuple[str, Any]]:
    mode = str(node.get("security_mode") or "").lower()
    if not mode:
        mode = "tls" if node.get("tls") else "none"
    params: list[tuple[str, Any]] = [("security", mode)]
    if node.get("sni"):
        params.append(("sni", node.get("sni")))
    if node.get("alpn"):
        params.append(("alpn", ",".join(str(item) for item in node.get("alpn") or [])))
    if node.get("fingerprint"):
        params.append(("fp", node.get("fingerprint")))
    if node.get("allow_insecure"):
        params.append(("allowInsecure", "1"))
    if str(node.get("reality_public_key") or ""):
        params.append(("pbk", node.get("reality_public_key")))
    if str(node.get("reality_short_id") or ""):
        params.append(("sid", node.get("reality_short_id")))
    if str(node.get("reality_spider_x") or ""):
        params.append(("spx", node.get("reality_spider_x")))
    return params


def _transport_params(node: dict[str, Any]) -> list[tuple[str, Any]]:
    network = str(node.get("network") or "tcp").lower()
    mapped = {"raw": "tcp", "tcp": "tcp", "ws": "ws", "grpc": "grpc",
              "http": "h2", "h2": "h2", "httpupgrade": "httpupgrade"}.get(network, "tcp")
    params: list[tuple[str, Any]] = [("type", mapped)]
    if mapped == "ws":
        if node.get("ws_host"):
            params.append(("host", node.get("ws_host")))
        params.append(("path", node.get("ws_path") or "/"))
    elif mapped == "httpupgrade":
        if node.get("ws_host"):
            params.append(("host", node.get("ws_host")))
        params.append(("path", node.get("ws_path") or "/"))
    elif mapped == "grpc":
        if node.get("grpc_service"):
            params.append(("serviceName", node.get("grpc_service")))
        if node.get("ws_host"):
            params.append(("authority", node.get("ws_host")))
    elif mapped == "h2":
        if node.get("http_host") or node.get("ws_host"):
            params.append(("host", node.get("http_host") or node.get("ws_host")))
        params.append(("path", node.get("http_path") or "/"))
    return params


def node_to_share_link(node: dict[str, Any]) -> str:
    """Export a normalized node back to a common share URI."""
    node_type = str(node.get("type") or "").lower()
    server = _uri_host(str(node.get("server") or ""))
    try:
        port = int(node.get("port") or 0)
    except (TypeError, ValueError):
        return ""
    if not server or not 0 < port < 65536:
        return ""
    fragment = _uri_fragment(node)

    if node_type == "vless":
        uuid = urllib.parse.quote(str(node.get("uuid") or node.get("password") or ""), safe="")
        if not uuid:
            return ""
        params: list[tuple[str, Any]] = [("encryption", node.get("encryption") or "none")]
        params.extend(_tls_params(node))
        if node.get("flow"):
            params.append(("flow", node.get("flow")))
        params.extend(_transport_params(node))
        return f"vless://{uuid}@{server}:{port}{_encode_query(params)}{fragment}"

    if node_type == "vmess":
        uuid = str(node.get("uuid") or node.get("password") or "")
        if not uuid:
            return ""
        network = str(node.get("network") or "tcp").lower()
        net = {"raw": "tcp", "tcp": "tcp", "ws": "ws", "grpc": "grpc",
               "http": "h2", "h2": "h2", "httpupgrade": "httpupgrade"}.get(network, "tcp")
        host = node.get("ws_host") or node.get("http_host") or ""
        path = node.get("ws_path") or node.get("http_path") or ""
        if net == "grpc":
            path = node.get("grpc_service") or path
            host = node.get("ws_host") or ""
        payload = {
            "v": 2,
            "ps": str(node.get("name") or f"{server}:{port}"),
            "add": str(node.get("server") or ""),
            "port": port,
            "id": uuid,
            "aid": int(node.get("alter_id") or 0),
            "scy": str(node.get("security") or "auto"),
            "net": net,
            "type": "none",
            "host": str(host),
            "path": str(path or ("/" if net in {"ws", "h2", "httpupgrade"} else "")),
            "tls": "tls" if node.get("tls") else "",
            "sni": str(node.get("sni") or ""),
            "alpn": ",".join(str(item) for item in node.get("alpn") or []),
            "fp": str(node.get("fingerprint") or ""),
            "insecure": "1" if node.get("allow_insecure") else "0",
        }
        encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode()
        return "vmess://" + encoded

    if node_type == "trojan":
        password = urllib.parse.quote(str(node.get("password") or ""), safe="")
        if not password:
            return ""
        params = _tls_params(node)
        if node.get("flow"):
            params.append(("flow", node.get("flow")))
        params.extend(_transport_params(node))
        return f"trojan://{password}@{server}:{port}{_encode_query(params)}{fragment}"

    if node_type == "hysteria2":
        password = urllib.parse.quote(str(node.get("password") or ""), safe="")
        if not password:
            return ""
        params: list[tuple[str, Any]] = []
        if node.get("sni"):
            params.append(("sni", node.get("sni")))
        if node.get("alpn"):
            params.append(("alpn", ",".join(str(item) for item in node.get("alpn") or [])))
        if node.get("allow_insecure"):
            params.append(("insecure", "1"))
        if str(node.get("obfs") or "") == "salamander":
            params.append(("obfs", "salamander"))
            if node.get("obfs_password"):
                params.append(("obfs-password", node.get("obfs_password")))
        return f"hysteria2://{password}@{server}:{port}{_encode_query(params)}{fragment}"

    if node_type == "tuic":
        uuid = urllib.parse.quote(str(node.get("uuid") or ""), safe="")
        password = urllib.parse.quote(str(node.get("password") or ""), safe="")
        if not uuid:
            return ""
        params = []
        if node.get("sni"):
            params.append(("sni", node.get("sni")))
        if node.get("alpn"):
            params.append(("alpn", ",".join(str(item) for item in node.get("alpn") or [])))
        if node.get("allow_insecure"):
            params.append(("allow_insecure", "1"))
        if node.get("congestion_control"):
            params.append(("congestion_control", node.get("congestion_control")))
        if node.get("udp_relay_mode"):
            params.append(("udp_relay_mode", node.get("udp_relay_mode")))
        return f"tuic://{uuid}:{password}@{server}:{port}{_encode_query(params)}{fragment}"

    if node_type == "shadowsocks":
        method = str(node.get("method") or "")
        password = str(node.get("password") or "")
        if not method or not password:
            return ""
        userinfo = base64.urlsafe_b64encode(f"{method}:{password}".encode("utf-8")).decode().rstrip("=")
        params = []
        plugin = str(node.get("plugin") or "").strip()
        if plugin:
            plugin_value = plugin
            if str(node.get("plugin_opts") or "").strip():
                plugin_value += ";" + str(node.get("plugin_opts") or "").strip().strip(";")
            params.append(("plugin", plugin_value))
        return f"ss://{userinfo}@{server}:{port}{_encode_query(params)}{fragment}"

    if node_type == "socks":
        username = urllib.parse.quote(str(node.get("username") or ""), safe="")
        password = urllib.parse.quote(str(node.get("password") or ""), safe="")
        userinfo = ""
        if username or password:
            userinfo = username + (":" + password if password else "") + "@"
        return f"socks5://{userinfo}{server}:{port}{fragment}"

    if node_type == "http":
        username = urllib.parse.quote(str(node.get("username") or ""), safe="")
        password = urllib.parse.quote(str(node.get("password") or ""), safe="")
        userinfo = ""
        if username or password:
            userinfo = username + (":" + password if password else "") + "@"
        scheme = "https" if node.get("tls") else "http"
        params = _tls_params(node) if node.get("tls") else []
        return f"{scheme}://{userinfo}{server}:{port}{_encode_query(params)}{fragment}"

    if node_type == "anytls":
        password = urllib.parse.quote(str(node.get("password") or ""), safe="")
        if not password:
            return ""
        params = _tls_params(node)
        return f"anytls://{password}@{server}:{port}{_encode_query(params)}{fragment}"

    return ""
