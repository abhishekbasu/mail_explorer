"""Convert bounded email bodies to Markdown and render without active HTML or images."""

from html import escape
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from markdownify import MarkdownConverter

from .quotes import html_quote_kind, merge_parts, text_parts


def safe_link(url: str) -> bool:
    try:
        return urlsplit(url).scheme.lower() in {"http", "https", "mailto"}
    except ValueError:
        return False


class EmailMarkdown(MarkdownConverter):
    def convert_img(self, el, text, parent_tags):
        # Keep image labels without loading tracking pixels or copying large data URLs.
        return escape(el.get("alt", ""))

    def convert_a(self, el, text, parent_tags):
        if not safe_link(el.get("href", "")):
            return text
        return super().convert_a(el, text, parent_tags)

    def process_text(self, el, parent_tags=None):
        text = super().process_text(el, parent_tags)
        if parent_tags and "_noformat" in parent_tags:
            return text
        return text.replace("<", "&lt;").replace(">", "&gt;")


def from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(["script", "style", "iframe", "object", "svg", "math", "template"]):
        node.decompose()
    for node in list(soup.find_all(True)):
        attrs = [
            (name, " ".join(value) if isinstance(value, list) else value)
            for name, value in node.attrs.items()
        ]
        kind = html_quote_kind(node.name, attrs)
        if not kind:
            continue
        if kind == "tail":
            following = list(node.next_siblings)
            wrapper = soup.new_tag("blockquote")
            node.wrap(wrapper)
            for sibling in following:
                wrapper.append(sibling)
        else:
            node.name = "blockquote"
    # Email layout tables can contain whole replies. Markdown table cells cannot
    # contain block quotes, so flatten these layouts while keeping their text.
    for table in soup.find_all("table"):
        if table.find("blockquote") and not table.find(["th", "thead"]):
            for node in [table, *table.find_all(["thead", "tbody", "tfoot", "tr", "td", "th"])]:
                node.name = "div"
    return EmailMarkdown(heading_style="ATX", bullets="-", autolinks=False).convert_soup(soup)


def markdown_preview(plain: str, html: str, limit: int) -> tuple[str, list[dict], bool]:
    converted = False
    text = plain
    if html:
        try:
            converted_text = from_html(html)
            if converted_text.strip():
                text = converted_text
                converted = True
        except RecursionError:
            # Pathologically nested markup still has the bounded plain-text fallback.
            pass
    truncated = len(text) > limit
    text = text[:limit]
    parser = MarkdownIt("js-default", {"breaks": True})
    parser.validateLink = safe_link
    parser.add_render_rule(
        "image", lambda self, tokens, idx, options, env: escape(tokens[idx].content)
    )

    def link_open(self, tokens, idx, options, env):
        tokens[idx].attrSet("target", "_blank")
        tokens[idx].attrSet("rel", "noopener noreferrer")
        return self.renderToken(tokens, idx, options, env)

    parser.add_render_rule("link_open", link_open)
    environment = {}
    tokens = parser.parse(text, environment)
    lines = text.splitlines(keepends=True)
    quoted = [False] * len(lines)
    if not converted:
        position = 0
        for part in text_parts(text):
            count = len(part["text"].splitlines(keepends=True))
            quoted[position : position + count] = [part["quoted"]] * count
            position += count
    for token in tokens:
        if token.level != 0 or token.map is None:
            continue
        start, end = token.map
        if token.type in {"fence", "code_block"} or (converted and token.type == "blockquote_open"):
            quoted[start:end] = [token.type == "blockquote_open"] * (end - start)
    parts = merge_parts(zip(lines, quoted))
    for part in parts:
        part["html"] = parser.render(part["text"], environment)
    return text, parts, truncated
