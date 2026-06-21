// tests/js/setup-drop-zone.test.mjs
// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { setupDropZone } from '../../js/ui-helpers.js';

function makeFile(name) { return new File(['x'], name, { type: 'image/png' }); }

describe('setupDropZone multi mode', () => {
  beforeEach(() => {
    document.body.innerHTML =
      `<div id="z"><input id="i" type="file"></div>`;
  });

  it('passes an array of files on drop when multi', () => {
    const onFile = vi.fn();
    setupDropZone('z', 'i', () => {}, onFile, { multi: true });
    const dt = { files: [makeFile('a.png'), makeFile('b.png')] };
    const ev = new Event('drop'); ev.dataTransfer = dt;
    document.getElementById('z').dispatchEvent(ev);
    expect(Array.isArray(onFile.mock.calls[0][0])).toBe(true);
    expect(onFile.mock.calls[0][0]).toHaveLength(2);
  });

  it('passes a single File when not multi (default)', () => {
    const onFile = vi.fn();
    setupDropZone('z', 'i', () => {}, onFile);
    const dt = { files: [makeFile('a.png')] };
    const ev = new Event('drop'); ev.dataTransfer = dt;
    document.getElementById('z').dispatchEvent(ev);
    expect(onFile.mock.calls[0][0]).toBeInstanceOf(File);
  });
});
