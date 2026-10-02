import binascii
import hashlib
import tracemalloc
from dataclasses import replace
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

import pytest
from fastapi.testclient import TestClient

from mail_explorer.app import create_app
from mail_explorer.attachments import Attachment, attachment_chunks, attachments, disposition
from mail_explorer.config import Settings

PNG = binascii.a2b_base64(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


def write_message(path, message):
    path.write_bytes(
        b"From sender@example.com Thu Oct 1 00:00:00 2026\n" + message.as_bytes() + b"\n"
    )


def start_index(client, app, mailbox_id):
    base = f"/api/mailboxes/{mailbox_id}/messages/1"
    client.post(f"/api/mailboxes/{mailbox_id}/index")
    app.state.catalog.get(mailbox_id)._thread.join(timeout=5)
    return base


def test_image_preview_download_names_and_missing_attachments(archive):
    app, _, mailbox_id, path, _ = archive
    message = EmailMessage(policy=policy.SMTP)
    message["Subject"] = "Attachments"
    message.set_content("Read this message")
    message.add_alternative("<p>Read this message</p>", subtype="html")
    message.add_attachment(PNG, maintype="image", subtype="png", filename='café "photo".png')
    message.add_attachment(b"second image", maintype="image", subtype="jpeg", filename="photo.jpg")
    message.add_attachment(b"report", maintype="application", subtype="pdf", filename="report.pdf")
    write_message(path, message)
    with TestClient(app) as client:
        base = start_index(client, app, mailbox_id)
        items = client.get(base).json()["attachments"]
        assert [item["id"] for item in items] == [0, 1, 2]
        assert [item["previewable"] for item in items] == [True, True, False]
        image = client.get(f"{base}/attachments/0?download=false")
        assert image.status_code == 200
        assert image.content == PNG
        assert image.headers["content-type"] == "image/png"
        assert image.headers["content-disposition"].startswith("inline;")
        assert image.headers["x-content-type-options"] == "nosniff"
        assert image.headers["cache-control"] == "no-store"
        assert "caf%C3%A9%20%22photo%22.png" in image.headers["content-disposition"]
        download = client.get(f"{base}/attachments/0")
        assert download.content == PNG
        assert download.headers["content-disposition"].startswith("attachment;")
        assert download.headers["content-type"] == "application/octet-stream"
        assert client.get(f"{base}/attachments/1").content == b"second image"
        assert client.get(f"{base}/attachments/2").content == b"report"
        assert client.get(f"{base}/attachments/99").status_code == 404
        assert client.get(f"{base}/attachments/-1").status_code == 404
        assert client.get(base.replace("/1", "/999") + "/attachments/0").status_code == 404


@pytest.mark.parametrize("content_type", ["text/html", "image/svg+xml"])
def test_active_content_is_downloaded_and_never_previewed(archive, content_type):
    app, _, mailbox_id, path, _ = archive
    message = EmailMessage(policy=policy.SMTP)
    message.set_content("Body")
    maintype, subtype = content_type.split("/")
    payload = b"<script>window.parent.hacked=true</script>"
    message.add_attachment(payload, maintype=maintype, subtype=subtype, filename="active-file")
    write_message(path, message)
    with TestClient(app) as client:
        base = start_index(client, app, mailbox_id)
        assert not client.get(base).json()["attachments"][0]["previewable"]
        assert client.get(f"{base}/attachments/0?download=false").status_code == 415
        response = client.get(f"{base}/attachments/0")
        assert response.content == payload
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")


@pytest.mark.parametrize("encoding", ["base64", "quoted-printable", "7bit", "8bit", "binary"])
@pytest.mark.parametrize("chunk_bytes", [1, 2, 3, 5, 73])
def test_transfer_encoding_preserves_payload_across_reads(tmp_path, encoding, chunk_bytes):
    payload = b"first line\r\nsecond line\n= a long line "
    payload += bytes(range(128 if encoding == "7bit" else 256)) * 4
    message = EmailMessage(policy=policy.SMTP)
    message.set_content("Body")
    message.add_attachment(
        payload, maintype="application", subtype="octet-stream", filename="data.bin", cte=encoding
    )
    path = tmp_path / "message.eml"
    path.write_bytes(message.as_bytes())
    record = {"content_start": 0, "end": path.stat().st_size}
    item = next(attachments(path, record, Settings(tmp_path, tmp_path)))
    # SMTP serialization normalizes newlines in unencoded payloads.
    parsed = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    expected = next(parsed.iter_attachments()).get_payload(decode=True)
    assert b"".join(attachment_chunks(path, item, chunk_bytes)) == expected


def test_attachments_after_truncated_body_are_available(archive):
    _, _, mailbox_id, path, settings = archive
    message = EmailMessage(policy=policy.SMTP)
    message.set_content("x" * 5000)
    message.add_attachment(PNG, maintype="image", subtype="png", filename="late.png")
    message.add_attachment(
        b"late report", maintype="application", subtype="pdf", filename="late.pdf"
    )
    write_message(path, message)
    app = create_app(settings=replace(settings, preview_bytes=512, text_chars=128))
    with TestClient(app) as client:
        base = start_index(client, app, mailbox_id)
        detail = client.get(base).json()
        assert detail["truncated"]
        assert len(detail["body_text"]) <= 128
        assert [item["name"] for item in detail["attachments"]] == ["late.png", "late.pdf"]
        assert client.get(f"{base}/attachments/0?download=false").content == PNG
        assert client.get(f"{base}/attachments/1").content == b"late report"


def test_forwarded_email_is_downloaded_as_a_complete_message(archive):
    app, _, mailbox_id, path, _ = archive
    forwarded = EmailMessage(policy=policy.SMTP)
    forwarded["Subject"] = "Forwarded subject"
    forwarded.set_content("Forwarded body")
    forwarded.add_attachment(PNG, maintype="image", subtype="png", filename="nested.png")
    message = EmailMessage(policy=policy.SMTP)
    message.set_content("Outer body")
    message.add_attachment(forwarded, filename="forwarded.eml")
    write_message(path, message)
    with TestClient(app) as client:
        base = start_index(client, app, mailbox_id)
        assert client.get(base).json()["attachments"][0]["name"] == "forwarded.eml"
        downloaded = BytesParser(policy=policy.default).parsebytes(
            client.get(f"{base}/attachments/0").content
        )
        assert downloaded["Subject"] == "Forwarded subject"
        assert downloaded.get_body().get_content().strip() == "Forwarded body"
        assert next(downloaded.iter_attachments()).get_payload(decode=True) == PNG


def test_inline_image_without_filename_and_boundary_whitespace(archive):
    app, _, mailbox_id, path, _ = archive
    path.write_bytes(
        b"From sender@example.com Thu Oct 1 00:00:00 2026\n"
        b'Content-Type: multipart/related; boundary="outer"\n\nPreamble\n'
        b"--outer \t\nContent-Type: text/plain\n\nBody\n"
        b"--outer\t\nContent-Type: image/png\nContent-ID: <photo>\n"
        b"Content-Disposition: inline\nContent-Transfer-Encoding: base64\n\n"
        + binascii.b2a_base64(PNG)
        + b"--outer-- \t\nEpilogue\n"
    )
    with TestClient(app) as client:
        base = start_index(client, app, mailbox_id)
        assert client.get(base).json()["attachments"][0]["previewable"]
        assert client.get(f"{base}/attachments/0?download=false").content == PNG


def test_attachment_scanning_and_download_memory_stays_bounded(tmp_path):
    path = tmp_path / "large.eml"
    block = b"x" * 65536
    with path.open("wb") as output:
        output.write(b'Content-Type: multipart/mixed; boundary="parts"\r\n\r\n')
        output.write(
            b"--parts\r\nContent-Type: application/octet-stream\r\n"
            b'Content-Disposition: attachment; filename="large.bin"\r\n\r\n'
        )
        for _ in range(256):
            output.write(block)
        output.write(b"\r\n--parts--\r\n")
    settings = Settings(tmp_path, tmp_path, chunk_bytes=65536)
    record = {"content_start": 0, "end": path.stat().st_size}
    expected = hashlib.sha256()
    for _ in range(256):
        expected.update(block)
    tracemalloc.start()
    try:
        item = next(attachments(path, record, settings))
        actual = hashlib.sha256()
        length = 0
        for chunk in attachment_chunks(path, item, settings.chunk_bytes):
            assert len(chunk) <= settings.chunk_bytes
            length += len(chunk)
            actual.update(chunk)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert length == 16 * 1024 * 1024
    assert actual.digest() == expected.digest()
    assert peak < 1024 * 1024


def test_download_filename_removes_paths_and_header_controls():
    item = Attachment(0, '../folder\\café "photo"\r\n.png', "image/png", "base64", 0, 0)
    header = disposition(item, True)
    assert "\r" not in header and "\n" not in header
    assert "folder" not in header and "../" not in header
    assert 'filename="caf? _photo_.png"' in header
    assert "filename*=UTF-8''caf%C3%A9%20%22photo%22.png" in header


def test_boundary_crlf_split_by_a_long_body_line(tmp_path):
    path = tmp_path / "message.eml"
    payload = b"x" * (65536 - 1)
    path.write_bytes(
        b'Content-Type: multipart/mixed; boundary="parts"\r\n\r\n'
        b"--parts\r\nContent-Type: application/octet-stream\r\n"
        b'Content-Disposition: attachment; filename="data.bin"\r\n\r\n'
        + payload
        + b"\r\n--parts--\r\n"
    )
    record = {"content_start": 0, "end": path.stat().st_size}
    item = next(attachments(path, record, Settings(tmp_path, tmp_path)))
    assert b"".join(attachment_chunks(path, item, 73)) == payload


def test_unsupported_attachment_encoding_explains_original_fallback(archive):
    app, _, mailbox_id, path, _ = archive
    path.write_bytes(
        b"From sender@example.com Thu Oct 1 00:00:00 2026\n"
        b'Content-Type: multipart/mixed; boundary="parts"\n\n'
        b"--parts\nContent-Type: image/png\n"
        b'Content-Disposition: attachment; filename="unsupported.png"\n'
        b"Content-Transfer-Encoding: unsupported\n\npayload\n--parts--\n"
    )
    with TestClient(app) as client:
        base = start_index(client, app, mailbox_id)
        assert not client.get(base).json()["attachments"][0]["previewable"]
        response = client.get(f"{base}/attachments/0")
        assert response.status_code == 422
        assert "original message" in response.json()["detail"]
