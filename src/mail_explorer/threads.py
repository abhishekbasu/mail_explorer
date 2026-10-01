"""Conversation links live in SQLite, including placeholders for missing ancestors."""

import json
import re
import sqlite3
from datetime import UTC
from email.message import Message
from email.utils import parsedate_to_datetime


def identifiers(value: str) -> list[str]:
    values = re.findall(r"<[^<>\s]+>", value)
    # Tolerate a common malformed Message-ID without angle brackets.
    if not values and "@" in value and not re.search(r"\s", value.strip()):
        values = [f"<{value.strip()}>"]
    values = [value for value in values if len(value) <= 1024]
    return list(dict.fromkeys(values[:64] + values[-64:]))


def threading_headers(message: Message) -> dict:
    def field(name: str, limit: int = 4096) -> str:
        try:
            return str(message.get(name, ""))[:limit]
        except (ValueError, IndexError):
            return ""

    gmail_id = field("X-GM-THRID").strip()
    if not re.fullmatch(r"\d{1,30}", gmail_id):
        gmail_id = ""
    message_ids = identifiers(field("Message-ID"))
    sent_at = 0
    try:
        date = parsedate_to_datetime(field("Date"))
        sent_at = int((date if date.tzinfo else date.replace(tzinfo=UTC)).timestamp())
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    return {
        "gmail_thread_id": gmail_id,
        "message_id": message_ids[0] if message_ids else "",
        "in_reply_to": identifiers(field("In-Reply-To", 65536)),
        "reference_ids": identifiers(field("References", 65536)),
        "sent_at": sent_at,
    }


def initialize_threads(db: sqlite3.Connection) -> None:
    columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
    additions = {
        "thread_id": "INTEGER",
        "sent_at": "INTEGER NOT NULL DEFAULT 0",
        "gmail_thread_id": "TEXT NOT NULL DEFAULT ''",
        "message_id": "TEXT NOT NULL DEFAULT ''",
        "in_reply_to": "TEXT NOT NULL DEFAULT '[]'",
        "reference_ids": "TEXT NOT NULL DEFAULT '[]'",
    }
    for column, declaration in additions.items():
        if column not in columns:
            db.execute(f"ALTER TABLE messages ADD COLUMN {column} {declaration}")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_id TEXT UNIQUE,
            message_count INTEGER NOT NULL DEFAULT 0,
            latest_message_id INTEGER,
            latest_timestamp INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS thread_nodes (
            message_id TEXT PRIMARY KEY,
            thread_id INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS thread_nodes_by_thread ON thread_nodes(thread_id);
        CREATE TABLE IF NOT EXISTS thread_aliases (
            id INTEGER PRIMARY KEY,
            thread_id INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS thread_aliases_by_thread ON thread_aliases(thread_id);
        CREATE INDEX IF NOT EXISTS messages_by_thread ON messages(thread_id, sent_at, id);
        CREATE INDEX IF NOT EXISTS threads_by_activity
            ON threads(latest_timestamp DESC, latest_message_id DESC);
    """)


class ThreadStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.count = int(
            db.execute("SELECT value FROM metadata WHERE key='thread_count'").fetchone()[0]
        )

    def _thread(self, thread_id: int) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()

    def _merge(self, target_id: int, source_id: int) -> None:
        if target_id == source_id:
            return
        target, source = self._thread(target_id), self._thread(source_id)
        # Gmail's explicit grouping wins even when reply headers cross conversations.
        if source["gmail_id"] and source["gmail_id"] != target["gmail_id"]:
            return
        latest = target
        if source["latest_message_id"] is not None and (
            target["latest_message_id"] is None
            or (source["latest_timestamp"], source["latest_message_id"])
            > (target["latest_timestamp"], target["latest_message_id"])
        ):
            latest = source
        self.db.execute(
            "UPDATE threads SET message_count=?, latest_message_id=?, latest_timestamp=? WHERE id=?",
            (
                target["message_count"] + source["message_count"],
                latest["latest_message_id"],
                latest["latest_timestamp"],
                target_id,
            ),
        )
        self.db.execute("UPDATE messages SET thread_id=? WHERE thread_id=?", (target_id, source_id))
        self.db.execute(
            "UPDATE thread_nodes SET thread_id=? WHERE thread_id=?", (target_id, source_id)
        )
        self.db.execute(
            "UPDATE thread_aliases SET thread_id=? WHERE thread_id=?", (target_id, source_id)
        )
        self.db.execute("INSERT INTO thread_aliases VALUES (?, ?)", (source_id, target_id))
        self.db.execute("DELETE FROM threads WHERE id=?", (source_id,))
        self.count -= 1

    def add(self, record_id: int, fields: dict) -> None:
        # At most 257 header identifiers; no in-memory graph of the archive.
        node_ids = list(
            dict.fromkeys(
                list(reversed(fields["in_reply_to"]))
                + list(reversed(fields["reference_ids"]))
                + ([fields["message_id"]] if fields["message_id"] else [])
            )
        )
        candidates = {}
        for identifier in node_ids:
            row = self.db.execute(
                "SELECT t.* FROM thread_nodes n JOIN threads t ON t.id=n.thread_id "
                "WHERE n.message_id=?",
                (identifier,),
            ).fetchone()
            if row is not None:
                candidates[row["id"]] = row

        gmail_id = fields["gmail_thread_id"]
        if gmail_id:
            chosen = self.db.execute(
                "SELECT id FROM threads WHERE gmail_id=?", (gmail_id,)
            ).fetchone()
            thread_id = chosen[0] if chosen else None
        else:
            provider = next((row for row in candidates.values() if row["gmail_id"]), None)
            thread_id = provider["id"] if provider else min(candidates, default=None)
        if thread_id is None:
            thread_id = self.db.execute(
                "INSERT INTO threads(gmail_id) VALUES (?)",
                (gmail_id or None,),
            ).lastrowid
            self.count += 1
        for candidate_id in candidates:
            self._merge(thread_id, candidate_id)
        for identifier in node_ids:
            self.db.execute(
                "INSERT OR IGNORE INTO thread_nodes VALUES (?, ?)", (identifier, thread_id)
            )
        if fields["message_id"]:
            # An actual message supersedes a placeholder for its own identity.
            self.db.execute(
                "UPDATE thread_nodes SET thread_id=? WHERE message_id=?",
                (thread_id, fields["message_id"]),
            )
        self.db.execute(
            "UPDATE messages SET thread_id=?, sent_at=?, gmail_thread_id=?, message_id=?, "
            "in_reply_to=?, reference_ids=? WHERE id=?",
            (
                thread_id,
                fields["sent_at"],
                gmail_id,
                fields["message_id"],
                json.dumps(fields["in_reply_to"]),
                json.dumps(fields["reference_ids"]),
                record_id,
            ),
        )
        root = self._thread(thread_id)
        latest_id, latest_timestamp = root["latest_message_id"], root["latest_timestamp"]
        if latest_id is None or (fields["sent_at"], record_id) > (latest_timestamp, latest_id):
            latest_id, latest_timestamp = record_id, fields["sent_at"]
        self.db.execute(
            "UPDATE threads SET message_count=message_count+1, latest_message_id=?, "
            "latest_timestamp=? WHERE id=?",
            (latest_id, latest_timestamp, thread_id),
        )

    def checkpoint(self, message_id: int) -> None:
        self.db.executemany(
            "UPDATE metadata SET value=? WHERE key=?",
            [
                (str(message_id), "thread_checkpoint"),
                (str(self.count), "thread_count"),
            ],
        )
