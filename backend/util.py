from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", ROOT))
else:
    ROOT = Path(__file__).resolve().parent.parent
    BUNDLE_DIR = ROOT

# Writable data must never live inside a read-only one-file bundle.
_override = os.environ.get("QINGLIAN_HOME", "").strip()
if _override:
    DATA_ROOT = Path(_override).expanduser()
elif not getattr(sys, "frozen", False) or (ROOT / "portable.flag").exists():
    # Source mode and explicitly portable builds keep data beside the app.
    DATA_ROOT = ROOT
else:
    # The delivered executable may run at low integrity when launched from a
    # sandboxed folder. LocalLow is the standard per-user location writable by
    # both low- and medium-integrity processes, unlike LocalAppData/Temp.
    _profile = Path(os.environ.get("USERPROFILE") or Path.home())
    DATA_ROOT = _profile / "AppData" / "LocalLow" / "QingLian"

DATA_DIR = DATA_ROOT / "data"
RUNTIME_DIR = DATA_ROOT / "runtime"
WEB_DIR = BUNDLE_DIR / "web"
CORE_DIR = BUNDLE_DIR / "core"
VENDOR_DIR = ROOT / "vendor"
STORE_FILE = DATA_DIR / "store.json"
LOG_FILE = RUNTIME_DIR / "singbox.log"


def ensure_dirs() -> None:
    for path in (DATA_DIR, RUNTIME_DIR):
        path.mkdir(parents=True, exist_ok=True)


def migrate_legacy_store() -> bool:
    """Copy an old adjacent data/store.json into the new per-user data folder."""
    if DATA_ROOT == ROOT:
        return False
    legacy = ROOT / "data" / "store.json"
    if not legacy.exists() or STORE_FILE.exists():
        return False
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, STORE_FILE)
        return True
    except OSError:
        return False



def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            return [str(v).strip() for v in parsed if str(v).strip()]
        except ValueError:
            pass
    return [part.strip() for part in re.split(r"[,|\n]", text) if part.strip()]


# 自定义分流名单里一个域名最长 253 字符；条目数上限防止把配置文件撑爆。
MAX_CUSTOM_DOMAINS = 300
_DOMAIN_PATTERN = re.compile(
    r"^(?=.{3,253}$)"
    r"([a-z0-9]([a-z0-9_-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"
)


def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def normalize_domains(value: Any, limit: int = MAX_CUSTOM_DOMAINS) -> list[str]:
    """把用户填写的自定义域名名单归一化成 sing-box 能直接用的后缀列表。

    接受数组或文本：换行、逗号、分号、空格都算分隔符；会去掉协议头、
    端口、路径和通配符前缀，统一小写，并校验是不是合法域名。
    """
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        items = re.split(r"[\s,;]+", str(value or ""))
    result: list[str] = []
    for raw in items:
        token = str(raw).strip().lower()
        if not token:
            continue
        token = re.sub(r"^[a-z][a-z0-9+.-]*://", "", token)
        head = token.split("/", 1)[0]
        if _is_ip_literal(head) or _is_ip_literal(head.strip("[]")):
            raise ValueError(f"暂不支持直接填 IP：{raw.strip()}，请填写域名（例如 lib.example.edu.cn）")
        token = head.split(":", 1)[0]
        token = token.strip().lstrip("*").lstrip(".").rstrip(".")
        if not token:
            continue
        if not _DOMAIN_PATTERN.match(token):
            raise ValueError(f"域名格式不正确：{raw.strip()}（例如 lib.example.edu.cn）")
        if token not in result:
            result.append(token)
    if len(result) > limit:
        raise ValueError(f"自定义域名最多 {limit} 个，请精简后再保存")
    return result


def split_host_port(value: str, default_port: int = 443) -> tuple[str, int]:
    value = value.strip()
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        port = int(rest.lstrip(":") or default_port)
        return host, port
    if value.count(":") == 1:
        host, raw_port = value.rsplit(":", 1)
        if raw_port.isdigit():
            return host, int(raw_port)
    return value, default_port


def b64decode_flexible(text: str) -> bytes:
    text = re.sub(r"\s+", "", text)
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def port_is_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_until(predicate, timeout: float = 8.0, interval: float = 0.1) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def human_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分 {seconds % 60} 秒"
    return f"{seconds // 3600} 时 {(seconds % 3600) // 60} 分"


def safe_tag(prefix: str, value: Any) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value)).strip("-")[:36]
    return f"{prefix}-{cleaned or 'node'}"
