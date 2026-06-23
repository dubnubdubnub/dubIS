// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { html, raw, el, escapeHtml } from '../../js/dom/html.js';

describe('escapeHtml', () => {
  it('escapes ampersand', () => {
    expect(escapeHtml('a & b')).toBe('a &amp; b');
  });

  it('escapes less-than and greater-than', () => {
    expect(escapeHtml('<script>')).toBe('&lt;script&gt;');
  });

  it('escapes double quotes', () => {
    expect(escapeHtml('"hello"')).toBe('&quot;hello&quot;');
  });

  it('escapes single quotes', () => {
    expect(escapeHtml("it's")).toBe('it&#39;s');
  });

  it('escapes all five characters in one string', () => {
    expect(escapeHtml(`<a href="it's">&`)).toBe('&lt;a href=&quot;it&#39;s&quot;&gt;&amp;');
  });

  it('coerces numbers to strings', () => {
    expect(escapeHtml(42)).toBe('42');
  });

  it('returns empty string for null/undefined', () => {
    expect(escapeHtml(null)).toBe('');
    expect(escapeHtml(undefined)).toBe('');
  });
});

describe('html tagged template', () => {
  it('returns a DocumentFragment', () => {
    const frag = html`<span>hello</span>`;
    expect(frag).toBeInstanceOf(DocumentFragment);
  });

  it('escapes string interpolations (XSS guard)', () => {
    const xss = '<img src=x onerror=alert(1)>';
    const frag = html`<div>${xss}</div>`;
    const div = frag.querySelector('div');
    // The content must be a text node, not a live <img>
    expect(frag.querySelector('img')).toBeNull();
    expect(div.textContent).toBe(xss);
  });

  it('escapes & in interpolated strings', () => {
    const frag = html`<span>${'a & b'}</span>`;
    expect(frag.querySelector('span').textContent).toBe('a & b');
    expect(frag.querySelector('span').innerHTML).toContain('&amp;');
  });

  it('escapes double quotes in interpolated strings (verify via escapeHtml)', () => {
    // jsdom decodes &quot; back to " when reading .innerHTML in text context,
    // so we verify via escapeHtml directly — the html() template uses it.
    const frag = html`<span>${'"hello"'}</span>`;
    expect(frag.querySelector('span').textContent).toBe('"hello"');
    // The escaping is confirmed by escapeHtml's own tests; the value reaches
    // the DOM correctly when used in attribute context (e.g., title attr).
    const e = el('div', { title: '"hello"' });
    // setAttribute stores the raw value; getAttribute returns the raw value
    expect(e.getAttribute('title')).toBe('"hello"');
    // escapeHtml itself is the contract
    expect(escapeHtml('"hello"')).toContain('&quot;');
  });

  it('escapes single quotes in interpolated strings (verify via escapeHtml)', () => {
    const frag = html`<span>${"it's"}</span>`;
    expect(frag.querySelector('span').textContent).toBe("it's");
    // escapeHtml is the contract — jsdom decodes &#39; in text context
    expect(escapeHtml("it's")).toContain('&#39;');
  });

  it('interpolates numbers as escaped text', () => {
    const frag = html`<b>${42}</b>`;
    expect(frag.querySelector('b').textContent).toBe('42');
  });

  it('inserts nodes by identity (not stringified)', () => {
    const inner = document.createElement('em');
    inner.textContent = 'world';
    const frag = html`<div>${inner}</div>`;
    const div = frag.querySelector('div');
    expect(div.contains(inner)).toBe(true);
    expect(div.querySelector('em')).toBe(inner);
  });

  it('inserts DocumentFragment values', () => {
    const inner = html`<em>nested</em>`;
    const frag = html`<div>${inner}</div>`;
    const div = frag.querySelector('div');
    expect(div.querySelector('em')).toBeTruthy();
    expect(div.querySelector('em').textContent).toBe('nested');
  });

  it('renders null as nothing', () => {
    const frag = html`<div>${null}</div>`;
    expect(frag.querySelector('div').textContent).toBe('');
  });

  it('renders undefined as nothing', () => {
    const frag = html`<div>${undefined}</div>`;
    expect(frag.querySelector('div').textContent).toBe('');
  });

  it('renders false as nothing', () => {
    const frag = html`<div>${false}</div>`;
    expect(frag.querySelector('div').textContent).toBe('');
  });

  it('renders true as nothing', () => {
    const frag = html`<div>${true}</div>`;
    expect(frag.querySelector('div').textContent).toBe('');
  });

  it('renders array of mixed strings and nodes in order', () => {
    const span = document.createElement('span');
    span.textContent = 'B';
    const frag = html`<div>${['A', span, 'C']}</div>`;
    const div = frag.querySelector('div');
    expect(div.textContent).toBe('ABC');
    expect(div.querySelector('span')).toBe(span);
  });

  it('raw() values are inserted as unescaped HTML', () => {
    const frag = html`<div>${raw('<em>bold</em>')}</div>`;
    const div = frag.querySelector('div');
    expect(div.querySelector('em')).toBeTruthy();
    expect(div.querySelector('em').textContent).toBe('bold');
  });

  it('raw() does NOT escape angle brackets', () => {
    const frag = html`<div>${raw('<b>test</b>')}</div>`;
    // If it were escaped we'd see no <b> element
    expect(frag.querySelector('b')).toBeTruthy();
  });

  it('non-raw strings ARE escaped (contrast with raw)', () => {
    const frag = html`<div>${'<b>test</b>'}</div>`;
    expect(frag.querySelector('b')).toBeNull();
    expect(frag.querySelector('div').textContent).toBe('<b>test</b>');
  });
});

describe('raw()', () => {
  it('returns a marker object with __raw property', () => {
    const r = raw('<em>hi</em>');
    expect(r).toHaveProperty('__raw');
    expect(r.__raw).toBe('<em>hi</em>');
  });

  it('coerces its argument to string', () => {
    const r = raw(123);
    expect(r.__raw).toBe('123');
  });
});

describe('el()', () => {
  it('creates an element with the given tag', () => {
    const e = el('div');
    expect(e.tagName).toBe('DIV');
  });

  it('sets class attribute', () => {
    const e = el('div', { class: 'foo bar' });
    expect(e.className).toBe('foo bar');
  });

  it('sets id attribute', () => {
    const e = el('div', { id: 'my-id' });
    expect(e.id).toBe('my-id');
  });

  it('sets arbitrary attributes via setAttribute', () => {
    const e = el('input', { type: 'text', placeholder: 'Search' });
    expect(e.getAttribute('type')).toBe('text');
    expect(e.getAttribute('placeholder')).toBe('Search');
  });

  it('sets dataset properties', () => {
    const e = el('div', { dataset: { partId: '42', foo: 'bar' } });
    expect(e.dataset.partId).toBe('42');
    expect(e.dataset.foo).toBe('bar');
  });

  it('wires event listeners from on: map', () => {
    const handler = vi.fn();
    const e = el('button', { on: { click: handler } });
    e.click();
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('wires multiple event listeners', () => {
    const clickHandler = vi.fn();
    const mouseoverHandler = vi.fn();
    const e = el('div', { on: { click: clickHandler, mouseover: mouseoverHandler } });
    e.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    e.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
    expect(clickHandler).toHaveBeenCalledTimes(1);
    expect(mouseoverHandler).toHaveBeenCalledTimes(1);
  });

  it('skips null attribute values', () => {
    const e = el('div', { title: null });
    expect(e.hasAttribute('title')).toBe(false);
  });

  it('skips undefined attribute values', () => {
    const e = el('div', { title: undefined });
    expect(e.hasAttribute('title')).toBe(false);
  });

  it('skips false attribute values', () => {
    const e = el('div', { hidden: false });
    expect(e.hasAttribute('hidden')).toBe(false);
  });

  it('sets boolean true attribute as present (empty string)', () => {
    const e = el('div', { hidden: true });
    expect(e.hasAttribute('hidden')).toBe(true);
  });

  it('appends string children as text nodes (not HTML-parsed)', () => {
    const e = el('div', {}, '<script>alert(1)</script>');
    // Must be a text node — no actual <script> element
    expect(e.querySelector('script')).toBeNull();
    expect(e.textContent).toBe('<script>alert(1)</script>');
  });

  it('appends number children as text nodes', () => {
    const e = el('span', {}, 42);
    expect(e.textContent).toBe('42');
  });

  it('appends Node children directly', () => {
    const child = document.createElement('strong');
    child.textContent = 'hi';
    const e = el('div', {}, child);
    expect(e.contains(child)).toBe(true);
    expect(e.querySelector('strong')).toBe(child);
  });

  it('appends array children in order', () => {
    const span = document.createElement('span');
    span.textContent = 'world';
    const e = el('div', {}, ['hello ', span]);
    expect(e.textContent).toBe('hello world');
  });

  it('null attrs argument is safe (no crash)', () => {
    const e = el('div', null, 'text');
    expect(e.textContent).toBe('text');
  });
});
