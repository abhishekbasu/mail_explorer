from fastapi.testclient import TestClient

from mail_explorer.markdown import markdown_preview


def rendered(parts):
    return "".join(part["html"] for part in parts)


def test_html_markdown_keeps_formatting_and_collapses_provider_history():
    source = (
        "<h1>Launch notes</h1><p>Hello <b>reader</b>, see "
        '<a href="https://example.com/plan">the plan</a>.</p>'
        "<ul><li>One</li><li>Two</li></ul>"
        '<div class="gmail_quote"><p>On Friday, Alice wrote:</p>'
        "<blockquote><p>Old context</p></blockquote></div><p>Current inline answer</p>"
    )
    text, parts, cut = markdown_preview("Fallback", source, 4096)
    assert not cut
    assert "# Launch notes" in text
    assert "**reader**" in text
    assert "[the plan](https://example.com/plan)" in text
    assert "- One" in text and "- Two" in text
    assert "".join(part["text"] for part in parts) == text
    quoted = [part for part in parts if part["quoted"]]
    assert len(quoted) == 1
    assert "Old context" in quoted[0]["text"]
    assert "On Friday" in quoted[0]["text"]
    assert "Current inline answer" not in quoted[0]["text"]
    output = rendered(parts)
    assert "<h1>Launch notes</h1>" in output
    assert '<a href="https://example.com/plan" target="_blank" rel="noopener noreferrer">' in output


def test_outlook_tail_is_quoted_in_markdown():
    text, parts, _ = markdown_preview(
        "",
        '<p>Current reply</p><div id="divRplyFwdMsg">From: Alice</div>'
        "<p>Old Outlook reply</p><p>Old signature</p>",
        4096,
    )
    assert "Current reply" in parts[0]["text"] and not parts[0]["quoted"]
    assert parts[-1]["quoted"]
    assert "Old Outlook reply" in parts[-1]["text"]
    assert "Old signature" in parts[-1]["text"]
    assert "".join(part["text"] for part in parts) == text


def test_layout_table_keeps_reply_history_collapsed_and_data_tables_render():
    text, parts, _ = markdown_preview(
        "",
        '<table role="presentation"><tr><td><p>Current reply</p>'
        '<div class="gmail_quote">Old reply</div><p>Current answer</p>'
        "</td></tr></table><table><tr><th>Item</th><th>Count</th></tr>"
        "<tr><td>Launches</td><td>2</td></tr></table>",
        4096,
    )
    quoted = "".join(part["text"] for part in parts if part["quoted"])
    assert "Old reply" in quoted
    assert "Current reply" not in quoted and "Current answer" not in quoted
    assert "<table>" in rendered(parts) and "<th>Item</th>" in rendered(parts)
    assert "".join(part["text"] for part in parts) == text


def test_plain_markdown_quotes_keep_inline_answers_and_code_visible():
    source = (
        "# Plan\n\n**Ready**\n\n> Old question\nMy inline answer\n\n"
        "```text\n> This is code, not history\n```\n\n"
        "On Friday, Alice <alice@example.com> wrote:\n> Earlier reply\n"
    )
    text, parts, cut = markdown_preview(source, "", 4096)
    assert text == source and not cut
    assert "".join(part["text"] for part in parts) == source
    quoted = "".join(part["text"] for part in parts if part["quoted"])
    assert "Old question" in quoted and "Earlier reply" in quoted
    assert "On Friday" in quoted
    assert "inline answer" not in quoted
    assert "This is code" not in quoted
    assert '<code class="language-text">&gt; This is code, not history' in rendered(parts)


def test_markdown_cannot_execute_html_or_load_remote_images():
    source = (
        "<script>window.hacked=true</script>\n\n"
        "[unsafe](javascript:alert%281%29)\n"
        "[local](/api/mailboxes/x/pause)\n"
        "![tracking](https://tracker.invalid/pixel)\n"
        "[safe](https://example.com)\n"
    )
    _, parts, _ = markdown_preview(source, "", 4096)
    output = rendered(parts)
    assert "<script>" not in output and "&lt;script&gt;" in output
    assert "<img" not in output and "tracking" in output
    assert 'href="javascript:' not in output
    assert 'href="/api/' not in output
    assert 'href="https://example.com"' in output


def test_html_to_markdown_removes_active_content_and_escapes_literal_html():
    source = (
        "<script>secret</script><style>secret</style><iframe>secret</iframe>"
        "<p>&lt;script&gt;literal&lt;/script&gt; "
        '<a href="javascript:alert(1)">Unsafe</a></p>'
        '<img src="https://tracker.invalid/pixel" alt="Image description">'
    )
    text, parts, _ = markdown_preview("", source, 4096)
    assert "secret" not in text
    assert "javascript:" not in text and "tracker.invalid" not in text
    assert "&lt;script&gt;" in text and "Image description" in text
    assert "<script>" not in rendered(parts)


def test_markdown_limit_and_deep_html_fallback():
    text, parts, cut = markdown_preview("x" * 100, "", 32)
    assert text == "x" * 32 and cut
    assert "".join(part["text"] for part in parts) == text
    text, parts, cut = markdown_preview("Readable fallback", "<div>" * 1500 + "x", 32)
    assert text == "Readable fallback" and not cut
    assert "Readable fallback" in rendered(parts)


def test_empty_html_alternative_keeps_the_plain_body():
    text, parts, cut = markdown_preview("Current message", "<p></p><style>p {}</style>", 32)
    assert text == "Current message" and not cut
    assert "Current message" in rendered(parts)


def test_preview_exposes_markdown_without_changing_text_or_headers(archive):
    app, catalog, mailbox_id, _, _ = archive
    with TestClient(app) as client:
        base = f"/api/mailboxes/{mailbox_id}"
        client.post(f"{base}/index")
        catalog.get(mailbox_id)._thread.join(timeout=5)
        detail = client.get(f"{base}/messages/1").json()
        assert "Message body 0" in detail["body_text"]
        assert "Hello **reader**" in detail["body_markdown"]
        assert detail["body_markdown_parts"]
        assert "script" not in detail["body_markdown"]
        assert detail["headers"][0]["name"] == "From"
