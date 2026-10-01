import threading
import tracemalloc
from dataclasses import replace
from itertools import pairwise

import pytest

from mail_explorer import index as index_module
from mail_explorer.index import MailboxChanged, MailboxIndex, message_ranges


def finish(index):
    index.start()
    index._thread.join(timeout=5)
    assert index.status()["state"] == "complete", index.status()


@pytest.mark.parametrize("chunk_size", [1, 2, 5, 6, 7, 31, 64, 1024])
def test_separators_cross_chunk_boundaries_and_crlf(archive, chunk_size):
    _, _, _, path, _ = archive
    ranges = list(
        message_ranges(path, 0, path.stat().st_size, threading.Event(), lambda _: None, chunk_size)
    )
    assert len(ranges) == 3
    assert ranges[0][0] == 0
    assert ranges[-1][1] == path.stat().st_size
    assert all(left[1] == right[0] for left, right in pairwise(ranges))
    with path.open("rb") as source:
        for start, _ in ranges:
            source.seek(start)
            assert source.read(5) == b"From "


def test_index_search_keyset_pagination_and_disk_reuse(archive):
    _, catalog, mailbox_id, path, settings = archive
    index = catalog.get(mailbox_id)
    finish(index)
    first = index.list_messages(limit=2)
    assert [message["id"] for message in first["items"]] == [1, 2]
    assert first["next_cursor"] == 2
    assert [message["id"] for message in index.list_messages(after=2)["items"]] == [3]
    assert "café" in first["items"][0]["subject"]
    assert len(index.list_messages(query="pric café")["items"]) == 3
    assert [message["id"] for message in index.list_messages(query="person1")["items"]] == [2]
    assert not index.list_messages(query='" OR * --')["items"]
    assert not index.list_messages(query="*")["items"]
    assert MailboxIndex(path, mailbox_id, settings).status()["indexed_messages"] == 3


def test_pause_resume_commits_atomic_offsets_without_duplicates(archive, monkeypatch):
    _, catalog, mailbox_id, path, settings = archive
    index = catalog.get(mailbox_id)
    original = index_module.message_ranges

    def stop_after_one(*args, **kwargs):
        for item in original(*args, **kwargs):
            yield item
            args[3].set()
            return

    monkeypatch.setattr(index_module, "message_ranges", stop_after_one)
    index.start()
    index._thread.join(timeout=5)
    assert index.status()["state"] == "paused"
    assert index.status()["indexed_messages"] == 1
    monkeypatch.setattr(index_module, "message_ranges", original)
    reopened = MailboxIndex(path, mailbox_id, settings)
    finish(reopened)
    assert [message["id"] for message in reopened.list_messages()["items"]] == [1, 2, 3]
    assert reopened.status()["threaded_messages"] == 3
    assert sum(thread["message_count"] for thread in reopened.list_threads()["items"]) == 3


def test_changed_file_invalidates_offsets_and_search(archive):
    _, catalog, mailbox_id, path, _ = archive
    index = catalog.get(mailbox_id)
    finish(index)
    path.write_bytes(b"From new@example.com Thu Oct 1 00:00:00 2026\nSubject: Replacement\n\nnew\n")
    with pytest.raises(MailboxChanged):
        index.list_messages()
    replacement = catalog.get(mailbox_id)
    assert replacement.status()["indexed_messages"] == 0
    finish(replacement)
    assert len(replacement.list_messages()["items"]) == 1
    assert not replacement.list_messages(query="pricing")["items"]


def test_scanner_memory_is_bounded_even_for_a_single_huge_line(tmp_path):
    path = tmp_path / "large.mbox"
    block = b"x" * 65536
    with path.open("wb") as output:
        output.write(b"From test@example.com Thu Oct 1 00:00:00 2026\nSubject: Huge\n\n")
        for _ in range(256):
            output.write(block)
    tracemalloc.start()
    try:
        ranges = list(
            message_ranges(path, 0, path.stat().st_size, threading.Event(), lambda _: None, 65536)
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert ranges == [(0, path.stat().st_size)]
    assert peak < 1024 * 1024


def test_empty_and_invalid_archives(archive):
    _, _, _, path, settings = archive
    path.write_bytes(b"")
    empty = MailboxIndex(path, "empty", settings)
    finish(empty)
    assert empty.status()["indexed_messages"] == 0
    path.write_bytes(b"not an mbox\n")
    invalid = MailboxIndex(path, "invalid", settings)
    invalid.start()
    invalid._thread.join(timeout=5)
    assert invalid.status()["state"] == "error"
    assert "separator" in invalid.status()["error"]


def test_header_reads_are_capped(archive):
    _, _, _, path, settings = archive
    path.write_bytes(
        b"From test@example.com Thu Oct 1 00:00:00 2026\nSubject: " + b"x" * 100_000 + b"\n\nbody\n"
    )
    index = MailboxIndex(path, "headers", replace(settings, header_bytes=1024))
    finish(index)
    assert len(index.list_messages()["items"][0]["subject"]) < 1024
