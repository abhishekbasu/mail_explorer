"""Disk-backed offsets and headers. No mailbox-sized Python collections or mmap."""

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from email import policy
from email.parser import BytesHeaderParser
from pathlib import Path
from typing import BinaryIO

from .config import Settings
from .threads import ThreadStore, initialize_threads, threading_headers

SCHEMA_VERSION = 2
HEADER_FIELD_CHARS = 4096


class MailboxChanged(ValueError):
    pass


def fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def message_ranges(
    path: Path,
    start: int,
    size: int,
    stop: threading.Event,
    progress: Callable[[int], None],
    chunk_bytes: int,
) -> Iterator[tuple[int, int]]:
    """Find line-leading mbox separators using fixed-size reads, including split markers."""
    if start >= size:
        return
    with path.open("rb") as source:
        source.seek(start)
        if source.read(5) != b"From ":
            raise ValueError("This file does not start with a valid mbox 'From ' separator.")
        source.seek(start)
        current = start
        position = start
        tail = b""
        while position < size:
            if stop.is_set():
                return
            chunk = source.read(min(chunk_bytes, size - position))
            if not chunk:
                raise MailboxChanged("The mailbox changed during indexing. Reopen it.")
            data = tail + chunk
            base = position - len(tail)
            cursor = 0
            while (found := data.find(b"\nFrom ", cursor)) != -1:
                boundary = base + found + 1
                cursor = found + 6
                if boundary <= current:
                    continue
                if stop.is_set():
                    return
                yield current, boundary
                current = boundary
            position += len(chunk)
            tail = data[-5:]
            progress(position)
        if not stop.is_set():
            yield current, size


def read_headers(source: BinaryIO, start: int, end: int, limit: int) -> tuple[int, dict]:
    source.seek(start)
    envelope = source.readline(min(limit, end - start))
    if not envelope.endswith(b"\n"):
        raise ValueError("An mbox envelope line exceeds the header limit or is incomplete.")
    content_start = source.tell()
    header = bytearray()
    while len(header) < limit and source.tell() < end:
        line = source.readline(min(limit - len(header), end - source.tell()))
        header.extend(line)
        if not line or line in {b"\n", b"\r\n"}:
            break
    # Stop at the blank line, including during upgrades of saved offsets.
    message = BytesHeaderParser(policy=policy.default).parsebytes(bytes(header))

    def field(name: str, fallback: str = "") -> str:
        try:
            return str(message.get(name, fallback))[:HEADER_FIELD_CHARS]
        except (ValueError, IndexError):
            return fallback

    return content_start, {
        "subject": field("Subject", "(No subject)"),
        "sender": field("From", "(Unknown sender)"),
        "recipients": field("To"),
        "date": field("Date"),
        "threading": threading_headers(message),
    }


class MailboxIndex:
    def __init__(self, path: Path, mailbox_id: str, settings: Settings):
        self.path = path
        self.id = mailbox_id
        self.settings = settings
        self.signature = fingerprint(path)
        self.size = self.signature["size"]
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = settings.cache_dir / f"{mailbox_id}.sqlite3"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._scanned = 0
        self._error: str | None = None
        self._initialize()

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA cache_size = -4096")
        connection.execute("PRAGMA mmap_size = 0")
        connection.execute("PRAGMA temp_store = FILE")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connection() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY,
                    start INTEGER NOT NULL,
                    content_start INTEGER NOT NULL,
                    end INTEGER NOT NULL,
                    subject TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    recipients TEXT NOT NULL,
                    date TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS message_search USING fts5(
                    subject, sender, recipients, content='messages', content_rowid='id'
                );
            """)
            initialize_threads(db)
            expected = json.dumps(
                {"version": SCHEMA_VERSION, "file": self.signature}, sort_keys=True
            )
            existing = db.execute("SELECT value FROM metadata WHERE key = 'signature'").fetchone()
            saved = json.loads(existing[0]) if existing else {}
            compatible = saved.get("file") == self.signature and saved.get("version") in {1, 2}
            if not compatible:
                db.execute("DELETE FROM messages")
                db.execute("INSERT INTO message_search(message_search) VALUES ('rebuild')")
                db.execute("DELETE FROM thread_nodes")
                db.execute("DELETE FROM thread_aliases")
                db.execute("DELETE FROM threads")
                db.execute("DELETE FROM metadata")
                db.executemany(
                    "INSERT INTO metadata VALUES (?, ?)",
                    [
                        ("signature", expected),
                        ("checkpoint", "0"),
                        ("count", "0"),
                        ("complete", "0"),
                    ],
                )
            else:
                db.execute("UPDATE metadata SET value=? WHERE key='signature'", (expected,))
            db.executemany(
                "INSERT OR IGNORE INTO metadata VALUES (?, ?)",
                [
                    ("thread_checkpoint", "0"),
                    ("thread_count", "0"),
                ],
            )
            db.commit()
            self._scanned = int(self._metadata(db)["checkpoint"])

    @staticmethod
    def _metadata(db: sqlite3.Connection) -> dict:
        return dict(db.execute("SELECT key, value FROM metadata").fetchall())

    def ensure_current(self) -> None:
        if fingerprint(self.path) != self.signature:
            raise MailboxChanged("The mailbox changed. Refresh the page to rebuild its index.")

    def status(self) -> dict:
        with self.connection() as db:
            meta = self._metadata(db)
        active = self._thread is not None and self._thread.is_alive()
        count = int(meta["count"])
        threaded = int(meta["thread_checkpoint"])
        complete = meta["complete"] == "1" and threaded >= count
        state = (
            "error"
            if self._error
            else "complete"
            if complete
            else "indexing"
            if active
            else "paused"
        )
        scanned = max(int(meta["checkpoint"]), self._scanned)
        return {
            "state": state,
            "indexed_messages": int(meta["count"]),
            "threaded_messages": threaded,
            "thread_count": int(meta["thread_count"]),
            "phase": "grouping" if threaded < count else "scanning",
            "threading_progress": min(1, threaded / count) if count else 1,
            "scanned_bytes": scanned,
            "total_bytes": self.size,
            "progress": min(1, scanned / self.size) if self.size else 1,
            "error": self._error,
        }

    def start(self) -> None:
        with self._lock:
            self.ensure_current()
            if self._thread is not None and self._thread.is_alive():
                return
            with self.connection() as db:
                meta = self._metadata(db)
                if meta["complete"] == "1" and int(meta["thread_checkpoint"]) >= int(meta["count"]):
                    return
            self._stop.clear()
            self._error = None
            self._thread = threading.Thread(target=self._run, name=f"index-{self.id}", daemon=True)
            self._thread.start()

    def pause(self) -> None:
        with self._lock:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            with self.connection() as db, self.path.open("rb") as headers:
                store = ThreadStore(db)
                self._backfill_threads(db, headers, store)
                if self._stop.is_set():
                    return
                meta = self._metadata(db)
                if meta["complete"] == "1":
                    return
                checkpoint = int(meta["checkpoint"])
                count = int(meta["count"])
                batch: list[tuple] = []
                thread_fields: list[dict] = []
                last_commit = time.monotonic()
                last_check = last_commit

                def progress(position: int) -> None:
                    nonlocal last_check
                    self._scanned = position
                    if time.monotonic() - last_check > 1:
                        self.ensure_current()
                        last_check = time.monotonic()

                def commit(complete: bool = False) -> None:
                    nonlocal last_commit
                    self.ensure_current()
                    with db:
                        db.executemany(
                            "INSERT INTO messages(id, start, content_start, end, subject, sender, "
                            "recipients, date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            batch,
                        )
                        for row, fields in zip(batch, thread_fields, strict=True):
                            store.add(row[0], fields)
                        store.checkpoint(count)
                        db.executemany(
                            "INSERT INTO message_search(rowid, subject, sender, recipients) "
                            "VALUES (?, ?, ?, ?)",
                            [(row[0], row[4], row[5], row[6]) for row in batch],
                        )
                        db.executemany(
                            "UPDATE metadata SET value = ? WHERE key = ?",
                            [
                                (str(checkpoint), "checkpoint"),
                                (str(count), "count"),
                                ("1" if complete else "0", "complete"),
                            ],
                        )
                    batch.clear()
                    thread_fields.clear()
                    last_commit = time.monotonic()

                for start, end in message_ranges(
                    self.path,
                    checkpoint,
                    self.size,
                    self._stop,
                    progress,
                    self.settings.chunk_bytes,
                ):
                    content_start, fields = read_headers(
                        headers, start, end, self.settings.header_bytes
                    )
                    count += 1
                    batch.append(
                        (
                            count,
                            start,
                            content_start,
                            end,
                            fields["subject"],
                            fields["sender"],
                            fields["recipients"],
                            fields["date"],
                        )
                    )
                    thread_fields.append(fields["threading"])
                    checkpoint = end
                    if len(batch) >= 64 or time.monotonic() - last_commit >= 0.25 or count == 1:
                        commit()
                commit(complete=checkpoint == self.size)
        except Exception as exc:
            logging.getLogger(__name__).exception("Mailbox indexing failed")
            self._error = str(exc)

    def _backfill_threads(self, db, headers, store: ThreadStore) -> None:
        checkpoint = int(self._metadata(db)["thread_checkpoint"])
        while not self._stop.is_set():
            rows = db.execute(
                "SELECT id, start, end FROM messages WHERE id>? ORDER BY id LIMIT 64",
                (checkpoint,),
            ).fetchall()
            if not rows:
                return
            self.ensure_current()
            with db:
                for row in rows:
                    if self._stop.is_set():
                        break
                    _, fields = read_headers(
                        headers, row["start"], row["end"], self.settings.header_bytes
                    )
                    store.add(row["id"], fields["threading"])
                    checkpoint = row["id"]
                store.checkpoint(checkpoint)

    @staticmethod
    def search_expression(query: str) -> str:
        words = re.findall(r"[^\W_]+", query, flags=re.UNICODE)[:12]
        return " AND ".join(f'"{word}"*' for word in words)

    def list_messages(self, after: int = 0, limit: int = 50, query: str = "") -> dict:
        self.ensure_current()
        expression = self.search_expression(query)
        with self.connection() as db:
            columns = (
                "m.id, m.subject, m.sender, m.recipients, m.date, m.end - m.content_start AS size"
            )
            if query.strip() and not expression:
                rows = []
            elif expression:
                rows = db.execute(
                    f"SELECT {columns} FROM message_search s JOIN messages m ON m.id = s.rowid "
                    "WHERE message_search MATCH ? AND s.rowid > ? ORDER BY s.rowid LIMIT ?",
                    (expression, after, limit + 1),
                ).fetchall()
            else:
                rows = db.execute(
                    f"SELECT {columns} FROM messages m WHERE m.id > ? ORDER BY m.id LIMIT ?",
                    (after, limit + 1),
                ).fetchall()
        items = [dict(row) for row in rows[:limit]]
        return {
            "items": items,
            "next_cursor": items[-1]["id"] if len(rows) > limit else None,
            "status": self.status(),
        }

    @staticmethod
    def _thread_columns() -> str:
        return (
            "t.id, t.message_count, t.latest_timestamp, t.latest_message_id, "
            "m.subject, m.sender, m.recipients, m.date, m.end-m.content_start AS size"
        )

    def list_threads(self, after: str = "", limit: int = 50, query: str = "") -> dict:
        self.ensure_current()
        conditions, params = [], []
        if after:
            conditions.append("(t.latest_timestamp, t.latest_message_id) < (?, ?)")
            params.extend(map(int, after.split(":")))
        expression = self.search_expression(query)
        if query.strip() and not expression:
            conditions.append("0")
        elif expression:
            conditions.append(
                "t.id IN (SELECT m.thread_id FROM message_search s "
                "JOIN messages m ON m.id=s.rowid WHERE message_search MATCH ?)"
            )
            params.append(expression)
        clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connection() as db:
            rows = db.execute(
                f"SELECT {self._thread_columns()} FROM threads t "
                "JOIN messages m ON m.id=t.latest_message_id"
                + clause
                + " ORDER BY t.latest_timestamp DESC, t.latest_message_id DESC LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
        items = [dict(row) for row in rows[:limit]]
        last = items[-1] if items else None
        return {
            "items": items,
            "status": self.status(),
            "next_cursor": f"{last['latest_timestamp']}:{last['latest_message_id']}"
            if len(rows) > limit
            else None,
        }

    def thread_messages(self, thread_id: int, after: str = "", limit: int = 50) -> dict:
        self.ensure_current()
        with self.connection() as db:
            # A background merge must not change the group between these reads.
            db.execute("BEGIN")
            alias = db.execute(
                "SELECT thread_id FROM thread_aliases WHERE id=?", (thread_id,)
            ).fetchone()
            thread_id = alias[0] if alias else thread_id
            thread = db.execute(
                f"SELECT {self._thread_columns()} FROM threads t "
                "JOIN messages m ON m.id=t.latest_message_id WHERE t.id=?",
                (thread_id,),
            ).fetchone()
            if thread is None:
                raise KeyError("Conversation not found in the indexed portion of this mailbox.")
            clause, params = "", [thread_id]
            if after:
                clause = " AND (sent_at, id) > (?, ?)"
                params.extend(map(int, after.split(":")))
            rows = db.execute(
                "SELECT id, subject, sender, recipients, date, sent_at, end-content_start AS size "
                "FROM messages WHERE thread_id=?" + clause + " ORDER BY sent_at, id LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
        items = [dict(row) for row in rows[:limit]]
        last = items[-1] if items else None
        return {
            "thread": dict(thread),
            "items": items,
            "next_cursor": f"{last['sent_at']}:{last['id']}" if len(rows) > limit else None,
        }

    def message(self, message_id: int) -> dict:
        self.ensure_current()
        with self.connection() as db:
            row = db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise KeyError("Message not found in the indexed portion of this mailbox.")
        return dict(row)


class MailboxCatalog:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._indexes: dict[str, MailboxIndex] = {}
        self._lock = threading.RLock()

    def files(self) -> dict[str, Path]:
        root = self.settings.mailbox_dir
        paths = sorted(path for path in root.glob("*") if path.suffix.lower() in {".mbox", ".mbx"})
        return {
            hashlib.sha256(path.name.encode()).hexdigest()[:16]: path.resolve()
            for path in paths
            if path.is_file() and path.resolve().parent == root
        }

    def list_mailboxes(self) -> list[dict]:
        return [
            {"id": key, "name": path.name, "size": path.stat().st_size}
            for key, path in self.files().items()
        ]

    def get(self, mailbox_id: str) -> MailboxIndex:
        path = self.files().get(mailbox_id)
        if path is None:
            raise KeyError("Mailbox not found. Place .mbox files in the mailbox directory.")
        with self._lock:
            index = self._indexes.get(mailbox_id)
            if index is not None and index.signature != fingerprint(path):
                index.pause()
                index = None
            if index is None:
                index = MailboxIndex(path, mailbox_id, self.settings)
                self._indexes[mailbox_id] = index
            return index

    def start(self, mailbox_id: str) -> MailboxIndex:
        with self._lock:
            index = self.get(mailbox_id)
            # One scanner at a time, even when the user switches archives.
            for key, other in self._indexes.items():
                if key != mailbox_id:
                    other.pause()
            index.start()
            return index

    def close(self) -> None:
        with self._lock:
            for index in self._indexes.values():
                index.pause()
