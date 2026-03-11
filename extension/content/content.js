'use strict';

// Only run in the top frame — never in iframes
(function() {
let isTop = false;
try { isTop = window.self === window.top; } catch (_) {}
if (!isTop) return;

console.log('[Lumos] content script loaded at', location.href);

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
let activeTooltip = null;

function showNoteTooltip(mark) {
  const note = mark.dataset.lumosNote;
  const priority = parseInt(mark.dataset.lumosPriority || '0', 10);
  if (!note && !priority) return;
  hideNoteTooltip();

  const tip = document.createElement('div');
  tip.className = 'lumos-tooltip';
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

function applyMark(textNodes, itemId, color = '#FFEB3B', note = null, priority = 0) {
  const marks = [];
  for (const { node, start, end } of textNodes) {
    let target = node;
    if (end < target.length) target.splitText(end);
    if (start > 0) target = target.splitText(start);

    const mark = document.createElement('mark');
    mark.className = 'lumos-highlight';
    mark.dataset.lumosId = itemId;
    if (note) mark.dataset.lumosNote = note;
    if (priority) mark.dataset.lumosPriority = String(priority);
    mark.style.backgroundColor = color;
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
    target.parentNode.insertBefore(mark, target);
    mark.appendChild(target);
    marks.push(mark);
  }
  return marks;
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

  const btnUp = document.createElement('button');
  btnUp.textContent = '👍';
  btnUp.title = 'Priority up';
  btnUp.addEventListener('pointerdown', (e) => {
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    updatePriority(mark, 1);
  }, true);

  const btnDown = document.createElement('button');
  btnDown.textContent = '👎';
  btnDown.title = 'Priority down';
  btnDown.addEventListener('pointerdown', (e) => {
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    updatePriority(mark, -1);
  }, true);

  const btnNote = document.createElement('button');
  btnNote.textContent = mark.dataset.lumosNote ? '📝 Edit Note' : '📝 Add Note';
  btnNote.addEventListener('pointerdown', (e) => {
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    showEditNoteInput(mark);
  }, true);

  const btnRemove = document.createElement('button');
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

  const textarea = document.createElement('textarea');
  textarea.placeholder = 'Add a note…';
  textarea.value = mark.dataset.lumosNote || '';

  const actions = document.createElement('div');
  actions.className = 'lumos-note-actions';

  const btnCancel = document.createElement('button');
  btnCancel.textContent = 'Cancel';
  btnCancel.addEventListener('click', () => removeNotePopup());

  const btnSave = document.createElement('button');
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
  console.log('[Lumos] showToolbar called at', location.href);
  removeToolbar();
  removeNotePopup();
  toolbarShownAt = Date.now();

  toolbar = document.createElement('div');
  toolbar.className = 'lumos-toolbar';
  toolbar.setAttribute('data-lumos', 'toolbar');

  const btnHighlight = document.createElement('button');
  btnHighlight.textContent = '💡 LUMOS Highlight';
  btnHighlight.onclick = function() {
    console.log('[Lumos] Highlight button CLICKED at', location.href);
    saveHighlight(null);
  };
  btnHighlight.onpointerdown = function(e) {
    console.log('[Lumos] Highlight button pointerdown at', location.href);
    e.preventDefault();
    e.stopPropagation();
  };
  btnHighlight.onmousedown = function(e) {
    console.log('[Lumos] Highlight button mousedown at', location.href);
    e.preventDefault();
    e.stopPropagation();
  };

  const btnNote = document.createElement('button');
  btnNote.textContent = '📝 LUMOS Note';
  btnNote.onclick = () => showNoteInput(rect);
  btnNote.onpointerdown = (e) => { e.preventDefault(); e.stopPropagation(); };
  btnNote.onmousedown = (e) => { e.preventDefault(); e.stopPropagation(); };

  const btnTest = document.createElement('button');
  btnTest.textContent = '🔍 TEST';
  btnTest.onclick = function() { alert('Lumos toolbar works! URL: ' + location.href); };

  toolbar.appendChild(btnHighlight);
  toolbar.appendChild(btnNote);
  toolbar.appendChild(btnTest);
  positionElement(toolbar, rect);
}

function showNoteInput(anchorRect) {
  removeToolbar();
  removeNotePopup();

  notePopup = document.createElement('div');
  notePopup.className = 'lumos-note-input';

  const textarea = document.createElement('textarea');
  textarea.placeholder = 'Add a note…';

  const actions = document.createElement('div');
  actions.className = 'lumos-note-actions';

  const btnCancel = document.createElement('button');
  btnCancel.textContent = 'Cancel';
  btnCancel.addEventListener('click', () => {
    removeNotePopup();
    pendingRange = null;
  });

  const btnSave = document.createElement('button');
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
  console.log('[Lumos] saveHighlight called — pendingRange:', !!pendingRange);
  const range = pendingRange;
  if (!range || range.collapsed) {
    console.log('[Lumos] saveHighlight — no range or collapsed, bailing');
    removeToolbar();
    removeNotePopup();
    return;
  }

  const text = range.toString().trim();
  if (!text) {
    console.log('[Lumos] saveHighlight — empty text, bailing');
    removeToolbar();
    removeNotePopup();
    return;
  }
  console.log('[Lumos] saveHighlight — text:', text.slice(0, 50), '| url:', location.href);

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
      console.log('[Lumos] highlight saved — sent url:', location.href.split('#')[0], '| stored url:', response.item.url);
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
    console.log('[Lumos] selectionchange — showing toolbar at', location.href);
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

// ─── Messages from Service Worker ────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type === 'GET_CANONICAL_URL') {
    sendResponse({ url: getCanonicalUrl() });
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

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', restoreHighlights);
} else {
  restoreHighlights();
}

})(); // end top-frame-only IIFE
