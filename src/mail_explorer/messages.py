"""Bounded MIME previews and chunked original-message downloads."""

from collections.abc import Iterator
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from .attachments import attachments
from .config import Settings
from .markdown import markdown_preview
from .quotes import html_quote_kind, merge_parts, text_parts

HTML_TAGS = frozenset(
    {
        "html",
        "head",
        "body",
        "div",
        "p",
        "span",
        "br",
        "hr",
        "a",
        "img",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        "caption",
        "col",
        "colgroup",
        "b",
        "strong",
        "i",
        "em",
        "u",
        "s",
        "small",
        "big",
        "sub",
        "sup",
        "pre",
        "code",
        "blockquote",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "font",
        "center",
        "style",
    }
)
HTML_ATTRS = frozenset(
    {
        "style",
        "class",
        "id",
        "title",
        "width",
        "height",
        "align",
        "valign",
        "colspan",
        "rowspan",
        "cellpadding",
        "cellspacing",
        "border",
        "bgcolor",
        "color",
        "face",
        "size",
        "alt",
        "dir",
    }
)


class SafeHTML(HTMLParser):
    """Remove navigation, active content and resource URLs before sandbox rendering."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0
        self.in_style = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "iframe", "object", "svg", "math", "template"}:
            self.hidden += 1
        if self.hidden or tag not in HTML_TAGS:
            return
        safe_attrs = []
        for name, value in attrs:
            if value is None:
                continue
            if name in HTML_ATTRS or (
                tag == "img"
                and name == "src"
                and value.lower().startswith(
                    ("data:image/png;", "data:image/jpeg;", "data:image/gif;", "data:image/webp;")
                )
            ):
                safe_attrs.append(f' {name}="{escape(value, quote=True)}"')
        if quote_kind := html_quote_kind(tag, attrs):
            safe_attrs.append(f' data-mail-quote="{quote_kind}"')
        self.parts.append(f"<{tag}{''.join(safe_attrs)}>")
        if tag == "style":
            self.in_style = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "iframe", "object", "svg", "math", "template"}:
            self.hidden = max(0, self.hidden - 1)
            return
        if not self.hidden and tag in HTML_TAGS:
            self.parts.append(f"</{tag}>")
            if tag == "style":
                self.in_style = False

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data if self.in_style else escape(data))


def safe_html(html: str) -> str:
    sanitizer = SafeHTML()
    sanitizer.feed(html)
    sanitizer.close()
    return "".join(sanitizer.parts)


class HTMLText(HTMLParser):
    def __init__(self, limit: int):
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.length = 0
        self.parts: list[str] = []
        self.hidden = 0
        self.quoted: list[bool] = []
        self.elements = [("", False, False)]

    def handle_starttag(self, tag: str, attrs) -> None:
        kind = html_quote_kind(tag, attrs)
        if kind == "tail":
            parent, quoted, _ = self.elements[-1]
            self.elements[-1] = (parent, quoted, True)
        parent_quoted = self.elements[-1][1] or self.elements[-1][2]
        if tag not in {"br", "hr", "img", "meta", "input", "col", "link", "wbr"}:
            self.elements.append((tag, parent_quoted or kind == "block", False))
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "blockquote"}:
            self.handle_data("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "tr", "li", "blockquote"}:
            self.handle_data("\n")
        for index in range(len(self.elements) - 1, 0, -1):
            if self.elements[index][0] == tag:
                del self.elements[index:]
                break

    def handle_data(self, data: str) -> None:
        if not self.hidden and self.length < self.limit:
            data = data[: self.limit - self.length]
            self.parts.append(data)
            self.quoted.append(self.elements[-1][1] or self.elements[-1][2])
            self.length += len(data)

    def segments(self) -> list[dict]:
        return merge_parts(zip(self.parts, self.quoted), strip=True)


def decode_text(part: EmailMessage | None, limit: int) -> tuple[str, bool]:
    if part is None:
        return "", False
    payload = part.get_payload(decode=True)
    if payload is None:
        return "", False
    charset = part.get_content_charset() or "utf-8"
    try:
        text = payload.decode(charset, errors="replace")
    except (LookupError, UnicodeError):
        text = payload.decode("utf-8", errors="replace")
    return text[:limit], len(text) > limit


def preview(path: Path, record: dict, settings: Settings) -> dict:
    size = record["end"] - record["content_start"]
    with path.open("rb") as source:
        source.seek(record["content_start"])
        raw = source.read(min(size, settings.preview_bytes))
    message = BytesParser(policy=policy.default).parsebytes(raw)
    plain, plain_cut = decode_text(message.get_body(preferencelist=("plain",)), settings.text_chars)
    html, html_cut = decode_text(message.get_body(preferencelist=("html",)), settings.text_chars)
    plain_parts = text_parts(plain)
    if not plain and html:
        parser = HTMLText(settings.text_chars)
        parser.feed(html)
        plain = "".join(parser.parts).strip()
        plain_parts = parser.segments()
    markdown, markdown_parts, markdown_cut = markdown_preview(plain, html, settings.text_chars)
    return {
        "id": record["id"],
        "subject": record["subject"],
        "sender": record["sender"],
        "recipients": record["recipients"],
        "date": record["date"],
        "size": size,
        "body_text": plain,
        "body_text_parts": plain_parts,
        "body_html": safe_html(html),
        "body_markdown": markdown,
        "body_markdown_parts": markdown_parts,
        "headers": [{"name": name, "value": str(value)[:4096]} for name, value in message.items()],
        "attachments": [item.metadata() for item in attachments(path, record, settings)],
        "truncated": size > settings.preview_bytes or plain_cut or html_cut or markdown_cut,
        "preview_limit": settings.preview_bytes,
    }


def raw_chunks(path: Path, start: int, end: int, chunk_bytes: int) -> Iterator[bytes]:
    with path.open("rb") as source:
        source.seek(start)
        remaining = end - start
        while remaining:
            chunk = source.read(min(chunk_bytes, remaining))
            if not chunk:
                return
            remaining -= len(chunk)
            yield chunk
