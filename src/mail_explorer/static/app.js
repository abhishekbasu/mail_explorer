"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  mailbox: null,
  mode: "threads",
  query: "",
  items: [],
  listStart: 1,
  previous: null,
  next: null,
  listLoading: false,
  selected: null,
  thread: null,
  threadOrder: "oldest",
  status: null,
  listRequest: 0,
  readerRequest: 0,
  threadRequest: 0,
  mailboxRequest: 0,
  polling: false,
};
let searchTimer;
let readerSequence = 0;
let sidebarReturnFocus;
let sidebarPinned = false;
let sidebarHideTimer;
let restoringSidebarFocus = false;
let readerKeyboardNavigation = false;
let lastFocusedRegion;
let listScrollFrame;
let listKeyboardNavigation = false;
const LIST_BATCH_SIZE = 50;
const LIST_WINDOW_SIZE = 200;
const MESSAGE_FORMATS = ["markdown", "text", "html", "headers"];
const compactLayout = matchMedia("(max-width: 720px)");
const drawerLayout = matchMedia("(max-width: 1150px)");
const hoverSidebar = matchMedia("(hover: hover) and (pointer: fine)");

function icon(name, className = "icon") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#icon-${name}`);
  svg.append(use);
  return svg;
}

function setSidebar(open, { peek = false } = {}) {
  clearTimeout(sidebarHideTimer);
  if (!peek) sidebarPinned = open;
  const workspace = $("workspace");
  const hadFocus = !!document.activeElement.closest(".sidebar");
  const modal = open && drawerLayout.matches && !peek;
  workspace.classList.toggle("sidebar-open", modal);
  workspace.classList.toggle("sidebar-peeking", open && peek);
  workspace.classList.toggle("sidebar-collapsed", !open);
  $("sidebar-backdrop").hidden = !modal;
  $("sidebar").inert = !open;
  document.querySelector(".mail-panel").inert = modal;
  document.querySelector(".reader").inert = modal;
  if (modal) {
    sidebarReturnFocus = document.activeElement;
    $("sidebar").setAttribute("role", "dialog");
    $("sidebar").setAttribute("aria-modal", "true");
    $("sidebar-close").focus();
  } else {
    $("sidebar").removeAttribute("role");
    $("sidebar").removeAttribute("aria-modal");
    if (!open) {
      restoringSidebarFocus = true;
      try {
        if (sidebarReturnFocus?.isConnected) sidebarReturnFocus.focus();
        else if (hadFocus) $("sidebar-toggle").focus({ preventScroll: true });
      } finally {
        restoringSidebarFocus = false;
      }
      sidebarReturnFocus = null;
    }
  }
  $("sidebar-toggle").setAttribute("aria-expanded", String(open));
  $("sidebar-reveal").setAttribute("aria-expanded", String(open));
  $("sidebar-reveal").setAttribute(
    "aria-label",
    open ? "Hide archives" : "Show archives",
  );
  $("sidebar-reveal").title = open ? "Hide archives" : "Show archives";
}

function peekSidebar() {
  clearTimeout(sidebarHideTimer);
  if (
    sidebarPinned ||
    !hoverSidebar.matches ||
    compactLayout.matches ||
    document.querySelector("dialog[open]")
  )
    return;
  setSidebar(true, { peek: true });
}

function hideSidebarLater() {
  clearTimeout(sidebarHideTimer);
  if (sidebarPinned) return;
  sidebarHideTimer = setTimeout(() => {
    const focused = document.activeElement;
    if (
      $("sidebar").contains(focused) ||
      $("sidebar-rail").contains(focused) ||
      $("sidebar").matches(":hover") ||
      $("sidebar-rail").matches(":hover")
    )
      return;
    setSidebar(false, { peek: true });
  }, 220);
}

function syncLayout() {
  const sidebarHadFocus =
    !!document.activeElement.closest(".sidebar") ||
    !!document.activeElement.closest(".sidebar-rail") ||
    lastFocusedRegion === "sidebar";
  $("workspace").classList.remove("sidebar-open");
  $("sidebar-backdrop").hidden = true;
  document.querySelector(".mail-panel").inert = false;
  document.querySelector(".reader").inert = false;
  sidebarReturnFocus = null;
  setSidebar(!drawerLayout.matches && sidebarPinned);
  if (sidebarHadFocus && $("sidebar").inert)
    (compactLayout.matches && $("workspace").classList.contains("reader-active")
      ? $("reader-back")
      : $("sidebar-toggle")
    ).focus({ preventScroll: true });
}

function showReader() {
  $("workspace").classList.add("reader-active");
  $("reader-scroll").scrollTop = 0;
  if (compactLayout.matches && !readerKeyboardNavigation)
    document.querySelector(".reader").focus({ preventScroll: true });
}

function backToList() {
  $("workspace").classList.remove("reader-active");
  const row = $("message-list").querySelector(`[data-id="${state.selected}"]`);
  (row || $("search")).focus({ preventScroll: true });
}

function bytes(value) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const power = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** power).toLocaleString(undefined, { maximumFractionDigits: power > 1 ? 1 : 0 })} ${units[power]}`;
}

function shortName(sender) {
  return (
    sender
      .replace(/\s*<[^>]*>\s*/g, "")
      .replace(/^"|"$/g, "")
      .trim() || sender
  );
}

function dateLabel(value, full = false) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "No date";
  return full
    ? date.toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showError(error) {
  $("toast-message").textContent = error.message || String(error);
  $("toast").hidden = false;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok)
    throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

function base() {
  return `/api/mailboxes/${state.mailbox}`;
}
function firstCursor() {
  return state.mode === "threads" ? "" : 0;
}
function itemCursor(item) {
  return state.mode === "threads"
    ? `${item.latest_timestamp}:${item.latest_message_id}`
    : item.id;
}
function messageCount(count) {
  return `${count.toLocaleString()} ${count === 1 ? "message" : "messages"}`;
}

function updateStatus(status) {
  state.status = status;
  const grouping = status.phase === "grouping";
  const progress = grouping ? status.threading_progress : status.progress;
  $("index-progress").value = progress;
  $("index-percent").textContent =
    `${(progress * 100).toFixed(progress < 0.01 ? 2 : 1)}%`;
  $("message-count").textContent = status.indexed_messages.toLocaleString();
  $("thread-count").textContent = status.thread_count.toLocaleString();
  $("list-count").textContent = (
    state.mode === "threads" ? status.thread_count : status.indexed_messages
  ).toLocaleString();
  $("scanned-size").textContent = bytes(status.scanned_bytes);
  const labels = {
    indexing: grouping
      ? `Grouping ${status.threaded_messages.toLocaleString()} of ${status.indexed_messages.toLocaleString()} messages`
      : "Indexing · ready to browse",
    paused: "Paused · progress saved",
    complete: "Your archive is ready",
    error: `Indexing stopped: ${status.error}`,
  };
  $("index-state").textContent = labels[status.state];
  $("index-card")?.setAttribute("data-state", status.state);
  $("index-toggle").disabled = status.state === "complete";
  $("index-toggle").textContent =
    status.state === "indexing"
      ? "Pause indexing"
      : status.state === "complete"
        ? "Index complete"
        : status.indexed_messages
          ? "Resume indexing"
          : "Start indexing";
  updateListRange();
}

function listAnchor() {
  const list = $("message-list");
  const top = list.getBoundingClientRect().top + list.clientTop;
  for (const row of list.children) {
    const rect = row.getBoundingClientRect();
    if (rect.bottom > top)
      return { id: row.dataset.id, offset: rect.top - top };
  }
  return null;
}

function updateListRange() {
  const list = $("message-list");
  const top = list.getBoundingClientRect().top + list.clientTop;
  const bottom = top + list.clientHeight;
  let first = -1;
  let last = -1;
  if (!list.hidden && list.clientHeight)
    [...list.children].forEach((row, index) => {
      const rect = row.getBoundingClientRect();
      if (rect.bottom > top && rect.top < bottom) {
        if (first === -1) first = index;
        last = index;
      }
    });
  const count =
    state.mode === "threads"
      ? state.status?.thread_count
      : state.status?.indexed_messages;
  $("list-range").textContent =
    first === -1
      ? "0 shown"
      : `${(state.listStart + first).toLocaleString()}–${(state.listStart + last).toLocaleString()}${!state.query && count ? ` of ${count.toLocaleString()}` : ""}`;
  $("list-range").title =
    state.mode === "threads" ? "Visible conversations" : "Visible emails";
}

function renderList({ anchor = listAnchor() } = {}) {
  const list = $("message-list");
  const scrollTop = list.scrollTop;
  const focusedId = document.activeElement.closest(".message-row")?.dataset.id;
  const fragment = document.createDocumentFragment();
  const threads = state.mode === "threads";
  $("view-heading").textContent = threads ? "Threads" : "All messages";
  $("view-order").textContent = threads
    ? "Latest activity first"
    : "In archive order";
  for (const mode of ["threads", "messages"]) {
    $(`view-${mode}`).classList.toggle("active", state.mode === mode);
    $(`view-${mode}`).setAttribute("aria-pressed", String(state.mode === mode));
  }
  for (const item of state.items) {
    const row = element("button", "message-row");
    row.type = "button";
    row.dataset.id = item.id;
    row.classList.toggle("selected", item.id === state.selected);
    row.setAttribute("aria-pressed", String(item.id === state.selected));
    const sender = shortName(item.sender);
    const avatar = element(
      "span",
      "row-avatar",
      sender.slice(0, 1).toUpperCase() || "?",
    );
    avatar.setAttribute("aria-hidden", "true");
    avatar.dataset.tone = String(
      [...sender].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 3,
    );
    const content = element("div", "row-content");
    const top = element("div", "row-top");
    const date = element("time", "row-date", dateLabel(item.date));
    date.title = dateLabel(item.date, true);
    top.append(element("span", "row-sender", sender), date);
    content.append(
      top,
      element("div", "row-subject", item.subject || "(No subject)"),
      element(
        "div",
        "row-recipient",
        `To: ${item.recipients || "Undisclosed recipients"}`,
      ),
    );
    const bottom = element("div", "row-bottom");
    const tag = element("span", "row-tag");
    tag.append(
      icon(threads ? "thread" : "mail"),
      document.createTextNode(
        threads ? messageCount(item.message_count) : bytes(item.size),
      ),
    );
    bottom.append(tag, icon("arrow-right", "icon row-arrow"));
    content.append(bottom);
    row.append(avatar, content);
    row.addEventListener("click", () => openItem(item.id));
    fragment.append(row);
  }
  list.replaceChildren(fragment);
  if (focusedId)
    list
      .querySelector(`[data-id="${focusedId}"]`)
      ?.focus({ preventScroll: true });
  const empty = !state.items.length;
  $("list-empty").hidden = !empty;
  list.hidden = empty;
  const anchorRow = anchor && list.querySelector(`[data-id="${anchor.id}"]`);
  list.scrollTop = scrollTop;
  if (anchorRow)
    list.scrollTop +=
      anchorRow.getBoundingClientRect().top -
      list.getBoundingClientRect().top -
      list.clientTop -
      anchor.offset;
  $("list-empty").querySelector("strong").textContent = state.query
    ? "No matches yet"
    : state.status?.state === "complete"
      ? "This archive is empty"
      : state.status?.phase === "grouping"
        ? "Finding your conversations"
        : "Your archive is on its way";
  $("list-empty").querySelector("p").textContent = state.query
    ? "Search matches word beginnings in subjects, senders, and recipients."
    : state.status?.state === "error"
      ? "Check archive progress for details."
      : state.status?.state === "paused"
        ? "Resume indexing to make more conversations available."
        : state.status?.state === "complete"
          ? "Choose another .mbox archive to explore."
          : "Messages and conversations appear as they are indexed.";
  $("list-label").textContent = state.query
    ? threads
      ? "Matching conversations"
      : "Matching indexed messages"
    : "Subject, sender, or recipient";
  updateListRange();
}

function resetList() {
  ++state.listRequest;
  state.items = [];
  state.listStart = 1;
  state.previous = null;
  state.next = null;
  state.listLoading = false;
  $("message-list").scrollTop = 0;
  $("message-list").setAttribute("aria-busy", "false");
  $("list-loading").hidden = true;
  renderList({ anchor: null });
}

async function loadItems(direction = "replace") {
  if (!state.mailbox) return false;
  const replacing = direction === "replace";
  const backwards = direction === "backward";
  const cursor = backwards ? state.previous : state.next;
  if (!replacing && (state.listLoading || cursor === null)) return false;
  if (replacing) resetList();
  const request = ++state.listRequest;
  state.listLoading = true;
  $("message-list").setAttribute("aria-busy", "true");
  $("list-loading").textContent = replacing ? "Loading…" : "Loading more…";
  $("list-loading").hidden = false;
  const params = new URLSearchParams({
    [backwards ? "before" : "after"]: replacing ? firstCursor() : cursor,
    limit: LIST_BATCH_SIZE,
    q: state.query,
  });
  try {
    const data = await api(`${base()}/${state.mode}?${params}`);
    if (request !== state.listRequest) return false;
    const anchor = replacing ? null : listAnchor();
    if (replacing) {
      state.items = data.items;
      state.previous = data.previous_cursor;
      state.next = data.next_cursor;
    } else if (backwards) {
      state.items.unshift(...data.items);
      state.listStart -= data.items.length;
      state.previous = data.previous_cursor;
      if (state.items.length > LIST_WINDOW_SIZE) {
        state.items.length = LIST_WINDOW_SIZE;
        state.next = itemCursor(state.items.at(-1));
      }
    } else {
      state.items.push(...data.items);
      state.next = data.next_cursor;
      const excess = state.items.length - LIST_WINDOW_SIZE;
      if (excess > 0) {
        state.items.splice(0, excess);
        state.listStart += excess;
        state.previous = itemCursor(state.items[0]);
      }
    }
    updateStatus(data.status);
    renderList({ anchor });
    return true;
  } catch (error) {
    if (request === state.listRequest) {
      showError(error);
    }
    return false;
  } finally {
    if (request === state.listRequest) {
      state.listLoading = false;
      $("message-list").setAttribute("aria-busy", "false");
      $("list-loading").hidden = true;
      updateListRange();
    }
  }
}

function scrollList() {
  cancelAnimationFrame(listScrollFrame);
  listScrollFrame = requestAnimationFrame(() => {
    updateListRange();
    const list = $("message-list");
    if (state.listLoading || list.hidden || !list.clientHeight) return;
    if (
      state.next !== null &&
      list.scrollHeight - list.scrollTop - list.clientHeight < 300
    )
      loadItems("forward");
    else if (state.previous !== null && list.scrollTop < 300)
      loadItems("backward");
  });
}

$("message-list").addEventListener("scroll", scrollList, { passive: true });
new ResizeObserver(scrollList).observe($("message-list"));

function resetReader(
  title = "Your archive, in focus.",
  description = "Select a conversation or message to revisit the details.",
) {
  closeCopyOptions();
  if ($("attachment-dialog").open) $("attachment-dialog").close();
  ++state.readerRequest;
  ++state.threadRequest;
  state.thread = null;
  $("workspace").classList.remove("reader-active");
  $("message-detail").hidden = true;
  $("message-detail").replaceChildren();
  $("conversation").hidden = true;
  $("conversation-messages").replaceChildren();
  $("reader-empty").hidden = false;
  $("reader-empty").querySelector("h2").textContent = title;
  $("reader-empty").querySelector("p").textContent = description;
}

function previewAttachment(attachment, url) {
  const dialog = $("attachment-dialog");
  const status = $("attachment-status");
  $("attachment-title").textContent = attachment.name;
  $("attachment-download").href = url;
  $("attachment-download").download = attachment.name;
  status.hidden = false;
  status.textContent = "Loading image…";
  const image = element("img", "attachment-image");
  image.alt = attachment.name;
  image.hidden = true;
  image.addEventListener("load", () => {
    if (!dialog.open || !image.isConnected) return;
    image.hidden = false;
    status.hidden = true;
  });
  image.addEventListener("error", () => {
    if (!dialog.open || !image.isConnected) return;
    status.textContent =
      "This image couldn’t be previewed. Download it to open it on your device.";
  });
  $("attachment-preview").replaceChildren(image);
  dialog.showModal();
  image.src = `${url}?download=false`;
}

function quotedTextSummary(doc = document) {
  const summary = doc.createElement("summary");
  summary.className = "quoted-summary";
  for (const [className, label] of [
    ["quote-show", "Show quoted text"],
    ["quote-hide", "Hide quoted text"],
  ]) {
    const span = doc.createElement("span");
    span.className = className;
    span.textContent = label;
    summary.append(span);
  }
  return summary;
}

function renderTextParts(container, parts) {
  container.replaceChildren(
    ...parts.map((part) => {
      const text = element("pre", "body-text", part.text);
      if (!part.quoted) return text;
      const disclosure = element("details", "quoted-text");
      disclosure.append(quotedTextSummary(), text);
      return disclosure;
    }),
  );
}

function renderMarkdownParts(container, parts) {
  container.replaceChildren(
    ...parts.map((part) => {
      const content = element("div", "markdown-part");
      // The server renders Markdown with raw HTML disabled and image URLs removed.
      content.innerHTML = part.html;
      if (!part.quoted) return content;
      const disclosure = element("details", "quoted-text");
      disclosure.append(quotedTextSummary(), content);
      return disclosure;
    }),
  );
}

async function copyText(text) {
  if (navigator.clipboard && isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const focused = document.activeElement;
  const selection = element("textarea", "clipboard-selection", text);
  selection.setAttribute("aria-label", "Message to copy");
  document.body.append(selection);
  try {
    selection.select();
    if (!document.execCommand("copy"))
      throw new Error("Clipboard access is unavailable.");
  } finally {
    selection.remove();
    focused?.focus({ preventScroll: true });
  }
}

function closeCopyOptions() {
  for (const menu of document.querySelectorAll(".copy-options:popover-open"))
    menu.hidePopover();
}

function copyHTMLContent(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  const policy = doc.createElement("meta");
  policy.httpEquiv = "Content-Security-Policy";
  policy.content =
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'";
  doc.head.prepend(policy);
  const charset = doc.createElement("meta");
  charset.setAttribute("charset", "utf-8");
  doc.head.prepend(charset);
  const text = `<!doctype html>${doc.documentElement.outerHTML}`;
  const quotes = [...doc.querySelectorAll("[data-mail-quote]")];
  for (const quote of quotes) {
    if (!quote.isConnected) continue;
    if (quote.dataset.mailQuote === "tail") {
      let sibling = quote;
      while (sibling) {
        const next = sibling.nextSibling;
        sibling.remove();
        sibling = next;
      }
    } else quote.remove();
  }
  return {
    text,
    withoutQuotes: `<!doctype html>${doc.documentElement.outerHTML}`,
    hasQuotes: quotes.length > 0,
    label: "HTML",
  };
}

function copyTextContent(text, parts, label) {
  return {
    text: text || "",
    withoutQuotes: parts?.length
      ? parts
          .filter((part) => !part.quoted)
          .map((part) => part.text)
          .join("")
      : text || "",
    hasQuotes: parts?.some((part) => part.quoted) || false,
    label,
  };
}

function quotedHTMLDocument(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  for (const quote of doc.querySelectorAll("[data-mail-quote]")) {
    if (quote.closest("details.quoted-text")) continue;
    const disclosure = doc.createElement("details");
    disclosure.className = "quoted-text";
    disclosure.append(quotedTextSummary(doc));
    quote.before(disclosure);
    if (quote.dataset.mailQuote === "tail") {
      // Outlook places the old message after its reply-header block.
      let sibling = quote;
      while (sibling) {
        const next = sibling.nextSibling;
        disclosure.append(sibling);
        sibling = next;
      }
    } else disclosure.append(quote);
  }
  const policy = doc.createElement("meta");
  policy.httpEquiv = "Content-Security-Policy";
  policy.content =
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'";
  doc.head.prepend(policy);
  const style = doc.createElement("style");
  style.textContent = `
    .quoted-text { margin: 12px 0; }
    .quoted-text > .quoted-summary {
      cursor: pointer; width: fit-content; padding: 8px 12px;
      border: 1px solid #90e0ef; border-radius: 7px; background: #e8f7fc;
      color: #023e8a; font: 13px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .quoted-summary:focus-visible { outline: 2px solid #0077b6; outline-offset: 3px; }
    .quoted-text > .quoted-summary .quote-hide { display: none; }
    .quoted-text[open] > .quoted-summary .quote-show { display: none; }
    .quoted-text[open] > .quoted-summary .quote-hide { display: inline; }
    .quoted-text:not([open]) > :not(summary) { display: none !important; }
    .quoted-text[open] > :not(summary) { margin-top: 12px; }
  `;
  doc.head.append(style);
  return `<!doctype html>${doc.documentElement.outerHTML}`;
}

function createMessageReader(detail) {
  const reader =
    $("message-template").content.firstElementChild.cloneNode(true);
  const field = (name) => reader.querySelector(`[data-field="${name}"]`);
  const prefix = `reader-${++readerSequence}`;
  field("detail-number").textContent = `#${detail.id}`;
  field("detail-subject").textContent = detail.subject || "(No subject)";
  field("detail-sender").textContent = detail.sender;
  field("detail-to").textContent =
    detail.recipients || "Undisclosed recipients";
  field("detail-date").textContent = dateLabel(detail.date, true);
  field("detail-size").textContent = bytes(detail.size);
  field("sender-avatar").textContent = shortName(detail.sender)
    .slice(0, 1)
    .toUpperCase();
  field("original-download").href = `${base()}/messages/${detail.id}/raw`;
  renderMarkdownParts(field("body-markdown"), detail.body_markdown_parts || []);
  if (!detail.body_markdown)
    field("body-markdown").textContent =
      "No message body is available. Try headers or download the original email.";
  renderTextParts(
    field("body-text"),
    detail.body_text_parts?.length
      ? detail.body_text_parts
      : [
          {
            text:
              detail.body_text ||
              "No text body is available. Try HTML or download the original email.",
            quoted: false,
          },
        ],
  );
  const headers = document.createDocumentFragment();
  for (const header of detail.headers)
    headers.append(
      element("dt", "", header.name),
      element("dd", "", header.value),
    );
  field("body-headers").replaceChildren(headers);
  field("attachment-list").replaceChildren(
    ...detail.attachments.map((attachment) => {
      const url = `${base()}/messages/${detail.id}/attachments/${attachment.id}`;
      const group = element("span", "attachment");
      const action = element(
        attachment.previewable ? "button" : "a",
        "attachment-open",
      );
      action.append(icon("paperclip"), element("span", "", attachment.name));
      action.title = `${attachment.previewable ? "Preview" : "Download"} ${attachment.name} · ${attachment.content_type}`;
      if (attachment.previewable) {
        action.type = "button";
        action.setAttribute("aria-haspopup", "dialog");
        action.addEventListener("click", () =>
          previewAttachment(attachment, url),
        );
        const download = element("a", "attachment-download");
        download.href = url;
        download.download = attachment.name;
        download.title = `Download ${attachment.name}`;
        download.setAttribute("aria-label", download.title);
        download.append(icon("download"));
        group.append(action, download);
      } else {
        action.href = url;
        action.download = attachment.name;
        action.append(icon("download"));
        group.append(action);
      }
      return group;
    }),
  );
  field("preview-note").hidden = !detail.truncated;
  field("preview-note").textContent =
    `This message preview is limited to ${bytes(detail.preview_limit)} and 524,288 characters per format. Attachments are available separately; download the original for the complete message.`;
  const formats = {
    markdown: copyTextContent(
      detail.body_markdown,
      detail.body_markdown_parts,
      "Markdown",
    ),
    text: copyTextContent(detail.body_text, detail.body_text_parts, "text"),
    html: copyHTMLContent(detail.body_html),
    headers: {
      text: detail.headers
        .map((header) => `${header.name}: ${header.value}`)
        .join("\n"),
      label: "headers",
    },
  };
  let selectedFormat = "markdown";
  let copyFeedback;
  let copying = false;
  const copyOptions = field("copy-options");
  copyOptions.id = `${prefix}-copy-options`;
  field("copy").setAttribute("aria-controls", copyOptions.id);
  function updateCopyAction() {
    if (copyOptions.matches(":popover-open")) copyOptions.hidePopover();
    clearTimeout(copyFeedback);
    const format = formats[selectedFormat];
    field("copy-label").textContent = "Copy";
    field("copy").title = `Copy ${format.label}`;
    field("copy").setAttribute("aria-label", field("copy").title);
    field("copy-status").textContent = "";
    field("copy-chevron").hidden = !format.hasQuotes;
    if (format.hasQuotes) {
      field("copy").setAttribute("aria-haspopup", "menu");
      field("copy").setAttribute("aria-expanded", "false");
    } else {
      field("copy").removeAttribute("aria-haspopup");
      field("copy").removeAttribute("aria-expanded");
    }
    copyOptions.setAttribute("aria-label", `Copy ${format.label}`);
  }
  updateCopyAction();
  async function copySelectedFormat(includeQuoted) {
    if (copying) return;
    clearTimeout(copyFeedback);
    const format = formats[selectedFormat];
    if (copyOptions.matches(":popover-open")) copyOptions.hidePopover();
    field("copy").focus({ preventScroll: true });
    copying = true;
    field("copy").setAttribute("aria-busy", "true");
    try {
      await copyText(includeQuoted ? format.text : format.withoutQuotes);
      if (!reader.isConnected || formats[selectedFormat] !== format) return;
      field("copy-label").textContent = "Copied";
      field("copy-status").textContent =
        `${format.label} copied${format.hasQuotes ? (includeQuoted ? " with quoted text" : " without quoted text") : ""}.`;
      copyFeedback = setTimeout(updateCopyAction, 1800);
    } catch (error) {
      showError(new Error(`Couldn’t copy this message: ${error.message}`));
    } finally {
      copying = false;
      field("copy").setAttribute("aria-busy", "false");
    }
  }
  function positionCopyOptions() {
    const button = field("copy").getBoundingClientRect();
    const menu = copyOptions.getBoundingClientRect();
    copyOptions.style.left = `${Math.max(16, Math.min(button.left, innerWidth - menu.width - 16))}px`;
    copyOptions.style.top = `${button.bottom + menu.height + 8 <= innerHeight - 16 ? button.bottom + 8 : Math.max(16, button.top - menu.height - 8)}px`;
  }
  function showCopyOptions() {
    if (copying) return;
    clearTimeout(copyFeedback);
    field("copy-label").textContent = "Copy";
    field("copy-status").textContent = "";
    copyOptions.showPopover();
    positionCopyOptions();
    field("copy-without-quotes").focus({ preventScroll: true });
    field("copy").setAttribute("aria-expanded", "true");
  }
  field("copy").addEventListener("click", () => {
    if (copyOptions.matches(":popover-open")) copyOptions.hidePopover();
    else if (formats[selectedFormat].hasQuotes) showCopyOptions();
    else copySelectedFormat(true);
  });
  field("copy").addEventListener("keydown", (event) => {
    if (
      ["ArrowDown", "ArrowUp"].includes(event.key) &&
      formats[selectedFormat].hasQuotes
    ) {
      event.preventDefault();
      showCopyOptions();
      if (event.key === "ArrowUp") field("copy-with-quotes").focus();
    }
  });
  field("copy-without-quotes").addEventListener("click", () =>
    copySelectedFormat(false),
  );
  field("copy-with-quotes").addEventListener("click", () =>
    copySelectedFormat(true),
  );
  copyOptions.addEventListener("toggle", (event) => {
    if (formats[selectedFormat].hasQuotes)
      field("copy").setAttribute(
        "aria-expanded",
        String(event.newState === "open"),
      );
    else field("copy").removeAttribute("aria-expanded");
  });
  copyOptions.addEventListener("keydown", (event) => {
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      event.stopPropagation();
      const options = [...copyOptions.querySelectorAll("button")];
      const index = options.indexOf(document.activeElement);
      const next =
        event.key === "Home"
          ? 0
          : event.key === "End"
            ? options.length - 1
            : (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) %
              options.length;
      options[next].focus();
    } else if (event.key === "Tab") {
      copyOptions.hidePopover();
      field("copy").focus({ preventScroll: true });
    }
  });
  let htmlLoaded = false;
  for (const name of MESSAGE_FORMATS) {
    const tab = reader.querySelector(`[data-tab="${name}"]`);
    const body = field(`body-${name}`);
    tab.id = `${prefix}-tab-${name}`;
    body.id = `${prefix}-body-${name}`;
    tab.setAttribute("aria-controls", body.id);
    body.setAttribute("aria-labelledby", tab.id);
    tab.disabled = name === "html" && !detail.body_html;
    tab.addEventListener("click", () => {
      selectedFormat = name;
      updateCopyAction();
      for (const other of MESSAGE_FORMATS) {
        const active = other === name;
        const button = reader.querySelector(`[data-tab="${other}"]`);
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", String(active));
        button.tabIndex = active ? 0 : -1;
        field(`body-${other}`).hidden = !active;
      }
      if (name === "html" && !htmlLoaded) {
        field("body-html").srcdoc = quotedHTMLDocument(detail.body_html);
        htmlLoaded = true;
      }
    });
  }
  reader.querySelector(".reader-tabs").addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...reader.querySelectorAll(".tab:not(:disabled)")];
    const current = tabs.indexOf(document.activeElement);
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? tabs.length - 1
          : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) %
            tabs.length;
    tabs[next].focus();
    tabs[next].click();
  });
  return reader;
}

function downloadLink(id) {
  return Object.assign(
    element("a", "button secondary", "Download original .eml"),
    { href: `${base()}/messages/${id}/raw` },
  );
}

async function openMessage(id) {
  resetReader("Opening your message…");
  const request = state.readerRequest;
  state.selected = id;
  renderList();
  showReader();
  try {
    const detail = await api(`${base()}/messages/${id}`);
    if (request !== state.readerRequest) return;
    $("message-detail").replaceChildren(createMessageReader(detail));
    $("reader-empty").hidden = true;
    $("message-detail").hidden = false;
  } catch (error) {
    if (request !== state.readerRequest) return;
    $("reader-empty").querySelector("h2").textContent =
      "This preview is unavailable.";
    $("reader-empty")
      .querySelector("p")
      .replaceChildren(
        element("span", "", error.message),
        document.createElement("br"),
        downloadLink(id),
      );
    showError(error);
  }
}

function conversationCard(item) {
  const card = element("details", "conversation-card");
  card.dataset.messageId = item.id;
  const summary = element("summary", "conversation-summary");
  const avatar = element(
    "span",
    "avatar",
    shortName(item.sender).slice(0, 1).toUpperCase(),
  );
  const info = element("span", "conversation-sender");
  info.append(
    element("strong", "", shortName(item.sender)),
    element("span", "muted small", item.subject),
  );
  summary.append(
    avatar,
    info,
    element("time", "muted small", dateLabel(item.date, true)),
    icon("chevron", "icon conversation-chevron"),
  );
  const content = element("div", "conversation-body");
  card.append(summary, content);
  let controller = null;
  card.addEventListener("toggle", async () => {
    if (!card.open) {
      for (const menu of content.querySelectorAll(".copy-options:popover-open"))
        menu.hidePopover();
      controller?.abort();
      content.replaceChildren();
      return;
    }
    // Keep a single expanded body, even in a conversation with thousands of replies.
    for (const other of $("conversation-messages").querySelectorAll(
      "details.conversation-card[open]",
    )) {
      if (other !== card) {
        for (const menu of other.querySelectorAll(".copy-options:popover-open"))
          menu.hidePopover();
        other.open = false;
        other.querySelector(".conversation-body").replaceChildren();
      }
    }
    controller?.abort();
    controller = new AbortController();
    const request = state.readerRequest;
    content.replaceChildren(
      element("p", "muted small", "Opening your message…"),
    );
    try {
      const detail = await api(`${base()}/messages/${item.id}`, {
        signal: controller.signal,
      });
      if (request !== state.readerRequest || !card.open || !card.isConnected)
        return;
      content.replaceChildren(createMessageReader(detail));
    } catch (error) {
      if (
        error.name === "AbortError" ||
        request !== state.readerRequest ||
        !card.open ||
        !card.isConnected
      )
        return;
      content.replaceChildren(
        element("p", "muted small", error.message),
        downloadLink(item.id),
      );
    }
  });
  return card;
}

function updateConversationOrder() {
  const newest = (state.thread?.order || state.threadOrder) === "newest";
  $("conversation-order-label").textContent = newest
    ? "Newest first"
    : "Oldest first";
  $("conversation-order").title = newest
    ? "Show oldest first"
    : "Show newest first";
  $("conversation-order-arrow").classList.toggle("descending", newest);
}

async function loadThreadMessages() {
  const thread = state.thread;
  if (!thread) return false;
  closeCopyOptions();
  const request = ++state.threadRequest;
  const orderFocused = document.activeElement === $("conversation-order");
  $("conversation-previous").disabled = true;
  $("conversation-next").disabled = true;
  $("conversation-order").disabled = true;
  updateConversationOrder();
  const params = new URLSearchParams({
    after: thread.cursors[thread.page],
    limit: 50,
    order: thread.order,
  });
  try {
    const data = await api(`${base()}/threads/${thread.id}/messages?${params}`);
    if (request !== state.threadRequest || state.thread !== thread)
      return false;
    thread.id = data.thread.id;
    thread.next = data.next_cursor;
    state.selected = thread.id;
    $("conversation-subject").textContent =
      data.thread.subject || "(No subject)";
    $("conversation-count").textContent = messageCount(
      data.thread.message_count,
    );
    $("conversation-messages").replaceChildren(
      ...data.items.map(conversationCard),
    );
    $("conversation-page").textContent = `Page ${thread.page + 1}`;
    $("conversation-previous").disabled = thread.page === 0;
    $("conversation-next").disabled = thread.next === null;
    $("reader-empty").hidden = true;
    $("conversation").hidden = false;
    $("reader-scroll").scrollTop = 0;
    renderList();
    return true;
  } catch (error) {
    if (request !== state.threadRequest) return false;
    $("conversation-previous").disabled = thread.page === 0;
    $("conversation-next").disabled = thread.next === null;
    if ($("conversation").hidden)
      $("reader-empty").querySelector("h2").textContent =
        "This conversation is unavailable.";
    showError(error);
    return false;
  } finally {
    if (request === state.threadRequest && state.thread === thread) {
      $("conversation-order").disabled = false;
      if (
        orderFocused &&
        document.activeElement === document.body &&
        !$("conversation").hidden
      )
        $("conversation-order").focus({ preventScroll: true });
    }
  }
}

async function openThread(id) {
  resetReader("Opening your conversation…");
  state.selected = id;
  state.thread = {
    id,
    cursors: [""],
    page: 0,
    next: null,
    order: state.threadOrder,
  };
  renderList();
  showReader();
  await loadThreadMessages();
}

function openItem(id) {
  return state.mode === "threads" ? openThread(id) : openMessage(id);
}

async function selectMailbox(id) {
  clearTimeout(searchTimer);
  const request = ++state.mailboxRequest;
  ++state.listRequest;
  resetReader();
  state.mailbox = id;
  state.query = "";
  state.selected = null;
  state.status = null;
  $("search").value = "";
  resetList();
  $("index-toggle").disabled = true;
  try {
    const status = await api(`${base()}/index`, { method: "POST" });
    if (request !== state.mailboxRequest) return;
    updateStatus(status);
    await loadItems();
  } catch (error) {
    if (request === state.mailboxRequest) showError(error);
  }
}

function search() {
  clearTimeout(searchTimer);
  state.query = $("search").value.trim();
  loadItems();
}

async function poll() {
  if (!state.mailbox || state.polling || document.hidden) return;
  state.polling = true;
  const mailbox = state.mailbox;
  try {
    const status = await api(`${base()}/status`);
    if (state.mailbox !== mailbox) return;
    const changed =
      status.indexed_messages !== state.status?.indexed_messages ||
      status.threaded_messages !== state.status?.threaded_messages ||
      status.thread_count !== state.status?.thread_count ||
      status.state !== state.status?.state;
    updateStatus(status);
    if (changed && !state.listLoading) {
      if (
        !state.items.length ||
        (state.listStart === 1 && $("message-list").scrollTop === 0)
      )
        await loadItems();
      else if (
        state.mode === "messages" &&
        state.next === null &&
        status.state !== "paused"
      )
        state.next = itemCursor(state.items.at(-1));
    }
  } catch (error) {
    if (state.mailbox === mailbox) showError(error);
  } finally {
    state.polling = false;
  }
}

for (const mode of ["threads", "messages"])
  $(`view-${mode}`).addEventListener("click", () => {
    if (state.mode === mode) return;
    ++state.listRequest;
    state.mode = mode;
    state.selected = null;
    resetList();
    resetReader();
    renderList();
    if (state.status) updateStatus(state.status);
    loadItems();
  });
$("search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  search();
});
$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(search, 300);
});
$("refresh").addEventListener("click", async () => {
  if (!state.mailbox) {
    await init();
    return;
  }
  await loadItems();
  if (state.thread) await loadThreadMessages();
});
$("conversation-refresh").addEventListener("click", loadThreadMessages);
$("conversation-order").addEventListener("click", async () => {
  const thread = state.thread;
  if (!thread) return;
  const previous = {
    order: thread.order,
    cursors: thread.cursors,
    page: thread.page,
    next: thread.next,
  };
  thread.order = thread.order === "oldest" ? "newest" : "oldest";
  state.threadOrder = thread.order;
  thread.cursors = [""];
  thread.page = 0;
  thread.next = null;
  const request = state.threadRequest + 1;
  const loaded = await loadThreadMessages();
  if (!loaded && state.thread === thread && request === state.threadRequest) {
    Object.assign(thread, previous);
    state.threadOrder = previous.order;
    updateConversationOrder();
    $("conversation-previous").disabled = thread.page === 0;
    $("conversation-next").disabled = thread.next === null;
  }
});
$("mailbox-select").addEventListener("change", (event) => {
  const option = event.target.selectedOptions[0];
  $("archive-name").textContent = option.textContent;
  $("archive-size").textContent =
    `${bytes(Number(option.dataset.size))} on disk`;
  selectMailbox(event.target.value);
  if (drawerLayout.matches) setSidebar(false);
});
$("index-toggle").addEventListener("click", async () => {
  const mailbox = state.mailbox;
  $("index-toggle").disabled = true;
  try {
    const action = state.status?.state === "indexing" ? "pause" : "index";
    const status = await api(`${base()}/${action}`, { method: "POST" });
    if (state.mailbox === mailbox) {
      updateStatus(status);
      await loadItems();
    }
  } catch (error) {
    showError(error);
    $("index-toggle").disabled = false;
  }
});
$("conversation-previous").addEventListener("click", () => {
  if (state.thread?.page > 0) {
    --state.thread.page;
    loadThreadMessages();
  }
});
$("conversation-next").addEventListener("click", () => {
  if (state.thread && state.thread.next !== null) {
    state.thread.cursors[++state.thread.page] = state.thread.next;
    loadThreadMessages();
  }
});

$("sidebar-toggle").addEventListener("click", () => {
  const open = $("sidebar-toggle").getAttribute("aria-expanded") === "true";
  setSidebar(!open);
});
$("sidebar-reveal").addEventListener("click", () => {
  setSidebar(!sidebarPinned);
});
$("sidebar-reveal").addEventListener("focus", () => {
  if (!restoringSidebarFocus && !sidebarPinned && !compactLayout.matches)
    setSidebar(true, { peek: true });
});
for (const region of [$("sidebar-rail"), $("sidebar")]) {
  region.addEventListener("pointerenter", peekSidebar);
  region.addEventListener("pointerleave", hideSidebarLater);
}
$("sidebar-close").addEventListener("click", () => setSidebar(false));
$("sidebar-backdrop").addEventListener("click", () => setSidebar(false));
$("reader-back").addEventListener("click", backToList);
$("toast-close").addEventListener("click", () => {
  $("toast").hidden = true;
});
$("shortcuts-open").addEventListener("click", () => {
  if ($("workspace").classList.contains("sidebar-open")) {
    setSidebar(false);
    $("sidebar-toggle").focus({ preventScroll: true });
  }
  $("shortcuts-dialog").showModal();
});
$("shortcuts-close").addEventListener("click", () =>
  $("shortcuts-dialog").close(),
);
$("shortcuts-dialog").addEventListener("click", (event) => {
  const bounds = $("shortcuts-dialog").getBoundingClientRect();
  if (
    event.clientX < bounds.left ||
    event.clientX > bounds.right ||
    event.clientY < bounds.top ||
    event.clientY > bounds.bottom
  )
    $("shortcuts-dialog").close();
});
$("attachment-close").addEventListener("click", () =>
  $("attachment-dialog").close(),
);
$("attachment-dialog").addEventListener("close", () => {
  // A queued close event must not clear a preview that has already reopened.
  if ($("attachment-dialog").open) return;
  $("attachment-preview").querySelector("img")?.removeAttribute("src");
  $("attachment-preview").replaceChildren();
  $("attachment-download").removeAttribute("href");
});
$("attachment-dialog").addEventListener("click", (event) => {
  const bounds = $("attachment-dialog").getBoundingClientRect();
  if (
    event.clientX < bounds.left ||
    event.clientX > bounds.right ||
    event.clientY < bounds.top ||
    event.clientY > bounds.bottom
  )
    $("attachment-dialog").close();
});
drawerLayout.addEventListener("change", syncLayout);
hoverSidebar.addEventListener("change", syncLayout);
document.addEventListener("focusin", (event) => {
  if (event.target.closest(".sidebar, .sidebar-rail")) {
    lastFocusedRegion = "sidebar";
    clearTimeout(sidebarHideTimer);
  } else if (event.target.closest(".mail-panel")) lastFocusedRegion = "list";
  else if (event.target.closest(".reader")) lastFocusedRegion = "reader";
  else if (event.target !== document.body) lastFocusedRegion = "other";
  if (!event.target.closest(".sidebar, .sidebar-rail")) hideSidebarLater();
});
compactLayout.addEventListener("change", () => {
  // Moving from a hidden pane must never leave keyboard focus behind it.
  if (
    !compactLayout.matches ||
    $("shortcuts-dialog").open ||
    $("attachment-dialog").open ||
    $("workspace").classList.contains("sidebar-open")
  )
    return;
  const reading = $("workspace").classList.contains("reader-active");
  if (reading && !document.activeElement.closest(".reader"))
    document.querySelector(".reader").focus({ preventScroll: true });
  else if (
    !reading &&
    (document.activeElement.closest(".reader") ||
      (document.activeElement === document.body &&
        lastFocusedRegion === "reader"))
  )
    backToList();
});
syncLayout();

document.addEventListener("scroll", closeCopyOptions, {
  capture: true,
  passive: true,
});
window.addEventListener("resize", closeCopyOptions);

document.addEventListener("keydown", async (event) => {
  if (
    $("shortcuts-dialog").open ||
    $("attachment-dialog").open ||
    document.querySelector(".copy-options:popover-open")
  )
    return;
  if ($("workspace").classList.contains("sidebar-open")) {
    if (event.key === "Escape") {
      event.preventDefault();
      setSidebar(false);
    }
    if (event.key === "Tab") {
      const controls = [
        ...$("sidebar").querySelectorAll(
          "a, button:not(:disabled), select:not(:disabled)",
        ),
      ];
      const first = controls[0],
        last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    return;
  }
  if (
    event.key === "Escape" &&
    !$("workspace").classList.contains("sidebar-collapsed")
  ) {
    event.preventDefault();
    setSidebar(false);
    return;
  }
  if (
    event.key === "Escape" &&
    compactLayout.matches &&
    $("workspace").classList.contains("reader-active")
  ) {
    event.preventDefault();
    backToList();
    return;
  }
  if (
    event.target.closest("input, select, textarea, [contenteditable='true']") ||
    event.ctrlKey ||
    event.metaKey ||
    event.altKey
  )
    return;
  if (event.key === "?") {
    event.preventDefault();
    $("shortcuts-dialog").showModal();
    return;
  }
  if (event.key === "/") {
    event.preventDefault();
    if (compactLayout.matches) backToList();
    $("search").focus();
  }
  if (
    ["ArrowDown", "ArrowUp"].includes(event.key) &&
    state.items.length &&
    (!event.target.closest("button, a, summary, [role='tab']") ||
      event.target.closest(".message-row"))
  ) {
    event.preventDefault();
    if (listKeyboardNavigation || state.listLoading) return;
    const selected = state.selected;
    let current = state.items.findIndex((item) => item.id === selected);
    const step = event.key === "ArrowDown" ? 1 : -1;
    if (
      (step === 1 &&
        current === state.items.length - 1 &&
        state.next !== null) ||
      (step === -1 && current === 0 && state.previous !== null)
    ) {
      listKeyboardNavigation = true;
      try {
        const loaded = await loadItems(step === 1 ? "forward" : "backward");
        if (!loaded || state.selected !== selected) return;
        current = state.items.findIndex((item) => item.id === selected);
      } finally {
        listKeyboardNavigation = false;
      }
    }
    const next =
      current < 0
        ? 0
        : Math.max(0, Math.min(state.items.length - 1, current + step));
    readerKeyboardNavigation = true;
    openItem(state.items[next].id);
    readerKeyboardNavigation = false;
    const row = $("message-list").querySelector(
      `[data-id="${state.items[next].id}"]`,
    );
    if (!compactLayout.matches) {
      row?.focus({ preventScroll: true });
      row?.scrollIntoView({ block: "nearest", behavior: "instant" });
    } else document.querySelector(".reader").focus({ preventScroll: true });
  }
});

async function init() {
  try {
    const data = await api("/api/mailboxes");
    if (!data.items.length) {
      $("mailbox-select").replaceChildren(
        element("option", "", "No .mbox files found"),
      );
      $("mailbox-select").disabled = true;
      $("list-empty").querySelector("strong").textContent =
        "Add your first archive";
      $("list-empty").querySelector("p").textContent =
        "Place a .mbox file in the mailbox folder, then refresh this page.";
      $("message-list").hidden = true;
      resetReader(
        "Your correspondence starts here.",
        "Add an .mbox archive to the mailbox folder, then refresh to explore it.",
      );
      return;
    }
    $("mailbox-select").disabled = false;
    $("mailbox-select").replaceChildren(
      ...data.items.map((mailbox) => {
        const option = element("option", "", mailbox.name);
        option.value = mailbox.id;
        option.dataset.size = mailbox.size;
        return option;
      }),
    );
    $("archive-name").textContent = data.items[0].name;
    $("archive-size").textContent = `${bytes(data.items[0].size)} on disk`;
    await selectMailbox(data.items[0].id);
    setInterval(poll, 1500);
  } catch (error) {
    showError(error);
  }
}

init();
