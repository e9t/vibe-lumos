'use strict';

// Only run in the top frame — never in iframes
(function() {
let isTop = false;
try { isTop = window.self === window.top; } catch (_) {}
if (!isTop) return;

// ─── URL Normalization ────────────────────────────────────────────────────────

/** Extract root domain (last two segments), e.g. "sub.medium.com" → "medium.com" */
function rootDomain(hostname) {
  const parts = hostname.split('.');
  return parts.slice(-2).join('.');
}

/** Returns canonical URL with fragment stripped. Only trusts canonical/og:url if same root domain. */
function getCanonicalUrl() {
  const currentRoot = rootDomain(location.hostname);
  const canonical = document.querySelector('link[rel="canonical"]')?.href;
  const ogUrl = document.querySelector('meta[property="og:url"]')?.content;

  let raw = location.href;
  for (const candidate of [canonical, ogUrl]) {
    if (!candidate) continue;
    try {
      if (rootDomain(new URL(candidate).hostname) === currentRoot) { raw = candidate; break; }
    } catch (_) {}
  }

  let u = raw.split('#')[0];
  try {
    const parsed = new URL(u);
    if (parsed.pathname === '/') u = parsed.origin;
  } catch (_) {}
  return u;
}

// ─── State ────────────────────────────────────────────────────────────────────

let toolbar = null;
let notePopup = null;
let pendingRange = null; // cloned Range saved on mouseup
let toolbarShownAt = 0; // timestamp to guard against immediate click dismiss

// ─── XPath & Fingerprint Utils ────────────────────────────────────────────────

function getXPath(element) {
  if (!element || element === document.documentElement) return '/html';
  if (element === document.body) return '/html/body';

  const tagName = element.tagName.toLowerCase();
  let idx = 1;
  let sib = element.previousSibling;
  while (sib) {
    if (sib.nodeType === Node.ELEMENT_NODE && sib.tagName === element.tagName) idx++;
    sib = sib.previousSibling;
  }

  const parentXPath = getXPath(element.parentElement);
  return `${parentXPath}/${tagName}[${idx}]`;
}

/** djb2 hash → hex string */
function textFingerprint(text) {
  let h = 5381;
  const s = text.trim().slice(0, 100);
  for (let i = 0; i < s.length; i++) {
    h = Math.imul(h, 31) + s.charCodeAt(i) | 0;
  }
  return (h >>> 0).toString(16).padStart(8, '0');
}

// ─── Range Helpers ────────────────────────────────────────────────────────────

/** Returns [{node, start, end}] for text nodes overlapping the range */
function getTextNodesInRange(range) {
  const root =
    range.commonAncestorContainer.nodeType === Node.TEXT_NODE
      ? range.commonAncestorContainer.parentNode
      : range.commonAncestorContainer;

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
  const result = [];

  let node;
  while ((node = walker.nextNode())) {
    if (!range.intersectsNode(node)) continue;
    const start = node === range.startContainer ? range.startOffset : 0;
    const end = node === range.endContainer ? range.endOffset : node.length;
    if (start < end) result.push({ node, start, end });
  }
  return result;
}

/**
 * Wraps each text-node slice in a <mark class="lumos-highlight">.
 * Link-safe: operates on individual text nodes, never uses surroundContents.
 */
// ─── Style Pinning ────────────────────────────────────────────────────────────
//
// Our UI lives in the page's DOM, so the page's stylesheet can reach it. A rule
// like `#app button { color:#888 !important }` outranks ours on specificity and
// leaves the toolbar unreadable. Inline + !important is the one declaration an
// author stylesheet cannot beat, so pin whatever decides legibility.

function pin(el, styles) {
  for (const key in styles) el.style.setProperty(key, styles[key], 'important');
  return el;
}

const PANEL_STYLE = {
  opacity: '1',
  filter: 'none',
  'font-family': '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  'text-transform': 'none',
  'letter-spacing': 'normal',
  'text-shadow': 'none',
};

const BUTTON_STYLE = {
  ...PANEL_STYLE,
  color: '#fff',
  '-webkit-text-fill-color': '#fff',
  background: 'none',
  border: 'none',
  'font-size': '12px',
  'line-height': '1.4',
  'text-decoration': 'none',
};

const NOTE_BUTTON_STYLE = {
  ...PANEL_STYLE,
  color: '#ccc',
  '-webkit-text-fill-color': '#ccc',
  background: '#333',
  border: 'none',
  'font-size': '12px',
};

const PRIMARY_BUTTON_STYLE = {
  ...NOTE_BUTTON_STYLE,
  color: '#fff',
  '-webkit-text-fill-color': '#fff',
  background: '#4CAF50',
};

// <mark> is a tag sites style themselves (borders, padding, custom backgrounds).
// Ours must look the same everywhere, so pin it rather than hope the page is quiet.
const MARK_STYLE = {
  display: 'inline',
  color: 'inherit',
  border: 'none',
  outline: 'none',
  'box-shadow': 'none',
  padding: '0',
  margin: '0',
  'border-radius': '2px',
  'font-size': 'inherit',
  'font-weight': 'inherit',
  'font-style': 'inherit',
  'line-height': 'inherit',
  opacity: '1',
  filter: 'none',
  '-webkit-box-decoration-break': 'clone',
  'box-decoration-break': 'clone',
};

/** Toolbar button with its look pinned against the page, hover included. */
function pinButton(btn, style = BUTTON_STYLE) {
  pin(btn, style);
  const base = style.background || 'none';
  btn.addEventListener('mouseenter', () =>
    btn.style.setProperty('background', 'rgba(255, 255, 255, 0.15)', 'important'));
  btn.addEventListener('mouseleave', () =>
    btn.style.setProperty('background', base, 'important'));
  return btn;
}

let activeTooltip = null;

function showNoteTooltip(mark) {
  const note = mark.dataset.lumosNote;
  const priority = parseInt(mark.dataset.lumosPriority || '0', 10);
  if (!note && !priority) return;
  hideNoteTooltip();

  const tip = document.createElement('div');
  tip.className = 'lumos-tooltip';
  pin(tip, { ...PANEL_STYLE, color: '#fff', '-webkit-text-fill-color': '#fff',
             background: '#1a1a1a', 'font-size': '12px' });
  const parts = [];
  if (priority) parts.push(`⭐ ${priority}`);
  if (note) parts.push(note);
  tip.textContent = parts.join('\n');
  document.body.appendChild(tip);

  const rect = mark.getBoundingClientRect();
  const tipRect = tip.getBoundingClientRect();
  let top = rect.top - tipRect.height - 6;
  let left = rect.left + rect.width / 2 - tipRect.width / 2;
  if (top < 4) top = rect.bottom + 6;
  left = Math.max(4, Math.min(left, window.innerWidth - tipRect.width - 4));
  tip.style.top = `${top}px`;
  tip.style.left = `${left}px`;
  activeTooltip = tip;
}

function hideNoteTooltip() {
  activeTooltip?.remove();
  activeTooltip = null;
}

function wrapTextNodes(textNodes, configure) {
  const marks = [];
  for (const { node, start, end } of textNodes) {
    let target = node;
    if (end < target.length) target.splitText(end);
    if (start > 0) target = target.splitText(start);

    const mark = document.createElement('mark');
    configure(mark);
    target.parentNode.insertBefore(mark, target);
    mark.appendChild(target);
    marks.push(mark);
  }
  return marks;
}

function applyMark(textNodes, itemId, color = '#FFEB3B', note = null, priority = 0) {
  return wrapTextNodes(textNodes, (mark) => {
    mark.className = 'lumos-highlight';
    mark.dataset.lumosId = itemId;
    if (note) mark.dataset.lumosNote = note;
    if (priority) mark.dataset.lumosPriority = String(priority);
    pin(mark, { ...MARK_STYLE, 'background-color': color });
    mark.addEventListener('mouseenter', () => showNoteTooltip(mark));
    mark.addEventListener('mouseleave', hideNoteTooltip);
    mark.addEventListener('click', (e) => {
      // If user is selecting text, don't intercept
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed && sel.toString().trim()) return;
      e.preventDefault();
      e.stopPropagation();
      showHighlightMenu(mark);
    });
    mark.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      showHighlightMenu(mark);
    });
  });
}

/** Remove mark elements, restoring original text nodes */
function removeMark(mark) {
  const parent = mark.parentNode;
  if (!parent) return;
  while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
  mark.remove();
  parent.normalize();
}

// ─── Highlight Context Menu ──────────────────────────────────────────────────

function showHighlightMenu(mark) {
  removeToolbar();
  removeNotePopup();
  hideNoteTooltip();
  toolbarShownAt = Date.now();

  toolbar = document.createElement('div');
  toolbar.className = 'lumos-toolbar';
  pin(toolbar, { ...PANEL_STYLE, background: '#1a1a1a', 'font-size': '13px' });

  const btnUp = document.createElement('button');
  pinButton(btnUp);
  btnUp.textContent = '👍';
  btnUp.title = 'Priority up';
  btnUp.addEventListener('pointerdown', (e) => {
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    updatePriority(mark, 1);
  }, true);

  const btnDown = document.createElement('button');
  pinButton(btnDown);
  btnDown.textContent = '👎';
  btnDown.title = 'Priority down';
  btnDown.addEventListener('pointerdown', (e) => {
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    updatePriority(mark, -1);
  }, true);

  const btnNote = document.createElement('button');
  pinButton(btnNote);
  btnNote.textContent = mark.dataset.lumosNote ? '📝 Edit Note' : '📝 Add Note';
  btnNote.addEventListener('pointerdown', (e) => {
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    showEditNoteInput(mark);
  }, true);

  const btnRemove = document.createElement('button');
  pinButton(btnRemove);
  btnRemove.textContent = '🗑 Remove';
  btnRemove.addEventListener('pointerdown', (e) => {
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    deleteHighlight(mark);
  }, true);

  toolbar.appendChild(btnUp);
  toolbar.appendChild(btnDown);
  toolbar.appendChild(btnNote);
  toolbar.appendChild(btnRemove);
  positionElement(toolbar, mark.getBoundingClientRect());
}

function showEditNoteInput(mark) {
  removeToolbar();
  removeNotePopup();

  notePopup = document.createElement('div');
  notePopup.className = 'lumos-note-input';
  pin(notePopup, { ...PANEL_STYLE, background: '#1a1a1a' });

  const textarea = document.createElement('textarea');
  pin(textarea, { ...PANEL_STYLE, color: '#fff', '-webkit-text-fill-color': '#fff',
                  background: '#2a2a2a', border: '1px solid #444', 'font-size': '13px' });
  textarea.placeholder = 'Add a note…';
  textarea.value = mark.dataset.lumosNote || '';

  const actions = document.createElement('div');
  actions.className = 'lumos-note-actions';

  const btnCancel = document.createElement('button');
  pinButton(btnCancel, NOTE_BUTTON_STYLE);
  btnCancel.textContent = 'Cancel';
  btnCancel.addEventListener('click', () => removeNotePopup());

  const btnSave = document.createElement('button');
  pinButton(btnSave, PRIMARY_BUTTON_STYLE);
  btnSave.textContent = 'Save';
  btnSave.className = 'primary';
  btnSave.addEventListener('click', async () => {
    const note = textarea.value.trim() || null;
    const id = mark.dataset.lumosId;
    try {
      const resp = await chrome.runtime.sendMessage({ type: 'UPDATE_NOTE', id, note });
      if (resp.ok) {
        document.querySelectorAll(`mark.lumos-highlight[data-lumos-id="${id}"]`).forEach((m) => {
          if (note) m.dataset.lumosNote = note;
          else delete m.dataset.lumosNote;
        });
        showToast('Note updated ✓');
      } else {
        showToast('Failed to update note');
      }
    } catch (e) {
      showToast('Error: ' + e.message);
    }
    removeNotePopup();
  });

  actions.appendChild(btnCancel);
  actions.appendChild(btnSave);
  notePopup.appendChild(textarea);
  notePopup.appendChild(actions);
  positionElement(notePopup, mark.getBoundingClientRect());
  textarea.focus();
}

async function updatePriority(mark, delta) {
  const id = mark.dataset.lumosId;
  try {
    const resp = await chrome.runtime.sendMessage({ type: 'UPDATE_PRIORITY', id, delta });
    if (resp.ok) {
      document.querySelectorAll(`mark.lumos-highlight[data-lumos-id="${id}"]`).forEach((m) => {
        m.dataset.lumosPriority = String(resp.priority);
      });
      showToast(`Priority: ${resp.priority}`);
    } else {
      showToast('Failed to update priority');
    }
  } catch (e) {
    showToast('Error: ' + e.message);
  }
  removeToolbar();
}

async function deleteHighlight(mark) {
  const id = mark.dataset.lumosId;
  removeToolbar();
  removeNotePopup();

  try {
    const resp = await chrome.runtime.sendMessage({ type: 'DELETE_ITEM', id });
    if (resp.ok) {
      document.querySelectorAll(`mark.lumos-highlight[data-lumos-id="${id}"]`).forEach(removeMark);
      showToast('Highlight removed');
    } else {
      showToast('Failed to remove');
    }
  } catch (e) {
    showToast('Error: ' + e.message);
  }
}

// ─── Compute location info from a Range ──────────────────────────────────────

function computeLocation(range) {
  const startNode = range.startContainer;
  const containerEl =
    startNode.nodeType === Node.TEXT_NODE ? startNode.parentElement : startNode;

  let xpath = null;
  let absStart = range.startOffset;
  let absEnd = range.endOffset;

  try {
    xpath = getXPath(containerEl);

    // Walk text nodes to get absolute character offsets within containerEl
    const walker = document.createTreeWalker(containerEl, NodeFilter.SHOW_TEXT, null);
    let charCount = 0;
    let node;

    while ((node = walker.nextNode())) {
      if (node === range.startContainer) {
        absStart = charCount + range.startOffset;
      }
      if (node === range.endContainer) {
        absEnd = charCount + range.endOffset;
        break;
      }
      charCount += node.length;
    }
  } catch (_) {}

  return { xpath, startOffset: absStart, endOffset: absEnd };
}

// ─── Toast ────────────────────────────────────────────────────────────────────

function showToast(text) {
  const el = document.createElement('div');
  el.className = 'lumos-toast';
  pin(el, { ...PANEL_STYLE, color: '#fff', '-webkit-text-fill-color': '#fff',
            background: '#1a1a1a', 'font-size': '13px' });
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2500);
}

// ─── Toolbar ──────────────────────────────────────────────────────────────────

function removeToolbar() {
  toolbar?.remove();
  toolbar = null;
}

function removeNotePopup() {
  notePopup?.remove();
  notePopup = null;
}

function positionElement(el, anchorRect) {
  // Must be in DOM for getBoundingClientRect to work; position off-screen first
  el.style.visibility = 'hidden';
  el.style.position = 'fixed';
  el.style.top = '-9999px';
  el.style.left = '-9999px';
  document.body.appendChild(el);

  const elRect = el.getBoundingClientRect();
  let top = anchorRect.top - elRect.height - 8;
  let left = anchorRect.left + anchorRect.width / 2 - elRect.width / 2;

  if (top < 8) top = anchorRect.bottom + 8;
  left = Math.max(8, Math.min(left, window.innerWidth - elRect.width - 8));

  el.style.top = `${top}px`;
  el.style.left = `${left}px`;
  el.style.visibility = '';
}

function showToolbar(rect) {
  removeToolbar();
  removeNotePopup();
  toolbarShownAt = Date.now();

  toolbar = document.createElement('div');
  toolbar.className = 'lumos-toolbar';
  pin(toolbar, { ...PANEL_STYLE, background: '#1a1a1a', 'font-size': '13px' });

  const btnHighlight = document.createElement('button');
  pinButton(btnHighlight);
  btnHighlight.textContent = '💡 Highlight';
  btnHighlight.onpointerdown = function(e) {
    e.preventDefault();
    e.stopPropagation();
  };
  btnHighlight.onclick = function() { saveHighlight(null); };

  const btnNote = document.createElement('button');
  pinButton(btnNote);
  btnNote.textContent = '📝 Note';
  btnNote.onpointerdown = function(e) {
    e.preventDefault();
    e.stopPropagation();
  };
  btnNote.onclick = function() { showNoteInput(rect); };

  toolbar.appendChild(btnHighlight);
  toolbar.appendChild(btnNote);
  positionElement(toolbar, rect);
}

function showNoteInput(anchorRect) {
  removeToolbar();
  removeNotePopup();

  notePopup = document.createElement('div');
  notePopup.className = 'lumos-note-input';
  pin(notePopup, { ...PANEL_STYLE, background: '#1a1a1a' });

  const textarea = document.createElement('textarea');
  pin(textarea, { ...PANEL_STYLE, color: '#fff', '-webkit-text-fill-color': '#fff',
                  background: '#2a2a2a', border: '1px solid #444', 'font-size': '13px' });
  textarea.placeholder = 'Add a note…';

  const actions = document.createElement('div');
  actions.className = 'lumos-note-actions';

  const btnCancel = document.createElement('button');
  pinButton(btnCancel, NOTE_BUTTON_STYLE);
  btnCancel.textContent = 'Cancel';
  btnCancel.addEventListener('click', () => {
    removeNotePopup();
    pendingRange = null;
  });

  const btnSave = document.createElement('button');
  pinButton(btnSave, PRIMARY_BUTTON_STYLE);
  btnSave.textContent = 'Save';
  btnSave.className = 'primary';
  btnSave.addEventListener('click', () => saveHighlight(textarea.value.trim() || null));

  actions.appendChild(btnCancel);
  actions.appendChild(btnSave);
  notePopup.appendChild(textarea);
  notePopup.appendChild(actions);
  positionElement(notePopup, anchorRect);
  textarea.focus();
}

// ─── Save Highlight ───────────────────────────────────────────────────────────

async function saveHighlight(note) {
  const range = pendingRange;
  if (!range || range.collapsed) {
    removeToolbar();
    removeNotePopup();
    return;
  }

  const text = range.toString().trim();
  if (!text) {
    removeToolbar();
    removeNotePopup();
    return;
  }

  // Capture original HTML before touching the DOM
  let originalHtml = null;
  try {
    const frag = range.cloneContents();
    originalHtml = new XMLSerializer().serializeToString(frag);
  } catch (_) {}

  const { xpath, startOffset, endOffset } = computeLocation(range);
  const fingerprint = textFingerprint(text);

  // Collect text nodes while range is still valid
  const textNodes = getTextNodesInRange(range);

  // Clear selection and UI
  window.getSelection().removeAllRanges();
  removeToolbar();
  removeNotePopup();
  pendingRange = null;

  // Apply mark optimistically with a temp ID
  const tempId = `temp_${Date.now()}`;
  const marks = applyMark(textNodes, tempId);

  // Highlighting over a suggestion supersedes it — unwrap so the real
  // highlight isn't nested inside a pale-yellow one
  marks.forEach((m) => {
    const suggestion = m.closest('mark.lumos-suggestion');
    if (suggestion) removeMark(suggestion);
  });

  try {
    const response = await chrome.runtime.sendMessage({
      type: 'SAVE_HIGHLIGHT',
      url: location.href.split('#')[0],
      title: document.title,
      text,
      note,
      xpath,
      startOffset,
      endOffset,
      textFingerprint: fingerprint,
      originalHtml,
    });

    if (response.ok) {
      const realId = response.item.id;
      marks.forEach((m) => {
        m.dataset.lumosId = realId;
        if (note) m.dataset.lumosNote = note;
      });
      showToast('Saved to Lumos ✓');
    } else {
      marks.forEach(removeMark);
      showToast('Failed to save');
    }
  } catch (e) {
    marks.forEach(removeMark);
    showToast('Error: ' + e.message);
  }
}

// ─── Mouse / Selection Events ─────────────────────────────────────────────────

// Use selectionchange to detect text selection — works even when sites intercept mouseup
let _selectionDebounce = null;
document.addEventListener('selectionchange', () => {
  clearTimeout(_selectionDebounce);
  _selectionDebounce = setTimeout(() => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.toString().trim()) return;
    const range = selection.getRangeAt(0);
    const rect = range.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return;
    pendingRange = range.cloneRange();
    showToolbar(rect);
  }, 300);
});

document.addEventListener('mouseup', (e) => {
  if (e.target.closest('.lumos-toolbar, .lumos-note-input')) return;

  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.toString().trim()) {
    // Don't clear toolbar if user clicked a highlight (highlight menu is showing)
    if (!e.target.closest('mark.lumos-highlight')) {
      removeToolbar();
      removeNotePopup();
      pendingRange = null;
    }
    return;
  }

  const range = selection.getRangeAt(0);
  const rect = range.getBoundingClientRect();
  pendingRange = range.cloneRange();
  showToolbar(rect);
});

document.addEventListener('click', (e) => {
  if (Date.now() - toolbarShownAt < 300) return;
  if (!e.target.closest('.lumos-toolbar, .lumos-note-input, mark.lumos-highlight')) {
    removeToolbar();
    removeNotePopup();
    pendingRange = null;
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    removeToolbar();
    removeNotePopup();
    pendingRange = null;
  }
});

// ─── Restore Highlights ───────────────────────────────────────────────────────

async function restoreHighlights(retries = 3) {
  try {
    const canonicalUrl = getCanonicalUrl();
    const browserUrl = location.href.split('#')[0];

    // Try canonical URL first, then browser URL as fallback
    const urlsToTry = [canonicalUrl];
    if (browserUrl !== canonicalUrl) urlsToTry.push(browserUrl);

    let items = [];
    for (const url of urlsToTry) {
      const response = await chrome.runtime.sendMessage({ type: 'GET_PAGE_ITEMS', url });
      if (response?.ok && response.items.length) {
        items = response.items;
        break;
      }
      if (!response?.ok) {
        if (retries > 0) setTimeout(() => restoreHighlights(retries - 1), 500);
        return;
      }
    }

    for (const item of items) {
      if (item.type === 'highlight' && item.text) {
        tryRestoreHighlight(item);
      }
    }
  } catch (_) {
    // Service worker may not be ready yet — retry
    if (retries > 0) setTimeout(() => restoreHighlights(retries - 1), 1000);
  }
}

function tryRestoreHighlight(item) {
  const loc = item.location;

  // 1. XPath + offset
  if (loc?.xpath) {
    try {
      const result = document.evaluate(
        loc.xpath,
        document,
        null,
        XPathResult.FIRST_ORDERED_NODE_TYPE,
        null
      );
      const el = result.singleNodeValue;
      if (el) {
        const textNodes = findTextNodeAtOffset(el, item.text, loc.start_offset ?? 0);
        if (textNodes) {
          applyMark(textNodes, item.id, '#FFEB3B', item.note, item.priority);
          return;
        }
      }
    } catch (_) {}
  }

  // 2. Fingerprint / text search fallback
  if (item.text) {
    const textNodes = findTextInBody(item.text);
    if (textNodes) applyMark(textNodes, item.id, '#FFEB3B', item.note, item.priority);
  }
}

/**
 * Collect all text nodes spanning searchText starting at charOffset within el.
 * Returns [{node, start, end}] covering the full text across node boundaries.
 */
function collectTextNodes(el, searchText, charOffset) {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
  let pos = 0;
  let node;
  const nodes = [];
  let remaining = searchText.length;
  let started = false;

  while ((node = walker.nextNode())) {
    const nodeEnd = pos + node.length;
    if (!started) {
      if (pos <= charOffset && charOffset < nodeEnd) {
        const start = charOffset - pos;
        const end = Math.min(start + remaining, node.length);
        nodes.push({ node, start, end });
        remaining -= (end - start);
        started = true;
        if (remaining <= 0) break;
      }
    } else {
      const end = Math.min(remaining, node.length);
      nodes.push({ node, start: 0, end });
      remaining -= end;
      if (remaining <= 0) break;
    }
    pos = nodeEnd;
  }
  return remaining <= 0 ? nodes : null;
}

/** Find text nodes spanning searchText within el, starting near hintOffset */
function findTextNodeAtOffset(el, searchText, hintOffset) {
  // Try at hint offset first
  const full = el.textContent;
  if (full.slice(hintOffset, hintOffset + searchText.length) === searchText) {
    return collectTextNodes(el, searchText, hintOffset);
  }
  // Fallback: find anywhere in element
  const idx = full.indexOf(searchText);
  if (idx === -1) return null;
  return collectTextNodes(el, searchText, idx);
}

/** Scan the entire body for a text occurrence, returning all spanning nodes */
function findTextInBody(searchText) {
  const full = document.body.textContent;
  const snippet = searchText.slice(0, 80);
  const idx = full.indexOf(snippet);
  if (idx === -1) return null;
  return collectTextNodes(document.body, searchText, idx);
}

// ─── Suggested Highlights ────────────────────────────────────────────────────
//
// On load we hand the page's readable text to the native host, which asks the
// LLM which phrases *this* reader would highlight (primed with their own past
// highlights). Suggestions are painted pale yellow — click one to keep it as a
// real highlight, or dismiss it.

const SUGGEST_COLOR = '#FFF9C4';
const SUGGEST_MIN_CHARS = 500;
const SUGGEST_MAX_INDEX = 200000;
const SUGGEST_RETRY_MS = 1500;
const SPA_SETTLE_MS = 800;

const BLOCK_TAGS = new Set([
  'ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'DD', 'DIV', 'DL', 'DT',
  'FIELDSET', 'FIGCAPTION', 'FIGURE', 'FOOTER', 'FORM', 'H1', 'H2', 'H3',
  'H4', 'H5', 'H6', 'HEADER', 'HR', 'LI', 'MAIN', 'NAV', 'OL', 'P', 'PRE',
  'SECTION', 'TABLE', 'TD', 'TH', 'TR', 'UL', 'BODY',
]);

const SKIP_SELECTOR =
  'script, style, noscript, template, textarea, nav, footer, aside, ' +
  '[role="navigation"], [role="banner"], [role="complementary"], ' +
  // Titles and section headings label the content, they aren't the content —
  // marking them tells you nothing you didn't get from glancing at the page
  'h1, h2, h3, h4, h5, h6, [role="heading"], ' +
  '.lumos-toolbar, .lumos-note-input, .lumos-tooltip, .lumos-toast';

let suggestEnabled = true;

function nearestBlock(node) {
  let el = node.parentElement;
  while (el && !BLOCK_TAGS.has(el.tagName)) el = el.parentElement;
  return el;
}

function suggestScope() {
  return document.querySelector('article, main, [role="main"]') || document.body;
}

/**
 * Flatten a subtree into whitespace-normalised text plus a map back to the
 * original text nodes. Text already inside a real highlight is skipped, so we
 * never suggest something the user has already kept.
 *
 * Returns { text, entries: [{ node, start, offsets }] } where `offsets[i]` is
 * the character offset inside `node` for normalised character `start + i`.
 * Every whitespace run in `text` is exactly one character, so a phrase can be
 * matched with a length-preserving `replace(/\s/g, ' ')`.
 */
function buildTextIndex(scope) {
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      const parent = node.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      if (parent.closest(SKIP_SELECTOR)) return NodeFilter.FILTER_REJECT;
      if (parent.closest('mark.lumos-highlight')) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });

  const entries = [];
  let text = '';
  let prevBlock = null;
  let node;

  while ((node = walker.nextNode())) {
    const raw = node.nodeValue;
    let chunk = '';
    const offsets = [];
    let lastWasSpace = true; // drops the node's leading whitespace run

    for (let i = 0; i < raw.length; i++) {
      const ch = raw[i];
      if (/\s/.test(ch)) {
        if (lastWasSpace) continue;
        chunk += ' ';
        lastWasSpace = true;
      } else {
        chunk += ch;
        lastWasSpace = false;
      }
      offsets.push(i);
    }
    if (!chunk.trim()) continue;

    const block = nearestBlock(node);
    if (text && !/\s$/.test(text)) {
      if (block !== prevBlock) text += '\n';
      else if (/^\s/.test(raw)) text += ' ';
    }
    prevBlock = block;

    entries.push({ node, start: text.length, offsets });
    text += chunk;
    if (text.length > SUGGEST_MAX_INDEX) break;
  }

  return { text, entries };
}

/** Locate a phrase in the index, skipping ranges already taken. */
function findPhrase(index, phrase, taken) {
  const haystack = index.text.replace(/\s/g, ' ').toLowerCase();
  const needle = phrase.replace(/\s+/g, ' ').trim().toLowerCase();
  if (needle.length < 12) return null;

  let from = 0;
  for (;;) {
    const start = haystack.indexOf(needle, from);
    if (start === -1) return null;
    const end = start + needle.length;
    if (!taken.some(([s, e]) => start < e && s < end)) return { start, end };
    from = start + 1;
  }
}

/** Map a [start, end) range in the index back to text-node slices. */
function rangeToTextNodes(index, start, end) {
  const result = [];
  for (const entry of index.entries) {
    const entryEnd = entry.start + entry.offsets.length;
    if (entryEnd <= start || entry.start >= end) continue;
    const from = Math.max(start, entry.start) - entry.start;
    const to = Math.min(end, entryEnd) - entry.start;
    if (to <= from) continue;
    const raw = entry.node.nodeValue;
    let localStart = entry.offsets[from];
    const localEnd = entry.offsets[to - 1] + 1;
    // Whitespace between two nodes of the same phrase belongs to the phrase —
    // without this a highlight spanning <em>/<a> paints with visible gaps.
    if (result.length && !raw.slice(0, localStart).trim()) localStart = 0;
    result.push({ node: entry.node, start: localStart, end: localEnd });
  }

  for (let i = 0; i < result.length - 1; i++) {
    const slice = result[i];
    const raw = slice.node.nodeValue;
    if (!raw.slice(slice.end).trim()) slice.end = raw.length;
  }
  return result;
}

function clearSuggestions() {
  document.querySelectorAll('mark.lumos-suggestion').forEach(removeMark);
}

/** Titles are often repeated in the body as plain text — catch those too. */
function isPageTitle(phrase) {
  const title = document.title.replace(/\s+/g, ' ').trim().toLowerCase();
  if (!title) return false;
  return title.includes(phrase.replace(/\s+/g, ' ').trim().toLowerCase());
}

function paintSuggestions(phrases, color) {
  const index = buildTextIndex(suggestScope());
  const taken = [];
  const hits = [];

  for (const phrase of phrases) {
    if (isPageTitle(phrase)) continue;
    const hit = findPhrase(index, phrase, taken);
    if (!hit) continue;
    taken.push([hit.start, hit.end]);
    hits.push({ phrase, ...hit });
  }

  // Paint back-to-front: wrapping splits text nodes, which would invalidate
  // offsets of any hit that sits later in the same node.
  hits.sort((a, b) => b.start - a.start);

  let painted = 0;
  for (const hit of hits) {
    const textNodes = rangeToTextNodes(index, hit.start, hit.end);
    if (!textNodes.length) continue;
    // Purely visual: no handlers, so selecting the text and highlighting it
    // yourself works exactly as it does anywhere else on the page.
    wrapTextNodes(textNodes, (mark) => {
      mark.className = 'lumos-suggestion';
      mark.dataset.lumosPhrase = hit.phrase;
      pin(mark, { ...MARK_STYLE, 'background-color': color || SUGGEST_COLOR });
    });
    painted++;
  }
  return painted;
}

async function requestSuggestions(refresh = false, retries = 3) {
  if (!suggestEnabled) return;
  clearSuggestions();

  const index = buildTextIndex(suggestScope());
  if (index.text.length < SUGGEST_MIN_CHARS) {
    // Client-rendered pages often have no article text yet at document_idle
    if (retries > 0) {
      setTimeout(() => requestSuggestions(refresh, retries - 1), SUGGEST_RETRY_MS);
    }
    return;
  }

  const requestedUrl = getCanonicalUrl();
  try {
    const response = await chrome.runtime.sendMessage({
      type: 'SUGGEST_HIGHLIGHTS',
      url: requestedUrl,
      title: document.title,
      text: index.text,
      refresh,
    });
    // The page may have navigated away while the LLM was thinking
    if (getCanonicalUrl() !== requestedUrl) return;
    if (!response?.ok) return;
    // Config problems (missing API key, unreachable model) would otherwise be
    // invisible — say it once instead of quietly doing nothing
    if (response.error) {
      showToast('Lumos: ' + response.error);
      return;
    }
    if (response.phrases?.length) paintSuggestions(response.phrases, response.color);
  } catch (_) {
    // Native host unavailable — suggestions are best-effort
  }
}

async function initSuggestions() {
  try {
    const stored = await chrome.storage.local.get('suggestEnabled');
    suggestEnabled = stored.suggestEnabled !== false;
  } catch (_) {}
  requestSuggestions();
}

/**
 * Client-side navigation (Reddit, Medium, X…) never reloads the content
 * script, so re-run everything when the service worker reports a new URL.
 */
let lastSeenUrl = location.href.split('#')[0];

function onUrlChanged() {
  const url = location.href.split('#')[0];
  if (url === lastSeenUrl) return;
  lastSeenUrl = url;

  clearSuggestions();
  document.querySelectorAll('mark.lumos-highlight').forEach(removeMark);
  // Let the new view render before reading it
  setTimeout(() => {
    Promise.resolve(restoreHighlights()).finally(() => requestSuggestions());
  }, SPA_SETTLE_MS);
}

// ─── Messages from Service Worker ────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === 'GET_CANONICAL_URL') {
    sendResponse({ url: getCanonicalUrl() });
    return;
  }
  if (message.type === 'URL_CHANGED') {
    onUrlChanged();
    sendResponse({ ok: true });
    return;
  }
  if (message.type === 'TOGGLE_SUGGESTIONS') {
    suggestEnabled = !!message.enabled;
    if (suggestEnabled) requestSuggestions(message.refresh);
    else clearSuggestions();
    sendResponse({ ok: true });
    return;
  }
  if (message.type === 'PAGE_SAVED') showToast('Page saved to Lumos ✓');
  if (message.type === 'IMAGE_SAVED') showToast('Image saved to Lumos ✓');
  if (message.type === 'SCROLL_TO_HIGHLIGHT') {
    const mark = document.querySelector(`mark.lumos-highlight[data-lumos-id="${message.id}"]`);
    if (mark) {
      mark.scrollIntoView({ behavior: 'smooth', block: 'center' });
      // Brief flash to draw attention
      const orig = mark.style.outline;
      mark.style.outline = '2px solid #FFEB3B';
      setTimeout(() => { mark.style.outline = orig; }, 1500);
    }
  }
});

// ─── Init ─────────────────────────────────────────────────────────────────────

function init() {
  // Suggestions run after restore so they never land on an existing highlight
  Promise.resolve(restoreHighlights()).finally(initSuggestions);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

})(); // end top-frame-only IIFE
