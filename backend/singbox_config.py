from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from typing import Any

from .util import CORE_DIR, RUNTIME_DIR

RULE_FILES = {
    "geosite-cn": CORE_DIR / "rules" / "geosite-cn.srs",
    "geosite-geolocation-cn": CORE_DIR / "rules" / "geosite-geolocation-cn.srs",
    "geosite-private": CORE_DIR / "rules" / "geosite-private.srs",
    "geosite-category-ads-all": CORE_DIR / "rules" / "geosite-category-ads-all.srs",
    "geoip-cn": CORE_DIR / "rules" / "geoip-cn.srs",
    "geoip-private": CORE_DIR / "rules" / "geoip-private.srs",
}


def _posix(path: Path) -> str:
    return path.resolve().as_posix()


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _tls_object(node: dict[str, Any], force: bool = False) -> dict[str, Any] | None:
    enabled = force or bool(node.get("tls"))
    if not enabled:
        return None
    tls: dict[str, Any] = {"enabled": True}
    sni = str(node.get("sni") or "").strip()
    server = str(node.get("server") or "").strip()
    if not sni and server and not _is_ip(server):
        sni = server
    if sni:
        tls["server_name"] = sni
    if node.get("allow_insecure"):
        tls["insecure"] = True
    alpn = node.get("alpn") or []
    if alpn:
        tls["alpn"] = [str(x) for x in alpn if str(x).strip()]
    fingerprint = str(node.get("fingerprint") or "").strip()
    is_datagram = str(node.get("type") or "") in {"hysteria2", "tuic"}
    if fingerprint and not is_datagram:
        tls["utls"] = {"enabled": True, "fingerprint": fingerprint}
    public_key = str(node.get("reality_public_key") or "").strip()
    if public_key:
        reality: dict[str, Any] = {"enabled": True, "public_key": public_key}
        short_id = str(node.get("reality_short_id") or "").strip()
        if short_id:
            reality["short_id"] = short_id
        tls["reality"] = reality
    return tls


def _transport_object(node: dict[str, Any]) -> dict[str, Any] | None:
    network = str(node.get("network") or "tcp").lower()
    if network in {"", "tcp", "raw"}:
        return None
    if network == "ws":
        result: dict[str, Any] = {"type": "ws", "path": str(node.get("ws_path") or "/")}
        host = str(node.get("ws_host") or "").strip()
        if host:
            result["headers"] = {"Host": host}
        return result
    if network == "grpc":
        return {"type": "grpc", "service_name": str(node.get("grpc_service") or "")}
    if network in {"http", "h2"}:
        result = {"type": "http", "path": str(node.get("http_path") or "/")}
        host = str(node.get("http_host") or "").strip()
        if host:
            result["host"] = [part.strip() for part in host.split(",") if part.strip()]
        return result
    if network == "httpupgrade":
        result = {"type": "httpupgrade", "path": str(node.get("ws_path") or "/")}
        host = str(node.get("ws_host") or "").strip()
        if host:
            result["host"] = host
        return result
    return None


def build_outbound(node: dict[str, Any], tag: str = "proxy") -> dict[str, Any]:
    node_type = str(node.get("type") or "").lower()
    server = str(node.get("server") or "")
    port = int(node.get("port") or 443)
    common: dict[str, Any] = {
        "type": node_type,
        "tag": tag,
        "server": server,
        "server_port": port,
        "domain_resolver": "dns-local",
    }
    tls = _tls_object(node, force=node_type in {"hysteria2", "tuic", "anytls"})
    transport = _transport_object(node)

    if node_type == "vless":
        result = dict(common)
        result["uuid"] = str(node.get("uuid") or node.get("password") or "")
        flow = str(node.get("flow") or "").strip()
        if flow:
            result["flow"] = flow
        result["packet_encoding"] = "xudp"
        if tls:
            result["tls"] = tls
        if transport:
            result["transport"] = transport
        return result
    if node_type == "vmess":
        result = dict(common)
        result["uuid"] = str(node.get("uuid") or node.get("password") or "")
        result["security"] = str(node.get("security") or "auto")
        result["alter_id"] = int(node.get("alter_id") or 0)
        result["packet_encoding"] = "xudp"
        if tls:
            result["tls"] = tls
        if transport:
            result["transport"] = transport
        return result
    if node_type == "trojan":
        result = dict(common)
        result["password"] = str(node.get("password") or "")
        if tls:
            result["tls"] = tls
        if transport:
            result["transport"] = transport
        return result
    if node_type == "shadowsocks":
        result = dict(common)
        result["method"] = str(node.get("method") or "aes-256-gcm")
        result["password"] = str(node.get("password") or "")
        plugin = str(node.get("plugin") or "").strip()
        if plugin:
            result["plugin"] = plugin
            if str(node.get("plugin_opts") or "").strip():
                result["plugin_opts"] = str(node.get("plugin_opts") or "")
        return result
    if node_type == "hysteria2":
        result = dict(common)
        result["password"] = str(node.get("password") or "")
        obfs = str(node.get("obfs") or "").strip()
        obfs_password = str(node.get("obfs_password") or "").strip()
        if obfs == "salamander" and obfs_password:
            result["obfs"] = {"type": "salamander", "password": obfs_password}
        if int(node.get("up_mbps") or 0) > 0:
            result["up_mbps"] = int(node.get("up_mbps"))
        if int(node.get("down_mbps") or 0) > 0:
            result["down_mbps"] = int(node.get("down_mbps"))
        result["tls"] = tls or {"enabled": True}
        return result
    if node_type == "tuic":
        result = dict(common)
        result["uuid"] = str(node.get("uuid") or "")
        result["password"] = str(node.get("password") or "")
        cc = str(node.get("congestion_control") or "").strip()
        if cc in {"cubic", "new_reno", "bbr"}:
            result["congestion_control"] = cc
        mode = str(node.get("udp_relay_mode") or "").strip()
        if mode in {"native", "quic"}:
            result["udp_relay_mode"] = mode
        result["tls"] = tls or {"enabled": True}
        return result
    if node_type == "anytls":
        result = dict(common)
        result["password"] = str(node.get("password") or "")
        result["tls"] = tls or {"enabled": True}
        return result
    if node_type == "socks":
        result = dict(common)
        result["version"] = "5"
        if node.get("username"):
            result["username"] = str(node.get("username"))
        if node.get("password"):
            result["password"] = str(node.get("password"))
        return result
    if node_type == "http":
        result = dict(common)
        if node.get("username"):
            result["username"] = str(node.get("username"))
        if node.get("password"):
            result["password"] = str(node.get("password"))
        if tls:
            result["tls"] = tls
        return result
    if node_type == "wireguard":
        result = dict(common)
        result["private_key"] = str(node.get("password") or "")
        result["peer_public_key"] = str(node.get("uuid") or "")
        address = str(node.get("address") or "").strip()
        if address:
            result["local_address"] = [part.strip() for part in address.split(",") if part.strip()]
        return result
    raise ValueError(f"暂不支持此节点协议：{node_type}")


def _rule_sets(names: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    result = []
    for tag, path in RULE_FILES.items():
        if names is not None and tag not in names:
            continue
        if path.exists():
            result.append({"type": "local", "tag": tag, "format": "binary", "path": _posix(path)})
    return result


def _custom_domains(settings: dict[str, Any], key: str) -> list[str]:
    """读取用户在设置里维护的自定义域名名单（Store 已归一化，这里再兜底清洗）。"""
    raw = settings.get(key)
    if not isinstance(raw, (list, tuple)):
        return []
    result: list[str] = []
    for item in raw:
        value = str(item).strip().lower().lstrip("*").lstrip(".").rstrip(".")
        if value and value not in result:
            result.append(value)
    return result


def _base_dns(mode: str, include_remote: bool, settings: dict[str, Any]) -> dict[str, Any]:
    servers: list[dict[str, Any]] = [
        {"type": "udp", "tag": "dns-local", "server": "223.5.5.5"},
    ]
    rules: list[dict[str, Any]] = []
    direct_domains = _custom_domains(settings, "direct_domains")
    proxy_domains = _custom_domains(settings, "proxy_domains")
    # 自定义名单的解析方式要和分流方式一致：强制直连的域名用国内 DNS 解析，
    # 避免被远程 DNS 带到境外 CDN 节点；强制代理的域名用远程 DNS 解析，
    # 避免本地 DNS 污染。
    if include_remote and proxy_domains:
        rules.append({"domain_suffix": proxy_domains, "server": "dns-remote"})
    if include_remote and direct_domains:
        rules.append({"domain_suffix": direct_domains, "server": "dns-local"})
    if mode == "rule":
        rules.extend([
            {"rule_set": "geosite-private", "server": "dns-local"},
            {"rule_set": "geosite-cn", "server": "dns-local"},
            {"rule_set": "geosite-geolocation-cn", "server": "dns-local"},
        ])
    if include_remote:
        servers.append({"type": "https", "tag": "dns-remote", "server": "1.1.1.1", "detour": "proxy"})
    return {
        "servers": servers,
        "rules": rules,
        "final": "dns-remote" if include_remote else "dns-local",
        # 校园网/部分宽带的 IPv6 常常只是半通不达。按 AAAA 优先时，直连域名
        # 容易先撞上一个必然超时的 IPv6 地址，表现为「代理已经连上，但网页
        # 一直转圈」。优先 IPv4 但不放弃 IPv6，和手机版保持一致。
        "strategy": "prefer_ipv4",
    }


def _route(mode: str, tun_mode: bool, settings: dict[str, Any]) -> dict[str, Any]:
    rules: list[dict[str, Any]] = [
        {"action": "sniff"},
        {"protocol": "dns", "action": "hijack-dns"},
        {"ip_is_private": True, "outbound": "direct"},
    ]
    rule_sets: list[dict[str, Any]] = []
    include_remote = mode in {"rule", "global"}
    direct_domains = _custom_domains(settings, "direct_domains")
    proxy_domains = _custom_domains(settings, "proxy_domains")
    if mode == "rule":
        rule_sets = _rule_sets()
        # 用户自定义名单优先级最高：强制代理 > 强制直连 > 内置分流规则。
        # 加了这条后，「某个站被内置规则判错」就不用等更新规则文件了。
        if proxy_domains:
            rules.append({"domain_suffix": proxy_domains, "outbound": "proxy"})
        if direct_domains:
            rules.append({"domain_suffix": direct_domains, "outbound": "direct"})
        rules.extend([
            {"rule_set": "geosite-private", "outbound": "direct"},
            {"rule_set": "geosite-cn", "outbound": "direct"},
            {"rule_set": "geosite-geolocation-cn", "outbound": "direct"},
            {"rule_set": "geosite-category-ads-all", "action": "reject"},
            # sing-box 1.11 起 IP 类规则不再隐式解析域名。系统代理模式下目标
            # 地址只有域名，缺了这条 resolve，下面的 geoip-cn 永远不会命中，
            # 所有不在 geosite 收录里的国内站点都会被误送进代理（例如用独立
            # 域名托管的校园图书馆发现系统），表现就是一直转圈、最后超时，
            # 而断开代理反而能打开。解析按 DNS 规则走：命中国内规则的用国内
            # DNS，其余用远程 DNS，避免本地 DNS 污染把境外域名误判成直连。
            {"action": "resolve", "strategy": "prefer_ipv4"},
            {"ip_is_private": True, "outbound": "direct"},
            {"rule_set": "geoip-cn", "outbound": "direct"},
        ])
    elif mode == "global":
        # 全局模式承诺「局域网和私有地址直连」，域名形式的局域网入口
        # （路由器后台、群晖、打印机）也要直连，否则会卡在代理上；
        # 自定义直连名单在全局模式下同样生效。
        rule_sets = _rule_sets(("geosite-private",))
        if direct_domains:
            rules.append({"domain_suffix": direct_domains, "outbound": "direct"})
        rules.append({"rule_set": "geosite-private", "outbound": "direct"})
    final = "direct" if mode == "direct" else "proxy"
    result: dict[str, Any] = {
        "auto_detect_interface": True,
        "default_domain_resolver": {"server": "dns-local"},
        "rules": rules,
        "final": final,
    }
    if rule_sets:
        result["rule_set"] = rule_sets
    return result


def _inbounds(settings: dict[str, Any]) -> list[dict[str, Any]]:
    port = int(settings.get("local_port") or 2080)
    inbounds: list[dict[str, Any]] = [
        {
            "type": "mixed",
            "tag": "mixed-in",
            "listen": "127.0.0.1",
            "listen_port": port,
        }
    ]
    if settings.get("tun_mode"):
        inbounds.append({
            "type": "tun",
            "tag": "tun-in",
            "interface_name": "QingLian",
            "address": ["172.19.0.1/30"],
            "mtu": 1500,
            "auto_route": True,
            "strict_route": True,
            "stack": "gvisor",
        })
    return inbounds


def _experimental(api_port: int, api_secret: str, include_cache: bool = True) -> dict[str, Any]:
    experimental: dict[str, Any] = {
        "clash_api": {
            "external_controller": f"127.0.0.1:{api_port}",
            "secret": api_secret,
        }
    }
    if include_cache:
        experimental["cache_file"] = {
            "enabled": True,
            "path": _posix(RUNTIME_DIR / "cache.db"),
        }
    return experimental


def build_config(node: dict[str, Any], settings: dict[str, Any], api_port: int, api_secret: str) -> dict[str, Any]:
    mode = str(settings.get("mode") or "rule")
    tun_mode = bool(settings.get("tun_mode"))
    include_remote = mode in {"rule", "global"}
    return {
        "log": {"level": "info", "timestamp": True},
        "dns": _base_dns(mode, include_remote, settings),
        "inbounds": _inbounds(settings),
        "outbounds": [
            build_outbound(node, "proxy"),
            {"type": "direct", "tag": "direct"},
        ],
        "route": _route(mode, tun_mode, settings),
        "experimental": _experimental(api_port, api_secret),
    }


def build_test_config(nodes: list[dict[str, Any]], api_port: int, api_secret: str, inbound_port: int) -> dict[str, Any]:
    outbounds = []
    for index, node in enumerate(nodes):
        outbounds.append(build_outbound(node, f"node-{index}"))
    outbounds.append({"type": "direct", "tag": "direct"})
    return {
        "log": {"level": "warn", "timestamp": True},
        "dns": _base_dns("global", False, {}),
        "inbounds": [{
            "type": "mixed",
            "tag": "test-in",
            "listen": "127.0.0.1",
            "listen_port": inbound_port,
        }],
        "outbounds": outbounds,
        "route": {
            "auto_detect_interface": True,
            "default_domain_resolver": {"server": "dns-local"},
            "rules": [{"action": "sniff"}],
            "final": "direct",
        },
        "experimental": _experimental(api_port, api_secret, include_cache=False),
    }


def write_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
