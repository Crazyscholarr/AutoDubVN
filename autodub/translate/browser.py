from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from weakref import WeakKeyDictionary
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple

from .cache import ChunkCache, TranslationIncomplete
from .const import (
    ENABLE_BROWSER_SHORTENING, brief_confirmed, essay_hit_count,
    looks_like_document_vi, session_brief,
)
from .jsonutil import json_translation_complete
from .parse import (
    _bad_line_summary, _build_prompt, _cjk_line_numbers, _clean_vi_lines,
    _contains_cjk, match_by_position, parse_numbered_reply, shorten_long_lines,
)
from ..srt_utils import Segment, normalize_vi_subtitle_text
from ..utils import log


# --------------------------------------------------------------------------- #
#  Chế độ 2: điều khiển trình duyệt vào Gemini web
# --------------------------------------------------------------------------- #
# Xếp theo thứ tự CỤ THỂ -> CHUNG CHUNG. Không gộp thành 1 chuỗi CSS như bản cũ,
# vì query CSS gộp trả về phần tử ĐẦU TIÊN theo thứ tự DOM (có thể là ô ẩn của
# hộp thoại khác), chứ không phải theo thứ tự ưu tiên mình muốn.
_INPUT_CANDIDATES = [
    "rich-textarea div[contenteditable='true']",
    "div.ql-editor[contenteditable='true']",
    "div[role='textbox'][contenteditable='true']",
    "[aria-label*='Nhập câu lệnh' i][contenteditable='true']",
    "[aria-label*='Enter a prompt' i][contenteditable='true']",
    "[aria-label*='Ask Gemini' i][contenteditable='true']",
    "textarea[aria-label]",
]
_RESP_CANDIDATES = [
    "model-response",
    "model-response-content",
    "message-content",
    "[id^='message-content-id-r_']",
    "[id^='model-response-message-content']",
    "structured-content-container.model-response-text",
    ".model-response-text",
    "response-container",
    "[data-message-author-role='model']",
]
_RESP_FALLBACK = [
    "div.markdown.markdown-main-panel",
    "div.markdown",
]
_CONV_ROOT_SELS = [
    'infinite-scroller[data-test-id="chat-history-container"]',
    "chat-window-content",
    "infinite-scroller.chat-history",
    "chat-window",
    "#chat-history",
]
_SO_DONG_RE = re.compile(r"^\s*(?:\*\*)?(?:\[\d{1,4}\]|\d{1,4}[.)])\s+\S")
_NEW_CHAT_CANDIDATES = [
    "[aria-label*='Cuộc trò chuyện mới' i]",
    "[data-test-id='new-chat-button']",
    "button[data-test-id='new-chat-button']",
    "a[data-test-id='new-chat-button']",
    "button[aria-label='New chat']",
    "button[aria-label='Chat mới']",
    "button[aria-label='Đoạn chat mới']",
    "button[aria-label*='New chat' i]",
    "button[aria-label*='Chat mới' i]",
    "button[aria-label*='Đoạn chat mới' i]",
    "button[aria-label*='New conversation' i]",
    "a[aria-label*='New chat' i]",
    "a[aria-label*='Chat mới' i]",
    "a[aria-label*='Đoạn chat mới' i]",
]
_NEW_CHAT_CLICK_JS = r"""() => {
  const exact = /^\s*(new chat|new conversation|chat mới|đoạn chat mới|cuộc trò chuyện mới)\s*$/i;
  const loose = /new chat|new conversation|chat mới|đoạn chat mới|cuộc trò chuyện mới/i;
  const nodes = document.querySelectorAll('button, a, [role="button"]');
  const ranked = [];
  for (const node of nodes) {
    const testId = (node.getAttribute('data-test-id') || '').trim();
    const label = ((node.getAttribute('aria-label') || '') + ' '
                   + (node.innerText || '')).trim();
    if (testId !== 'new-chat-button' && !loose.test(label)) continue;
    const box = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    if (box.width < 1 || box.height < 1
        || style.visibility === 'hidden'
        || style.display === 'none'
        || style.opacity === '0') continue;
    const rank = (testId === 'new-chat-button' || exact.test(label)) ? 0 : 1;
    ranked.push([rank, node]);
  }
  ranked.sort((a, b) => a[0] - b[0]);
  if (!ranked.length) return false;
  ranked[0][1].click();
  return true;
}"""
_SEND_CANDIDATES = [
    "button.send-button",
    "button.send-button.submit",
    "[role='button'].send-button",
    "button[aria-label='Send']",
    "button[aria-label='Send message']",
    "button[aria-label='Send prompt']",
    "button[aria-label='Gửi']",
    "button[aria-label='Gửi tin nhắn']",
    "button[aria-label='Gửi câu lệnh']",
    "button[aria-label*='Send message' i]",
    "button[aria-label*='Gửi tin' i]",
    "button[aria-label*='Gửi câu' i]",
    "button[mattooltip*='Send' i]",
    "[data-test-id='send-button']",
    "button[data-test-id='send-button']",
]
# Gemini 2025/26: ô nhập xuống dòng bằng Enter, gửi bằng nút mũi tên hoặc Ctrl+Enter.
# Không bấm nhầm Flash/Tools/+ — chỉ nút Gửi thật.
_SEND_CLICK_JS = r"""() => {
  const visible = (node) => {
    if (!node) return false;
    const box = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    return box.width > 1 && box.height > 1
      && style.visibility !== 'hidden'
      && style.display !== 'none'
      && style.opacity !== '0';
  };
  const enabled = (node) => !!(node && !node.disabled
    && node.getAttribute('aria-disabled') !== 'true'
    && node.getAttribute('disabled') === null);
  const labelOf = (node) => ((node.getAttribute('aria-label') || '') + ' '
    + (node.getAttribute('mattooltip') || '') + ' '
    + (node.innerText || '')).trim();
  const chromeOnly = (label) =>
    /stop|dừng|ngừng|mic|microphone|attach|upload|image|ảnh|file|add files|plus|flash|fast|thinking|tools|spark|model|công cụ|chế độ|new chat|chat mới/i.test(label)
    && !/gửi|send message|send prompt|\bsend\b/i.test(label);
  const iconText = (node) => {
    const parts = [];
    node.querySelectorAll('mat-icon, .mat-icon, [data-mat-icon-name], [fonticon]').forEach((ic) => {
      parts.push(ic.textContent || '', ic.getAttribute('data-mat-icon-name') || '',
                 ic.getAttribute('fonticon') || '');
    });
    return parts.join(' ');
  };
  const isSend = (node) => {
    if (!node || chromeOnly(labelOf(node))) return false;
    const label = labelOf(node);
    if (node.classList.contains('send-button')) return true;
    if ((node.getAttribute('data-test-id') || '') === 'send-button') return true;
    if (/^(gửi|send)$/i.test(label)) return true;
    if (/gửi tin|gửi câu lệnh|gửi prompt|send message|send prompt/i.test(label)) return true;
    if (/arrow_upward/i.test(iconText(node))) return true;
    return false;
  };
  const seen = new Set();
  const found = [];
  const sels = [
    'button.send-button',
    '[role="button"].send-button',
    '[data-test-id="send-button"]',
    'button[aria-label="Send message"]',
    'button[aria-label="Gửi tin nhắn"]',
    'button[aria-label="Gửi"]',
    'button[aria-label="Send"]',
    'button[aria-label="Gửi câu lệnh"]',
    'button[aria-label="Send prompt"]',
  ];
  for (const sel of sels) {
    for (const node of document.querySelectorAll(sel)) {
      if (seen.has(node) || !visible(node) || !isSend(node)) continue;
      seen.add(node);
      found.push(node);
    }
  }
  for (const node of document.querySelectorAll('button, [role="button"]')) {
    if (seen.has(node) || !visible(node) || !isSend(node)) continue;
    seen.add(node);
    found.push(node);
  }
  found.sort((a, b) => {
    const ba = a.getBoundingClientRect();
    const bb = b.getBoundingClientRect();
    return (bb.top - ba.top) || (bb.left - ba.left);
  });
  if (!found.length) return '';
  const node = found[0];
  if (!enabled(node)) return 'disabled';
  node.click();
  return node.classList.contains('send-button') ? 'button' : 'role';
}"""
_COMPOSER_TEXT_JS = r"""() => {
  const sels = [
    "rich-textarea div[contenteditable='true']",
    "div.ql-editor[contenteditable='true']",
    "div[role='textbox'][contenteditable='true']",
    "[aria-label*='Nhập câu lệnh' i][contenteditable='true']",
    "[aria-label*='Enter a prompt' i][contenteditable='true']",
    "textarea[aria-label]",
  ];
  let best = '', bestTop = -1;
  for (const sel of sels) {
    for (const el of document.querySelectorAll(sel)) {
      const box = el.getBoundingClientRect();
      if (box.width < 1 || box.height < 1) continue;
      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none') continue;
      const text = (el.innerText || el.value || '').trim();
      if (box.top >= bestTop && text) {
        best = text;
        bestTop = box.top;
      }
    }
  }
  return best;
}"""
_WAKE_COMPOSER_JS = r"""() => {
  const sels = [
    "rich-textarea div[contenteditable='true']",
    "div.ql-editor[contenteditable='true']",
    "div[role='textbox'][contenteditable='true']",
    "textarea[aria-label]",
  ];
  let woke = false;
  for (const sel of sels) {
    for (const el of document.querySelectorAll(sel)) {
      const box = el.getBoundingClientRect();
      if (box.width < 1 || box.height < 1) continue;
      el.classList.remove('ql-blank');
      el.dispatchEvent(new InputEvent('input', {
        bubbles: true, composed: true, inputType: 'insertFromPaste'
      }));
      el.dispatchEvent(new Event('change', {bubbles: true}));
      woke = true;
    }
  }
  return woke;
}"""
_USER_QUERY_JS = r"""() => {
  const sels = [
    'user-query',
    '[data-message-author-role="user"]',
    '.query-text',
    '.user-query-bubble-with-background',
  ];
  const texts = [];
  for (const sel of sels) {
    const nodes = document.querySelectorAll(sel);
    if (!nodes.length) continue;
    for (const el of nodes) {
      const box = el.getBoundingClientRect();
      if (box.width < 1 && box.height < 1) continue;
      const t = (el.innerText || '').trim();
      if (t) texts.push(t);
    }
    if (texts.length) break;
  }
  return texts.slice(-4);
}"""
# Gemini can retain an empty/hidden scroller while mounting the conversation in
# another root. Never fall back to document.body (sidebar/prompt contamination).
_CONV_ROOT_JS = r"""(() => {
  const selectors = [
    'infinite-scroller[data-test-id="chat-history-container"]',
    'chat-window-content', 'infinite-scroller.chat-history', 'chat-window', '#chat-history'
  ];
  const candidates = [...new Set(selectors.flatMap(s => Array.from(document.querySelectorAll(s))))];
  const visible = el => {
    for(let n=el;n;n=n.parentElement){
      const s=getComputedStyle(n);
      if(s.display==='none'||s.visibility==='hidden'||n.getAttribute('aria-hidden')==='true') return false;
    }
    return true;
  };
  const live = candidates.filter(visible);
  return live.find(el => el.querySelector('user-query,model-response,pending-response'))
      || live[0] || null;
})()"""

# Verified on live Gemini: pending-response precedes model-response. During
# streaming innerText can be empty while textContent already contains JSON.
# user-query / model-response are siblings under a wrapper div inside
# infinite-scroller. Mounted count is a virtualizer window, not identity.
_CONV_SNAPSHOT_JS = r"""() => {
  const MARK = '__GEMINI_CONV_SNAPSHOT__';
  const readPair = (el) => {
    if (!el) return {inner: '', content: '', text: ''};
    let inner = '', content = '';
    try { inner = (el.innerText || '').trim(); } catch (e) {}
    try { content = (el.textContent || '').trim(); } catch (e) {}
    const text = (content && (!inner || (content.length > inner.length && content.indexOf('{') >= 0)))
      ? content : (inner || content);
    return {inner, content, text};
  };
  const root = __CONV_ROOT__;
  const scope = root || document.createElement('div');
  const qsa = (sel, r) => {
    try { return Array.from((r || scope).querySelectorAll(sel)); }
    catch (e) { return []; }
  };
  const stopRe = /stop generating|stop response|stop streaming|ngừng tạo câu trả lời|ngừng tạo|dừng tạo|dừng phản hồi/i;
  let generating = !!(scope.querySelector('pending-response')
    || scope.querySelector('thinking-dots-animation'));
  if (!generating) {
    const nodes = document.querySelectorAll('button, [role="button"]');
    for (const node of nodes) {
      const label = ((node.getAttribute('aria-label') || '') + ' '
                     + (node.innerText || '')).trim();
      if (!stopRe.test(label)) continue;
      const box = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      if (box.width > 1 && box.height > 1
          && style.visibility !== 'hidden'
          && style.display !== 'none'
          && style.opacity !== '0') {
        generating = true;
        break;
      }
    }
  }
  const userSels = [
    'user-query',
    '[id^="user-query-content-"]',
    '[data-message-author-role="user"]',
    '.query-text',
    '.user-query-bubble-with-background',
  ];
  const preferSels = [
    '[id^="message-content-id-r_"]',
    '[id^="model-response-message-content"]',
    'div.markdown.markdown-main-panel',
    'message-content',
    'model-response-content',
    'div.markdown',
    'structured-content-container.model-response-text',
    '.model-response-text',
  ];
  const pickText = (host) => {
    if (host.tagName.toLowerCase() === 'pending-response') return '';
    for (const sel of preferSels) {
      let inner = null;
      try { inner = host.querySelector(sel); } catch (e) { inner = null; }
      if (!inner) continue;
      const p = readPair(inner);
      if (!p.text) continue;
      if (p.text.indexOf('{') >= 0) return p.text;
      const numbered = [];
      inner.querySelectorAll('ol').forEach(ol => {
        const start = parseInt(ol.getAttribute('start') || '1', 10);
        ol.querySelectorAll(':scope > li').forEach((li, i) =>
          numbered.push((start + i) + '. ' + (li.innerText || li.textContent || '').trim()));
      });
      return numbered.length ? numbered.join('\n') : p.text;
    }
    // A completed refusal may be plain text directly inside model-response.
    // Extract only that model host, stripping controls/thoughts, never the root.
    if (host.matches('model-response, [data-message-author-role="model"]')) {
      const copy = host.cloneNode(true);
      copy.querySelectorAll('message-actions,button,copy-button,thumb-up-button,thinking-content,script,style').forEach(n=>n.remove());
      return (copy.textContent || '').trim();
    }
    const hostPair = readPair(host);
    if (hostPair.text && hostPair.text.indexOf('{') >= 0) return hostPair.text;
    return '';
  };
  const userRec = (el, seqIndex) => {
    const contentEl = el.querySelector && el.querySelector('[id^="user-query-content-"]');
    const p = readPair(contentEl || el);
    const id = el.id || (contentEl && contentEl.id) || '';
    return {
      kind: 'user',
      tag: (el.tagName || '').toLowerCase(),
      id,
      seq_index: seqIndex,
      fp: String(seqIndex) + ':' + p.text.length + ':' + p.text.slice(-48),
      chars: p.text.length,
      preview: p.text.slice(0, 80),
      text: p.text,
    };
  };
  const modelRec = (host, seqIndex) => {
    const text = pickText(host);
    const tag = (host.tagName || '').toLowerCase();
    const contentEl = host.querySelector && (
      host.querySelector('[id^="message-content-id-r_"]')
      || host.querySelector('[id^="model-response-message-content"]'));
    const mid = host.id || (contentEl && contentEl.id) || '';
    const busy = (host.getAttribute && host.getAttribute('aria-busy') === 'true')
      || (contentEl && contentEl.getAttribute && contentEl.getAttribute('aria-busy') === 'true');
    return {
      kind: 'model',
      tag,
      id: mid,
      index: seqIndex,
      seq_index: seqIndex,
      fp: mid ? 'id:' + mid : tag + '@' + seqIndex,
      chars: (text || '').length,
      preview: (text || '').slice(0, 80),
      text: text || '',
      has_actions: !!(host.querySelector
        && host.querySelector('message-actions, copy-button, thumb-up-button')),
      pending: tag === 'pending-response',
      busy: !!busy,
    };
  };
  const collect = [];
  const visit = (el) => {
    if (!el) return;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'user-query' || tag === 'model-response' || tag === 'pending-response') {
      collect.push(el);
      return;
    }
    const kids = el.children || [];
    for (let i = 0; i < kids.length; i++) visit(kids[i]);
  };
  visit(scope);
  if (!collect.length) {
    let hosts = qsa('model-response, pending-response', scope);
    if (!hosts.length) {
      const fallbackSels = [
        'model-response-content',
        'message-content',
        '[id^="message-content-id-r_"]',
        '[id^="model-response-message-content"]',
        'structured-content-container.model-response-text',
        '.model-response-text',
        'response-container',
        '[data-message-author-role="model"]',
        '[data-message-author="model"]',
      ];
      for (const sel of fallbackSels) {
        hosts = qsa(sel, scope);
        if (hosts.length) break;
      }
    }
    hosts.forEach((host) => collect.push(host));
    qsa('user-query', scope).forEach((el, i) => collect.splice(i, 0, el));
  }
  const sequence = collect.map((el, i) => {
    const tag = (el.tagName || '').toLowerCase();
    return tag === 'user-query' ? userRec(el, i) : modelRec(el, i);
  });
  const users = sequence.filter((x) => x.kind === 'user');
  const models = sequence.filter((x) => x.kind === 'model');
  const userEls = collect.filter((el) => (el.tagName || '').toLowerCase() === 'user-query');
  const modelEls = collect.filter((el) => {
    const tag = (el.tagName || '').toLowerCase();
    return tag === 'model-response' || tag === 'pending-response';
  });
  const nextModelEl = (userEl) => {
    let sib = userEl.nextElementSibling;
    while (sib) {
      const tag = (sib.tagName || '').toLowerCase();
      if (tag === 'user-query') break;
      if (tag === 'model-response' || tag === 'pending-response') return sib;
      try {
        const inner = sib.querySelector('model-response, pending-response');
        if (inner) return inner;
      } catch (e) {}
      sib = sib.nextElementSibling;
    }
    const parent = userEl.parentElement;
    sib = parent ? parent.nextElementSibling : null;
    while (sib) {
      const tag = (sib.tagName || '').toLowerCase();
      if (tag === 'user-query') break;
      if (tag === 'model-response' || tag === 'pending-response') return sib;
      try {
        const inner = sib.querySelector('model-response, pending-response');
        if (inner) return inner;
      } catch (e) {}
      sib = sib.nextElementSibling;
    }
    return null;
  };
  const turns = [];
  userEls.forEach((el, ui) => {
    const rec = userRec(el, collect.indexOf(el));
    let modelEl = null;
    if (userEls.length === modelEls.length) {
      modelEl = modelEls[ui] || null;
    } else {
      modelEl = nextModelEl(el);
    }
    turns.push({
      user: rec,
      model: modelEl ? modelRec(modelEl, collect.indexOf(modelEl)) : null,
    });
  });
  const selector_counts = {};
  const countSels = userSels.concat([
    'model-response', 'model-response-content', 'message-content',
    '[id^="message-content-id-r_"]', '[id^="model-response-message-content"]',
    'response-container', 'pending-response', 'pending-request',
    'div.markdown', '.model-response-text',
  ]);
  for (const sel of countSels) selector_counts[sel] = qsa(sel, scope).length;
  let scroll_top = 0, scroll_height = 0;
  if (root) {
    try { scroll_top = root.scrollTop || 0; } catch (e) {}
    try { scroll_height = root.scrollHeight || 0; } catch (e) {}
  }
  return {
    mark: MARK,
    root: root ? ((root.tagName || '').toLowerCase() + ':'
      + (root.getAttribute('data-test-id') || root.id || '')) : '',
    user_count: users.length,
    model_count: models.filter((m) => !m.pending).length,
    pending: !!scope.querySelector('pending-response'),
    generating,
    users,
    models,
    turns,
    sequence,
    scroll_top,
    scroll_height,
    selector_counts,
    root_candidates: Array.from(document.querySelectorAll(
      'infinite-scroller,chat-window-content,chat-window,#chat-history')).map(el=>({
        tag:el.tagName.toLowerCase(),selected:el===root,
        users:el.querySelectorAll('user-query').length,
        models:el.querySelectorAll('model-response').length,
        connected:el.isConnected,
        display:getComputedStyle(el).display,
    })),
  };
}"""
# Empty host after the model actually generated: fail fast. Empty host with no
# generating flag is a placeholder / virtualized turn — wait for first token.
_EMPTY_AFTER_GENERATION_S = 12.0
_ASK_START_TIMEOUT_S = 90.0
_REVEAL_LATEST_JS = r"""() => {
  const MARK = '__GEMINI_REVEAL_LATEST__';
  const root = __CONV_ROOT__;
  if (!root) return {ok: false, mark: MARK};
  const nodes = root.querySelectorAll(
    'user-query, model-response, pending-response');
  const last = nodes[nodes.length - 1];
  try {
    if (last && last.scrollIntoView) {
      last.scrollIntoView({block: 'end', inline: 'nearest'});
    }
  } catch (e) {}
  let el = last || root;
  while (el) {
    try { el.scrollTop = el.scrollHeight; } catch (e) {}
    el = el.parentElement;
  }
  try { root.scrollTop = root.scrollHeight; } catch (e) {}
  return {ok: true, mark: MARK, nodes: nodes.length};
}"""
_TURN_WATCH_INSTALL_JS = r"""(needle) => {
  const root = __CONV_ROOT__;
  if (window.__AUTODUB_TURN_OBS) {
    try { window.__AUTODUB_TURN_OBS.disconnect(); } catch (e) {}
  }
  const readPair = (el) => {
    if (!el) return '';
    let inner = '', content = '';
    try { inner = (el.innerText || '').trim(); } catch (e) {}
    try { content = (el.textContent || '').trim(); } catch (e) {}
    return (content && (!inner || (content.length > inner.length && content.indexOf('{') >= 0)))
      ? content : (inner || content);
  };
  const pickText = (host) => {
    if (!host || (host.tagName || '').toLowerCase() === 'pending-response') return '';
    const sels = [
      '[id^="message-content-id-r_"]',
      '[id^="model-response-message-content"]',
      'div.markdown.markdown-main-panel',
      'message-content',
      'div.markdown',
      '.model-response-text',
    ];
    for (const sel of sels) {
      const inner = host.querySelector(sel);
      if (!inner) continue;
      const text = readPair(inner);
      if (text) return text;
    }
    if (host.matches('model-response, [data-message-author-role="model"]')) {
      const copy = host.cloneNode(true);
      copy.querySelectorAll('message-actions,button,copy-button,thumb-up-button,thinking-content,script,style').forEach(n=>n.remove());
      return (copy.textContent || '').trim();
    }
    const hostText = readPair(host);
    return (hostText && hostText.indexOf('{') >= 0) ? hostText : '';
  };
  const scan = () => {
    const state = window.__AUTODUB_TURN || {};
    const scope = __CONV_ROOT__ || document.createElement("div");
    const users = Array.from(scope.querySelectorAll('user-query'));
    let userEl = null;
    for (let i = users.length - 1; i >= 0; i--) {
      const text = readPair(users[i]);
      const normalized=text.replace(/\s+/g, ' ').trim();
      const retryMarker=t=>['TASK_CLARIFICATION:','FORMAT_REPAIR:','Sửa lỗi validation:'].find(m=>t.includes(m))||'';
      if (needle && normalized.includes(needle) && retryMarker(normalized)===retryMarker(needle)) {
        userEl = users[i];
        break;
      }
    }
    // Preserve only an observed completed answer for this exact prompt when
    // Gemini unmounts its turn. Never freeze partial JSON or thinking text.
    if(!userEl && state.completed) return state;
    state.user_found = !!userEl;
    let modelEl = null;
    if (userEl) {
      let sib = userEl.nextElementSibling;
      while (sib) {
        const tag = (sib.tagName || '').toLowerCase();
        if (tag === 'user-query') break;
        if (tag === 'model-response' || tag === 'pending-response') { modelEl = sib; break; }
        const inner = sib.querySelector && sib.querySelector('model-response, pending-response');
        if (inner) { modelEl = inner; break; }
        sib = sib.nextElementSibling;
      }
      if (!modelEl) {
        const all = Array.from(scope.querySelectorAll('user-query, model-response, pending-response'));
        const usersOnly = all.filter((el) => (el.tagName || '').toLowerCase() === 'user-query');
        const modelsOnly = all.filter((el) => {
          const tag = (el.tagName || '').toLowerCase();
          return tag === 'model-response' || tag === 'pending-response';
        });
        const ui = usersOnly.indexOf(userEl);
        if (ui >= 0 && usersOnly.length === modelsOnly.length) modelEl = modelsOnly[ui];
      }
    }
    state.model_pending = !!(modelEl && (modelEl.tagName || '').toLowerCase() === 'pending-response');
    state.model_found = !!(modelEl && !state.model_pending);
    state.text = modelEl ? pickText(modelEl) : '';
    state.generating = !!(scope.querySelector('pending-response, thinking-dots-animation'));
    state.completed = !!(state.text && modelEl && !state.generating &&
      modelEl.querySelector('message-actions,copy-button,thumb-up-button'));
    window.__AUTODUB_TURN = state;
    return state;
  };
  const state = {
    needle: needle || '',
    user_found: false,
    model_found: false,
    model_pending: false,
    text: '',
    generating: false,
    mutations: 0,
  };
  window.__AUTODUB_TURN = state;
  scan();
  if (!root) return state;
  const obs = new MutationObserver(() => {
    state.mutations += 1;
    scan();
  });
  obs.observe(document.body, {subtree: true, childList: true, characterData: true});
  window.__AUTODUB_TURN_OBS = obs;
  return state;
}"""
# Shared root policy for polling, scroll recovery and the mutation observer.
_CONV_SNAPSHOT_JS = _CONV_SNAPSHOT_JS.replace("__CONV_ROOT__", _CONV_ROOT_JS)
_REVEAL_LATEST_JS = _REVEAL_LATEST_JS.replace("__CONV_ROOT__", _CONV_ROOT_JS)
_TURN_WATCH_INSTALL_JS = _TURN_WATCH_INSTALL_JS.replace("__CONV_ROOT__", _CONV_ROOT_JS)
_TURN_WATCH_READ_JS = r"""() => window.__AUTODUB_TURN || {}"""
_DEAD_PAGE_TOKENS = (
    "target closed", "target page", "has been closed",
    "browser has been closed", "protocol error", "connection closed",
)
# Không dùng aria-label*='Stop' / 'Dừng' trần: khớp nhầm nút khác trên trang
# rồi vòng chờ tưởng Gemini vẫn đang viết dù JSON đã hiện đủ.
_STOP_CANDIDATES = [
    "button[aria-label*='Stop generating' i]",
    "button[aria-label*='Stop response' i]",
    "button[aria-label*='Stop streaming' i]",
    "button[aria-label*='Ngừng tạo câu trả lời' i]",
    "button[aria-label*='Ngừng tạo' i]",
    "button[aria-label*='Dừng tạo' i]",
    "button[aria-label*='Dừng phản hồi' i]",
    "[data-test-id='stop-icon-button']",
]
_STOP_LABEL_JS = r"""() => {
  if (document.querySelector('pending-response, thinking-dots-animation')) return true;
  const re = /stop generating|stop response|stop streaming|ngừng tạo câu trả lời|ngừng tạo|dừng tạo|dừng phản hồi/i;
  const nodes = document.querySelectorAll('button, [role="button"]');
  for (const node of nodes) {
    const label = ((node.getAttribute('aria-label') || '') + ' '
                   + (node.innerText || '')).trim();
    if (!re.test(label)) continue;
    const box = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    if (box.width > 1 && box.height > 1
            && style.visibility !== 'hidden'
            && style.display !== 'none'
            && style.opacity !== '0') {
      return true;
    }
  }
  return false;
}"""
_PHAN_TICH_EASE_RE = re.compile(
    r'"production_ease"\s*:\s*"(Cao|Trung|Thấp)"')
_PHAN_TICH_RECO_RE = re.compile(
    r'"recommendation"\s*:\s*"(Nên làm|Chờ đợi|Không nên làm)"')


def _launch(p, profile_dir: str, channel: str):
    """Mở trình duyệt bền (nhớ đăng nhập). Ưu tiên Edge -> Chrome -> Chromium."""
    order = [channel] if channel else []
    order += [c for c in ("msedge", "chrome", None) if c not in order]
    last = None
    for ch in order:
        try:
            kw = dict(
                headless=False,
                args=["--start-maximized",
                      # Google hay đổi/khoá giao diện khi thấy cờ tự động hoá
                      "--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
                no_viewport=True,
            )
            if ch:
                kw["channel"] = ch
            ctx = p.chromium.launch_persistent_context(profile_dir, **kw)
            log(f"Đã mở trình duyệt: {ch or 'chromium'}", "ok")
            return ctx
        except Exception as e:
            last = e
    raise RuntimeError(
        f"Không mở được trình duyệt nào (Edge/Chrome/Chromium): {last}\n"
        "  - Nếu báo 'profile in use': đóng hết cửa sổ Edge đang mở, hoặc xoá "
        "thư mục browser_profile rồi đăng nhập lại."
    )


def _visible_locator(page, candidates: List[str], timeout: float = 30.0):
    """Trả về Locator ĐANG HIỂN THỊ đầu tiên khớp danh sách selector.

    Dùng Locator (KHÔNG dùng query_selector/ElementHandle) vì Gemini là ứng dụng
    Angular: nó dựng lại DOM sau khi trang tải xong, nên ElementHandle lấy được
    lúc trước sẽ bị 'detached' -> đúng lỗi 'Element is not attached to the DOM'.
    Locator tự tìm lại phần tử ở MỖI thao tác nên miễn nhiễm với việc này.
    """
    deadline = time.time() + timeout
    while True:
        remain_ms = max(0, int((deadline - time.time()) * 1000))
        vis_ms = 80 if timeout <= 0.05 else min(1000, max(80, remain_ms or 80))
        for sel in candidates:
            try:
                loc = page.locator(sel)
                n = loc.count()
            except Exception:
                continue
            # Có thể khớp nhiều phần tử, trong đó vài cái đang ẨN (hộp thoại
            # onboarding của Gemini chẳng hạn) -> lấy cái ĐANG HIỂN THỊ.
            for i in range(min(n, 5)):
                try:
                    item = loc.nth(i)
                    if item.is_visible(timeout=vis_ms):
                        return item
                except Exception:
                    continue
        if time.time() >= deadline:
            return None
        try:
            page.wait_for_timeout(500)
        except Exception:
            return None


_RESP_SEL_CHOSEN: Dict[int, str] = {}


def _resp_locator(page):
    """Locator trỏ tới các khối TRẢ LỜI của model (không tính câu mình gửi).

    Chọn được selector nào rồi thì DÙNG MÃI selector đó: nếu mỗi lần gọi lại
    chọn một selector khác, số khối đếm trước/sau khi gửi sẽ không so sánh được
    với nhau và vòng chờ trả lời sẽ treo cho tới hết giờ.
    """
    sel = _RESP_SEL_CHOSEN.get(id(page))
    if sel:
        return page.locator(sel)
    for s in _RESP_CANDIDATES:
        try:
            if page.locator(s).count() > 0:
                _RESP_SEL_CHOSEN[id(page)] = s
                return page.locator(s)
        except Exception:
            continue
    return page.locator(_RESP_CANDIDATES[0])


def _is_generating(page) -> bool:
    try:
        if page.evaluate(_STOP_LABEL_JS):
            return True
    except Exception:
        pass
    for sel in _STOP_CANDIDATES:
        try:
            if page.locator(sel).first.is_visible(timeout=80):
                return True
        except Exception:
            continue
    return False


def _page_dead(page) -> bool:
    try:
        closer = getattr(page, "is_closed", None)
        if callable(closer) and closer():
            return True
    except Exception:
        return True
    return False


def _probe_cuoi(msg: str) -> str:
    return (str(msg or "").strip().split("\n")[-1] or "")[:24]


def _composer_text(page) -> str:
    try:
        return str(page.evaluate(_COMPOSER_TEXT_JS) or "").strip()
    except Exception:
        return ""


def _wake_composer(page) -> None:
    """Quill/Angular chỉ bật nút Gửi sau sự kiện input; insert_text đôi khi thiếu."""
    try:
        page.evaluate(_WAKE_COMPOSER_JS)
    except Exception:
        pass


def _user_queries(page) -> List[str]:
    try:
        rows = page.evaluate(_USER_QUERY_JS) or []
    except Exception:
        return []
    if isinstance(rows, str):
        return [rows] if rows.strip() else []
    return [str(x).strip() for x in rows if str(x).strip()]


def _user_has_prompt(page, msg: str) -> bool:
    probe = _probe_cuoi(msg)
    if not probe:
        return False
    return any(probe in text for text in _user_queries(page))


def _van_con_trong_o_nhap(page, msg: str) -> bool:
    """True khi ô nhập vẫn còn đúng tin vừa gõ — tức là chưa gửi đi."""
    probe = _probe_cuoi(msg)
    cur = _composer_text(page)
    if not cur or not probe:
        return False
    return probe in cur and len(cur) >= min(40, max(8, int(len(msg) * 0.25)))


def _nut_gui_con_bam_duoc(page) -> bool:
    btn = _visible_locator(page, _SEND_CANDIDATES, timeout=0)
    if btn is None:
        return False
    try:
        return bool(btn.is_enabled(timeout=200))
    except Exception:
        return False


def _doi_nut_gui(page, timeout: float = 2.5) -> bool:
    steps = max(1, int(max(0.2, float(timeout)) / 0.15))
    for _ in range(steps):
        if _nut_gui_con_bam_duoc(page):
            return True
        try:
            page.wait_for_timeout(150)
        except Exception as exc:
            if _page_dead(page) or not _transient_wait_error(exc):
                return False
    return _nut_gui_con_bam_duoc(page)


def _da_gui(page, msg: str) -> bool:
    """Ack gửi: đang generate, hoặc user bubble chứa fingerprint prompt."""
    if _is_generating(page):
        return True
    if _user_has_prompt(page, msg):
        return True
    return False


def _empty_snap() -> Dict:
    return {
        "mark": "__GEMINI_CONV_SNAPSHOT__",
        "root": "",
        "user_count": 0,
        "model_count": 0,
        "pending": False,
        "generating": False,
        "users": [],
        "models": [],
        "turns": [],
        "sequence": [],
        "scroll_top": 0,
        "scroll_height": 0,
        "selector_counts": {},
    }


def _normalize_prompt(msg: str) -> str:
    return re.sub(r"\s+", " ", str(msg or "").strip())


def prompt_hash(msg: str) -> str:
    return hashlib.sha256(_normalize_prompt(msg).encode("utf-8")).hexdigest()[:16]


# Shared last-line tails (TASK_CLARIFICATION / JSON closers) must not identify a turn.
_RETRY_MARKERS = ("TASK_CLARIFICATION:", "FORMAT_REPAIR:", "Sửa lỗi validation:")


def _prompt_body_and_marker(msg: str):
    text = str(msg or "")
    marker = ""
    cut = len(text)
    for item in _RETRY_MARKERS:
        idx = text.find("\n" + item)
        if idx < 0 and text.startswith(item):
            idx = 0
        if 0 <= idx < cut:
            cut = idx
            marker = item
    return text[:cut], marker


def _payload_needle(body: str) -> str:
    raw = str(body or "")
    idx = raw.rfind("INPUT_JSON:")
    payload = raw[idx + len("INPUT_JSON:"):] if idx >= 0 else raw
    payload = _normalize_prompt(payload)
    if len(payload) >= 16:
        return payload[-80:] if len(payload) >= 80 else payload
    normalized = _normalize_prompt(raw)
    return normalized[-80:] if len(normalized) >= 16 else normalized


def _user_retry_marker(user: str) -> str:
    for item in _RETRY_MARKERS:
        if item.rstrip(":") in user:
            return item
    return ""


def _prompt_identity_needle(msg: str) -> str:
    # Context-after and retry suffixes are shared by different batches. The
    # observer must use the same full identity as the polling/duplicate guard.
    return _normalize_prompt(msg)


def _prompt_matches_user(msg: str, user_text: str) -> bool:
    prompt = _normalize_prompt(msg)
    user = _normalize_prompt(user_text)
    if not prompt or not user:
        return False
    if prompt == user:
        return True
    # Allow only surrounding UI labels, never a shared suffix or partial prompt.
    # A clipped bubble cannot establish identity; wait for its full textContent.
    return prompt in user and _user_retry_marker(user) == _prompt_body_and_marker(msg)[1]


def _find_user_turn(snap: Dict, msg: str) -> Optional[Dict]:
    matches = [user for user in (snap.get("users") or [])
               if _prompt_matches_user(msg, user.get("text") or "")]
    return matches[-1] if matches else None


def _associated_model(snap: Dict, user: Optional[Dict]) -> Optional[Dict]:
    if not user:
        return None
    for turn in (snap.get("turns") or []):
        turn_user = turn.get("user") or {}
        if (turn_user.get("seq_index") is not None
                and user.get("seq_index") is not None
                and turn_user.get("seq_index") == user.get("seq_index")):
            return turn.get("model")
    for turn in (snap.get("turns") or []):
        turn_user = turn.get("user") or {}
        if _prompt_matches_user(user.get("text") or "", turn_user.get("text") or ""):
            return turn.get("model")
    index = user.get("seq_index")
    if index is None:
        return None
    later = [model for model in (snap.get("models") or [])
             if (model.get("seq_index") if model.get("seq_index") is not None
                 else model.get("index", -1)) > index]
    return later[0] if later else None


def _current_turn(snap: Dict, msg: str) -> Optional[Dict]:
    user = _find_user_turn(snap, msg)
    if not user:
        return None
    return {"user": user, "model": _associated_model(snap, user)}


def _turn_generating(snap: Dict, turn: Optional[Dict]) -> bool:
    if not turn:
        return False
    model = turn.get("model") or {}
    if model.get("pending") or model.get("busy"):
        return True
    if not (model.get("text") or "").strip() and (snap.get("generating") or snap.get("pending")):
        return True
    if model.get("has_actions") and (model.get("text") or "").strip():
        return False
    return bool(snap.get("generating") or snap.get("pending"))


def _turn_text(turn: Optional[Dict]) -> str:
    model = (turn or {}).get("model") or {}
    if model.get("pending"):
        return ""
    return str(model.get("text") or "").strip()


def _existing_turn_action(snap: Dict, msg: str) -> str:
    turn = _current_turn(snap, msg)
    if not turn:
        return "send"
    text = _turn_text(turn)
    if text and not _turn_generating(snap, turn):
        return "consume"
    return "wait"


def _gemini_log(msg: str) -> None:
    log("[GEMINI] " + str(msg), "info")


def _snapshot(page) -> Dict:
    """Conversation snapshot from JS. Never uses Playwright inner_text actionability."""
    data = page.evaluate(_CONV_SNAPSHOT_JS)
    if not isinstance(data, dict) or data.get("mark") != "__GEMINI_CONV_SNAPSHOT__":
        raise RuntimeError("invalid Gemini conversation snapshot")
    if not data.get("root"):
        raise RuntimeError("Gemini conversation root not found")
    return data


def _snapshot_via_locators(page) -> Dict:
    snap = _empty_snap()
    snap["generating"] = _is_generating(page)
    users = _user_queries(page)
    snap["users"] = [
        {"text": t, "chars": len(t), "preview": t[:80], "fp": t[:48], "tag": "user", "id": ""}
        for t in users
    ]
    snap["user_count"] = len(snap["users"])
    models: List[Dict] = []
    counts: Dict[str, int] = {}
    for sel in list(_RESP_CANDIDATES) + list(_RESP_FALLBACK):
        try:
            loc = page.locator(sel)
            n = int(loc.count())
        except Exception:
            n = 0
        counts[sel] = n
        if n <= 0 or models:
            continue
        for i in range(n):
            try:
                text = (_reply_text(loc.nth(i)) or "").strip()
            except Exception:
                text = ""
            models.append({
                "tag": sel, "id": "", "index": i,
                "fp": "%s@%d" % (sel, i), "chars": len(text),
                "preview": text[:80], "text": text,
                "has_actions": False, "pending": False,
            })
    snap["models"] = models
    snap["model_count"] = len([m for m in models if not m.get("pending")])
    snap["selector_counts"] = counts
    return snap


def _before_from_legacy(baseline: Optional[Dict[str, int]], prev_count: int,
                        last_before: str = "") -> Dict:
    snap = _empty_snap()
    counts = dict(baseline or {})
    snap["selector_counts"] = counts
    n = 0
    if counts:
        n = max(int(v or 0) for v in counts.values())
    n = max(n, int(prev_count or 0))
    last_before = str(last_before or "")
    if last_before:
        snap["models"] = [{
            "tag": "model-response", "id": "", "index": 0,
            "fp": "legacy-last", "chars": len(last_before),
            "preview": last_before[:80], "text": last_before,
            "has_actions": False, "pending": False,
        }]
        snap["model_count"] = max(1, n)
    elif n:
        snap["models"] = [{
            "tag": "model-response", "id": "", "index": i,
            "fp": "legacy-%d" % i, "chars": 0, "preview": "", "text": "",
            "has_actions": False, "pending": False,
        } for i in range(n)]
        snap["model_count"] = n
    return snap


def _new_models(now: Dict, before: Dict) -> List[Dict]:
    previous = list(before.get("models") or [])
    models = list(now.get("models") or [])
    fingerprints = {m.get("fp") for m in previous}
    # IDs survive Angular replacement and distinguish identical answer text.
    return [m for i, m in enumerate(models)
            if (m.get("id") and m.get("fp") not in fingerprints)
            or (not m.get("id") and i >= len(previous))]


def has_new_response(now: Dict, before: Dict) -> bool:
    """Node detection is independent of text extraction and JSON parsing."""
    return bool(_new_models(now or {}, before or {}))


def extract_response_text(now: Dict, before: Optional[Dict] = None,
                          last_before: str = "", msg: str = "") -> str:
    """Read the model node paired with the current prompt, not models[-1]."""
    if msg:
        return _turn_text(_current_turn(now or {}, msg))
    models = [m for m in _new_models(now or {}, before or {}) if not m.get("pending")]
    return str(models[-1].get("text") or "").strip() if models else ""


def _dem_khoi_tra_loi(page) -> Dict[str, int]:
    snap = _snapshot(page)
    counts = dict(snap.get("selector_counts") or {})
    if counts:
        return counts
    for sel in list(_RESP_CANDIDATES) + list(_RESP_FALLBACK):
        try:
            counts[sel] = int(page.locator(sel).count())
        except Exception:
            counts[sel] = 0
    return counts


def _thu_thap_tra_loi(page, baseline: Optional[Dict[str, int]] = None,
                      chi_moi: bool = False,
                      before_snap: Optional[Dict] = None) -> List[str]:
    """Lấy text các khối trả lời mới; không đọc sidebar."""
    now = _snapshot(page)
    before = before_snap or _before_from_legacy(baseline or {}, 0, "")
    text = extract_response_text(now, before)
    if text:
        return [text]
    if chi_moi:
        return []
    found = []
    seen = set()
    for model in (now.get("models") or []):
        raw = str(model.get("text") or "").strip()
        if raw and raw not in seen:
            seen.add(raw)
            found.append(raw)
    return found


def _chon_tra_loi(candidates: List[str], previous: str = "") -> str:
    """Ưu tiên JSON phân tích đã khép, không lấy đề bài (schema) dù dài hơn."""
    if not candidates:
        return previous
    best = previous
    best_score = (-1, -1, -1, -1)
    for index, text in enumerate(candidates):
        raw = str(text or "").strip()
        if not raw:
            continue
        score = (
            1 if _json_phan_tich_du(raw) else 0,
            1 if ("{" in raw and not _json_ngoac_chua_khop(raw)) else 0,
            index,
            len(raw),
        )
        if score >= best_score:
            best_score, best = score, raw
    return best or previous


def _clear_composer(page, box) -> None:
    box.click(timeout=15000)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.wait_for_timeout(150)


def _put_text(page, box, msg: str) -> bool:
    """Đưa cả khối text vào ô soạn thảo rồi KIỂM CHỨNG là nó đã vào thật."""
    probe = (msg.strip().split("\n")[-1] or "")[:24]

    def landed() -> bool:
        try:
            cur = (box.inner_text() or "") or _composer_text(page)
        except Exception:
            cur = _composer_text(page)
        return len(cur) >= len(msg) * 0.5 and (not probe or probe in cur)

    def settle() -> bool:
        _wake_composer(page)
        page.wait_for_timeout(350)
        return landed()

    _clear_composer(page, box)
    try:
        box.fill(msg, timeout=15000)
        if settle():
            return True
    except Exception:
        pass

    _clear_composer(page, box)
    page.keyboard.insert_text(msg)
    if settle():
        return True

    _clear_composer(page, box)
    lines = msg.split("\n")
    for k, line in enumerate(lines):
        if line:
            page.keyboard.insert_text(line)
        if k < len(lines) - 1:
            page.keyboard.press("Shift+Enter")
    return settle()


def _bam_gui_js(page) -> str:
    try:
        return str(page.evaluate(_SEND_CLICK_JS) or "").strip()
    except Exception:
        return ""


def _submit(page) -> None:
    """Bấm đúng nút Gửi (mũi tên). Không bấm Flash/Tools. Enter chỉ xuống dòng."""
    result = _bam_gui_js(page)
    if result in {"button", "role", "icon"}:
        return
    btn = _visible_locator(page, _SEND_CANDIDATES, timeout=0.8)
    if btn is not None:
        try:
            if btn.is_enabled(timeout=800):
                btn.click(timeout=5000, force=True)
                return
        except Exception:
            pass
    try:
        box = _visible_locator(page, _INPUT_CANDIDATES, timeout=0.6)
        if box is not None:
            box.press("Control+Enter")
            return
    except Exception:
        pass
    try:
        page.keyboard.press("Control+Enter")
    except Exception:
        page.keyboard.press("Enter")


def _gui_va_xac_nhan(page, msg: str) -> bool:
    """WAIT_FOR_SEND_ACK ngắn. Không nhảy sang chờ trả lời 4 phút nếu chưa gửi."""
    _wake_composer(page)
    _doi_nut_gui(page, 2.5)
    had = _van_con_trong_o_nhap(page, msg)
    for _try in range(4):
        if _da_gui(page, msg):
            return True
        _submit(page)
        released = 0
        for _poll in range(12):
            if _da_gui(page, msg):
                return True
            # Composer rỗng không đủ để ACK. Chỉ dùng khi nút Gửi cũng tắt
            # (tin đã rời ô soạn) — vẫn ưu tiên generating / user bubble.
            if (had and not _van_con_trong_o_nhap(page, msg)
                    and not _nut_gui_con_bam_duoc(page)):
                released += 1
                if released >= 4:
                    return True
            try:
                page.wait_for_timeout(250)
            except Exception as exc:
                if _page_dead(page) or not _transient_wait_error(exc):
                    return False
        _wake_composer(page)
        _doi_nut_gui(page, 1.2)
    return _da_gui(page, msg)


def _transient_wait_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    if any(token in message for token in _DEAD_PAGE_TOKENS):
        return False
    return True


# BẪY LỚN: Gemini render "1. abc" của markdown thành <ol><li>abc</li>...
# Số thứ tự lúc đó là ::marker do CSS sinh ra, KHÔNG nằm trong text - nên
# innerText trả về "abc" trơ trọi, mất sạch số. Phải dựng lại số từ thẻ <ol>.
_JS_EXTRACT = """
el => {
  const out = [];
  el.querySelectorAll('ol').forEach(ol => {
    const start = parseInt(ol.getAttribute('start') || '1', 10) || 1;
    let i = 0;
    ol.querySelectorAll(':scope > li').forEach(li => {
      out.push((start + i) + '. ' + (li.innerText || '').trim().replace(/\\s*\\n\\s*/g, ' '));
      i++;
    });
  });
  return out.length ? out.join('\\n') : (el.innerText || '');
}
"""


def _reply_text(item) -> str:
    """Lấy nội dung câu trả lời, GIỮ ĐƯỢC số thứ tự của danh sách đánh số."""
    # Với câu trả lời JSON, innerText đã giữ đủ dấu ngoặc/khóa. Bộ dựng lại
    # <ol> bên dưới vốn dành cho phụ đề sẽ chỉ trả riêng các mục danh sách nếu
    # Gemini lỡ render một mảng thành <ol>, làm mất toàn bộ phần object JSON.
    try:
        plain = (item.inner_text() or "").strip()
    except Exception:
        plain = ""
    if re.search(r'\{\s*"[^"\n]+"\s*:', plain):
        return plain
    try:
        return (item.evaluate(_JS_EXTRACT) or plain).strip()
    except Exception:
        return plain


def _json_ngoac_chua_khop(text: str) -> bool:
    """True khi object/mảng JSON còn dang dở ngoặc hoặc chuỗi."""
    raw = str(text or "").strip()
    if not raw:
        return False
    start = raw.find("{")
    if start < 0:
        start = raw.find("[")
        if start < 0:
            return False
    depth = 0
    in_string = False
    escaped = False
    saw_open = False
    for char in raw[start:]:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char in "{[":
            depth += 1
            saw_open = True
        elif char in "}]":
            if depth:
                depth -= 1
    return saw_open and (in_string or depth > 0)


def _json_con_do_dang(text: str) -> bool:
    """True khi câu trả lời trông như JSON nhưng ngoặc/{chuỗi} chưa khép.

    Gemini Web hay tắt nút Stop giữa chừng vài giây trong lúc vẫn đang viết.
    Nếu lấy lúc đó, kho ý tưởng nhận JSON cụt rồi phải hỏi lại / rơi offline.

    Fence markdown ```json không khép KHÔNG tính là còn viết nếu ngoặc JSON
    đã đủ: UI hay thay fence bằng nút Copy nên innerText mất dấu đóng.
    """
    return _json_ngoac_chua_khop(text)


def _co_nhieu_dong_danh_so(text: str, toi_thieu: int = 4) -> bool:
    """Bản dịch/prompt cảnh đánh số đã đủ dòng — nhận sớm, không chờ UI gắn gợi ý."""
    return sum(1 for line in str(text or "").splitlines()
               if _SO_DONG_RE.match(line)) >= max(1, int(toi_thieu))


def _json_phan_tich_du(text: str) -> bool:
    """True khi JSON phân tích ý tưởng đã có trường kết luận thật, không phải schema đề bài."""
    raw = str(text or "")
    if _json_ngoac_chua_khop(raw):
        return False
    return bool(_PHAN_TICH_EASE_RE.search(raw) or _PHAN_TICH_RECO_RE.search(raw))


class GeminiResponseError(RuntimeError):
    def __init__(self, kind: str, state: str, diagnostic: Dict):
        self.kind, self.state, self.diagnostic = kind, state, diagnostic
        self.send_ack = bool(diagnostic.get("send_ack"))
        super().__init__(f"{kind} at {state}: {diagnostic}")


_UI_ERROR_JS = r"""() => {
  for (const el of document.querySelectorAll(
      'mat-snack-bar-container, snack-bar-container, [role="alert"], .mdc-snackbar, .error-message')) {
    if (el.closest('user-query,model-response,message-content,rich-textarea,[contenteditable="true"],aside')) continue;
    const box=el.getBoundingClientRect(), style=getComputedStyle(el);
    if (!box.width || !box.height || style.display==='none' || style.visibility==='hidden') continue;
    const text=(el.innerText || '').trim();
    if (/đã xảy ra lỗi|something went wrong|an error occurred|try again later/i.test(text))
      return {text:text.slice(0,300), code:(text.match(/\((\d+)\)/)||[])[1] || ''};
  }
  return null;
}"""


def _check_ui_error(page, trace):
    evaluate = getattr(page, "evaluate", None)
    if not callable(evaluate):
        # Lightweight page adapters used by replay/qualification paths may
        # only expose snapshots and timers. UI-error probing is supplemental;
        # it must not replace the actual response state machine with an
        # AttributeError before send/ack/timeout classification can run.
        return
    error = evaluate(_UI_ERROR_JS)
    if error:
        raise GeminiResponseError("UI_ERROR_RESPONSE", trace.get("state", "UI_CHECK"),
                                  {"send_ack": trace.get("send_ack", False), **error})


def _state(trace: Dict, state: str) -> None:
    trace["state"] = state
    trace.setdefault("states", []).append(state)
    _gemini_log(state)


def _reveal_latest(page) -> None:
    """Bring the newest chat turn into the virtualizer window before snapshot."""
    try:
        page.evaluate(_REVEAL_LATEST_JS)
    except Exception:
        pass


def _watch_install(page, msg: str) -> None:
    needle = _prompt_identity_needle(msg)
    try:
        page.evaluate(_TURN_WATCH_INSTALL_JS, needle)
    except Exception:
        pass


def _watch_read(page) -> Dict:
    try:
        data = page.evaluate(_TURN_WATCH_READ_JS)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _once_state(trace: Dict, state: str) -> None:
    if state not in (trace.get("states") or []):
        _state(trace, state)


def _wait_reply(page, prev_count: int, timeout: float,
                baseline: Optional[Dict[str, int]] = None,
                last_before: str = "", prompt_probe: str = "",
                unsent_after: float = 6.0, *, before_snap: Optional[Dict] = None,
                trace: Optional[Dict] = None,
                response_start_timeout: Optional[float] = None,
                generation_timeout: Optional[float] = None,
                stability_timeout: float = 2.5,
                msg: str = "") -> str:
    """Poll fresh snapshots. Current-turn identity is prompt_hash, not counts."""
    trace = trace if trace is not None else {}
    before = before_snap if before_snap is not None else _before_from_legacy(baseline, prev_count, last_before)
    started = time.monotonic()
    total_deadline = started + max(0.0, float(timeout))
    start_deadline = min(total_deadline, started + float(
        timeout if response_start_timeout is None else response_start_timeout))
    generation_deadline = None
    stability_deadline = None
    text, changed, seen = "", started, False
    seen_at = None
    saw_generating = False
    saw_generation_signal = False
    saw_response_node = False
    response_text_seen = False
    empty_idle_at = None
    recovered = False
    snap = _empty_snap()
    if msg:
        _watch_install(page, msg)
    _state(trace, "WAITING_MODEL_RESPONSE")
    while time.monotonic() < total_deadline:
        _check_ui_error(page, trace)
        mapped = bool(msg)
        active = (saw_response_node and (response_text_seen or saw_generation_signal)
                  if mapped else seen)
        phase_deadline = generation_deadline if active else start_deadline
        if time.monotonic() >= phase_deadline:
            break
        try:
            _reveal_latest(page)
            snap = _snapshot(page)
        except Exception as exc:
            raise GeminiResponseError("RESPONSE_DETECTION_FAILURE", trace["state"],
                                      {"send_ack": trace.get("send_ack", False), "error": str(exc)[:160]}) from exc
        now = time.monotonic()
        watch = _watch_read(page) if msg else {}
        turn = _current_turn(snap, msg) if msg else None
        if turn or watch.get("user_found"):
            _once_state(trace, "CURRENT_USER_TURN_IDENTIFIED")
            if turn:
                trace["current_user_id"] = (turn.get("user") or {}).get("id") or (
                    turn.get("user") or {}).get("fp")
            trace["turn_identification_ms"] = round(
                (now - trace.get("send_triggered_at", started)) * 1000)
        generating = (_turn_generating(snap, turn) if msg
                      else bool(snap.get("generating") or snap.get("pending")))
        if watch.get("generating") or watch.get("model_pending"):
            generating = True
        if generating:
            saw_generating = True
            saw_generation_signal = True
            if msg:
                _once_state(trace, "GENERATION_SIGNAL_SEEN")
        if msg:
            model = (turn or {}).get("model")
            if (model and not model.get("pending")) or watch.get("model_found"):
                saw_response_node = True
                _once_state(trace, "MODEL_RESPONSE_NODE_IDENTIFIED")
            cur = _turn_text(turn)
            watched = str(watch.get("text") or "").strip()
            if not cur and watched:
                cur = watched
            if cur:
                response_text_seen = True
            identified = bool(turn) or bool(watch.get("user_found"))
            if ((not seen) and identified and (saw_response_node or saw_generation_signal)
                    and (cur or generating)):
                if saw_response_node or cur:
                    seen = True
                    seen_at = now
                    trace["response_start_ms"] = round(
                        (now - trace.get("send_triggered_at", started)) * 1000)
                    trace["response_started"] = True
                    generation_deadline = min(total_deadline, now + float(
                        timeout if generation_timeout is None else generation_timeout))
                    if saw_response_node:
                        _once_state(trace, "MODEL_RESPONSE_STARTED")
                    if cur:
                        _once_state(trace, "MODEL_RESPONSE_STREAMING")
        else:
            new_turn = has_new_response(snap, before)
            cur = extract_response_text(snap, before) if (new_turn or seen) else ""
            if (not seen) and new_turn and (bool((cur or "").strip()) or generating):
                seen = True
                seen_at = now
                trace["response_start_ms"] = round((now - trace.get("send_triggered_at", started)) * 1000)
                trace["response_started"] = True
                generation_deadline = min(total_deadline, now + float(
                    timeout if generation_timeout is None else generation_timeout))
                _state(trace, "MODEL_RESPONSE_STARTED")
                _state(trace, "MODEL_RESPONSE_STREAMING")
        if (seen or response_text_seen) and cur != text:
            text, changed = cur, now
            stability_deadline = now + stability_timeout
            if text:
                _once_state(trace, "MODEL_RESPONSE_STREAMING")
            _gemini_log(f"response chars={len(text)}; generating={generating}")
        turn_done = (not generating) if (msg and turn) else (not generating)
        if ((seen or response_text_seen) and text and turn_done
                and stability_deadline is not None and now >= stability_deadline):
            if msg:
                _once_state(trace, "MODEL_RESPONSE_STABLE")
            trace["generation_ms"] = round((now - started) * 1000)
            trace["response_complete"] = True
            trace["chars"] = len(text)
            _state(trace, "MODEL_RESPONSE_COMPLETE")
            _state(trace, "RESPONSE_EXTRACTED")
            return text
        idle_empty = ((saw_generation_signal if msg else (seen and saw_generating))
                      and not (text or "").strip() and not generating)
        if idle_empty:
            if empty_idle_at is None:
                empty_idle_at = now
            elif now - empty_idle_at >= _EMPTY_AFTER_GENERATION_S:
                if msg and not recovered:
                    recovered = True
                    _once_state(trace, "RECOVERY_RESCAN")
                    _reveal_latest(page)
                    try:
                        snap = _snapshot(page)
                    except Exception as exc:
                        raise GeminiResponseError(
                            "RESPONSE_DETECTION_FAILURE", trace["state"],
                            {"send_ack": trace.get("send_ack", False),
                             "error": str(exc)[:160]}) from exc
                    turn = _current_turn(snap, msg)
                    generating = _turn_generating(snap, turn)
                    cur = _turn_text(turn)
                    if cur:
                        text, changed = cur, now
                        response_text_seen = True
                        seen = True
                        empty_idle_at = None
                        stability_deadline = now + stability_timeout
                        _once_state(trace, "MODEL_RESPONSE_NODE_IDENTIFIED")
                        _once_state(trace, "MODEL_RESPONSE_STREAMING")
                        continue
                    break
                break
        else:
            empty_idle_at = None
        if (not trace.get("send_ack") and prompt_probe and not seen
                and now - started >= unsent_after
                and _van_con_trong_o_nhap(page, prompt_probe)):
            return ""
        try:
            page.wait_for_timeout(250)
        except Exception as exc:
            if _page_dead(page):
                raise GeminiResponseError("RESPONSE_DETECTION_FAILURE", trace["state"],
                                          {"send_ack": trace.get("send_ack", False), "error": str(exc)[:160]}) from exc
    diag = {"send_ack": trace.get("send_ack", False),
            "user_count": snap.get("user_count"), "model_count": snap.get("model_count"),
            "candidate_nodes": snap.get("selector_counts"), "last_candidate_chars": len(text),
            "generating": snap.get("generating"), "root": snap.get("root"),
            "root_candidates": snap.get("root_candidates", []),
            "saw_generating": saw_generating, "seen": seen,
            "saw_generation_signal": saw_generation_signal,
            "saw_response_node": saw_response_node,
            "response_text_seen": response_text_seen,
            "current_user_id": trace.get("current_user_id"),
            "prompt_hash": trace.get("prompt_hash"),
            "scroll_top": snap.get("scroll_top"),
            "scroll_height": snap.get("scroll_height"),
            "response_start_deadline": start_deadline,
            "generation_deadline": generation_deadline, "stability_deadline": stability_deadline}
    _gemini_log(f"response detector diagnostic: {diag}")
    mapped_seen = seen or saw_response_node or response_text_seen
    kind = "RESPONSE_COMPLETION_TIMEOUT" if mapped_seen else "RESPONSE_START_TIMEOUT"
    if ((saw_generation_signal if msg else (seen and saw_generating))
            and not text and not snap.get("generating")):
        kind = "RESPONSE_EXTRACTION_FAILURE"
    raise GeminiResponseError(kind, trace["state"], diag)


def _dump_debug(path: Optional[str], chunk_no: int, attempt: int, reply: str,
                expected: int, missing: int) -> None:
    """Ghi lại NGUYÊN VĂN câu Gemini trả lời khi ghép không khớp.

    Khi giao diện Gemini đổi lần nữa, file này cho biết ngay nó trả về cái gì -
    khỏi phải đoán mò."""
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*70}\nLÔ {chunk_no} - lượt {attempt} - cần {expected} "
                    f"dòng, còn thiếu {missing}\n{'-'*70}\n{reply[:4000]}\n")
    except Exception:
        pass


# Retain the original baseline after a failed wait. Outer retry callers must
# rescan this turn instead of sending the same acknowledged request again.
_INFLIGHT = WeakKeyDictionary()


def _abandon_inflight(page) -> None:
    """Drop a stuck turn so the next batch can send. Same-msg rescan stays intact."""
    try:
        _INFLIGHT.pop(page, None)
    except Exception:
        pass


def _ask_once(page, msg: str, wait_reply: int, *, trace: Optional[Dict] = None) -> str:
    trace = trace if trace is not None else {}
    page = _trang_co_o_nhap(page)
    _check_ui_error(page, trace)
    trace.setdefault("request_id", uuid.uuid4().hex[:12])
    trace.setdefault("prompt_hash", prompt_hash(msg))
    pending = _INFLIGHT.get(page)
    if pending and pending.get("msg") != msg:
        _gemini_log("abandon stale inflight; next batch may send")
        _abandon_inflight(page)
        pending = None
    before = None
    if pending:
        before, saved = pending["before"], pending["trace"]
        trace.update(saved)
        _gemini_log("rescan existing turn; no duplicate send")
    else:
        box = _visible_locator(page, _INPUT_CANDIDATES, timeout=30.0)
        if box is None:
            raise RuntimeError("không thấy ô nhập của Gemini")
        _state(trace, "COMPOSER_READY")
        before = _snapshot(page)
        action = _existing_turn_action(before, msg)
        if action == "consume":
            turn = _current_turn(before, msg)
            text = _turn_text(turn)
            _once_state(trace, "SEND_ACKNOWLEDGED")
            _once_state(trace, "CURRENT_USER_TURN_IDENTIFIED")
            _once_state(trace, "MODEL_RESPONSE_NODE_IDENTIFIED")
            _once_state(trace, "RESPONSE_EXTRACTED")
            trace["send_ack"] = True
            trace["duplicate_guard"] = "consume_existing"
            _gemini_log("duplicate guard: consume existing response; no send")
            return text
        if action == "wait":
            _gemini_log("duplicate guard: prompt already in chat; wait, no send")
            trace["duplicate_guard"] = "wait_existing"
            _once_state(trace, "SEND_ACK_PENDING")
            _INFLIGHT[page] = {"msg": msg, "before": before, "trace": trace}
        elif before.get("generating"):
            raise GeminiResponseError("RESPONSE_DETECTION_FAILURE", trace["state"],
                                      {"send_ack": False, "error": "conversation already generating"})
        else:
            if not _put_text(page, box, msg):
                raise RuntimeError("chèn nội dung vào ô nhập không thành công")
            _state(trace, "PROMPT_INSERTED")
            trace["send_triggered_at"] = time.monotonic()
            _state(trace, "SEND_TRIGGERED")
            _INFLIGHT[page] = {"msg": msg, "before": before, "trace": trace}
            _submit(page)
            _state(trace, "SEND_ACK_PENDING")
            _watch_install(page, msg)
    if not trace.get("send_ack"):
        ack_start = time.monotonic()
        ack_deadline = ack_start + 12.0
        composer_released = False
        had_composer = True
        while time.monotonic() < ack_deadline:
            _check_ui_error(page, trace)
            _reveal_latest(page)
            snap = _snapshot(page)
            turn = _current_turn(snap, msg)
            still_in_box = _van_con_trong_o_nhap(page, msg)
            if had_composer and not still_in_box and not _nut_gui_con_bam_duoc(page):
                composer_released = True
            if turn:
                trace["send_ack"] = True
                trace["send_ack_ms"] = round(
                    (time.monotonic() - trace.get("send_triggered_at", ack_start)) * 1000)
                trace["user_count_after_send"] = snap.get("user_count")
                _gemini_log(
                    "SEND_ACK via current user turn; counts %s -> %s (diagnostic)"
                    % (before.get("user_count"), snap.get("user_count")))
                _state(trace, "SEND_ACKNOWLEDGED")
                _once_state(trace, "CURRENT_USER_TURN_IDENTIFIED")
                break
            if composer_released and (snap.get("generating") or snap.get("pending")):
                # Generation after our submit is an ACK signal, not turn mapping.
                trace["send_ack"] = True
                trace["send_ack_ms"] = round(
                    (time.monotonic() - trace.get("send_triggered_at", ack_start)) * 1000)
                _gemini_log("SEND_ACK via composer-clear + generation signal")
                _state(trace, "SEND_ACKNOWLEDGED")
                break
            page.wait_for_timeout(250)
        else:
            _reveal_latest(page)
            try:
                snap = _snapshot(page)
            except Exception:
                snap = _empty_snap()
            if _current_turn(snap, msg):
                trace["send_ack"] = True
                trace["send_ack_ms"] = round(
                    (time.monotonic() - trace.get("send_triggered_at", ack_start)) * 1000)
                _state(trace, "SEND_ACK_RECOVERED")
                _state(trace, "SEND_ACKNOWLEDGED")
                _once_state(trace, "CURRENT_USER_TURN_IDENTIFIED")
                _gemini_log("SEND_ACK_RECOVERED; prompt already accepted, not resending")
            else:
                _abandon_inflight(page)
                raise GeminiResponseError("SEND_ACK_TIMEOUT", trace["state"], {
                    "send_ack": False,
                    "user_count": snap.get("user_count"),
                    "model_count": snap.get("model_count"),
                    "prompt_hash": trace.get("prompt_hash"),
                })
    try:
        _reveal_latest(page)
        start_budget = min(float(wait_reply), _ASK_START_TIMEOUT_S)
        reply = _wait_reply(page, before.get("model_count", 0), wait_reply,
                            before_snap=before, trace=trace, msg=msg,
                            prompt_probe=msg,
                            response_start_timeout=start_budget)
    except GeminiResponseError:
        raise
    except Exception as exc:
        raise GeminiResponseError("RESPONSE_DETECTION_FAILURE", trace["state"],
                                  {"send_ack": True, "error": str(exc)[:160]}) from exc
    _INFLIGHT.pop(page, None)
    return reply


def _trang_co_o_nhap(page):
    """Ưu tiên tab vừa mở (Gemini hay bung Chat mới ra tab khác)."""
    ctx = getattr(page, "context", None)
    pages = list(getattr(ctx, "pages", None) or [page])
    for candidate in reversed(pages):
        if _visible_locator(candidate, _INPUT_CANDIDATES, timeout=1.2) is not None:
            if candidate is not page:
                try:
                    candidate.set_default_timeout(60000)
                    candidate.bring_to_front()
                except Exception:
                    pass
            return candidate
    return page


def _mo_chat_moi(page, url: str):
    """Mở đoạn chat trống. `goto /app` thường khôi phục chat cũ (kho ý tưởng).

    Trả về page đang dùng: Gemini đôi khi mở Chat mới thành tab khác.
    """
    clicked = False
    btn = _visible_locator(page, _NEW_CHAT_CANDIDATES, timeout=4.0)
    if btn is not None:
        try:
            btn.click(timeout=5000)
            clicked = True
        except Exception:
            clicked = False
    if not clicked:
        try:
            clicked = bool(page.evaluate(_NEW_CHAT_CLICK_JS))
        except Exception:
            clicked = False
    if not clicked:
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception:
            return page
    try:
        page.wait_for_timeout(900)
    except Exception:
        pass
    ctx = getattr(page, "context", None)
    newest = list(ctx.pages)[-1] if ctx and getattr(ctx, "pages", None) else page
    if newest is not page:
        try:
            newest.set_default_timeout(60000)
            newest.bring_to_front()
        except Exception:
            pass
        if _visible_locator(newest, _INPUT_CANDIDATES, timeout=12.0) is not None:
            log("Gemini mở chat mới ở tab khác — chuyển sang tab đó.", "info")
            return newest
    if _visible_locator(page, _INPUT_CANDIDATES, timeout=8.0) is not None:
        return page
    return _trang_co_o_nhap(page)


def _gieo_brief(page, wait_reply: int, film_hint: str, name_hint: str) -> bool:
    """Gieo vai lồng tiếng vào chat trống. Không dùng cho kho ý tưởng."""
    wait = max(20, min(int(wait_reply or 120), 60))
    try:
        reply = _ask_once(page, session_brief(film_hint, name_hint), wait)
    except Exception as e:
        log(f"Không gieo được brief lồng tiếng ({e}).", "warn")
        return False
    if brief_confirmed(reply):
        return True
    try:
        reply = _ask_once(
            page, "Chỉ trả đúng một dòng, không giải thích: SẴN SÀNG LỒNG TIẾNG",
            30)
    except Exception:
        return False
    return brief_confirmed(reply)


def _khoa_phien_long_tieng(page, url: str, wait_reply: int,
                           film_hint: str, name_hint: str):
    """Chat mới + khóa ADR. Gọi đầu phiên và sau mỗi reset_every. Trả về page."""
    page = _mo_chat_moi(page, url)
    if _visible_locator(page, _INPUT_CANDIDATES, timeout=8.0) is None:
        log("Không mở được đoạn chat mới trên Gemini Web.", "warn")
    if _gieo_brief(page, wait_reply, film_hint, name_hint):
        log("Đã khóa Gemini Web sang chế độ lồng tiếng phim.", "ok")
    else:
        log("Chưa nhận xác nhận SẴN SÀNG LỒNG TIẾNG. Mỗi lô vẫn gắn khóa thoại.",
            "warn")
    return page


@contextmanager
def phien_gemini_trinh_duyet(profile_dir: str, channel: str = "msedge",
                             url: str = "https://gemini.google.com/app",
                             wait_reply: int = 240, reset_every: int = 0):
    """Mở Gemini trong trình duyệt rồi trả về một hàm `hoi(prompt) -> str`.

    Dùng chung cho mọi việc cần Gemini mà không có API key: dịch phụ đề, và
    (mới) viết kịch bản truyện audio. KHÔNG gieo brief lồng tiếng ở đây —
    kho ý tưởng / kịch bản tự gửi prompt riêng. Toàn bộ phần khó - né cờ automation, chờ
    trả lời xong, gõ lại khi DOM dựng lại - nằm ở các hàm đã có sẵn bên dưới.

        with phien_gemini_trinh_duyet(profile) as hoi:
            tra_loi = hoi("Xin chào")
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise RuntimeError("Chưa cài Playwright. Chạy trong venv: pip install playwright")

    os.makedirs(profile_dir, exist_ok=True)
    log("Mở trình duyệt điều khiển Gemini (dùng phiên đăng nhập của bạn)...", "step")
    with sync_playwright() as p:
        ctx = _launch(p, profile_dir, channel)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(60000)
        try:
            page.goto(url, wait_until="domcontentloaded")
            if _visible_locator(page, _INPUT_CANDIDATES, timeout=25.0) is None:
                log("Chưa vào được ô chat Gemini (có thể chưa đăng nhập). Hãy "
                    "xử lý trong cửa sổ vừa mở...", "warn")
                try:
                    input("   → Xong rồi thì quay lại đây bấm ENTER để tiếp tục...")
                except EOFError:
                    page.wait_for_timeout(30000)
                if _visible_locator(page, _INPUT_CANDIDATES, timeout=180.0) is None:
                    raise RuntimeError("Vẫn không tìm thấy ô nhập của Gemini.")

            calls = 0
            failed_session = None
            def hoi(prompt: str) -> str:
                nonlocal page, calls, failed_session
                if failed_session is not None:
                    raise failed_session
                if reset_every and calls and calls % reset_every == 0:
                    page = _mo_chat_moi(page, url)
                calls += 1
                try:
                    page, reply = _ask_with_recovery(page, prompt, wait_reply, url)
                except GeminiResponseError as exc:
                    if exc.kind == "BROWSER_SESSION_UNHEALTHY":
                        failed_session = exc
                    raise
                return reply

            yield hoi
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def _ask_with_recovery(page, prompt, wait_reply, url):
    """Retry the same batch in a verified empty chat, at most twice.

    Exhaustion is terminal for this pipeline; never advance to another batch
    while the browser is broken. Successful translation cache is untouched.
    """
    retryable = {"UI_ERROR_RESPONSE", "SEND_ACK_TIMEOUT", "RESPONSE_DETECTION_FAILURE",
                 "RESPONSE_START_TIMEOUT", "RESPONSE_EXTRACTION_FAILURE",
                 "RESPONSE_COMPLETION_TIMEOUT"}
    for attempt in range(3):
        try:
            return page, _ask_once(page, prompt, wait_reply)
        except GeminiResponseError as exc:
            if exc.kind not in retryable:
                raise
            if attempt == 2:
                raise GeminiResponseError("BROWSER_SESSION_UNHEALTHY", "RECOVERY_EXHAUSTED",
                                          {"cause": exc.kind, **exc.diagnostic}) from exc
            _gemini_log(f"Recover same batch ({attempt + 1}/2): {exc.kind}; {exc.diagnostic}")
            _abandon_inflight(page)
            # A service rejection can outlive navigation. Back off only on
            # failure; successful batches pay no artificial delay.
            # Reload clears transient snackbar/optimistic turns before reset.
            try:
                page.wait_for_timeout(5000 if attempt == 0 else 15000)
                page.reload(wait_until="domcontentloaded")
                page = _mo_chat_moi(page, url)
                box = _visible_locator(page, _INPUT_CANDIDATES, timeout=12.0)
                snap = _snapshot(page)
                if box is None or snap.get("user_count") or snap.get("model_count") or snap.get("generating"):
                    raise RuntimeError("Không xác nhận được cuộc trò chuyện trống")
                _abandon_inflight(page)
            except Exception as reset_error:
                raise GeminiResponseError("BROWSER_SESSION_UNHEALTHY", "RESET_FAILED",
                                          {"cause": exc.kind, "error": str(reset_error)[:200]}) from reset_error
    raise AssertionError("unreachable")


def translate_via_browser(
    segments: List[Segment],
    profile_dir: str,
    channel: str = "msedge",
    url: str = "https://gemini.google.com/app",
    chunk_size: int = 25,
    wait_reply: int = 120,
    cache_path: Optional[str] = None,
    reset_every: int = 10,
    source_lang: Optional[str] = None,
    chars_per_sec: float = 0.0,
    name_hint: str = "",
    film_hint: str = "",
    shorten_long_lines_enabled: bool = True,
    translation_cfg: Optional[Dict] = None,
) -> List[Segment]:
    """Điều khiển Edge (hoặc Chrome) vào Gemini web để dịch - dùng tài khoản Pro
    đã đăng nhập, KHÔNG cần API key.

    Lần đầu: cửa sổ Edge mở ra -> tự đăng nhập Google/Gemini 1 lần. Profile được
    nhớ trong thư mục 'browser_profile' nên lần sau khỏi đăng nhập lại.

    Lô nào dịch hỏng thì GIỮ NGUYÊN câu gốc và đi tiếp, cuối cùng báo rõ số lô
    hỏng - thà thiếu vài câu còn hơn mất trắng cả tiếng đồng hồ đã chạy.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        raise RuntimeError("Chưa cài Playwright. Chạy trong venv: pip install playwright")

    from ..semantic import enabled
    if enabled(translation_cfg) and not str(source_lang or "").lower().startswith("vi"):
        from .semantic import translate_semantic
        with phien_gemini_trinh_duyet(profile_dir, channel, url, wait_reply,
                                     reset_every=max(1, int(reset_every or 10))) as ask:
            return translate_semantic(
                segments, ask, translation_cfg, cache_path=cache_path,
                identity=["browser", channel, url, translation_cfg.get("browser_model", "selected-in-browser")],
                film_hint=film_hint, name_hint=name_hint)
    os.makedirs(profile_dir, exist_ok=True)
    cache = ChunkCache(cache_path)
    debug_path = (os.path.join(os.path.dirname(cache_path), "_gemini_tra_loi.txt")
                  if cache_path else None)
    if debug_path and os.path.exists(debug_path):
        try:
            os.remove(debug_path)          # chỉ giữ log của lần chạy này
        except OSError:
            pass
    log("Mở trình duyệt điều khiển Gemini (dùng phiên đăng nhập Pro của bạn)...", "step")

    n = len(segments)
    total_chunks = max(1, (n + chunk_size - 1) // chunk_size)
    failed: List[int] = []
    context: List[str] = []
    # Audio vốn ĐÃ là tiếng Việt -> không dịch nữa mà nhờ Gemini SỬA chỗ nghe
    # nhầm. Đây là cách duy nhất chữa được lỗi nghe nhầm cả cụm ("sao chân
    # phải ta không bằng cảm giác" -> "sao chân ta không còn cảm giác gì cả").
    proofread = str(source_lang or "").lower().startswith("vi")
    if proofread:
        log("Nguồn đã là tiếng Việt -> chuyển sang chế độ SỬA LỖI NGHE NHẦM "
            "(không dịch).", "info")
    cps = max(0.0, float(chars_per_sec))
    if cps > 0:
        log(f"Canh độ dài bản dịch theo thời lượng từng câu (~{cps:.0f} ký tự/giây, "
            "có biên độ giữ nghĩa) để giọng đọc bám hình.", "info")
    long_lines = 0
    missing_lines = 0          # tổng số dòng thiếu ở các lô dịch DỞ (không phải lô hỏng hẳn)

    with sync_playwright() as p:
        ctx = _launch(p, profile_dir, channel)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(60000)
        try:
            page.goto(url, wait_until="domcontentloaded")

            # Chờ ô soạn thảo. Chưa đăng nhập -> nhắc người dùng làm thủ công.
            if _visible_locator(page, _INPUT_CANDIDATES, timeout=25.0) is None:
                log("Chưa vào được ô chat Gemini (có thể chưa đăng nhập, hoặc "
                    "đang hiện hộp thoại giới thiệu). Hãy xử lý trong cửa sổ "
                    "vừa mở...", "warn")
                try:
                    input("   → Xong rồi thì quay lại đây bấm ENTER để tiếp tục...")
                except EOFError:
                    page.wait_for_timeout(30000)
                if _visible_locator(page, _INPUT_CANDIDATES, timeout=180.0) is None:
                    raise RuntimeError("Vẫn không tìm thấy ô nhập của Gemini.")

            def _prompt(chunk, need, again=False, rewrite_spoken=False):
                return _build_prompt(
                    chunk, need, context, again=again, proofread=proofread,
                    chars_per_sec=cps, name_hint=name_hint, film_hint=film_hint,
                    rewrite_spoken=rewrite_spoken)

            def _khoa_chat():
                nonlocal page
                page = _khoa_phien_long_tieng(
                    page, url, wait_reply, film_hint, name_hint)

            if not proofread:
                _khoa_chat()
            sent_since_reset = 0
            for ci, i in enumerate(range(0, n, chunk_size), 1):
                chunk = segments[i:i + chunk_size]
                src_texts = [s.text for s in chunk]
                ckey = ChunkCache.key(i, src_texts)

                cached = cache.get(ckey)
                # CHỈ bỏ qua lô khi nó đã dịch ĐẦY ĐỦ (không còn dòng rỗng). Bản
                # cũ bỏ qua cả khi cache còn chứa dòng rỗng (lô dịch DỞ) -> các
                # dòng thiếu bị KẸT tiếng gốc VĨNH VIỄN dù chạy lại, trái với lời
                # hứa "chạy lại sẽ dịch tiếp phần còn thiếu". Lô dở: nạp sẵn phần
                # đã có rồi chỉ hỏi bổ sung đúng những dòng còn thiếu.
                seed: Dict[int, str] = {}
                if cached and len(cached) == len(chunk):
                    cleaned_cached = _clean_vi_lines(cached)
                    if cleaned_cached != cached:
                        cache.put(ckey, cleaned_cached)
                    cached = cleaned_cached
                    bad = _cjk_line_numbers(cached)
                    if bad:
                        log(f"Cache lô {ci}/{total_chunks} còn tiếng Trung ở dòng "
                            f"{_bad_line_summary(bad)} -> bỏ cache và dịch lại.", "warn")
                        cache.discard(ckey)
                        cached = None
                if cached and len(cached) == len(chunk):
                    if all(cached):
                        for s, t in zip(chunk, cached):
                            if t:
                                s.text = normalize_vi_subtitle_text(t)
                        context.extend([t for t in cached if t])
                        del context[:-8]
                        log(f"(cache) lô {ci}/{total_chunks} - bỏ qua, đã dịch trước đó", "info")
                        continue
                    seed = {k: cached[k - 1] for k in range(1, len(chunk) + 1) if cached[k - 1]}
                    log(f"(cache) lô {ci}/{total_chunks} - đã có {len(seed)}/{len(chunk)} "
                        "dòng từ lần trước, dịch tiếp phần còn thiếu.", "info")

                # Chat quá dài / chat mới = Gemini quên ADR, dịch như văn bản.
                if (not proofread and reset_every
                        and sent_since_reset >= reset_every):
                    try:
                        _khoa_chat()
                        sent_since_reset = 0
                    except Exception:
                        sent_since_reset = 0

                # Hỏi lần đầu cả lô; thiếu dòng nào thì hỏi BỔ SUNG đúng dòng đó
                # (giữ nguyên số thứ tự) thay vì hỏi lại cả lô -> nhanh và hội tụ.
                got: Dict[int, str] = dict(seed)
                need = [k for k in range(1, len(chunk) + 1) if k not in got]
                for attempt in range(3):
                    try:
                        reply = _ask_once(
                            page, _prompt(chunk, need, again=attempt > 0),
                            wait_reply)
                    except Exception as e:
                        log(f"Lô {ci}/{total_chunks} lỗi: {e}", "warn")
                        break
                    sent_since_reset += 1
                    new = parse_numbered_reply(reply, len(chunk))
                    if not new:
                        # Không thấy số nào -> ghép theo VỊ TRÍ, và chỉ khi số
                        # dòng khớp đúng khít.
                        new = match_by_position(reply, need)
                        if new:
                            log(f"Lô {ci}/{total_chunks}: trả lời không có số thứ tự, "
                                f"ghép theo vị trí ({len(new)} dòng khớp).", "warn")
                    for k, v in new.items():
                        if k in need:
                            clean_v = normalize_vi_subtitle_text(v)
                            if _contains_cjk(clean_v):
                                log(f"Lô {ci}/{total_chunks}, dòng {k}: phản hồi còn "
                                    "tiếng Trung, hỏi bổ sung lại.", "warn")
                                continue
                            got[k] = clean_v
                    need = [k for k in range(1, len(chunk) + 1) if k not in got]
                    if not need:
                        break
                    _dump_debug(debug_path, ci, attempt + 1, reply, len(chunk), len(need))
                    log(f"Lô {ci}/{total_chunks}: còn thiếu {len(need)}/{len(chunk)} "
                        "dòng, hỏi bổ sung...", "warn")
                    page.wait_for_timeout(1200)

                if not got:
                    failed.append(ci)
                    log(f"Lô {ci}/{total_chunks} BỎ QUA - giữ nguyên câu gốc.", "err")
                    continue

                if (not proofread and got
                        and looks_like_document_vi(
                            [got.get(k, "") for k in range(1, len(chunk) + 1)])):
                    log(f"Lô {ci}/{total_chunks}: nghi dịch kiểu văn bản/bài báo "
                        "-> hỏi lại 1 lần theo thoại lồng tiếng.", "warn")
                    all_nums = list(range(1, len(chunk) + 1))
                    try:
                        reply = _ask_once(
                            page, _prompt(chunk, all_nums, rewrite_spoken=True),
                            wait_reply)
                        sent_since_reset += 1
                        new = parse_numbered_reply(reply, len(chunk)) or {}
                        if not new:
                            new = match_by_position(reply, all_nums)
                        rewritten: Dict[int, str] = {}
                        for k, v in new.items():
                            clean_v = normalize_vi_subtitle_text(v)
                            if clean_v and not _contains_cjk(clean_v):
                                rewritten[k] = clean_v
                        if len(rewritten) == len(chunk):
                            old_lines = [got.get(k, "") for k in all_nums]
                            new_lines = [rewritten[k] for k in all_nums]
                            if (not looks_like_document_vi(new_lines)
                                    or essay_hit_count(new_lines)
                                    < essay_hit_count(old_lines)):
                                got = rewritten
                    except Exception as e:
                        log(f"Không hỏi lại được lô văn bản ({e}) — giữ bản vừa có.",
                            "warn")

                # Rút gọn lần hai từng bị tắt vì làm hỏng nghĩa ("mà bọn nó nói"
                # -> "mà bọn nói"). Nay bật lại kèm bộ chặn ở _accept_shortened:
                # phải giữ tên riêng, con số, và không được rút dưới sàn tỉ lệ.
                if (ENABLE_BROWSER_SHORTENING and shorten_long_lines_enabled
                        and cps > 0 and got):
                    def _ask(prompt: str) -> str:
                        nonlocal sent_since_reset
                        sent_since_reset += 1
                        return _ask_once(page, prompt, wait_reply)

                    long_lines += shorten_long_lines(
                        chunk, got, cps, _ask,
                        label=f"Lô {ci}/{total_chunks}: ")

                vi = _clean_vi_lines([got.get(k + 1, "") for k in range(len(chunk))])
                for s, t in zip(chunk, vi):
                    if t:
                        s.text = normalize_vi_subtitle_text(t)
                cache.put(ckey, vi)
                context.extend([t for t in vi if t])
                del context[:-8]     # chỉ dùng vài dòng cuối làm ngữ cảnh
                miss = sum(1 for t in vi if not t)
                missing_lines += miss
                log(f"(browser) lô {ci}/{total_chunks} - {min(i + chunk_size, n)}/{n} dòng"
                    + (f" (thiếu {miss} dòng, giữ bản gốc)" if miss else ""),
                    "warn" if miss else "info")
            if translation_cfg is not None:
                from ..vi_cues import finalize_spoken_vi_cues

                def _ask_beautify(prompt: str) -> str:
                    nonlocal sent_since_reset
                    sent_since_reset += 1
                    return _ask_once(page, prompt, wait_reply)

                try:
                    n_clean = finalize_spoken_vi_cues(
                        segments, translation_cfg, _ask_beautify)
                    if n_clean:
                        log(f"Đã chia lại {n_clean} dòng SRT Việt theo ngữ pháp "
                            "(giữ mốc).", "ok")
                except InterruptedError:
                    raise
                except Exception as exc:
                    log(f"Chia lại SRT Việt lỗi ({exc}); giữ bản dịch 1-1.", "warn")
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    if failed:
        log(f"Có {len(failed)}/{total_chunks} lô KHÔNG dịch được (lô "
            f"{', '.join(map(str, failed[:10]))}{'...' if len(failed) > 10 else ''}). "
            "Các dòng đó giữ nguyên tiếng gốc. Chạy lại chương trình sẽ chỉ dịch "
            "phần còn thiếu (đã có cache).", "err")
        if debug_path and os.path.exists(debug_path):
            log(f"Nguyên văn câu Gemini trả lời đã lưu ở: {debug_path}", "info")
        # Thiếu 1 lô thì bỏ qua cho xong việc; thiếu nhiều thì dừng, đừng lồng
        # tiếng một bản dịch lỗ chỗ rồi phải render lại từ đầu.
        if len(failed) > max(1.0, total_chunks * 0.1):
            raise TranslationIncomplete(failed, total_chunks)
    elif missing_lines:
        # Không lô nào hỏng HẲN nhưng vẫn có dòng lẻ chưa dịch được (nằm rải rác
        # trong các lô dở) -> đừng in "Dịch xong toàn bộ" gây hiểu nhầm là sạch.
        log(f"Dịch xong nhưng còn {missing_lines}/{n} dòng lẻ chưa dịch được, "
            "đang giữ tiếng gốc. Chạy lại chương trình sẽ dịch tiếp các dòng này "
            "(đã có cache).", "warn")
    else:
        log("Dịch xong toàn bộ.", "ok")
    if cps > 0 and long_lines:
        log(f"Còn {long_lines}/{n} dòng dài hơn thời lượng cho phép - bước lồng "
            "tiếng sẽ tự tăng tốc đọc để bù.", "warn")
    return segments
