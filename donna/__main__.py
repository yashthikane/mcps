"""Start Donna: `python -m donna` → http://127.0.0.1:8765 (opens the browser)."""
import argparse
import threading
import webbrowser

import uvicorn

from . import ROOT


def main() -> None:
    parser = argparse.ArgumentParser(prog="donna", description="Run the Donna web app locally.")
    parser.add_argument("command", nargs="?", choices=["serve", "migrate"], default="serve",
                        help="serve (default) or migrate: apply PostgreSQL migrations and exit")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes (development)")
    args = parser.parse_args()

    if args.command == "migrate":
        raise SystemExit(migrate())

    url = f"http://127.0.0.1:{args.port}"
    if not (ROOT / "web" / "dist" / "index.html").exists():
        print("The web UI isn't built yet. Run:  cd web; npm install; npm run build   (or scripts\\start.ps1)")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Donna is running at {url}  (Ctrl+C to stop)")
    uvicorn.run("donna.app:app", host="127.0.0.1", port=args.port, reload=args.reload,
                reload_dirs=[str(ROOT / "donna"), str(ROOT / "tools")] if args.reload else None, log_level="warning")


def migrate() -> int:
    import asyncio

    from . import pg

    async def run() -> int:
        db = pg.from_vault()
        if not await db.open():
            print(db.error)
            return 1
        try:
            applied = await db.migrate()
        finally:
            await db.close()
        print(f"Applied: {', '.join(applied)}" if applied else "PostgreSQL is up to date.")
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    main()
