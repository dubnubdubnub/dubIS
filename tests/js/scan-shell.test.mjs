// tests/js/scan-shell.test.mjs
// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { openScanShell, markShellTile, closeScanShell } from '../../js/import/mfg-direct/scan-shell.js';

describe('scan shell', () => {
  beforeEach(() => { document.body.innerHTML = ''; closeScanShell(); });

  it('renders one reading tile per item', () => {
    openScanShell([{ name: 'a.png' }, { name: 'b.png' }]);
    const overlay = document.getElementById('scan-shell-overlay');
    expect(overlay).toBeTruthy();
    expect(overlay.querySelectorAll('.scan-shell-tile').length).toBe(2);
    expect(overlay.querySelectorAll('.scan-shell-tile.reading').length).toBe(2);
  });

  it('marks a tile done with detail', () => {
    openScanShell([{ name: 'a.png' }]);
    markShellTile(0, 'done', '5 rows');
    const tile = document.querySelector('.scan-shell-tile');
    expect(tile.classList.contains('done')).toBe(true);
    expect(tile.classList.contains('reading')).toBe(false);
    expect(tile.textContent).toContain('5 rows');
  });

  it('closeScanShell removes the overlay', () => {
    openScanShell([{ name: 'a.png' }]);
    closeScanShell();
    expect(document.getElementById('scan-shell-overlay')).toBeNull();
  });
});
