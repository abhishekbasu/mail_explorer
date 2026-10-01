import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    mailbox_dir: Path
    cache_dir: Path
    chunk_bytes: int = 1024 * 1024
    header_bytes: int = 64 * 1024
    preview_bytes: int = 8 * 1024 * 1024
    text_chars: int = 512 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mailbox_dir=Path(os.getenv("MAILBOX_DIR", "mailbox")).resolve(),
            cache_dir=Path(os.getenv("MAIL_EXPLORER_CACHE", ".mail-explorer")).resolve(),
        )
