import json
import sqlite3
import threading
from email.parser import BytesHeaderParser

import pytest
from conftest import write_thread_mailbox
from fastapi.testclient import TestClient
from test_index import finish

from mail_explorer import index as index_module
from mail_explorer.app import create_app
from mail_explorer.config import Settings
from mail_explorer.index import MailboxIndex, fingerprint, message_ranges, read_headers
from mail_explorer.threads import threading_headers


def make_index(tmp_path, specifications):
    root = tmp_path / "mailbox"
    root.mkdir()
    path = root / "threaded.mbox"
    write_thread_mailbox(path, specifications)
    settings = Settings(root, tmp_path / "cache", chunk_bytes=73)
    index = MailboxIndex(path, "threaded", settings)
    finish(index)
    return index, settings


def create_legacy_cache(path, mailbox_id, settings):
    settings.cache_dir.mkdir()
    with sqlite3.connect(settings.cache_dir / f"{mailbox_id}.sqlite3") as db:
        db.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, start INTEGER, content_start INTEGER,
                end INTEGER, subject TEXT, sender TEXT, recipients TEXT, date TEXT);
            CREATE VIRTUAL TABLE message_search USING fts5(subject, sender, recipients,
                content='messages', content_rowid='id');
        """)
        count = 0
        with path.open("rb") as source:
            for count, (start, end) in enumerate(
                message_ranges(path, 0, path.stat().st_size, threading.Event(), lambda _: None, 73),
                start=1,
            ):
                content, fields = read_headers(source, start, end, settings.header_bytes)
                db.execute(
                    "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        count,
                        start,
                        content,
                        end,
                        fields["subject"],
                        fields["sender"],
                        fields["recipients"],
                        fields["date"],
                    ),
                )
        db.execute("INSERT INTO message_search(message_search) VALUES ('rebuild')")
        db.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [
                (
                    "signature",
                    json.dumps({"version": 1, "file": fingerprint(path)}, sort_keys=True),
                ),
                ("checkpoint", str(path.stat().st_size)),
                ("count", str(count)),
                ("complete", "1"),
            ],
        )


def test_gmail_threads_remain_separate_when_reply_headers_cross_them(tmp_path):
    index, _ = make_index(
        tmp_path,
        [
            {"Message-ID": "<a@example.com>", "X-GM-THRID": "11"},
            {"Message-ID": "<b@example.com>", "X-GM-THRID": "22", "References": "<a@example.com>"},
            {"Message-ID": "<c@example.com>", "X-GM-THRID": "11", "References": "<b@example.com>"},
            {"Message-ID": "<f@example.com>", "In-Reply-To": "<b@example.com>"},
        ],
    )
    assert index.status()["thread_count"] == 2
    groups = {
        tuple(item["id"] for item in index.thread_messages(thread["id"])["items"])
        for thread in index.list_threads()["items"]
    }
    assert groups == {(1, 3), (2, 4)}


def test_reply_graph_handles_out_of_order_messages_and_same_subjects(tmp_path):
    index, _ = make_index(
        tmp_path,
        [
            {
                "Message-ID": "<child@example.com>",
                "In-Reply-To": "<parent@example.com>",
                "References": "<root@example.com> <parent@example.com>",
                "Date": "Mon, 5 Oct 2026 10:00:00 +0000",
            },
            {"Message-ID": "<unrelated@example.com>"},
            {"Message-ID": "<root@example.com>", "Date": "Thu, 1 Oct 2026 10:00:00 +0000"},
            {
                "Message-ID": "<parent@example.com>",
                "References": "<root@example.com>",
                "Date": "Sat, 3 Oct 2026 10:00:00 +0000",
            },
            {
                "Message-ID": "<child2@example.com>",
                "In-Reply-To": "<parent@example.com>",
                "Date": "Sun, 4 Oct 2026 10:00:00 +0000",
            },
        ],
    )
    assert index.status()["thread_count"] == 2
    conversation = index.list_threads()["items"][0]
    assert conversation["message_count"] == 4
    assert [item["id"] for item in index.thread_messages(conversation["id"])["items"]] == [
        3,
        4,
        5,
        1,
    ]


def test_shared_missing_ancestors_and_reference_cycles(tmp_path):
    index, _ = make_index(
        tmp_path,
        [
            {"Message-ID": "<a@example.com>", "References": "<missing@example.com>"},
            {"Message-ID": "<b@example.com>", "In-Reply-To": "<missing@example.com>"},
            {"Message-ID": "<cycle1@example.com>", "In-Reply-To": "<cycle2@example.com>"},
            {"Message-ID": "<cycle2@example.com>", "In-Reply-To": "<cycle1@example.com>"},
            {},
            {},
        ],
    )
    assert index.status()["thread_count"] == 4
    assert sorted(thread["message_count"] for thread in index.list_threads()["items"]) == [
        1,
        1,
        2,
        2,
    ]


def test_late_gmail_identity_merges_fallback_and_preserves_old_thread_links(tmp_path):
    index, _ = make_index(
        tmp_path,
        [
            {"Message-ID": "<reply@example.com>", "References": "<root@example.com>"},
            {"Message-ID": "<root@example.com>", "X-GM-THRID": "18446744073709551615"},
        ],
    )
    assert index.status()["thread_count"] == 1
    old_link = index.thread_messages(1)
    assert old_link["thread"]["message_count"] == 2
    assert old_link["thread"]["id"] != 1


def test_bridge_merges_roots_and_keeps_summaries_and_aliases_consistent(tmp_path):
    index, _ = make_index(
        tmp_path,
        [
            {"Message-ID": "<a@example.com>"},
            {"Message-ID": "<b@example.com>"},
            {"Message-ID": "<bridge@example.com>", "References": "<a@example.com> <b@example.com>"},
        ],
    )
    assert index.status()["thread_count"] == 1
    assert index.thread_messages(2)["thread"]["message_count"] == 3
    assert index.list_threads()["items"][0]["latest_message_id"] == 3


def test_thread_search_matches_any_reply_and_cursor_pagination_is_stable(tmp_path):
    index, _ = make_index(
        tmp_path,
        [
            {"X-GM-THRID": "11", "Subject": "Original"},
            {
                "X-GM-THRID": "11",
                "Subject": "A distinctive keyword",
                "Date": "Fri, 2 Oct 2026 10:00:00 +0000",
            },
            {
                "X-GM-THRID": "11",
                "Subject": "Latest reply",
                "Date": "Sat, 3 Oct 2026 10:00:00 +0000",
            },
            {"X-GM-THRID": "22"},
            {"X-GM-THRID": "33"},
        ],
    )
    matching = index.list_threads(query="distinct")
    assert len(matching["items"]) == 1
    assert matching["items"][0]["subject"] == "Latest reply"
    assert not index.list_threads(query="*")["items"]
    first = index.list_threads(limit=2)
    second = index.list_threads(after=first["next_cursor"], limit=2)
    ids = [item["id"] for item in first["items"] + second["items"]]
    assert len(set(ids)) == len(ids) == 3
    thread_id = matching["items"][0]["id"]
    first = index.thread_messages(thread_id, limit=2)
    second = index.thread_messages(thread_id, after=first["next_cursor"], limit=2)
    assert [item["id"] for item in first["items"] + second["items"]] == [1, 2, 3]


@pytest.mark.parametrize(
    "order, expected", [("oldest", [3, 2, 5, 4, 1, 6]), ("newest", [6, 1, 4, 5, 2, 3])]
)
def test_thread_message_sorting_is_global_and_cursor_pagination_has_no_gaps(
    archive, order, expected
):
    app, catalog, mailbox_id, path, _ = archive
    dates = [
        "Sat, 3 Oct 2026 10:00:00 +0000",
        "Thu, 1 Oct 2026 10:00:00 +0000",
        "invalid",
        "Fri, 2 Oct 2026 10:00:00 +0000",
        "Thu, 1 Oct 2026 10:00:00 +0000",
        "Sat, 3 Oct 2026 10:00:00 +0000",
    ]
    write_thread_mailbox(path, [{"X-GM-THRID": "1234", "Date": date} for date in dates])
    with TestClient(app) as client:
        base = f"/api/mailboxes/{mailbox_id}"
        client.post(base + "/index")
        catalog.get(mailbox_id)._thread.join(timeout=5)
        thread_id = client.get(base + "/threads").json()["items"][0]["id"]
        endpoint = f"{base}/threads/{thread_id}/messages"
        after, ids = "", []
        while after is not None:
            response = client.get(endpoint, params={"limit": 2, "order": order, "after": after})
            assert response.status_code == 200
            page = response.json()
            assert page["thread"]["message_count"] == 6
            ids.extend(item["id"] for item in page["items"])
            after = page["next_cursor"]
        assert ids == expected
        assert [item["id"] for item in client.get(endpoint).json()["items"]] == [3, 2, 5, 4, 1, 6]
        assert client.get(endpoint, params={"order": "invalid"}).status_code == 422


def test_migration_preserves_offsets_and_search_without_rescanning_bodies(archive, monkeypatch):
    _, catalog, mailbox_id, path, settings = archive
    create_legacy_cache(path, mailbox_id, settings)
    original = index_module.read_headers
    reads = []

    def header_read(source, start, end, limit):
        result = original(source, start, end, limit)
        reads.append((start, source.tell()))
        assert source.tell() < end  # stopped before the message body
        return result

    def forbidden_scan(*args, **kwargs):
        pytest.fail("A header-only migration must not rescan the mailbox")

    monkeypatch.setattr(index_module, "read_headers", header_read)
    monkeypatch.setattr(index_module, "message_ranges", forbidden_scan)
    index = catalog.get(mailbox_id)
    before = index.list_messages()
    assert before["status"]["phase"] == "grouping"
    assert len(before["items"]) == 3
    assert before["status"]["scanned_bytes"] == path.stat().st_size
    finish(index)
    assert len(reads) == 3
    assert index.status()["threaded_messages"] == 3
    assert index.status()["thread_count"] == 3
    assert index.list_messages()["items"] == before["items"]
    assert len(index.list_threads(query="pricing")["items"]) == 3
    assert MailboxIndex(path, mailbox_id, settings).status()["state"] == "complete"


def test_paused_migration_resumes_without_double_counting(archive, monkeypatch):
    _, catalog, mailbox_id, path, settings = archive
    create_legacy_cache(path, mailbox_id, settings)
    index = catalog.get(mailbox_id)
    original = index_module.read_headers

    def pause_during_headers(*args):
        result = original(*args)
        index._stop.set()
        return result

    monkeypatch.setattr(index_module, "read_headers", pause_during_headers)
    index.start()
    index._thread.join(timeout=5)
    assert index.status()["state"] == "paused"
    assert index.status()["threaded_messages"] == 1
    monkeypatch.setattr(index_module, "read_headers", original)
    reopened = MailboxIndex(path, mailbox_id, settings)
    finish(reopened)
    assert reopened.status()["thread_count"] == 3
    assert sum(item["message_count"] for item in reopened.list_threads()["items"]) == 3


def test_thread_api_paginates_headers_without_loading_bodies(tmp_path, monkeypatch):
    _, settings = make_index(tmp_path, [{"X-GM-THRID": "11"} for _ in range(125)])
    app = create_app(settings=settings)

    def forbidden_preview(*args, **kwargs):
        pytest.fail("Listing a conversation must not parse message bodies")

    monkeypatch.setattr("mail_explorer.app.preview", forbidden_preview)
    with TestClient(app) as client:
        mailbox_id = client.get("/api/mailboxes").json()["items"][0]["id"]
        base = f"/api/mailboxes/{mailbox_id}"
        client.post(base + "/index")
        app.state.catalog.get(mailbox_id)._thread.join(timeout=5)
        threads = client.get(base + "/threads").json()
        assert len(threads["items"]) == 1
        thread_id = threads["items"][0]["id"]
        response = client.get(f"{base}/threads/{thread_id}/messages")
        assert response.status_code == 200
        page = response.json()
        assert len(page["items"]) == 50
        assert page["next_cursor"]
        assert all("body_text" not in item for item in page["items"])
        assert client.get(f"{base}/threads/999/messages").status_code == 404
        assert client.get(base + "/threads", params={"after": "malformed"}).status_code == 422
        assert (
            client.get(f"{base}/threads/{thread_id}/messages", params={"limit": 101}).status_code
            == 422
        )


def test_dates_and_identifiers_are_bounded_and_missing_dates_have_stable_order():
    message = BytesHeaderParser().parsebytes(
        b"Message-ID: raw@example.com\nX-GM-THRID: invalid\nReferences: <a@example.com>\n\n"
    )
    fields = threading_headers(message)
    assert fields["gmail_thread_id"] == ""
    assert fields["message_id"] == "<raw@example.com>"
    assert fields["reference_ids"] == ["<a@example.com>"]
    assert fields["sent_at"] == 0


def test_long_reference_headers_preserve_both_root_and_direct_parent():
    references = " ".join(f"<ancestor{i}@example.com>" for i in range(400))
    message = BytesHeaderParser().parsebytes(f"References: {references}\n\n".encode())
    fields = threading_headers(message)
    assert len(fields["reference_ids"]) == 128
    assert fields["reference_ids"][0] == "<ancestor0@example.com>"
    assert fields["reference_ids"][-1] == "<ancestor399@example.com>"
