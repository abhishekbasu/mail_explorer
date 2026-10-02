"""Capture the real app using an isolated, entirely fictional mail archive.

Run with: uv run --with playwright python scripts/capture_readme_screenshots.py
Install Chromium once with: uv run --with playwright playwright install chromium
"""

import socket
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright

from mail_explorer.app import create_app

OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "images" / "screenshots"
DEMO_HTML = """\
<h2>A little room to focus</h2>
<p>Here is our small plan for the week. A few clear priorities,
a little breathing room, and something lovely to look forward to.</p>
<h3>Three things that matter</h3>
<ul>
<li><strong>Make space.</strong> Keep the morning open for focused work.</li>
<li><strong>Share the good bits.</strong> Bring one idea to our studio catch-up.</li>
<li><strong>Finish gently.</strong> Wrap up with a short Friday walkthrough.</li>
</ul>
<h3>The week at a glance</h3>
<table>
<thead><tr><th>Day</th><th>Plan</th><th>With</th></tr></thead>
<tbody>
<tr><td>Monday</td><td>Sketch &amp; explore</td><td>Avery</td></tr>
<tr><td>Wednesday</td><td>Studio catch-up</td><td>Everyone</td></tr>
<tr><td>Friday</td><td>A relaxed walkthrough</td><td>Alex</td></tr>
</tbody>
</table>
<p>See you soon,<br>Avery</p>
<div class="gmail_quote"><div class="gmail_attr">On Thursday, Alex wrote:</div>
<blockquote><p>Could we leave a little more space in the calendar this week?</p>
<p>It would be nice to spend some time exploring the new sketches together.</p>
</blockquote></div>
"""


def write_demo_archive(path: Path) -> None:
    """All names, messages, and addresses below are fictional."""
    start = datetime(2026, 10, 2, 8, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    messages = []
    subjects = [
        ("The little things", "Jordan Lee"),
        ("A new corner of the studio", "Alex Rivera"),
        ("Sketchbook notes", "Avery Morgan"),
        ("A playlist for a slow morning", "Sam Taylor"),
        ("Next week's reading list", "Robin Ellis"),
        ("Coffee and a few good ideas", "Alex Rivera"),
        ("Paper, pencils, possibilities", "Jordan Lee"),
        ("Our October moodboard", "Avery Morgan"),
        ("A moment outside", "Sam Taylor"),
        ("Notes from the workshop", "Robin Ellis"),
        ("Friday's photo walk", "Jordan Lee"),
        ("A calmer kind of Monday", "Avery Morgan"),
        ("The garden is looking lovely", "Sam Taylor"),
        ("A few links to explore", "Alex Rivera"),
        ("Meet you at the library", "Robin Ellis"),
        ("Something worth keeping", "Jordan Lee"),
        ("Small wins this week", "Avery Morgan"),
        ("New ideas for the noticeboard", "Alex Rivera"),
        ("The weekend, on paper", "Sam Taylor"),
        ("A fresh page", "Robin Ellis"),
        ("An afternoon of making", "Jordan Lee"),
    ]
    for number, (subject, name) in enumerate(subjects, 1):
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = f"{name} <{name.split()[0].lower()}@example.com>"
        message["To"] = "Studio Team <team@example.com>"
        message["Subject"] = subject
        message["Date"] = format_datetime(start + timedelta(minutes=number * 5))
        message["Message-ID"] = f"<demo-{number}@example.com>"
        message["X-GM-THRID"] = str(1000 + number)
        message.set_content(
            "Hello friends,\n\nHere is a small, fictional note for our demo studio.\n"
            "Let's make a little time to share ideas this week.\n\nSee you soon!\n"
        )
        messages.append(message)

    for number, name in enumerate(["Avery Morgan", "Alex Rivera", "Avery Morgan"]):
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = f"{name} <{name.split()[0].lower()}@example.com>"
        message["To"] = "Studio Team <team@example.com>"
        message["Subject"] = "A little room to focus"
        message["Date"] = format_datetime(start + timedelta(hours=3, minutes=number * 30))
        message["Message-ID"] = f"<demo-focus-{number}@example.com>"
        message["X-GM-THRID"] = "2000"
        message.set_content(
            "A little room to focus\n\nHere is our small plan for the week.\n"
            "Make space. Share the good bits. Finish gently.\n\n"
            "On Thursday, Alex wrote:\n> Could we leave more space in the calendar?\n"
        )
        message.add_alternative(DEMO_HTML, subtype="html")
        messages.append(message)

    path.write_bytes(
        b"".join(
            b"From demo@example.com Fri Oct 2 00:00:00 2026\n" + message.as_bytes() + b"\n"
            for message in messages
        )
    )


def capture() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    # Neither the app's default mailbox directory nor its cache is ever used.
    with tempfile.TemporaryDirectory(prefix="mail-explorer-readme-demo-") as directory:
        root = Path(directory)
        mailboxes = root / "mailboxes"
        mailboxes.mkdir()
        write_demo_archive(mailboxes / "Studio demo.mbox")
        app = create_app(mailboxes, root / "cache")
        mailbox_id = app.state.catalog.list_mailboxes()[0]["id"]
        app.state.catalog.start(mailbox_id)._thread.join(timeout=10)
        assert app.state.catalog.get(mailbox_id).status()["state"] == "complete"
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        connection = socket.socket()
        connection.bind(("127.0.0.1", 0))
        port = connection.getsockname()[1]
        worker = threading.Thread(target=server.run, kwargs={"sockets": [connection]}, daemon=True)
        worker.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                errors = []
                page = browser.new_page(
                    viewport={"width": 1440, "height": 1080},
                    device_scale_factor=3,
                    color_scheme="light",
                    reduced_motion="reduce",
                )
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}")
                expect(page.locator(".message-row")).to_have_count(22)
                page.locator(".message-row").first.click()
                cards = page.locator(".conversation-card")
                expect(cards).to_have_count(3)
                cards.first.locator(".conversation-summary").click()
                expect(cards.first.locator('[data-field="copy"]')).to_be_visible()
                expect(cards.first.locator('[data-field="body-markdown"]')).to_contain_text(
                    "Three things that matter"
                )
                page.mouse.move(1420, 1060)
                page.screenshot(path=str(OUTPUT / "desktop-light.png"))

                page.emulate_media(color_scheme="dark")
                page.locator("#conversation-order").click()
                expect(page.locator("#conversation-order-label")).to_have_text("Newest first")
                cards.first.locator(".conversation-summary").click()
                expect(cards.first.locator('[data-field="copy"]')).to_be_visible()
                page.locator("#sidebar-reveal").click()
                expect(page.locator("#sidebar-reveal")).to_have_attribute("aria-expanded", "true")
                cards.first.locator('[data-field="copy"]').click()
                expect(cards.first.locator('[data-field="copy-options"]')).to_be_visible()
                page.mouse.move(1420, 1060)
                page.screenshot(path=str(OUTPUT / "desktop-dark.png"))

                mobile = browser.new_page(
                    viewport={"width": 430, "height": 932},
                    device_scale_factor=3,
                    is_mobile=True,
                    has_touch=True,
                    color_scheme="light",
                    reduced_motion="reduce",
                )
                mobile.on("pageerror", lambda error: errors.append(str(error)))
                mobile.goto(f"http://127.0.0.1:{port}")
                expect(mobile.locator(".message-row")).to_have_count(22)
                mobile.screenshot(path=str(OUTPUT / "mobile-list.png"))
                mobile.locator("#view-messages").click()
                expect(mobile.locator(".message-row")).to_have_count(24)
                mobile.locator(".message-row").nth(21).click()
                detail = mobile.locator("#message-detail")
                expect(detail.locator('[data-field="body-markdown"]')).to_contain_text(
                    "Three things that matter"
                )
                mobile.screenshot(path=str(OUTPUT / "mobile-reader.png"))
                assert not errors, errors
                browser.close()
        finally:
            server.should_exit = True
            worker.join(timeout=5)
            connection.close()
    print(f"Saved four screenshots to {OUTPUT}; all messages are fictional.")


if __name__ == "__main__":
    capture()
