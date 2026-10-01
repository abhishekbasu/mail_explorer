from dataclasses import replace

from fastapi.testclient import TestClient

from mail_explorer.app import create_app
from mail_explorer.messages import raw_chunks


def test_browse_preview_search_download_and_security(archive):
    app, catalog, mailbox_id, path, _ = archive
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/mailboxes").json()["items"][0]["name"] == "sample.mbox"
        base = f"/api/mailboxes/{mailbox_id}"
        assert client.post(f"{base}/index").status_code == 200
        catalog.get(mailbox_id)._thread.join(timeout=5)
        assert client.get(f"{base}/status").json()["state"] == "complete"
        page = client.get(f"{base}/messages", params={"limit": 2}).json()
        assert len(page["items"]) == 2
        assert page["next_cursor"] == 2
        assert len(client.get(f"{base}/messages", params={"q": "person2"}).json()["items"]) == 1
        response = client.get(f"{base}/messages/1")
        assert response.headers["cache-control"] == "no-store"
        assert "script-src 'self'" in response.headers["content-security-policy"]
        detail = response.json()
        assert "Message body 0" in detail["body_text"]
        assert "<strong>reader</strong>" in detail["body_html"]
        assert "<script" not in detail["body_html"]
        assert "<meta" not in detail["body_html"]
        assert "tracker.invalid" not in detail["body_html"]
        assert "onerror" not in detail["body_html"]
        assert detail["attachments"] == [
            {"name": "notes.bin", "content_type": "application/octet-stream"}
        ]
        assert not detail["truncated"]
        raw = client.get(f"{base}/messages/1/raw")
        record = catalog.get(mailbox_id).message(1)
        with path.open("rb") as source:
            source.seek(record["content_start"])
            assert raw.content == source.read(record["end"] - record["content_start"])
        assert len(raw.content) == int(raw.headers["content-length"])
        assert "attachment" in raw.headers["content-disposition"]
        assert client.get(f"{base}/messages/999").status_code == 404
        assert client.get("/api/mailboxes/unknown/status").status_code == 404
        assert client.get(f"{base}/messages", params={"limit": 101}).status_code == 422
        assert client.get(f"{base}/messages", params={"after": -1}).status_code == 422
        assert client.post(f"{base}/pause").status_code == 200


def test_large_preview_is_capped_and_original_stays_available(archive):
    _, _, _, path, settings = archive
    path.write_bytes(
        b"From test@example.com Thu Oct 1 00:00:00 2026\nSubject: Large\n\n" + b"x" * 10000
    )
    app = create_app(settings=replace(settings, preview_bytes=512, text_chars=128))
    with TestClient(app) as client:
        mailbox_id = client.get("/api/mailboxes").json()["items"][0]["id"]
        base = f"/api/mailboxes/{mailbox_id}"
        client.post(f"{base}/index")
        app.state.catalog.get(mailbox_id)._thread.join(timeout=5)
        detail = client.get(f"{base}/messages/1").json()
        assert detail["truncated"]
        assert len(detail["body_text"]) <= 128
        assert len(client.get(f"{base}/messages/1/raw").content) > 10000
        assert all(len(chunk) <= 73 for chunk in raw_chunks(path, 0, path.stat().st_size, 73))


def test_no_files_and_symlink_outside_mailbox_directory(tmp_path):
    root = tmp_path / "mailbox"
    root.mkdir()
    external = tmp_path / "outside.mbox"
    external.write_bytes(b"private")
    (root / "linked.mbox").symlink_to(external)
    app = create_app(root, tmp_path / "cache")
    with TestClient(app) as client:
        assert client.get("/api/mailboxes").json() == {"items": []}


def test_html_only_email_gets_readable_text(archive):
    app, catalog, mailbox_id, path, _ = archive
    path.write_bytes(
        b"From test@example.com Thu Oct 1 00:00:00 2026\n"
        b"Subject: HTML only\nContent-Type: text/html; charset=unknown-charset\n\n"
        b"<style>p{color:green}</style><p>Hello <b>world</b></p><script>secret()</script>"
    )
    with TestClient(app) as client:
        client.post(f"/api/mailboxes/{mailbox_id}/index")
        catalog.get(mailbox_id)._thread.join(timeout=5)
        detail = client.get(f"/api/mailboxes/{mailbox_id}/messages/1").json()
        assert detail["body_text"] == "Hello world"
