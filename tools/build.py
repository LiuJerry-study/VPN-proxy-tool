"""一键打包「轻连 VPN」单文件 exe。

用法：
    python tools/build.py              # 单文件 exe（和发布用的压缩包同款）
    python tools/build.py --onedir     # 目录版，启动更快，方便调试
    python tools/build.py --no-clean   # 复用上次的 build 缓存，快一点
    python tools/build.py --console    # 带控制台，打包后启动报错时能看到 traceback

产物：dist/轻连 VPN.exe

说明：
* core/（sing-box.exe、wintun.dll、rules/）和 web/ 以数据文件形式塞进包内，
  运行时由 backend/util.py 的 BUNDLE_DIR(= sys._MEIPASS) 定位。
* vendor/ 只给 PyInstaller 当搜索路径用（源码模式才依赖它），依赖本身会被
  打进 PYZ，因此不需要额外附带 vendor 目录。
* psutil / pystray / PIL 都有动态导入，靠 hidden-import 显式声明，否则打包后
  托盘图标或进程识别会在运行时报 ModuleNotFoundError。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "轻连 VPN"
ENTRY = ROOT / "app.py"
ICON = ROOT / "assets" / "icon.ico"
VERSION_FILE = ROOT / "assets" / "version_info.txt"

DATA_DIRS = ("core", "web")
HIDDEN_IMPORTS = (
    "psutil._pswindows",
    "pystray._win32",
    "PIL.PngImagePlugin",
    "PIL.IcoImagePlugin",
    "PIL.ImageDraw",
    "PIL.ImageFont",
)
EXCLUDES = (
    "tkinter", "_tkinter", "numpy", "matplotlib", "pandas", "scipy",
    "pytest", "unittest", "pydoc_data", "IPython", "setuptools", "pkg_resources",
)


def check_environment() -> None:
    missing = [name for name in DATA_DIRS if not (ROOT / name).is_dir()]
    if missing:
        raise SystemExit(f"缺少数据目录：{', '.join(missing)}")
    if not (ROOT / "core" / "sing-box.exe").exists():
        raise SystemExit("缺少 core/sing-box.exe，无法打包")
    if not ENTRY.exists():
        raise SystemExit("缺少 app.py")
    try:
        import PyInstaller  # noqa: F401
    except Exception as exc:  # pragma: no cover - 环境问题
        raise SystemExit(f"未安装 PyInstaller：{exc}\n请先执行  python -m pip install -U pyinstaller")


def build(onefile: bool, clean: bool, console: bool = False, tmpdir: str | None = None) -> Path:
    sep = ";" if sys.platform == "win32" else ":"
    dist_dir = ROOT / "dist"
    work_dir = ROOT / "build" / "work"
    spec_dir = ROOT / "build"

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir" if not onefile else "--onefile",
        "--console" if console else "--windowed",
        "--name", APP_NAME,
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--specpath", str(spec_dir),
        # 让 PyInstaller 能在 vendor/ 里找到 psutil / pystray / PIL
        "--paths", str(ROOT / "vendor"),
    ]
    if clean:
        args.append("--clean")
    if tmpdir:
        # 打包后解压到固定目录，不依赖 %TEMP%（低权限/沙箱环境里 %TEMP% 可能写不进去）
        args += ["--runtime-tmpdir", tmpdir]
    if ICON.exists():
        args += ["--icon", str(ICON)]
    if VERSION_FILE.exists():
        args += ["--version-file", str(VERSION_FILE)]
    for name in DATA_DIRS:
        args += ["--add-data", f"{ROOT / name}{sep}{name}"]
    for module in HIDDEN_IMPORTS:
        args += ["--hidden-import", module]
    for module in EXCLUDES:
        args += ["--exclude-module", module]
    args.append(str(ENTRY))

    print("执行：")
    print("  " + " ".join(args))
    result = subprocess.run(args, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit(f"打包失败，PyInstaller 退出码 {result.returncode}")

    exe = dist_dir / (f"{APP_NAME}.exe" if onefile else f"{APP_NAME}/{APP_NAME}.exe")
    if not exe.exists():
        raise SystemExit(f"打包结束但没找到产物：{exe}")
    fix_integrity_label(exe)
    return exe


def fix_integrity_label(exe: Path) -> None:
    """把产物的完整性级别标回 Medium。

    在带低完整性标记的目录里（比如沙箱工作区）打出来的 exe，Windows 会按
    低权限启动它，单文件版解压临时目录时会失败，双击只弹
    “Could not create temporary directory!”。这里尽量把 exe 标回普通用户级别。
    """
    if sys.platform != "win32":
        return
    try:
        result = subprocess.run(
            ["icacls", str(exe), "/setintegritylevel", "Medium"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except Exception:
        return
    if result.returncode == 0:
        print("已把产物完整性级别设为 Medium（低权限目录里也能正常双击运行）")
    else:
        print("提示：如果从低完整性目录运行 exe 提示 Could not create temporary directory，")
        print(f'  可手动执行：icacls "{exe}" /setintegritylevel Medium')


def main() -> int:
    parser = argparse.ArgumentParser(description="打包轻连 VPN")
    parser.add_argument("--onedir", action="store_true", help="打成目录版而不是单文件")
    parser.add_argument("--no-clean", action="store_true", help="不清空构建缓存")
    parser.add_argument("--console", action="store_true", help="保留控制台窗口，排错用")
    parser.add_argument("--tmpdir", default=None, help="onefile 解压目录（默认用系统 %%TEMP%%）")
    ns = parser.parse_args()

    check_environment()
    exe = build(onefile=not ns.onedir, clean=not ns.no_clean, console=ns.console, tmpdir=ns.tmpdir)
    size_mb = exe.stat().st_size / (1024 * 1024)
    print(f"\n打包完成：{exe}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
