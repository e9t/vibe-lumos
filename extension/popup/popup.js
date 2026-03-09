'use strict';

const TYPE_ICON = { page: '📄', highlight: '💡', image: '🖼' };
let _currentTab = null;
let _pageItem = null; // track the page item for header delete

function showStatus(text, type = 'ok') {
  const el = document.getElementById('status');
  el.textContent = text;
  el.className = `status ${type}`;
}

function hideStatus() {
  document.getElementById('status').className = 'status hidden';
}

async function refreshItems() {
  if (!_currentTab?.url) return;
  const resp = await chrome.runtime.sendMessage({ type: 'GET_PAGE_ITEMS', url: _currentTab.url });
  renderItems(resp?.ok ? resp.items : []);
}

async function deleteItem(id) {
  const resp = await chrome.runtime.sendMessage({ type: 'DELETE_ITEM', id });
  if (resp?.ok) {
    await refreshItems();
  } else {
    showStatus('Delete failed', 'error');
  }
}

async function updatePagePriority(delta) {
  if (!_pageItem) return;
  const resp = await chrome.runtime.sendMessage({ type: 'UPDATE_PRIORITY', id: _pageItem.id, delta });
  if (resp?.ok) {
    await refreshItems();
  }
}

async function deletePageAndAll() {
  if (!_pageItem) return;
  // Delete all items (page + children) one by one
  const resp = await chrome.runtime.sendMessage({ type: 'GET_PAGE_ITEMS', url: _currentTab.url });
  if (!resp?.ok) return;
  for (const item of resp.items) {
    await chrome.runtime.sendMessage({ type: 'DELETE_ITEM', id: item.id });
  }
  _pageItem = null;
  document.getElementById('btn-delete-page').classList.add('hidden');
  await refreshItems();
  showStatus('Page deleted', 'ok');
}

function renderItems(items) {
  const list = document.getElementById('items-list');
  const empty = document.getElementById('empty');
  const loading = document.getElementById('loading');
  const delPageBtn = document.getElementById('btn-delete-page');

  loading.classList.add('hidden');

  // Find page item
  _pageItem = items.find(i => i.type === 'page') || null;
  const priUp = document.getElementById('btn-pri-up');
  const priDown = document.getElementById('btn-pri-down');
  const cacheBtn = document.getElementById('btn-view-cache');
  if (_pageItem) {
    delPageBtn.classList.remove('hidden');
    priUp.classList.remove('hidden');
    priDown.classList.remove('hidden');
    cacheBtn.classList.remove('hidden');
    // Update cache button label based on whether cache exists
    if (_pageItem.cache?.readable) {
      cacheBtn.textContent = '📋 View Cache';
      cacheBtn.title = 'View cached page';
    } else {
      cacheBtn.textContent = '📋 Cache';
      cacheBtn.title = 'Cache this page';
    }
  } else {
    delPageBtn.classList.add('hidden');
    priUp.classList.add('hidden');
    priDown.classList.add('hidden');
    cacheBtn.classList.add('hidden');
  }

  if (!items.length) {
    list.classList.add('hidden');
    empty.classList.remove('hidden');
    return;
  }

  empty.classList.add('hidden');
  list.classList.remove('hidden');
  list.innerHTML = '';

  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'item';

    if (item.type === 'page') {
      // Page: show title + URL, no delete button (delete is in header)
      const textEl = document.createElement('div');
      textEl.className = 'item-text';
      textEl.textContent = item.title;
      li.appendChild(textEl);

      const urlEl = document.createElement('div');
      urlEl.className = 'item-url';
      urlEl.textContent = item.url;
      li.appendChild(urlEl);
    } else {
      // Highlight/Image: header with type + delete
      const headerEl = document.createElement('div');
      headerEl.className = 'item-header';

      const typeEl = document.createElement('span');
      typeEl.className = 'item-type';
      typeEl.textContent = `${TYPE_ICON[item.type] || ''} ${item.type}`;
      headerEl.appendChild(typeEl);

      const delBtn = document.createElement('button');
      delBtn.className = 'item-delete';
      delBtn.textContent = '🗑';
      delBtn.title = 'Delete';
      delBtn.addEventListener('click', () => deleteItem(item.id));
      headerEl.appendChild(delBtn);

      li.appendChild(headerEl);

      if (item.text) {
        const textEl = document.createElement('div');
        textEl.className = 'item-text highlight-text clickable';
        textEl.textContent = item.text.length > 120 ? item.text.slice(0, 117) + '…' : item.text;
        if (_currentTab?.id) {
          textEl.addEventListener('click', () => {
            chrome.tabs.sendMessage(_currentTab.id, { type: 'SCROLL_TO_HIGHLIGHT', id: item.id });
            window.close();
          });
        }
        li.appendChild(textEl);
      }
    }

    if (item.note) {
      const noteEl = document.createElement('div');
      noteEl.className = 'item-note';
      noteEl.textContent = '📝 ' + item.note;
      li.appendChild(noteEl);
    }

    if (item.type !== 'page') {
      const meta = document.createElement('div');
      meta.className = 'item-meta';
      const date = new Date(item.created_at).toLocaleDateString();
      meta.textContent = date;
      if (item.priority) meta.textContent += ` · Priority: ${item.priority}`;
      li.appendChild(meta);
    }

    list.appendChild(li);
  }
}

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url) {
    document.getElementById('loading').textContent = 'No active page.';
    return;
  }
  _currentTab = tab;

  // Load items for this page
  try {
    const response = await chrome.runtime.sendMessage({
      type: 'GET_PAGE_ITEMS',
      url: tab.url,
    });
    renderItems(response?.ok ? response.items : []);
  } catch (e) {
    document.getElementById('loading').textContent = 'Could not connect to Lumos.';
    document.getElementById('loading').classList.remove('hidden');
  }

  // Save Page button
  document.getElementById('btn-save-page').addEventListener('click', async () => {
    const btn = document.getElementById('btn-save-page');
    btn.disabled = true;
    btn.textContent = '⏳ Saving…';
    hideStatus();

    try {
      const response = await chrome.runtime.sendMessage({
        type: 'SAVE_PAGE_NOW',
        tabId: tab.id,
        url: tab.url,
        title: tab.title || '',
      });

      if (response?.ok) {
        showStatus('Page saved ✓', 'ok');
        await refreshItems();
      } else {
        showStatus(response?.error || 'Save failed', 'error');
      }
    } catch (e) {
      showStatus(e.message || 'Error', 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '📄 Save Page';
    }
  });

  // Delete Page button (in header)
  document.getElementById('btn-delete-page').addEventListener('click', deletePageAndAll);

  // Priority buttons
  document.getElementById('btn-pri-up').addEventListener('click', () => updatePagePriority(1));
  document.getElementById('btn-pri-down').addEventListener('click', () => updatePagePriority(-1));

  // Cache button: create cache if none exists, view if it does
  document.getElementById('btn-view-cache').addEventListener('click', async () => {
    if (!_currentTab?.url) return;
    const btn = document.getElementById('btn-view-cache');
    btn.disabled = true;

    try {
      if (_pageItem?.cache?.readable) {
        // View existing cache
        const resp = await chrome.runtime.sendMessage({ type: 'GET_CACHE', url: _currentTab.url });
        if (resp?.ok) {
          // Render markdown as styled HTML
          const escaped = resp.text.replace(/&/g,'&amp;').replace(/</g,'&lt;');
          const rendered = escaped
            .replace(/^#### (.+)$/gm, '<h4>$1</h4>')
            .replace(/^### (.+)$/gm, '<h3>$1</h3>')
            .replace(/^## (.+)$/gm, '<h2>$1</h2>')
            .replace(/^# (.+)$/gm, '<h1>$1</h1>')
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.+?)\*/g, '<em>$1</em>')
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>')
            .replace(/^- (.+)$/gm, '<li>$1</li>')
            .replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>')
            .replace(/\n{2,}/g, '</p><p>')
            .replace(/^---$/gm, '<hr>');
          const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${resp.title} — Lumos Cache</title>`
            + `<style>body{max-width:720px;margin:2em auto;padding:0 1em;font:16px/1.6 system-ui,sans-serif;color:#333}`
            + `a{color:#1a73e8}code{background:#f5f5f5;padding:2px 4px;border-radius:3px;font-size:0.9em}`
            + `pre{background:#f5f5f5;padding:1em;border-radius:6px;overflow-x:auto}`
            + `blockquote{border-left:3px solid #ddd;margin:0;padding-left:1em;color:#666}`
            + `h1,h2,h3,h4{margin-top:1.5em}li{margin:0.3em 0}</style></head>`
            + `<body><h1>${resp.title}</h1><div>${rendered}</div></body></html>`;
          const blob = new Blob([html], { type: 'text/html' });
          const url = URL.createObjectURL(blob);
          chrome.tabs.create({ url });
        } else {
          showStatus(resp?.error || 'No cache available', 'error');
        }
      } else {
        // Create cache on-demand
        btn.textContent = '⏳ Caching…';
        const resp = await chrome.runtime.sendMessage({
          type: 'CACHE_PAGE',
          tabId: _currentTab.id,
          url: _currentTab.url,
          title: _currentTab.title || '',
        });
        if (resp?.ok) {
          showStatus('Page cached ✓', 'ok');
          await refreshItems();
        } else {
          showStatus(resp?.error || 'Cache failed', 'error');
        }
      }
    } catch (e) {
      showStatus(e.message || 'Error', 'error');
    } finally {
      btn.disabled = false;
    }
  });
}

init();
