def main() -> None:
    import argparse
    import os

    import uvicorn

    parser = argparse.ArgumentParser(description="Browse local mbox archives in your browser.")
    parser.add_argument("--mailbox-dir", default=os.getenv("MAILBOX_DIR", "mailbox"))
    parser.add_argument("--cache-dir", default=os.getenv("MAIL_EXPLORER_CACHE", ".mail-explorer"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    from .app import create_app

    uvicorn.run(
        create_app(mailbox_dir=args.mailbox_dir, cache_dir=args.cache_dir),
        host=args.host,
        port=args.port,
    )
