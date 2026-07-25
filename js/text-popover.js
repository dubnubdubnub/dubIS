/* text-popover.js — Global text affordances:
   1. Hover any leaf text element for ~350ms → popover with the full text + Copy button.
   2. Double-click non-interactive text → select the whole element's text.
   3. If the behavior.autoCopySelection preference is on → copy any selection to clipboard.
   Modeled on part-preview.js: one element appended to <body>, wired once via initTextPopover(). */

import { AppLog } from './api.js';
import { getBehaviorPrefs } from './store.js';
import { innerRect, zoomedViewport } from './ui-zoom.js';

var SHOW_DELAY_MS = 350;
var INTERACTIVE_SELECTOR = 'button, a, input, select, textarea, [contenteditable], [contenteditable="true"], [role="button"]';
var ANCESTOR_SCAN_LIMIT = 4; // how far up to look for interactivity
// Elements owning the richer part-preview hover tooltip (js/part-preview.js) —
// the generic text popover must not compete with it.
var PART_PREVIEW_SELECTOR = '[data-lcsc], [data-digikey], [data-pololu], [data-mouser]';

var popover = null;
var showTimer = null;
var currentTarget = null;

export function isLeafTextElement(el) {
  if (!el || el.nodeType !== 1) return false;
  var tag = el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return false;
  if (popover && (el === popover || popover.contains(el))) return false;
  if (el.childElementCount !== 0) return false;
  var text = (el.textContent || '').trim();
  return text.length > 0;
}

export function isInteractive(el) {
  if (!el || el.nodeType !== 1) return false;
  var node = el;
  for (var i = 0; node && i <= ANCESTOR_SCAN_LIMIT; i++) {
    if (node.matches && node.matches(INTERACTIVE_SELECTOR)) return true;
    try {
      if (typeof getComputedStyle === 'function' && getComputedStyle(node).cursor === 'pointer') return true;
    } catch (styleErr) { AppLog.warn('getComputedStyle failed: ' + styleErr); }
    node = node.parentElement;
  }
  return false;
}

export async function copyText(text) {
  if (text === null || text === undefined || text === '') return false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) {
    AppLog.warn('clipboard.writeText failed, trying execCommand: ' + e);
  }
  try {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    var ok = document.execCommand('copy');
    document.body.removeChild(ta);
    if (!ok) AppLog.warn('execCommand("copy") returned false');
    return ok;
  } catch (e2) {
    AppLog.error('copyText failed entirely: ' + e2);
    return false;
  }
}

function hidePopover() {
  clearTimeout(showTimer);
  showTimer = null;
  currentTarget = null;
  if (popover) popover.classList.add('hidden');
}

function showPopover(el) {
  var text = (el.textContent || '').trim();
  if (!text) return;
  popover.innerHTML = '';
  var body = document.createElement('div');
  body.className = 'text-popover-text';
  body.textContent = text;                       // textContent — never innerHTML (no injection)
  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'text-popover-copy';
  btn.textContent = 'Copy';
  btn.addEventListener('click', function () {
    copyText(text).then(function (ok) {
      btn.textContent = ok ? 'Copied!' : 'Failed';
      setTimeout(function () { btn.textContent = 'Copy'; }, 1200);
    });
  });
  popover.appendChild(body);
  popover.appendChild(btn);
  popover.classList.remove('hidden');
  positionPopover(el);
}

function positionPopover(el) {
  // Authored-px space throughout (innerRect + zoomedViewport) so the maths match
  // offsetWidth and the px written below — see js/ui-zoom.js on the two spaces.
  var rect = innerRect(el);
  var vp = zoomedViewport();
  var pw = popover.offsetWidth || 320;
  var ph = popover.offsetHeight || 80;
  var top = rect.bottom + 6;
  var left = rect.left;
  if (left + pw > vp.w - 8) left = vp.w - pw - 8;
  if (left < 8) left = 8;
  if (top + ph > vp.h - 8) top = rect.top - ph - 6;
  if (top < 8) top = 8;
  popover.style.left = left + 'px';
  popover.style.top = top + 'px';
}

function selectWholeElement(el) {
  var sel = window.getSelection();
  if (!sel) return;
  var range = document.createRange();
  range.selectNodeContents(el);
  sel.removeAllRanges();
  sel.addRange(range);
}

function maybeAutoCopySelection() {
  if (!getBehaviorPrefs().autoCopySelection) return;
  var sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return;
  var text = sel.toString();
  if (text && text.trim()) copyText(text);
}

export function initTextPopover() {
  popover = document.createElement('div');
  popover.className = 'text-popover hidden';
  document.body.appendChild(popover);

  popover.addEventListener('mouseenter', function () { clearTimeout(showTimer); });
  popover.addEventListener('mouseleave', function () { hidePopover(); });

  document.addEventListener('mouseover', function (e) {
    var el = e.target;
    if (popover.contains(el)) return;
    if (!isLeafTextElement(el)) return;
    if (isInteractive(el)) return;
    if (el.closest && el.closest(PART_PREVIEW_SELECTOR)) return;
    if (el === currentTarget) return;
    clearTimeout(showTimer);
    currentTarget = el;
    showTimer = setTimeout(function () {
      if (currentTarget === el) showPopover(el);
    }, SHOW_DELAY_MS);
  });

  document.addEventListener('mouseout', function (e) {
    if (!currentTarget) return;
    var to = e.relatedTarget;
    if (to && (to === currentTarget || currentTarget.contains(to) || popover.contains(to) || to === popover)) return;
    hidePopover();
  });

  document.addEventListener('mousedown', function (e) {
    if (popover.contains(e.target)) return;
    hidePopover();
  });
  window.addEventListener('scroll', hidePopover, true);

  document.addEventListener('dblclick', function (e) {
    var el = e.target;
    if (!isLeafTextElement(el) || isInteractive(el)) return;
    selectWholeElement(el);
    maybeAutoCopySelection();
  });

  document.addEventListener('mouseup', maybeAutoCopySelection);
  document.addEventListener('keyup', function (e) {
    // Only react to selection-affecting keys to avoid copying on every keystroke.
    if (e.shiftKey || e.key === 'ArrowLeft' || e.key === 'ArrowRight' ||
        e.key === 'ArrowUp' || e.key === 'ArrowDown' || (e.ctrlKey && e.key === 'a')) {
      maybeAutoCopySelection();
    }
  });
}
