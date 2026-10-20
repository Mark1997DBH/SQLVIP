"""SQLVIP desktop launcher.

Double-clicking SQLVIP.exe starts a local FastAPI server, opens the default
browser, and shows a small control window. Closing the window stops the server.

The same executable also supports a headless mode for smoke testing:
    SQLVIP.exe --headless --no-browser --port 8765
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import multiprocessing
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "SQLVIP"
VERSION = "1.0.0"
DEFAULT_PORT = 8000
HOST = "127.0.0.1"

_LOG_STREAM = None


def _bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


BASE_DIR = _bundle_root()
ASSETS_DIR = BASE_DIR / "assets"


def _configure_logging() -> Path:
    """Create a persistent log file and make windowed-mode stdio safe."""
    global _LOG_STREAM

    candidates = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / APP_NAME / "sqlvip.log")
    candidates.append(Path.home() / ".sqlvip" / "sqlvip.log")
    candidates.append(Path(tempfile.gettempdir()) / "SQLVIP" / "sqlvip.log")

    log_path = candidates[-1]
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            _LOG_STREAM = open(candidate, "a", encoding="utf-8", buffering=1)
            log_path = candidate
            break
        except OSError:
            continue

    if _LOG_STREAM is None:
        _LOG_STREAM = open(os.devnull, "a", encoding="utf-8")
        log_path = Path(os.devnull)

    if sys.stdout is None:
        sys.stdout = _LOG_STREAM
    if sys.stderr is None:
        sys.stderr = _LOG_STREAM

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=_LOG_STREAM,
        force=True,
    )
    return log_path


LOG_PATH = _configure_logging()


def _fatal(message: str) -> None:
    logging.error(message)
    try:
        ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x10)
    except Exception:
        pass


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((HOST, port))
        except OSError:
            return False
    return True


def choose_port(preferred: int) -> int:
    if preferred and _port_available(preferred):
        return preferred

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return int(sock.getsockname()[1])


class DesktopServer:
    def __init__(self, port: int) -> None:
        import uvicorn

        from app.main import app

        self.port = port
        self.url = f"http://{HOST}:{port}"
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=HOST,
                port=port,
                log_level="warning",
                access_log=False,
            )
        )
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.run,
            name="sqlvip-uvicorn",
            daemon=True,
        )
        self._thread.start()

    def wait_until_ready(self, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._thread and not self._thread.is_alive():
                return False
            try:
                with urllib.request.urlopen(f"{self.url}/api/health", timeout=0.6) as response:
                    if response.status == 200:
                        return True
            except Exception:
                time.sleep(0.12)
        return False

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)


def _show_window(server: DesktopServer, open_browser: bool) -> int:
    """Run the small desktop control window."""
    import tkinter as tk

    url = server.url
    root = tk.Tk()
    root.title(f"SQLVIP {VERSION}")
    root.configure(bg="#ffffff")
    root.resizable(False, False)

    icon_path = ASSETS_DIR / "sqlvip.ico"
    if icon_path.exists():
        try:
            root.iconbitmap(default=str(icon_path))
        except tk.TclError:
            pass

    width, height = 500, 332
    root.update_idletasks()
    x = max(0, (root.winfo_screenwidth() - width) // 2)
    y = max(0, (root.winfo_screenheight() - height) // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")

    header = tk.Canvas(root, height=112, highlightthickness=0, bd=0)
    header.pack(fill="x")
    for px in range(width):
        ratio = px / (width - 1)
        red = round(52 + (117 - 52) * ratio)
        green = round(86 + (82 - 86) * ratio)
        blue = round(238 + (244 - 238) * ratio)
        header.create_line(px, 0, px, 112, fill=f"#{red:02x}{green:02x}{blue:02x}")

    header.create_text(
        30,
        42,
        text="SQLVIP",
        anchor="w",
        fill="#ffffff",
        font=("Segoe UI", 25, "bold"),
    )
    header.create_text(
        32,
        76,
        text="SQL 方言转换器  ·  本地运行",
        anchor="w",
        fill="#e7eaff",
        font=("Microsoft YaHei UI", 10),
    )

    # Small database mark on the right side of the header.
    header.create_oval(414, 30, 468, 49, outline="#ffffff", width=3)
    header.create_line(414, 40, 414, 83, fill="#ffffff", width=3)
    header.create_line(468, 40, 468, 83, fill="#ffffff", width=3)
    header.create_arc(414, 63, 468, 83, start=0, extent=180, style="arc", outline="#ffffff", width=3)
    header.create_arc(414, 51, 468, 71, start=0, extent=180, style="arc", outline="#ffffff", width=3)

    body = tk.Frame(root, bg="#ffffff")
    body.pack(fill="both", expand=True)

    status_row = tk.Frame(body, bg="#ffffff")
    status_row.pack(anchor="w", padx=30, pady=(23, 4))
    dot = tk.Canvas(status_row, width=12, height=12, bg="#ffffff", highlightthickness=0)
    dot.create_oval(2, 2, 10, 10, fill="#0e9f6e", outline="")
    dot.pack(side="left", padx=(0, 7))
    tk.Label(
        status_row,
        text="服务已启动",
        bg="#ffffff",
        fg="#0e9f6e",
        font=("Microsoft YaHei UI", 10, "bold"),
    ).pack(side="left")

    url_row = tk.Frame(body, bg="#ffffff")
    url_row.pack(anchor="w", fill="x", padx=30)
    url_label = tk.Label(
        url_row,
        text=url,
        bg="#ffffff",
        fg="#3d5aff",
        cursor="hand2",
        font=("Consolas", 12, "underline"),
    )
    url_label.pack(side="left")
    url_label.bind("<Button-1>", lambda _event: _open_url(url))

    def copy_url() -> None:
        root.clipboard_clear()
        root.clipboard_append(url)
        copy_button.configure(text="已复制")
        root.after(1400, lambda: copy_button.configure(text="复制地址"))

    copy_button = tk.Button(
        url_row,
        text="复制地址",
        command=copy_url,
        bg="#f2f4f8",
        fg="#536179",
        activebackground="#e8ecf4",
        activeforeground="#344054",
        bd=0,
        relief="flat",
        highlightthickness=0,
        cursor="hand2",
        font=("Microsoft YaHei UI", 9),
        padx=9,
        pady=3,
    )
    copy_button.pack(side="left", padx=(12, 0))

    tk.Label(
        body,
        text="浏览器将自动打开 SQLVIP 页面；也可以点击下方按钮重新打开。",
        bg="#ffffff",
        fg="#7b8798",
        font=("Microsoft YaHei UI", 9),
    ).pack(anchor="w", padx=30, pady=(8, 0))

    actions = tk.Frame(body, bg="#ffffff")
    actions.pack(anchor="w", fill="x", padx=30, pady=(20, 0))

    primary = tk.Button(
        actions,
        text="打开 SQLVIP",
        command=lambda: _open_url(url),
        bg="#3d5aff",
        fg="#ffffff",
        activebackground="#2942d8",
        activeforeground="#ffffff",
        bd=0,
        relief="flat",
        highlightthickness=0,
        cursor="hand2",
        font=("Microsoft YaHei UI", 10, "bold"),
        padx=24,
        pady=9,
    )
    primary.pack(side="left")

    def close_window() -> None:
        if getattr(root, "_sqlvip_closing", False):
            return
        root._sqlvip_closing = True
        root.destroy()

    tk.Button(
        actions,
        text="退出",
        command=close_window,
        bg="#eef1f6",
        fg="#344054",
        activebackground="#e1e6ef",
        activeforeground="#1d2939",
        bd=0,
        relief="flat",
        highlightthickness=0,
        cursor="hand2",
        font=("Microsoft YaHei UI", 10),
        padx=24,
        pady=9,
    ).pack(side="left", padx=(10, 0))

    tk.Label(
        body,
        text="关闭此窗口即可退出 SQLVIP 服务",
        bg="#ffffff",
        fg="#a0a9b7",
        font=("Microsoft YaHei UI", 8),
    ).pack(side="bottom", pady=(0, 16))

    root.protocol("WM_DELETE_WINDOW", close_window)
    root.attributes("-topmost", True)
    root.after(650, lambda: root.attributes("-topmost", False))

    def _open_url(target: str) -> None:
        try:
            webbrowser.open(target, new=2)
        except Exception:
            logging.exception("Could not open browser")

    if open_browser:
        root.after(350, lambda: _open_url(url))

    if os.environ.get("SQLVIP_SMOKE_TEST") == "1":
        root.after(1500, close_window)

    try:
        root.mainloop()
    finally:
        server.stop()
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SQLVIP desktop launcher")
    parser.add_argument("--port", type=int, default=0, help="preferred local port")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="run the local server without opening the control window",
    )
    return parser.parse_args()


def main() -> int:
    multiprocessing.freeze_support()
    args = _parse_args()
    port = choose_port(args.port or DEFAULT_PORT)
    logging.info("Starting SQLVIP %s on port %s (frozen=%s)", VERSION, port, getattr(sys, "frozen", False))

    try:
        server = DesktopServer(port)
        server.start()
    except Exception as exc:
        logging.exception("Could not create SQLVIP server")
        _fatal(
            f"SQLVIP 初始化失败：{exc}\n\n"
            f"请查看日志：{LOG_PATH}"
        )
        return 1

    if not server.wait_until_ready():
        message = (
            f"SQLVIP 服务启动失败。\n\n"
            f"请查看日志：{LOG_PATH}\n"
            f"也可以尝试指定其他端口：SQLVIP.exe --port 8010"
        )
        _fatal(message)
        server.stop()
        return 1

    logging.info("SQLVIP ready at %s", server.url)

    if args.headless:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            server.stop()
        return 0

    try:
        return _show_window(server, open_browser=not args.no_browser)
    except Exception as exc:
        logging.exception("Control window failed")
        _fatal(
            f"无法打开 SQLVIP 控制窗口：{exc}\n\n"
            f"服务可能仍在运行，请手动访问：{server.url}\n"
            f"日志：{LOG_PATH}"
        )
        server.stop()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
