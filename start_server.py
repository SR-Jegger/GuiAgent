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
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Start GUI Agent FastAPI Server"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="192.168.137.1",
        help="Host to bind to (default: 192.168.137.1)"
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

    # Build uvicorn command
    cmd = [
        sys.executable, "-m", "uvicorn",
        "app.server:app",
        "--host", args.host,
        "--port", str(args.port),
    ]

    if args.reload:
        cmd.append("--reload")

    address = "192.168.137.1"
    print("=" * 60)
    print("Starting GUI Agent Server...")
    print("=" * 60)
    print(f"  Host: {args.host}")
    print(f"  Port: {args.port}")
    print(f"  Reload: {args.reload}")
    print("=" * 60)
    print(f"\nAPI Documentation: http://{address}:{args.port}/docs")
    print(f"Health Check: http://{address}:{args.port}/api/v1/health")
    print(f"\nPress Ctrl+C to stop the server\n")
    print("=" * 60)

    # Run uvicorn
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\n[Server] Stopped by user")
    except subprocess.CalledProcessError as e:
        print(f"\n[Server] Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
