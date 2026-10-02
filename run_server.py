#!/usr/bin/env python3
"""Start the Chalkboard API server."""
import argparse
import uvicorn
from config import SERVER_HOST, SERVER_PORT

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev only — kills in-flight jobs on reload)")
    parser.add_argument("--port", type=int, default=SERVER_PORT)
    parser.add_argument("--host", default=SERVER_HOST,
                        help="Bind address (default 127.0.0.1; use 0.0.0.0 to serve your LAN; there is no auth)")
    args = parser.parse_args()
    reload_kwargs = {}
    if args.reload:
        reload_kwargs["reload_dirs"] = ["."]
        reload_kwargs["reload_excludes"] = ["output/*", "output/**/*", "*.db", "pipeline_state.db"]
    uvicorn.run("server.app:app", host=args.host, port=args.port, reload=args.reload, **reload_kwargs)
