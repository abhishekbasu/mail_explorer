"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  mailbox: null,
  mode: "threads",
  query: "",
  cursors: [""],
  page: 0,
  items: [],
  next: null,
  selected: null,
  thread: null,
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
let readerKeyboardNavigation = false;
let lastFocusedRegion;
const compactLayout = matchMedia("(max-width: 720px)");
const drawerLayout = matchMedia("(max-width: 1150px)");

function icon(name, className = "icon") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#icon-${name}`);
  svg.append(use);
  return svg;
}

function setSidebar(open) {
  const workspace = $("workspace");
  if (drawerLayout.matches) {
    if (open) sidebarReturnFocus = document.activeElement;
    workspace.classList.toggle("sidebar-open", open);
    $("sidebar-backdrop").hidden = !open;
    $("sidebar").inert = !open;
    $("sidebar").setAttribute("role", "dialog");
    $("sidebar").setAttribute("aria-modal", "true");
    document.querySelector(".mail-panel").inert = open;
    document.querySelector(".reader").inert = open;
    if (open) $("sidebar-close").focus();
    else if (sidebarReturnFocus?.isConnected) sidebarReturnFocus.focus();
  } else {
    workspace.classList.toggle("sidebar-collapsed", !open);
    $("sidebar").inert = !open;
    $("sidebar").removeAttribute("role");
    $("sidebar").removeAttribute("aria-modal");
  }
  $("sidebar-toggle").setAttribute("aria-expanded", String(open));
}

function syncLayout() {
  const sidebarHadFocus =
    !!document.activeElement.closest(".sidebar") ||
    lastFocusedRegion === "sidebar";
  $("workspace").classList.remove("sidebar-open");
  $("sidebar-backdrop").hidden = true;
  document.querySelector(".mail-panel").inert = false;
  document.querySelector(".reader").inert = false;
  sidebarReturnFocus = null;
  setSidebar(
    !drawerLayout.matches &&
      !$("workspace").classList.contains("sidebar-collapsed"),
  );
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
}

function renderList() {
  const list = $("message-list");
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
  $("previous").disabled = state.page === 0;
  $("next").disabled = state.next === null;
  $("page-label").textContent = `Page ${state.page + 1}`;
  $("list-label").textContent = state.query
    ? threads
      ? "Matching conversations"
      : "Matching indexed messages"
    : "Subject, sender, or recipient";
}

async function loadItems() {
  if (!state.mailbox) return;
  const request = ++state.listRequest;
  $("previous").disabled = true;
  $("next").disabled = true;
  const params = new URLSearchParams({
    after: state.cursors[state.page],
    limit: 50,
    q: state.query,
  });
  try {
    const data = await api(`${base()}/${state.mode}?${params}`);
    if (request !== state.listRequest) return;
    state.items = data.items;
    state.next = data.next_cursor;
    updateStatus(data.status);
    renderList();
  } catch (error) {
    if (request === state.listRequest) {
      renderList();
      showError(error);
    }
  }
}

function resetReader(
  title = "Your archive, in focus.",
  description = "Select a conversation or message to revisit the details.",
) {
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
  field("download").href = `${base()}/messages/${detail.id}/raw`;
  field("body-text").textContent =
    detail.body_text ||
    "No text body is available. Try HTML or download the original email.";
  const headers = document.createDocumentFragment();
  for (const header of detail.headers)
    headers.append(
      element("dt", "", header.name),
      element("dd", "", header.value),
    );
  field("body-headers").replaceChildren(headers);
  field("attachment-list").replaceChildren(
    ...detail.attachments.map((attachment) => {
      const chip = element("span", "attachment");
      chip.append(icon("paperclip"), element("span", "", attachment.name));
      chip.title = `${attachment.content_type} · Included in the original .eml download`;
      return chip;
    }),
  );
  field("preview-note").hidden = !detail.truncated;
  field("preview-note").textContent =
    `This preview is limited to ${bytes(detail.preview_limit)} of the message and 524,288 characters per format. Download the original for the complete message and attachments.`;
  let htmlLoaded = false;
  for (const name of ["text", "html", "headers"]) {
    const tab = reader.querySelector(`[data-tab="${name}"]`);
    const body = field(`body-${name}`);
    tab.id = `${prefix}-tab-${name}`;
    body.id = `${prefix}-body-${name}`;
    tab.setAttribute("aria-controls", body.id);
    body.setAttribute("aria-labelledby", tab.id);
    tab.disabled = name === "html" && !detail.body_html;
    tab.addEventListener("click", () => {
      for (const other of ["text", "html", "headers"]) {
        const active = other === name;
        const button = reader.querySelector(`[data-tab="${other}"]`);
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", String(active));
        button.tabIndex = active ? 0 : -1;
        field(`body-${other}`).hidden = !active;
      }
      if (name === "html" && !htmlLoaded) {
        const policy =
          "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'";
        field("body-html").srcdoc =
          `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy}">${detail.body_html}`;
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
      controller?.abort();
      content.replaceChildren();
      return;
    }
    // Keep a single expanded body, even in a conversation with thousands of replies.
    for (const other of $("conversation-messages").querySelectorAll(
      "details[open]",
    )) {
      if (other !== card) {
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

async function loadThreadMessages() {
  const thread = state.thread;
  if (!thread) return;
  const request = ++state.threadRequest;
  $("conversation-previous").disabled = true;
  $("conversation-next").disabled = true;
  const params = new URLSearchParams({
    after: thread.cursors[thread.page],
    limit: 50,
  });
  try {
    const data = await api(`${base()}/threads/${thread.id}/messages?${params}`);
    if (request !== state.threadRequest || state.thread !== thread) return;
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
  } catch (error) {
    if (request !== state.threadRequest) return;
    $("conversation-previous").disabled = thread.page === 0;
    $("conversation-next").disabled = thread.next === null;
    if ($("conversation").hidden)
      $("reader-empty").querySelector("h2").textContent =
        "This conversation is unavailable.";
    showError(error);
  }
}

async function openThread(id) {
  resetReader("Opening your conversation…");
  state.selected = id;
  state.thread = { id, cursors: [""], page: 0, next: null };
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
  state.cursors = [firstCursor()];
  state.page = 0;
  state.items = [];
  state.next = null;
  state.selected = null;
  state.status = null;
  $("search").value = "";
  renderList();
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
  state.cursors = [firstCursor()];
  state.page = 0;
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
    if (changed) await loadItems();
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
    state.page = 0;
    state.cursors = [firstCursor()];
    state.items = [];
    state.next = null;
    state.selected = null;
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
$("previous").addEventListener("click", () => {
  if (state.page > 0) {
    --state.page;
    loadItems();
  }
});
$("next").addEventListener("click", () => {
  if (state.next !== null) {
    state.cursors[++state.page] = state.next;
    loadItems();
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
$("sidebar-close").addEventListener("click", () => setSidebar(false));
$("sidebar-backdrop").addEventListener("click", () => setSidebar(false));
$("reader-back").addEventListener("click", backToList);
$("toast-close").addEventListener("click", () => {
  $("toast").hidden = true;
});
$("shortcuts-open").addEventListener("click", () =>
  $("shortcuts-dialog").showModal(),
);
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
drawerLayout.addEventListener("change", syncLayout);
document.addEventListener("focusin", (event) => {
  if (event.target.closest(".sidebar")) lastFocusedRegion = "sidebar";
  else if (event.target.closest(".mail-panel")) lastFocusedRegion = "list";
  else if (event.target.closest(".reader")) lastFocusedRegion = "reader";
  else if (event.target !== document.body) lastFocusedRegion = "other";
});
compactLayout.addEventListener("change", () => {
  // Moving from a hidden pane must never leave keyboard focus behind it.
  if (
    !compactLayout.matches ||
    $("shortcuts-dialog").open ||
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

document.addEventListener("keydown", (event) => {
  if ($("shortcuts-dialog").open) return;
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
    const current = state.items.findIndex((item) => item.id === state.selected);
    const next =
      current < 0
        ? 0
        : Math.max(
            0,
            Math.min(
              state.items.length - 1,
              current + (event.key === "ArrowDown" ? 1 : -1),
            ),
          );
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
