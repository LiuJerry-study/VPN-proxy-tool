from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time
from typing import Any


class TrafficMonitor:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._api_port = 0
        self._secret = ""
        self._up = 0
        self._down = 0
        self._up_total = 0
        self._down_total = 0
        self._history: list[dict[str, int]] = []
        self._stream_ok = False

    def start(self, api_port: int, secret: str) -> None:
        self.stop()
        with self._lock:
            self._api_port = api_port
            self._secret = secret
            self._up = 0
            self._down = 0
            self._up_total = 0
            self._down_total = 0
            self._history = []
            self._stream_ok = False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="traffic-monitor", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            sock = self._sock
            self._sock = None
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None
        with self._lock:
            self._up = 0
            self._down = 0
            self._stream_ok = False

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._connect_and_read()
            except Exception:
                pass
            if not self._stop.wait(1.0):
                continue

    def _connect_and_read(self) -> None:
        port = self._api_port
        if not port:
            return
        sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        with self._lock:
            self._sock = sock
        sock.settimeout(1.0)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            "GET /traffic HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Authorization: Bearer {self._secret}\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket closed")
            buffer += chunk
            if len(buffer) > 65536:
                raise ConnectionError("websocket handshake too large")
        header, remainder = buffer.split(b"\r\n\r\n", 1)
        if b" 101 " not in header.split(b"\r\n", 1)[0]:
            raise ConnectionError("websocket upgrade failed")
        with self._lock:
            self._stream_ok = True
        pending = remainder

        def recv_exact(count: int) -> bytes:
            nonlocal pending
            while len(pending) < count:
                chunk = sock.recv(max(4096, count - len(pending)))
                if not chunk:
                    raise ConnectionError("websocket ended")
                pending += chunk
            data, pending = pending[:count], pending[count:]
            return data

        while not self._stop.is_set():
            try:
                first = recv_exact(2)
            except socket.timeout:
                continue
            opcode = first[0] & 0x0F
            masked = bool(first[1] & 0x80)
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", recv_exact(8))[0]
            if length > 4 * 1024 * 1024:
                raise ConnectionError("websocket frame too large")
            mask = recv_exact(4) if masked else b""
            payload = recv_exact(length)
            if masked:
                payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
            if opcode == 0x8:
                raise ConnectionError("websocket close")
            if opcode == 0x9:
                self._send_pong(sock, payload)
                continue
            if opcode != 0x1:
                continue
            try:
                data = json.loads(payload.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            up = max(0, int(data.get("up") or 0))
            down = max(0, int(data.get("down") or 0))
            self._update(up, down)

    def _update(self, up: int, down: int) -> None:
        with self._lock:
            self._up = up
            self._down = down
            self._up_total += up
            self._down_total += down
            self._history.append({"up": up, "down": down})
            if len(self._history) > 60:
                self._history = self._history[-60:]

    @staticmethod
    def _send_pong(sock: socket.socket, payload: bytes) -> None:
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        header = bytes([0x8A, 0x80 | len(masked)])
        sock.sendall(header + mask + masked)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "up": self._up,
                "down": self._down,
                "up_total": self._up_total,
                "down_total": self._down_total,
                "history": list(self._history),
                "stream_ok": self._stream_ok,
            }
