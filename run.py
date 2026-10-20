"""SQLVIP development server entry point.

Usage:
    python run.py
    python run.py --port 9000 --host 0.0.0.0
"""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 SQLVIP SQL 方言转换服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", default=8000, type=int, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="开发模式，自动重载")
    args = parser.parse_args()

    print(f"\n  SQLVIP 已启动： http://{args.host}:{args.port}\n")
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
