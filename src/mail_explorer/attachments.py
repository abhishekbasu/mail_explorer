"""Locate MIME attachments on disk and decode their payloads in bounded chunks."""

import binascii
import re
from collections.abc import Iterator
from dataclasses import dataclass
from email import policy
from email.parser import BytesHeaderParser
from pathlib import Path
from urllib.parse import quote

from .config import Settings

IMAGE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "image/avif"}
)
ENCODINGS = frozenset({"7bit", "8bit", "binary", "base64", "quoted-printable"})


@dataclass(frozen=True)
class Attachment:
    id: int
    name: str
    content_type: str
    encoding: str
    start: int
    end: int

    def metadata(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "content_type": self.content_type,
            "previewable": self.content_type in IMAGE_TYPES and self.encoding in ENCODINGS,
        }


def _headers(path: Path, start: int, end: int, limit: int):
    with path.open("rb") as source:
        source.seek(start)
        header = bytearray()
        while source.tell() < end and len(header) < limit:
            line = source.readline(min(limit - len(header), end - source.tell()))
            header.extend(line)
            if line in {b"\n", b"\r\n"}:
                break
        else:
            if source.tell() < end:
                raise ValueError("Attachment headers exceed the preview limit.")
        return BytesHeaderParser(policy=policy.default).parsebytes(bytes(header)), source.tell()


def _part_ranges(
    path: Path, start: int, end: int, boundary: str, limit: int
) -> Iterator[tuple[int, int]]:
    # MIME boundaries have at most 70 characters. Never collect an entire body line.
    if not boundary or len(boundary) > 70:
        raise ValueError("Invalid MIME boundary.")
    marker = b"--" + boundary.encode("ascii")
    part_start = None
    at_line_start = True
    newline_bytes = 0
    last_byte = b""
    with path.open("rb") as source:
        source.seek(start)
        while source.tell() < end:
            position = source.tell()
            line = source.readline(min(max(1024, limit), end - position))
            if not line:
                raise ValueError("The message changed while reading attachments.")
            candidate = line.rstrip(b"\r\n").rstrip(b" \t")
            if at_line_start and candidate in {marker, marker + b"--"}:
                if part_start is not None:
                    yield part_start, max(part_start, position - newline_bytes)
                if candidate == marker + b"--":
                    return
                part_start = source.tell()
            at_line_start = line.endswith(b"\n")
            tail = (last_byte + line[-2:])[-2:]
            newline_bytes = 2 if tail == b"\r\n" else int(at_line_start)
            last_byte = line[-1:]
    if part_start is not None:
        yield part_start, end


def _parts(
    path: Path, start: int, end: int, settings: Settings, depth: int = 0
) -> Iterator[tuple[str, str, str, int, int]]:
    if depth > 32:
        raise ValueError("The message has too many nested MIME parts.")
    headers, body_start = _headers(path, start, end, settings.header_bytes)
    content_type = headers.get_content_type()
    filename = headers.get_filename()
    encoding = str(headers.get("Content-Transfer-Encoding", "7bit")).strip().lower()
    if (
        headers.get_content_disposition() == "attachment"
        or filename
        or headers.get_content_maintype() == "image"
    ):
        name = str(filename or "Unnamed attachment")[:4096]
        yield name, content_type, encoding, body_start, end
    elif headers.get_content_maintype() == "multipart" and headers.get_boundary():
        for child_start, child_end in _part_ranges(
            path, body_start, end, headers.get_boundary(), settings.header_bytes
        ):
            yield from _parts(path, child_start, child_end, settings, depth + 1)
    elif content_type == "message/rfc822" and encoding in {"7bit", "8bit", "binary"}:
        yield from _parts(path, body_start, end, settings, depth + 1)


def attachments(path: Path, record: dict, settings: Settings) -> Iterator[Attachment]:
    for number, (name, content_type, encoding, start, end) in enumerate(
        _parts(path, record["content_start"], record["end"], settings)
    ):
        # Bound metadata too, including messages made of thousands of tiny attachments.
        if number >= 1024:
            raise ValueError("This message has too many attachments. Download its original.")
        yield Attachment(number, name, content_type, encoding, start, end)


def disposition(attachment: Attachment, download: bool) -> str:
    # Only a basename is suggested, and control characters never enter an HTTP header.
    name = attachment.name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if ord(char) >= 32 and ord(char) != 127)[:255]
    name = name.strip(" .") or "attachment"
    fallback = name.encode("ascii", "replace").decode("ascii").replace('"', "_")
    kind = "attachment" if download else "inline"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def attachment_chunks(path: Path, attachment: Attachment, chunk_bytes: int) -> Iterator[bytes]:
    if attachment.encoding not in ENCODINGS:
        raise ValueError("Unsupported attachment encoding. Download the original message.")
    pending = b""
    with path.open("rb") as source:
        source.seek(attachment.start)
        remaining = attachment.end - attachment.start
        while remaining:
            chunk = source.read(min(chunk_bytes, remaining))
            if not chunk:
                raise ValueError("The message changed while downloading the attachment.")
            remaining -= len(chunk)
            if attachment.encoding == "base64":
                data = pending + re.sub(rb"[^A-Za-z0-9+/=]", b"", chunk)
                length = len(data) // 4 * 4
                pending = data[length:]
                decoded = binascii.a2b_base64(data[:length]) if length else b""
            elif attachment.encoding == "quoted-printable":
                data = pending + chunk
                # Keep an unfinished =XX escape or =CRLF soft break across reads.
                equal = data.rfind(b"=")
                length = equal if equal >= max(0, len(data) - 2) else len(data)
                pending = data[length:]
                decoded = binascii.a2b_qp(data[:length])
            else:
                decoded = chunk
            if decoded:
                yield decoded
    if pending:
        if attachment.encoding == "base64":
            yield binascii.a2b_base64(pending + b"=" * (-len(pending) % 4))
        else:
            yield binascii.a2b_qp(pending)
