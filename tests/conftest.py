from email import policy
from email.message import EmailMessage

import pytest

from mail_explorer.app import create_app
from mail_explorer.config import Settings


def write_mailbox(path, count=3):
    with path.open("wb") as output:
        for number in range(count):
            message = EmailMessage(policy=policy.SMTP)
            message["From"] = f"Sender {number} <person{number}@example.com>"
            message["To"] = "Reader <reader@example.com>"
            message["Subject"] = f"Pricing update {number} — café"
            message["Date"] = "Thu, 1 Oct 2026 10:00:00 +0530"
            message.set_content(f"Message body {number}\n>From escaped@example.com\n")
            if number == 0:
                message.add_alternative(
                    '<p style="color:green">Hello <strong>reader</strong></p>'
                    "<script>window.parent.hacked=true</script>"
                    '<meta http-equiv="refresh" content="0;url=https://tracker.invalid">'
                    '<img src="https://tracker.invalid/pixel" onerror="alert(1)">'
                    '<a href="https://tracker.invalid">External link</a>',
                    subtype="html",
                )
                message.add_attachment(
                    b"small attachment",
                    maintype="application",
                    subtype="octet-stream",
                    filename="notes.bin",
                )
            output.write(b"From sender@example.com Thu Oct  1 10:00:00 2026\n")
            output.write(message.as_bytes())
            output.write(b"\n")


def write_thread_mailbox(path, specifications):
    with path.open("wb") as output:
        for number, specification in enumerate(specifications):
            message = EmailMessage(policy=policy.SMTP)
            headers = {
                "From": f"Sender {number} <person{number}@example.com>",
                "To": "Reader <reader@example.com>",
                "Subject": "Pricing discussion",
                "Date": "Thu, 1 Oct 2026 10:00:00 +0530",
                **specification,
            }
            body = headers.pop("body", f"Body of message {number}")
            html = headers.pop("html", None)
            for name, value in headers.items():
                message[name] = value
            message.set_content(body)
            if html:
                message.add_alternative(html, subtype="html")
            output.write(b"From sender@example.com Thu Oct  1 10:00:00 2026\n")
            output.write(message.as_bytes())
            output.write(b"\n")


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "mailbox"
    root.mkdir()
    path = root / "sample.mbox"
    write_mailbox(path)
    settings = Settings(root, tmp_path / "cache", chunk_bytes=73)
    app = create_app(settings=settings)
    catalog = app.state.catalog
    mailbox_id = catalog.list_mailboxes()[0]["id"]
    yield app, catalog, mailbox_id, path, settings
    catalog.close()
