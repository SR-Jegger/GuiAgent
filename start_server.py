#!/usr/bin/env python3
"""
Start the GUI Agent FastAPI server.

Usage:
    python start_server.py [--host HOST] [--port PORT] [--reload]

Examples:
    python start_server.py                    # Default: 0.0.0.0:8000
    python start_server.py --port 8080        # Custom port
    python start_server.py --reload           # Auto-reload on code changes
"""

import argparse
import sys
import signal


def main():
    parser = argparse.ArgumentParser(
        description="Start GUI Agent FastAPI Server"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to (default: 8000)"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload on code changes"
    )

    args = parser.parse_args()

    address = args.host
    print("=" * 60)
    print("Starting GUI Agent Server...")
    print("=" * 60)
    print(f"  Host: {args.host}")
    print(f"  Port: {args.port}")
    print(f"  Reload: {args.reload}")
    print("=" * 60)
    print(f"\nAPI Documentation: http://{address}:{args.port}/docs")
    print(f"Visualizer: http://{address}:{args.port}/dashboard")
    print(f"Health Check: http://{address}:{args.port}/api/v1/health")
    print(f"\nPress Ctrl+C to stop the server\n")
    print("=" * 60)

    # 直接运行 uvicorn（而非 subprocess），以便正确处理信号
    import uvicorn

    try:
        uvicorn.run(
            "app.server:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
    except KeyboardInterrupt:
        print("\n[Server] Stopped by user")
    except Exception as e:
        print(f"\n[Server] Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
