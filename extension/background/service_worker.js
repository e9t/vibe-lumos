'use strict';

const HOST = 'com.lumos.host';

// ─── Native Messaging ─────────────────────────────────────────────────────────

function sendToHost(message) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(HOST, message, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve(response);
      }
    });
  });
}

// ─── URL Normalization ────────────────────────────────────────────────────────

/** Tracking / junk query params to strip from URLs */
const STRIP_PARAMS = new Set([
  // Facebook / Meta
  'fbclid', 'fb_action_ids', 'fb_action_types', 'fb_source', 'fb_ref',
  // Google / UTM
  'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
  'utm_id', 'utm_source_platform', 'utm_creative_format', 'utm_marketing_tactic',
  'gclid', 'gclsrc', 'dclid', 'gbraid', 'wbraid',
  // Microsoft / Bing
  'msclkid',
  // HubSpot
  'hsa_cam', 'hsa_grp', 'hsa_mt', 'hsa_src', 'hsa_ad', 'hsa_acc',
  'hsa_net', 'hsa_ver', 'hsa_la', 'hsa_ol', 'hsa_kw', 'hsa_tgt',
  // Mailchimp
  'mc_cid', 'mc_eid',
  // Others
  '_ga', '_gl', '_hsenc', '_hsmi', '_ke',
  'ref', 'ref_src', 'ref_url',
  'yclid', 'twclid', 'ttclid', 'igshid', 'li_fat_id',
  'spm', 'scm', 'aff_trace_key', 'terminal_id',
  'ns_mchannel', 'ns_source', 'ns_campaign', 'ns_linkname', 'ns_fee',
]);

/** Normalise URL: strip fragment, tracking params, trailing slash on bare-path URLs */
function normalizeUrl(url) {
  if (!url) return url;
  let u = url.split('#')[0];
  try {
    const parsed = new URL(u);
    for (const key of [...parsed.searchParams.keys()]) {
      if (STRIP_PARAMS.has(key)) parsed.searchParams.delete(key);
    }
    if (parsed.pathname === '/' && !parsed.search) return parsed.origin;
    u = parsed.toString();
  } catch (_) {}
  return u;
}


// ─── Icon / Badge ─────────────────────────────────────────────────────────────

async function updateBadge(tabId, url) {
  try {
    const response = await sendToHost({ action: 'check_url', url });
    if (response.ok && response.exists) {
      await chrome.action.setBadgeText({ text: '✓', tabId });
      await chrome.action.setBadgeBackgroundColor({ color: '#4CAF50', tabId });
    } else {
      await chrome.action.setBadgeText({ text: '', tabId });
    }
  } catch (e) {
    console.error('Lumos: updateBadge error', e);
    await chrome.action.setBadgeText({ text: '', tabId });
  }
}

async function flashBadge(tabId, ok) {
  const text = ok ? '✓' : '!';
  const color = ok ? '#4CAF50' : '#F44336';
  await chrome.action.setBadgeText({ text, tabId });
  await chrome.action.setBadgeBackgroundColor({ color, tabId });
}

// ─── Lifecycle ────────────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'lumos-save-image',
    title: 'Save to Lumos',
    contexts: ['image'],
  });
});

/** Ask the content script for the canonical URL; fall back to tab.url */
async function getCanonicalUrlFromTab(tabId, fallbackUrl) {
  try {
    const resp = await chrome.tabs.sendMessage(tabId, { type: 'GET_CANONICAL_URL' });
    return resp?.url || fallbackUrl;
  } catch (_) {
    return fallbackUrl;
  }
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete' && tab.url && !tab.url.startsWith('chrome://')) {
    getCanonicalUrlFromTab(tabId, tab.url).then((url) => updateBadge(tabId, normalizeUrl(url)));
  }
});

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  try {
    const tab = await chrome.tabs.get(tabId);
    if (tab.url && !tab.url.startsWith('chrome://')) {
      const url = await getCanonicalUrlFromTab(tabId, tab.url);
      updateBadge(tabId, normalizeUrl(url));
    }
  } catch (_) {}
});

// ─── Save Page (Cmd+D) — bookmark only, no cache ────────────────────────────

chrome.commands.onCommand.addListener(async (command) => {
  if (command === 'save-page') {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url || tab.url.startsWith('chrome://')) return;

    const pageUrl = normalizeUrl(tab.url);
    try {
      const response = await sendToHost({
        action: 'save_page',
        url: pageUrl,
        title: tab.title || '',
      });
      await flashBadge(tab.id, response.ok);
    } catch (e) {
      console.error('Lumos: save-page command failed', e);
      await flashBadge(tab.id, false);
    }
  }

  if (command === 'cache-page') {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url || tab.url.startsWith('chrome://')) return;

    const pageUrl = normalizeUrl(tab.url);
    try {
      // Save page first
      await sendToHost({ action: 'save_page', url: pageUrl, title: tab.title || '' });

      // Then cache
      let readableText = null;
      try {
        const [{ result }] = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: _extractReadableMarkdown,
        });
        readableText = result;
      } catch (_) {}
      const mhtmlBase64 = await _captureMhtml(tab.id);
      const response = await sendToHost({
        action: 'cache_page',
        url: pageUrl,
        title: tab.title || '',
        readable_text: readableText,
        mhtml_data: mhtmlBase64,
      });
      await flashBadge(tab.id, response.ok);
    } catch (e) {
      console.error('Lumos: cache-page command failed', e);
      await flashBadge(tab.id, false);
    }
  }
});

// ─── Cache helpers ───────────────────────────────────────────────────────────

/** Injected into the page to extract readable markdown-like text */
function _extractReadableMarkdown() {
  const clone = document.cloneNode(true);
  for (const el of clone.querySelectorAll(
    'script, style, nav, footer, header, aside, .sidebar, [role="navigation"], [role="banner"], [role="complementary"]'
  )) {
    el.remove();
  }
  const main = clone.querySelector('main, article, [role="main"]') || clone.body;
  if (!main) return '';

  function walk(node) {
    if (node.nodeType === 3) return node.textContent;
    if (node.nodeType !== 1) return '';
    const tag = node.tagName.toLowerCase();
    if (node.hidden) return '';
    const kids = () => Array.from(node.childNodes).map(walk).join('');
    switch (tag) {
      case 'h1': return '\n# ' + kids().trim() + '\n';
      case 'h2': return '\n## ' + kids().trim() + '\n';
      case 'h3': return '\n### ' + kids().trim() + '\n';
      case 'h4': case 'h5': case 'h6':
        return '\n#### ' + kids().trim() + '\n';
      case 'p': return '\n' + kids().trim() + '\n';
      case 'br': return '\n';
      case 'a': {
        const href = node.getAttribute('href');
        const text = kids().trim();
        if (!text) return '';
        if (href && href.startsWith('http')) return '[' + text + '](' + href + ')';
        return text;
      }
      case 'strong': case 'b': return '**' + kids().trim() + '**';
      case 'em': case 'i': return '*' + kids().trim() + '*';
      case 'code': return '`' + kids().trim() + '`';
      case 'pre': return '\n```\n' + (node.textContent || '').trim() + '\n```\n';
      case 'blockquote': return '\n> ' + kids().trim().replace(/\n/g, '\n> ') + '\n';
      case 'li': return '- ' + kids().trim() + '\n';
      case 'ul': case 'ol': return '\n' + kids();
      case 'img': {
        const alt = node.getAttribute('alt');
        return alt ? '[image: ' + alt + ']' : '';
      }
      case 'hr': return '\n---\n';
      case 'div': case 'section': case 'article': case 'main':
        return '\n' + kids();
      default: return kids();
    }
  }
  const md = walk(main).replace(/\n{3,}/g, '\n\n').trim();
  return md.slice(0, 50000);
}

async function _captureMhtml(tabId) {
  try {
    const mhtmlBlob = await new Promise((resolve) => {
      chrome.pageCapture.saveAsMHTML({ tabId }, resolve);
    });
    if (!mhtmlBlob) return null;
    const buf = await mhtmlBlob.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = '';
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  } catch (_) {
    return null;
  }
}

// ─── Context Menu: Save Image ─────────────────────────────────────────────────

// Injected into page to fetch an image and return base64
async function _fetchImageAsBase64(srcUrl) {
  try {
    const resp = await fetch(srcUrl);
    const blob = await resp.blob();
    const ext = (blob.type.split('/')[1] || 'png').replace('jpeg', 'jpg');
    const base64 = await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(',')[1]);
      reader.readAsDataURL(blob);
    });
    return { base64, ext };
  } catch {
    return null;
  }
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== 'lumos-save-image') return;
  if (!info.srcUrl || !tab) return;

  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: _fetchImageAsBase64,
      args: [info.srcUrl],
    });

    if (!result) throw new Error('Failed to fetch image data');

    const pageUrl = normalizeUrl(tab.url);
    const response = await sendToHost({
      action: 'save_image',
      url: pageUrl,
      title: tab.title || '',
      image_data: result.base64,
      ext: result.ext,
    });

    if (response.ok) {
      await updateBadge(tab.id, pageUrl);
      chrome.tabs.sendMessage(tab.id, { type: 'IMAGE_SAVED' }).catch(() => {});
    }
  } catch (e) {
    console.error('Lumos: save_image failed', e);
  }
});

// ─── Messages from Content Script ────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab?.id;
  const tabUrl = sender.tab?.url;

  if (message.type === 'SAVE_HIGHLIGHT') {
    (async () => {
      try {
        const pageUrl = normalizeUrl(message.url);
        // Ensure the page is saved first
        const check = await sendToHost({ action: 'check_url', url: pageUrl });
        if (!check.ok || !check.ids.length) {
          await sendToHost({
            action: 'save_page',
            url: pageUrl,
            title: message.title || '',
          });
        }
        const response = await sendToHost({
          action: 'save_highlight',
          url: pageUrl,
          title: message.title,
          text: message.text,
          note: message.note || null,
          xpath: message.xpath || null,
          start_offset: message.startOffset ?? null,
          end_offset: message.endOffset ?? null,
          text_fingerprint: message.textFingerprint || null,
          original_html: message.originalHtml || null,
        });
        if (response.ok) {
          if (tabId != null) await updateBadge(tabId, pageUrl);
        }
        sendResponse(response);
      } catch (e) {
        sendResponse({ ok: false, error: e.message });
      }
    })();
    return true;
  }

  if (message.type === 'GET_ITEMS_BY_IDS') {
    sendToHost({ action: 'get_items_by_ids', ids: message.ids })
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }

  if (message.type === 'DELETE_ITEM') {
    sendToHost({ action: 'delete_item', id: message.id })
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }

  if (message.type === 'UPDATE_NOTE') {
    sendToHost({ action: 'update_note', id: message.id, note: message.note })
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }

  if (message.type === 'UPDATE_PRIORITY') {
    sendToHost({ action: 'update_priority', id: message.id, delta: message.delta })
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }

  if (message.type === 'GET_MEDIA') {
    const payload = { action: 'get_media', path: message.path };
    if (message.max_dim !== undefined) payload.max_dim = message.max_dim;
    sendToHost(payload)
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }

  if (message.type === 'GET_CACHE') {
    const url = normalizeUrl(message.url);
    sendToHost({ action: 'get_cache', url })
      .then(sendResponse)
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }

  if (message.type === 'GET_PAGE_ITEMS') {
    // Called from popup and content script — get all items for a URL
    (async () => {
      try {
        const url = normalizeUrl(message.url);
        const check = await sendToHost({ action: 'check_url', url });
        if (!check.ok || !check.ids.length) return sendResponse({ ok: true, items: [] });
        const resp = await sendToHost({ action: 'get_items_by_ids', ids: check.ids });
        sendResponse(resp);
      } catch (e) {
        sendResponse({ ok: false, error: e.message });
      }
    })();
    return true;
  }

  if (message.type === 'SAVE_PAGE_NOW') {
    // Called from popup — bookmark only, no cache
    (async () => {
      const { tabId, url, title } = message;
      try {
        const pageUrl = normalizeUrl(url);
        const response = await sendToHost({
          action: 'save_page',
          url: pageUrl,
          title,
        });

        if (response.ok) {
          await flashBadge(tabId, true);
          chrome.tabs.sendMessage(tabId, { type: 'PAGE_SAVED' }).catch(() => {});
        }
        sendResponse(response);
      } catch (e) {
        sendResponse({ ok: false, error: e.message });
      }
    })();
    return true;
  }

  if (message.type === 'CACHE_PAGE') {
    // On-demand cache: extract readable markdown + MHTML
    (async () => {
      const { tabId, url, title } = message;
      try {
        const pageUrl = normalizeUrl(url);

        let readableText = null;
        try {
          const [{ result }] = await chrome.scripting.executeScript({
            target: { tabId },
            func: _extractReadableMarkdown,
          });
          readableText = result;
        } catch (_) {}

        const mhtmlBase64 = await _captureMhtml(tabId);

        const response = await sendToHost({
          action: 'cache_page',
          url: pageUrl,
          title,
          readable_text: readableText,
          mhtml_data: mhtmlBase64,
        });
        sendResponse(response);
      } catch (e) {
        sendResponse({ ok: false, error: e.message });
      }
    })();
    return true;
  }
});

