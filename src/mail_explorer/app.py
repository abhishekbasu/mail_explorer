import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .attachments import ENCODINGS, IMAGE_TYPES, attachment_chunks, attachments, disposition
from .config import Settings
from .index import MailboxCatalog, MailboxChanged
from .messages import preview, raw_chunks

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    mailbox_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or Settings(
        mailbox_dir=Path(mailbox_dir or os.getenv("MAILBOX_DIR", "mailbox")).resolve(),
        cache_dir=Path(cache_dir or os.getenv("MAIL_EXPLORER_CACHE", ".mail-explorer")).resolve(),
    )
    catalog = MailboxCatalog(settings)
    preview_slots = threading.BoundedSemaphore(2)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        catalog.close()

    app = FastAPI(title="Mail Explorer", lifespan=lifespan)
    app.state.catalog = catalog
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; frame-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    async def not_found(request: Request, exc: KeyError):
        return JSONResponse({"detail": str(exc.args[0])}, status_code=404)

    @app.exception_handler(MailboxChanged)
    async def changed(request: Request, exc: MailboxChanged):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/mailboxes")
    def mailboxes():
        return {"items": catalog.list_mailboxes()}

    @app.get("/api/mailboxes/{mailbox_id}/status")
    def status(mailbox_id: str):
        return catalog.get(mailbox_id).status()

    @app.post("/api/mailboxes/{mailbox_id}/index")
    def start_index(mailbox_id: str):
        return catalog.start(mailbox_id).status()

    @app.post("/api/mailboxes/{mailbox_id}/pause")
    def pause_index(mailbox_id: str):
        index = catalog.get(mailbox_id)
        index.pause()
        return index.status()

    @app.get("/api/mailboxes/{mailbox_id}/messages")
    def messages(
        mailbox_id: str,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        q: str = Query(default="", max_length=200),
        before: int | None = Query(default=None, ge=1),
    ):
        return catalog.get(mailbox_id).list_messages(after, limit, q, before)

    @app.get("/api/mailboxes/{mailbox_id}/threads")
    def threads(
        mailbox_id: str,
        after: str = Query(default="", pattern=r"^(?:-?\d{1,12}:\d{1,18})?$"),
        limit: int = Query(default=50, ge=1, le=100),
        q: str = Query(default="", max_length=200),
        before: str = Query(default="", pattern=r"^(?:-?\d{1,12}:\d{1,18})?$"),
    ):
        return catalog.get(mailbox_id).list_threads(after, limit, q, before)

    @app.get("/api/mailboxes/{mailbox_id}/threads/{thread_id}/messages")
    def thread_messages(
        mailbox_id: str,
        thread_id: int,
        after: str = Query(default="", pattern=r"^(?:-?\d{1,12}:\d{1,18})?$"),
        limit: int = Query(default=50, ge=1, le=100),
        order: Literal["oldest", "newest"] = "oldest",
    ):
        return catalog.get(mailbox_id).thread_messages(thread_id, after, limit, order)

    @app.get("/api/mailboxes/{mailbox_id}/messages/{message_id}")
    def message(mailbox_id: str, message_id: int):
        index = catalog.get(mailbox_id)
        record = index.message(message_id)
        with preview_slots:
            try:
                result = preview(index.path, record, settings)
            except (ValueError, RecursionError, TypeError) as exc:
                raise HTTPException(
                    422, "This message cannot be previewed. Download its original."
                ) from exc
        index.ensure_current()
        return result

    @app.get("/api/mailboxes/{mailbox_id}/messages/{message_id}/attachments/{attachment_id}")
    def attachment(mailbox_id: str, message_id: int, attachment_id: int, download: bool = True):
        index = catalog.get(mailbox_id)
        record = index.message(message_id)
        with preview_slots:
            try:
                item = next(
                    (
                        item
                        for item in attachments(index.path, record, settings)
                        if item.id == attachment_id
                    ),
                    None,
                )
            except (ValueError, RecursionError, TypeError) as exc:
                raise HTTPException(
                    422, "This attachment cannot be opened. Download the original message."
                ) from exc
        index.ensure_current()
        if item is None:
            raise HTTPException(404, "Attachment not found.")
        if item.encoding not in ENCODINGS:
            raise HTTPException(422, "Unsupported encoding. Download the original message.")
        if not download and item.content_type not in IMAGE_TYPES:
            raise HTTPException(415, "This file cannot be previewed. Download the attachment.")
        return StreamingResponse(
            attachment_chunks(index.path, item, settings.chunk_bytes),
            media_type="application/octet-stream" if download else item.content_type,
            headers={"Content-Disposition": disposition(item, download)},
        )

    @app.get("/api/mailboxes/{mailbox_id}/messages/{message_id}/raw")
    def raw(mailbox_id: str, message_id: int):
        index = catalog.get(mailbox_id)
        record = index.message(message_id)
        return StreamingResponse(
            raw_chunks(index.path, record["content_start"], record["end"], settings.chunk_bytes),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="message-{message_id}.eml"',
                "Content-Length": str(record["end"] - record["content_start"]),
            },
        )

    return app


app = create_app()
