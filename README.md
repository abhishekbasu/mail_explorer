# Mail Explorer

A local FastAPI app for browsing large `.mbox` archives, with a simple mailbox list and reading pane. The example in `mailbox/` is approximately 95 GB; neither startup nor browsing loads that file into memory.

## Run

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run mail-explorer
```

Open **http://127.0.0.1:8000**. The app discovers `.mbox` and `.mbx` files directly inside `mailbox/`. Selecting an archive starts background indexing; messages become available as the scanner finds them. Use **Pause indexing** to stop and **Resume indexing** to continue.

To use another directory or port:

```sh
uv run mail-explorer --mailbox-dir /path/to/archives --port 8001
```

`--cache-dir` changes the index location (default: `.mail-explorer/`). `MAILBOX_DIR` and `MAIL_EXPLORER_CACHE` provide the same defaults through environment variables. `uv run python -m mail_explorer` and `uv run uvicorn mail_explorer.app:app` also work. Run a single server worker against a given cache directory.

## Browse

- Choose an archive. **Threads** groups conversations and sorts them by latest activity; **Messages** shows individual emails in archive order.
- Open a thread to see its messages in chronological order. Expand a message to read it. One body stays open at a time, and long conversations have paginated message lists. Use the conversation's **Refresh** button to pick up replies indexed since you opened it.
- Search subjects, senders, and recipients. Search uses word prefixes; `pric` matches `pricing`, and multiple words must all match. In Threads view, a match in any message brings up the conversation. Search covers the indexed portion of the archive, with more results available as indexing advances.
- Open an email to see its text, HTML, headers, and attachment names. HTML is sanitized and displayed in a sandbox; scripts, remote images, and external links are blocked. Inline CID images are not resolved.
- Download the original `.eml` to access the complete message and attachments. Downloads preserve the stored message bytes, including mbox body escaping, and omit the mbox envelope line.
- Press `/` to focus search, use `↑` and `↓` to browse messages, or press `?` to see keyboard shortcuts. Message-format tabs support the left and right arrow keys.
- The three-pane workspace adapts to smaller screens: open archives with the sidebar button, and use **Back** or `Esc` to return from a message to the list. Appearance follows your system’s light or dark setting.

## How large archives are handled

The scanner reads **1 MiB chunks**, retaining only five bytes between chunks to recognize separators that cross read boundaries. It reads at most **64 KiB of headers per message** and writes small batches of offsets and decoded headers to SQLite. Email bodies and attachments remain in the original archive. Full-text search indexes only the stored subject, sender, and recipient headers. Message lists use bounded pages and cursor pagination rather than collecting the whole archive or all search results.

Selecting a message seeks directly to its recorded offset and reads at most **8 MiB** for MIME parsing. Each text/HTML format is capped at **524,288 characters**, and the server permits at most two concurrent previews. Large messages display a truncation notice; their complete original remains available as a streamed download. SQLite uses a 4 MiB page cache per connection and disables memory mapping. Actual process memory also includes Python, MIME parsing, and concurrent HTTP requests; these limits are per operation, not a fixed RSS ceiling.

Index checkpoints and message inserts commit together. Restarting the app resumes at the last fully indexed message; an interrupted partial message is rescanned. Indexing the full 95 GB archive takes a sequential disk pass, but you can read the first messages long before it finishes. The index persists across restarts. A changed file size, modification time, or file identity invalidates its cache and rebuilds the index. Keep archives unchanged while browsing.

### Conversation grouping

`X-GM-THRID` is authoritative when present. Without it, `Message-ID`, `References`, and `In-Reply-To` link replies and ancestors. Missing ancestors have placeholders in SQLite, so replies can arrive before their parents. Later links can merge fallback groups; separate explicit Gmail thread IDs remain separate. Subjects alone never merge unrelated messages. Messages without usable identifiers or reply links form their own conversations. Dates that cannot be parsed use the Unix epoch for sorting; equal dates use archive order.

An existing version 1 index upgrades without discarding offsets, search data, or scan progress. The background worker reads bounded headers directly at the saved offsets, grouping up to 64 messages per transaction. This header backfill supports pause/resume and runs before scanning any remaining unindexed portion. The UI shows grouping progress. Thread membership, ancestor links, counts, and checkpoints are disk-backed; opening a conversation fetches at most 50 message summaries and only requests a body when it is expanded. Headers use at most 128 identifiers each from References and In-Reply-To, retaining the first and last identifiers in unusually long chains.

Supports conventional mboxo/mboxrd archives with line-leading `From ` envelope separators, LF or CRLF line endings, and escaped body `From ` lines. Content-Length-based mboxcl/mboxcl2 archives with unescaped separators in their bodies are not supported.

## Development

```sh
uv sync --group dev
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests cover chunk-boundary detection, giant-line memory use, encoded headers, resumable indexing, thread grouping and chronological pagination, out-of-order replies, missing parents, Gmail precedence, resumable header-only migration, search, file-change invalidation, MIME previews, HTML isolation, and original-message downloads.
