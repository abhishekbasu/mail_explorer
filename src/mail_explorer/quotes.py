"""Conservative quote detection that keeps every character available to the reader."""

import re
from collections.abc import Iterable

QUOTE_CLASSES = frozenset(
    {
        "gmail_quote",
        "gmail_quote_container",
        "yahoo_quoted",
        "protonmail_quote",
        "moz-forward-container",
    }
)
QUOTE_IDS = frozenset({"olk_src_body_section"})
QUOTE_TAIL_IDS = frozenset({"divrplyfwdmsg", "replyforwardmsg"})
QUOTED_LINE = re.compile(r"^[ \t]*>")
ATTRIBUTION = re.compile(r"^On\b.{1,1000}\bwrote:[ \t]*$", re.IGNORECASE | re.DOTALL)
HISTORY_SEPARATOR = re.compile(
    r"^[ \t]*-{2,}[ \t]*(?:Original Message|Forwarded message)[ \t]*-{2,}[ \t]*$",
    re.IGNORECASE,
)
HEADER = re.compile(r"^[ \t]*(From|Sent|Date|To|Cc|Subject):[ \t]*\S", re.IGNORECASE)
DATE_OR_ADDRESS = re.compile(
    r"@|\b\d{4}\b|\d{1,2}[/.-]\d{1,2}|\b(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|"
    r"Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\b",
    re.IGNORECASE,
)


def html_quote_kind(tag: str, attrs) -> str | None:
    attributes = dict(attrs)
    identity = (attributes.get("id") or "").lower()
    classes = (attributes.get("class") or "").lower().split()
    if tag == "div" and identity in QUOTE_TAIL_IDS:
        return "tail"
    if tag == "blockquote" or identity in QUOTE_IDS or QUOTE_CLASSES.intersection(classes):
        return "block"
    return None


def merge_parts(items: Iterable[tuple[str, bool]], strip: bool = False) -> list[dict]:
    result = []
    chunks = []
    current = False
    for text, quoted in items:
        if not text:
            continue
        if chunks and quoted != current:
            result.append({"text": "".join(chunks), "quoted": current})
            chunks = []
        current = quoted
        chunks.append(text)
    if chunks:
        result.append({"text": "".join(chunks), "quoted": current})
    if strip:
        while result:
            result[0]["text"] = result[0]["text"].lstrip()
            if result[0]["text"]:
                break
            result.pop(0)
        while result:
            result[-1]["text"] = result[-1]["text"].rstrip()
            if result[-1]["text"]:
                break
            result.pop()
    return result


def text_parts(text: str) -> list[dict]:
    lines = text.splitlines(keepends=True)
    quoted = [bool(QUOTED_LINE.match(line)) for line in lines]
    for position, line in enumerate(lines):
        if quoted[position]:
            continue
        if HISTORY_SEPARATOR.fullmatch(line.rstrip("\r\n")):
            quoted[position:] = [True] * (len(lines) - position)
            break
        # A forwarded header block needs a date and a recipient or subject as evidence.
        header = HEADER.match(line)
        if header and header[1].lower() == "from":
            fields = set()
            for following in lines[position : position + 8]:
                match = HEADER.match(following)
                if match:
                    fields.add(match[1].lower())
                elif following.strip() and not following.startswith((" ", "\t")):
                    break
            if fields.intersection({"sent", "date"}) and fields.intersection({"to", "subject"}):
                quoted[position:] = [True] * (len(lines) - position)
                break
        if not line.lstrip().lower().startswith("on "):
            continue
        # Some clients wrap the sender/date attribution over two or three lines.
        for end in range(position + 1, min(position + 3, len(lines)) + 1):
            attribution = " ".join(value.strip() for value in lines[position:end])
            if not ATTRIBUTION.fullmatch(attribution):
                continue
            following = end
            while following < len(lines) and not lines[following].strip():
                following += 1
            if following == len(lines):
                break
            if quoted[following]:
                quoted[position:following] = [True] * (following - position)
            elif DATE_OR_ADDRESS.search(attribution):
                quoted[position:] = [True] * (len(lines) - position)
            break
    # Blank lines between two quoted lines belong to the same disclosure.
    position = 0
    while position < len(lines):
        if lines[position].strip():
            position += 1
            continue
        end = position + 1
        while end < len(lines) and not lines[end].strip():
            end += 1
        if position and end < len(lines) and quoted[position - 1] and quoted[end]:
            quoted[position:end] = [True] * (end - position)
        position = end
    return merge_parts(zip(lines, quoted))
