#!/usr/bin/env python3
"""Start the Chalkboard API server."""
import argparse
import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev only — kills in-flight jobs on reload)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("server.app:app", host="0.0.0.0", port=args.port, reload=args.reload)
