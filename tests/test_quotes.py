from email import policy
from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from mail_explorer.messages import HTMLText, safe_html
from mail_explorer.quotes import text_parts


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("A fresh message.\nNo history here.\n", [(False, "A fresh message.\nNo history here.\n")]),
        (
            "My reply.\n\n> First question\n> More context\n\nMy answer.\n",
            [
                (False, "My reply.\n\n"),
                (True, "> First question\n> More context\n"),
                (False, "\nMy answer.\n"),
            ],
        ),
        (
            "> First question\nMy first answer\n> Second question\nMy second answer\n",
            [
                (True, "> First question\n"),
                (False, "My first answer\n"),
                (True, "> Second question\n"),
                (False, "My second answer\n"),
            ],
        ),
        (
            "Current reply\r\n\r\n  > Quote\r\n\r\n>> Older quote\r\n",
            [(False, "Current reply\r\n\r\n"), (True, "  > Quote\r\n\r\n>> Older quote\r\n")],
        ),
        (
            "Thanks!\n\nOn Tuesday, Alice wrote:\n\n> Question\nAnswer below.\n",
            [
                (False, "Thanks!\n\n"),
                (True, "On Tuesday, Alice wrote:\n\n> Question\n"),
                (False, "Answer below.\n"),
            ],
        ),
        (
            "Thanks!\nOn Tuesday, 1 October 2026,\nAlice <alice@example.com> wrote:\n> Question\n",
            [
                (False, "Thanks!\n"),
                (
                    True,
                    "On Tuesday, 1 October 2026,\nAlice <alice@example.com> wrote:\n> Question\n",
                ),
            ],
        ),
        (
            "New reply\nOn Tuesday, Alice wrote:\nOld message without quote prefixes\n",
            [
                (False, "New reply\n"),
                (True, "On Tuesday, Alice wrote:\nOld message without quote prefixes\n"),
            ],
        ),
        (
            "New reply\n-----Original Message-----\nFrom: Alice\nOld message\n",
            [
                (False, "New reply\n"),
                (True, "-----Original Message-----\nFrom: Alice\nOld message\n"),
            ],
        ),
        (
            "New reply\nFrom: Alice\nSent: Thursday, 1 October 2026\nTo: Bob\nSubject: Old message\n\nHistory\n",
            [
                (False, "New reply\n"),
                (
                    True,
                    "From: Alice\nSent: Thursday, 1 October 2026\nTo: Bob\nSubject: Old message\n\nHistory\n",
                ),
            ],
        ),
        (
            "On a whiteboard I wrote:\nA plan for tomorrow.\n",
            [(False, "On a whiteboard I wrote:\nA plan for tomorrow.\n")],
        ),
        (
            "From: New York\nTo: San Francisco\nTravel details\n",
            [(False, "From: New York\nTo: San Francisco\nTravel details\n")],
        ),
        ("On Tuesday, Alice wrote:\n", [(False, "On Tuesday, Alice wrote:\n")]),
        ("a > b\n1 < 2\n", [(False, "a > b\n1 < 2\n")]),
        ("", []),
    ],
)
def test_plain_quote_detection_preserves_all_text_and_inline_answers(body, expected):
    parts = text_parts(body)
    assert [(part["quoted"], part["text"]) for part in parts] == expected
    assert "".join(part["text"] for part in parts) == body


@pytest.mark.parametrize(
    ("opening", "closing", "kind"),
    [
        ("<blockquote>", "</blockquote>", "block"),
        ('<blockquote type="cite">', "</blockquote>", "block"),
        ('<div class="gmail_quote">', "</div>", "block"),
        ('<div class="gmail_quote_container other-class">', "</div>", "block"),
        ('<div class="yahoo_quoted">', "</div>", "block"),
        ('<div class="protonmail_quote">', "</div>", "block"),
        ('<div id="OLK_SRC_BODY_SECTION">', "</div>", "block"),
        ('<div id="divRplyFwdMsg">', "</div>", "tail"),
    ],
)
def test_html_quotes_are_marked_and_preserved_in_readable_text(opening, closing, kind):
    html = f"<p>Current reply</p>{opening}<p>Old reply</p>{closing}"
    sanitized = safe_html(html)
    assert f'data-mail-quote="{kind}"' in sanitized
    assert "Current reply" in sanitized and "Old reply" in sanitized
    parser = HTMLText(1000)
    parser.feed(html)
    parts = parser.segments()
    assert "Current reply" in "".join(part["text"] for part in parts if not part["quoted"])
    assert "Old reply" in "".join(part["text"] for part in parts if part["quoted"])
    assert "".join(part["text"] for part in parts) == "".join(parser.parts).strip()


def test_html_inline_answers_are_visible_after_nested_quotes():
    parser = HTMLText(1000)
    parser.feed(
        "<p>Current answer</p><blockquote><p>Older question</p>"
        "<blockquote>Oldest context</blockquote></blockquote>"
        "<p>Another current answer</p><blockquote>Another older question</blockquote>"
    )
    parts = parser.segments()
    visible = "".join(part["text"] for part in parts if not part["quoted"])
    quoted = "".join(part["text"] for part in parts if part["quoted"])
    assert "Current answer" in visible and "Another current answer" in visible
    assert "Older question" in quoted and "Oldest context" in quoted
    assert "Another older question" in quoted
    assert "".join(part["text"] for part in parts) == "".join(parser.parts).strip()


def test_outlook_html_history_includes_following_siblings_until_parent_ends():
    parser = HTMLText(1000)
    parser.feed(
        '<div><p>Current answer</p><div id="divRplyFwdMsg">From: Alice</div>'
        "<p>Old message body</p></div><p>Current footer</p>"
    )
    parts = parser.segments()
    visible = "".join(part["text"] for part in parts if not part["quoted"])
    quoted = "".join(part["text"] for part in parts if part["quoted"])
    assert "Current answer" in visible and "Current footer" in visible
    assert "From: Alice" in quoted and "Old message body" in quoted


def test_quote_markers_cannot_be_supplied_by_untrusted_html():
    sanitized = safe_html(
        '<div data-mail-quote="tail">Current answer</div>'
        '<blockquote data-mail-quote="tail" onclick="alert(1)">History'
        '<script>secret()</script><img src="https://tracker.invalid"></blockquote>'
    )
    assert sanitized.count("data-mail-quote") == 1
    assert 'data-mail-quote="block"' in sanitized
    assert "onclick" not in sanitized and "<script" not in sanitized
    assert "tracker.invalid" not in sanitized
    assert "Current answer" in sanitized and "History" in sanitized


def test_html_quote_conversion_obeys_text_limit():
    parser = HTMLText(128)
    parser.feed("<p>Current answer</p><blockquote>" + "x" * 10000 + "</blockquote>")
    assert sum(len(part["text"]) for part in parser.segments()) <= 128


@pytest.mark.parametrize("html_only", [False, True])
def test_message_api_returns_expandable_quote_parts(archive, html_only):
    app, catalog, mailbox_id, path, _ = archive
    message = EmailMessage(policy=policy.SMTP)
    message["Subject"] = "Reply with history"
    if html_only:
        message.set_content(
            "<p>Current answer</p><blockquote>Old question</blockquote>", subtype="html"
        )
    else:
        message.set_content("Current answer\n\n> Old question\n")
    path.write_bytes(
        b"From sender@example.com Thu Oct 1 00:00:00 2026\n" + message.as_bytes() + b"\n"
    )
    with TestClient(app) as client:
        base = f"/api/mailboxes/{mailbox_id}"
        client.post(f"{base}/index")
        catalog.get(mailbox_id)._thread.join(timeout=5)
        detail = client.get(f"{base}/messages/1").json()
        parts = detail["body_text_parts"]
        assert any(part["quoted"] and "Old question" in part["text"] for part in parts)
        assert any(not part["quoted"] and "Current answer" in part["text"] for part in parts)
        assert "".join(part["text"] for part in parts) == detail["body_text"]
