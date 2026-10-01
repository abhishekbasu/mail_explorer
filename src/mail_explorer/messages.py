"""Bounded MIME previews and chunked original-message downloads."""

from collections.abc import Iterator
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from .config import Settings

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

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "tr", "li", "h1", "h2", "h3"}:
            self.handle_data("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "tr", "li"}:
            self.handle_data("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden and self.length < self.limit:
            data = data[: self.limit - self.length]
            self.parts.append(data)
            self.length += len(data)


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
    if not plain and html:
        parser = HTMLText(settings.text_chars)
        parser.feed(html)
        plain = "".join(parser.parts).strip()
    attachments = []
    for part in message.walk():
        if part.get_content_disposition() == "attachment" or part.get_filename():
            attachments.append(
                {
                    "name": str(part.get_filename() or "Unnamed attachment")[:4096],
                    "content_type": part.get_content_type(),
                }
            )
    return {
        "id": record["id"],
        "subject": record["subject"],
        "sender": record["sender"],
        "recipients": record["recipients"],
        "date": record["date"],
        "size": size,
        "body_text": plain,
        "body_html": safe_html(html),
        "headers": [{"name": name, "value": str(value)[:4096]} for name, value in message.items()],
        "attachments": attachments,
        "truncated": size > settings.preview_bytes or plain_cut or html_cut,
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
