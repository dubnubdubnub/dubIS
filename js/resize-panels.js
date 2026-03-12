/* Drag-to-resize panels — self-contained, no dependencies */
'use strict';

const panels = [
  document.getElementById('panel-import'),
  document.getElementById('panel-inventory'),
  document.getElementById('panel-bom')
];
const PANEL_MIN_WIDTHS = [240, 300, 380];  // [import, inventory, bom]
const CONSOLE_MIN_HEIGHT = 100;

const panelsContainer = document.querySelector('.panels');
const consoleLog = document.getElementById('console-log');

/* ── Horizontal handles (between panels) ─────────────── */
function createHHandle(leftIdx) {
  const h = document.createElement('div');
  h.className = 'resize-handle-h';
  panels[leftIdx].after(h);
  return h;
}

const hHandle1 = createHHandle(0);
const hHandle2 = createHHandle(1);

function snapshotWidths() {
  return panels.map(p => p.getBoundingClientRect().width);
}

function lockWidths(widths) {
  panels[0].style.width = widths[0] + 'px';
  panels[0].style.flex = 'none';
  panels[1].style.width = widths[1] + 'px';
  panels[1].style.flex = 'none';
  panels[2].style.width = widths[2] + 'px';
  panels[2].style.flex = 'none';
}

function initHDrag(handle, leftIdx) {
  const rightIdx = leftIdx + 1;

  handle.addEventListener('pointerdown', function (e) {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);

    const widths = snapshotWidths();
    lockWidths(widths);

    const startX = e.clientX;
    const startLeft = widths[leftIdx];
    const startRight = widths[rightIdx];

    handle.classList.add('active');
    document.body.classList.add('resizing');

    function onMove(ev) {
      const dx = ev.clientX - startX;
      let newLeft = startLeft + dx;
      let newRight = startRight - dx;

      if (newLeft < PANEL_MIN_WIDTHS[leftIdx]) {
        newLeft = PANEL_MIN_WIDTHS[leftIdx];
        newRight = startLeft + startRight - newLeft;
      }
      if (newRight < PANEL_MIN_WIDTHS[rightIdx]) {
        newRight = PANEL_MIN_WIDTHS[rightIdx];
        newLeft = startLeft + startRight - newRight;
      }

      panels[leftIdx].style.width = newLeft + 'px';
      panels[rightIdx].style.width = newRight + 'px';
    }

    function onUp() {
      handle.classList.remove('active');
      document.body.classList.remove('resizing');
      handle.removeEventListener('pointermove', onMove);
      handle.removeEventListener('pointerup', onUp);
    }

    handle.addEventListener('pointermove', onMove);
    handle.addEventListener('pointerup', onUp);
  });
}

initHDrag(hHandle1, 0);
initHDrag(hHandle2, 1);

/* ── Vertical handle (above console log) ─────────────── */
const vHandle = document.createElement('div');
vHandle.className = 'resize-handle-v';
consoleLog.before(vHandle);

vHandle.addEventListener('pointerdown', function (e) {
  e.preventDefault();
  vHandle.setPointerCapture(e.pointerId);

  const startY = e.clientY;
  const startH = consoleLog.getBoundingClientRect().height;
  const panelH = panels[0].getBoundingClientRect().height;
  const headerH = panels[0].querySelector('.panel-header').getBoundingClientRect().height;
  const importBody = document.getElementById('import-body');
  const dropZone = importBody ? importBody.querySelector('.drop-zone') : null;
  const reservedH = headerH + (dropZone ? dropZone.getBoundingClientRect().height + 48 : 120);
  const maxH = panelH - reservedH;

  vHandle.classList.add('active');
  document.body.classList.add('resizing-v');

  function onMove(ev) {
    const dy = startY - ev.clientY;
    let newH = startH + dy;
    if (newH < CONSOLE_MIN_HEIGHT) newH = CONSOLE_MIN_HEIGHT;
    if (newH > maxH) newH = maxH;
    consoleLog.style.height = newH + 'px';
  }

  function onUp() {
    vHandle.classList.remove('active');
    document.body.classList.remove('resizing-v');
    vHandle.removeEventListener('pointermove', onMove);
    vHandle.removeEventListener('pointerup', onUp);
  }

  vHandle.addEventListener('pointermove', onMove);
  vHandle.addEventListener('pointerup', onUp);
});
