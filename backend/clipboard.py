from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

if os.name == "nt":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _user32.OpenClipboard.argtypes = [wintypes.HWND]
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.argtypes = []
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.GetClipboardData.argtypes = [wintypes.UINT]
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.EmptyClipboard.argtypes = []
    _user32.EmptyClipboard.restype = wintypes.BOOL
    _user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _user32.SetClipboardData.restype = wintypes.HANDLE

    _kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _kernel32.GlobalAlloc.restype = wintypes.HANDLE
    _kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    _kernel32.GlobalUnlock.restype = wintypes.BOOL
    _kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
    _kernel32.GlobalFree.restype = wintypes.HANDLE


def _open_clipboard() -> None:
    for _ in range(12):
        if _user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise RuntimeError("剪贴板正被其他程序占用，请稍后重试")


def read_text() -> str:
    if os.name != "nt":
        raise RuntimeError("剪贴板功能仅支持 Windows")
    _open_clipboard()
    try:
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            raise RuntimeError("无法读取剪贴板内容")
        try:
            return ctypes.wstring_at(pointer)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def write_text(text: str) -> None:
    if os.name != "nt":
        raise RuntimeError("剪贴板功能仅支持 Windows")
    payload = (str(text) + "\x00").encode("utf-16-le")
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    if not handle:
        raise RuntimeError("无法分配剪贴板内存")
    pointer = _kernel32.GlobalLock(handle)
    if not pointer:
        _kernel32.GlobalFree(handle)
        raise RuntimeError("无法写入剪贴板内存")
    try:
        ctypes.memmove(pointer, payload, len(payload))
    finally:
        _kernel32.GlobalUnlock(handle)
    try:
        _open_clipboard()
    except Exception:
        _kernel32.GlobalFree(handle)
        raise
    try:
        if not _user32.EmptyClipboard():
            raise RuntimeError("无法清空剪贴板")
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise RuntimeError("无法写入剪贴板")
        # Ownership of the global memory moves to the clipboard on success.
        handle = None
    finally:
        _user32.CloseClipboard()
        if handle:
            _kernel32.GlobalFree(handle)
