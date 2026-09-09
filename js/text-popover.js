/* text-popover.js — Global text affordances:
   1. Hover any element that renders text of its own for ~350ms → popover with
      that text + a Copy button.
   2. Double-click non-interactive text → select the whole element's text.
   3. If the behavior.autoCopySelection preference is on → copy any selection to clipboard.
   Modeled on part-preview.js: one element appended to <body>, wired once via initTextPopover().

   The popover is REACHABLE: the pointer can travel from the value into it and
   click Copy. That took two things — a delayed hide instead of a synchronous one,
   and a hide path that re-checks `:hover` and the trigger→popover corridor before
   committing. See HIDE_DELAY_MS below and js/hover-affordance.js. */

import { AppLog } from './api.js';
import { getBehaviorPrefs } from './store.js';
import { innerRect, zoomedViewport, toInnerPx } from './ui-zoom.js';
import { copyText, inHoverCorridor } from './hover-affordance.js';

// copyText lives in hover-affordance.js so part-preview.js can share the one
// clipboard path, but it stays exported here: this module is its historical home
// and the app's import site.
export { copyText };

var SHOW_DELAY_MS = 350;
/* Grace period before the popover goes away. It used to hide *synchronously* on
   the trigger's `mouseout`, which made it unreachable: the popover sits GAP_PX
   below the trigger, so any real pointer travel passes through a gap where
   `relatedTarget` is neither the trigger nor the popover, and the popover was
   torn down before the pointer arrived. (A teleporting `hover()` + `click()` in
   a test never sees that gap, which is why it went unnoticed.) The delay is only
   the safety net — the hide path also re-checks `:hover` and the trigger→popover
   corridor before it commits, so being slow with the mouse is fine too. */
var HIDE_DELAY_MS = 260;
/** Vertical offset between trigger and popover — also the width of the gap. */
var GAP_PX = 6;
var INTERACTIVE_SELECTOR = 'button, a, input, select, textarea, [contenteditable], [contenteditable="true"], [role="button"]';
var ANCESTOR_SCAN_LIMIT = 4; // how far up to look for interactivity
// Elements owning the richer part-preview hover tooltip (js/part-preview.js) —
// the generic text popover must not compete with it.
var PART_PREVIEW_SELECTOR = '[data-lcsc], [data-digikey], [data-pololu], [data-mouser]';
/* ...nor may it open on top of that tooltip's own content. `.part-preview` has
   z-index 500 and this popover 10000, so a popover opened over the tooltip both
   covers it and steals its hover, hiding the tooltip the user was reading. The
   tooltip carries its own Copy control instead (see js/part-preview.js). */
var SUPPRESS_INSIDE_SELECTOR = '.part-preview';

var popover = null;
var showTimer = null;
var hideTimer = null;
var currentTarget = null;
/** Trigger box in authored px, captured when the popover is positioned. */
var currentTriggerBox = null;
/** Last pointer position in authored px — the corridor test's input. */
var pointer = { x: NaN, y: NaN };

export function isLeafTextElement(el) {
  if (!el || el.nodeType !== 1) return false;
  var tag = el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return false;
  if (popover && (el === popover || popover.contains(el))) return false;
  if (el.childElementCount !== 0) return false;
  var text = (el.textContent || '').trim();
  return text.length > 0;
}

/**
 * The element's OWN text — its direct text-node children only, ignoring text
 * that belongs to nested elements.
 * @param {Element} el
 * @returns {string}
 */
export function ownText(el) {
  if (!el || el.nodeType !== 1) return '';
  var out = '';
  var kids = el.childNodes;
  for (var i = 0; i < kids.length; i++) {
    if (kids[i].nodeType === 3) out += kids[i].nodeValue;
  }
  return out.trim();
}

/**
 * Does this element render text of its own?
 *
 * Deliberately weaker than `isLeafTextElement`: a value can sit next to a small
 * control in the same cell — `<span class="part-qty"><button>⚠</button>30</span>`
 * is a quantity beside a "no price data" button — and a leaf-only test skips it,
 * leaving that value with no copy affordance at all. `ownText` is what the
 * popover then shows, so the neighbouring control's glyph never leaks into the
 * copied string.
 * @param {Element} el
 * @returns {boolean}
 */
export function hasOwnText(el) {
  if (!el || el.nodeType !== 1) return false;
  var tag = el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return false;
  if (popover && (el === popover || popover.contains(el))) return false;
  return ownText(el).length > 0;
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

/** Is the popover currently on screen? */
function isVisible() {
  return !!popover && !popover.classList.contains('hidden');
}

/**
 * Is the pointer still engaged with this affordance — over the trigger, over the
 * popover, or crossing the gap between them?
 *
 * `:hover` is the authoritative "the pointer is on the popover" answer and stays
 * correct even if the popover was repositioned under a stationary pointer (no
 * mousemove would have fired to tell us). The corridor covers the gap, which no
 * DOM event reports at all.
 */
function pointerEngaged() {
  if (!popover) return false;
  if (popover.matches(':hover')) return true;
  if (currentTarget && currentTarget.matches && currentTarget.matches(':hover')) return true;
  return inHoverCorridor(pointer.x, pointer.y, currentTriggerBox, innerRect(popover), GAP_PX + 2);
}

function cancelHide() {
  clearTimeout(hideTimer);
  hideTimer = null;
}

/**
 * Hide after HIDE_DELAY_MS — unless, when the timer fires, the pointer turns out
 * to be engaged after all. Re-checking at fire time is what makes the traversal
 * survive a slow hand: the timer is a fallback, not the decision.
 */
function scheduleHide() {
  if (!isVisible()) return;
  // Never restart a pending timer: mousemove calls this on every sample, and
  // resetting the deadline each time would keep the popover alive for as long as
  // the pointer kept moving anywhere on the page.
  if (hideTimer) return;
  hideTimer = setTimeout(function () {
    hideTimer = null;
    if (pointerEngaged()) return;
    hidePopover();
  }, HIDE_DELAY_MS);
}

function hidePopover() {
  clearTimeout(showTimer);
  showTimer = null;
  cancelHide();
  currentTarget = null;
  currentTriggerBox = null;
  if (popover) popover.classList.add('hidden');
}

function showPopover(el) {
  var text = ownText(el);
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
  var top = rect.bottom + GAP_PX;
  var left = rect.left;
  if (left + pw > vp.w - 8) left = vp.w - pw - 8;
  if (left < 8) left = 8;
  if (top + ph > vp.h - 8) top = rect.top - ph - GAP_PX;
  if (top < 8) top = 8;
  popover.style.left = left + 'px';
  popover.style.top = top + 'px';
  // Remember the trigger in the same authored space the corridor test uses.
  currentTriggerBox = rect;
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

  popover.addEventListener('mouseenter', function () {
    clearTimeout(showTimer);
    cancelHide();
  });
  popover.addEventListener('mouseleave', function () { scheduleHide(); });

  /* The pointer's authored-px position, sampled globally. This is the only way to
     know the pointer is in the trigger→popover gap: no element there fires an
     event we could hang the answer on. Passive + capture so nothing can swallow
     it. clientX/clientY are post-zoom, hence toInnerPx — mixing them with an
     innerRect() corridor would only line up at 100% zoom. */
  document.addEventListener('mousemove', function (e) {
    pointer.x = toInnerPx(e.clientX);
    pointer.y = toInnerPx(e.clientY);
    if (!isVisible()) return;
    if (pointerEngaged()) cancelHide();
    else scheduleHide();
  }, { passive: true, capture: true });

  document.addEventListener('mouseover', function (e) {
    var el = e.target;
    if (popover.contains(el)) return;
    if (!hasOwnText(el)) return;
    if (isInteractive(el)) return;
    if (el.closest && el.closest(PART_PREVIEW_SELECTOR)) return;
    if (el.closest && el.closest(SUPPRESS_INSIDE_SELECTOR)) return;
    if (el === currentTarget) return;
    clearTimeout(showTimer);
    cancelHide();
    currentTarget = el;
    showTimer = setTimeout(function () {
      if (currentTarget === el) showPopover(el);
    }, SHOW_DELAY_MS);
  });

  document.addEventListener('mouseout', function (e) {
    if (!currentTarget) return;
    var to = e.relatedTarget;
    if (to && (to === currentTarget || currentTarget.contains(to) || popover.contains(to) || to === popover)) return;
    if (!isVisible()) {
      // Nothing on screen yet: drop the pending show outright, since leaving the
      // trigger before the delay elapses means the user never asked for it.
      clearTimeout(showTimer);
      showTimer = null;
      currentTarget = null;
      return;
    }
    // Visible: hand it to the delayed, re-checked hide rather than tearing the
    // popover down under a pointer that may be on its way into it.
    scheduleHide();
  });

  document.addEventListener('mousedown', function (e) {
    if (popover.contains(e.target)) return;
    hidePopover();
  });
  window.addEventListener('scroll', function (e) {
    // Scrolling the popover's own overflowing text must not dismiss it; only a
    // scroll of the page underneath invalidates its anchor.
    var t = e.target;
    if (t === popover || (t && t.nodeType === 1 && popover.contains(t))) return;
    hidePopover();
  }, true);

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
