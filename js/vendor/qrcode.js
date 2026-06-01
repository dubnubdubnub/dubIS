/* qrcode.js — dependency-free, pure-JS QR code generator (ES module).
 *
 * Source: derived from Kazuhiko Arase's "qrcode-generator"
 *   https://github.com/kazuhikoarase/qrcode-generator
 * License: MIT (Copyright (c) 2009 Kazuhiko Arase)
 *
 * Adapted to a standalone ES module: no DOM, no npm/CDN dependency. Exposes
 *   qrModules(text, { errorCorrectLevel })  -> boolean[][]  (true = dark module)
 *   renderQrToCanvas(canvas, text, opts)     -> renders into a <canvas>
 *
 * Only the byte (8-bit) encoding mode is supported, which is sufficient for the
 * ASCII URLs we encode. Error correction level defaults to 'M'.
 */

// ── Error correction levels ──────────────────────────────────────────────
const QRErrorCorrectLevel = { L: 1, M: 0, Q: 3, H: 2 };

// ── Galois field math (GF(256)) ──────────────────────────────────────────
const EXP_TABLE = new Array(256);
const LOG_TABLE = new Array(256);
for (let i = 0; i < 8; i++) EXP_TABLE[i] = 1 << i;
for (let i = 8; i < 256; i++) {
  EXP_TABLE[i] = EXP_TABLE[i - 4] ^ EXP_TABLE[i - 5] ^ EXP_TABLE[i - 6] ^ EXP_TABLE[i - 8];
}
for (let i = 0; i < 255; i++) LOG_TABLE[EXP_TABLE[i]] = i;

function gexp(n) {
  while (n < 0) n += 255;
  while (n >= 256) n -= 255;
  return EXP_TABLE[n];
}
function glog(n) {
  if (n < 1) throw new Error('glog(' + n + ')');
  return LOG_TABLE[n];
}

// ── Polynomial ────────────────────────────────────────────────────────────
function Polynomial(num, shift) {
  let offset = 0;
  while (offset < num.length && num[offset] === 0) offset++;
  const arr = new Array(num.length - offset + shift);
  for (let i = 0; i < num.length - offset; i++) arr[i] = num[i + offset];
  return arr;
}
function polyMultiply(a, b) {
  const num = new Array(a.length + b.length - 1).fill(0);
  for (let i = 0; i < a.length; i++) {
    for (let j = 0; j < b.length; j++) {
      num[i + j] ^= gexp(glog(a[i]) + glog(b[j]));
    }
  }
  return Polynomial(num, 0);
}
function polyMod(a, b) {
  if (a.length - b.length < 0) return a;
  const ratio = glog(a[0]) - glog(b[0]);
  const num = a.slice();
  for (let i = 0; i < b.length; i++) num[i] ^= gexp(glog(b[i]) + ratio);
  return polyMod(Polynomial(num, 0), b);
}
function errorCorrectPolynomial(ecLength) {
  let a = [1];
  for (let i = 0; i < ecLength; i++) a = polyMultiply(a, [1, gexp(i)]);
  return a;
}

// ── BitBuffer ───────────────────────────────────────────────────────────
function BitBuffer() {
  return { buffer: [], length: 0,
    put(num, len) { for (let i = 0; i < len; i++) this.putBit(((num >>> (len - i - 1)) & 1) === 1); },
    putBit(bit) {
      const bufIndex = Math.floor(this.length / 8);
      if (this.buffer.length <= bufIndex) this.buffer.push(0);
      if (bit) this.buffer[bufIndex] |= (0x80 >>> (this.length % 8));
      this.length++;
    },
    get(index) { return ((this.buffer[Math.floor(index / 8)] >>> (7 - (index % 8))) & 1) === 1; },
  };
}

// ── RS block layout (RS_BLOCK_TABLE indexed by (typeNumber-1)*4 + ecLevel) ──
// Each entry: [totalCount, dataCount] groups flattened (count pairs).
const RS_BLOCK_TABLE = [
  [1, 26, 19], [1, 26, 16], [1, 26, 13], [1, 26, 9],
  [1, 44, 34], [1, 44, 28], [1, 44, 22], [1, 44, 16],
  [1, 70, 55], [1, 70, 44], [2, 35, 17], [2, 35, 13],
  [1, 100, 80], [2, 50, 32], [2, 50, 24], [4, 25, 9],
  [1, 134, 108], [2, 67, 43], [2, 33, 15, 2, 34, 16], [2, 33, 11, 2, 34, 12],
  [2, 86, 68], [4, 43, 27], [4, 43, 19], [4, 43, 15],
  [2, 98, 78], [4, 49, 31], [2, 32, 14, 4, 33, 15], [4, 39, 13, 1, 40, 14],
  [2, 121, 97], [2, 60, 38, 2, 61, 39], [4, 40, 18, 2, 41, 19], [4, 40, 14, 2, 41, 15],
  [2, 146, 116], [3, 58, 36, 2, 59, 37], [4, 36, 16, 4, 37, 17], [4, 36, 12, 4, 37, 13],
  [2, 86, 68, 2, 87, 69], [4, 69, 43, 1, 70, 44], [6, 43, 19, 2, 44, 20], [6, 43, 15, 2, 44, 16],
];

function getRsBlocks(typeNumber, ecLevel) {
  // ecLevel here is QRErrorCorrectLevel value (L=1,M=0,Q=3,H=2). Map to table order L,M,Q,H -> index.
  const order = { 1: 0, 0: 1, 3: 2, 2: 3 }; // L,M,Q,H
  const idx = (typeNumber - 1) * 4 + order[ecLevel];
  const def = RS_BLOCK_TABLE[idx];
  if (!def) throw new Error('no RS block for type ' + typeNumber + ' level ' + ecLevel);
  const blocks = [];
  for (let i = 0; i < def.length; i += 3) {
    const count = def[i], total = def[i + 1], data = def[i + 2];
    for (let j = 0; j < count; j++) blocks.push({ total, data });
  }
  return blocks;
}

// ── BCH helpers for format/version info ─────────────────────────────────
function bchDigit(data) { let d = 0; while (data !== 0) { d++; data >>>= 1; } return d; }
const G15 = (1 << 10) | (1 << 8) | (1 << 5) | (1 << 4) | (1 << 2) | (1 << 1) | 1;
const G18 = (1 << 12) | (1 << 11) | (1 << 10) | (1 << 9) | (1 << 8) | (1 << 5) | (1 << 2) | 1;
const G15_MASK = (1 << 14) | (1 << 12) | (1 << 10) | (1 << 4) | (1 << 1);
function bchTypeInfo(data) {
  let d = data << 10;
  while (bchDigit(d) - bchDigit(G15) >= 0) d ^= (G15 << (bchDigit(d) - bchDigit(G15)));
  return ((data << 10) | d) ^ G15_MASK;
}
function bchTypeNumber(data) {
  let d = data << 12;
  while (bchDigit(d) - bchDigit(G18) >= 0) d ^= (G18 << (bchDigit(d) - bchDigit(G18)));
  return (data << 12) | d;
}

const PATTERN_POSITION_TABLE = [
  [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50],
];

function maskFunc(maskPattern, i, j) {
  switch (maskPattern) {
    case 0: return (i + j) % 2 === 0;
    case 1: return i % 2 === 0;
    case 2: return j % 3 === 0;
    case 3: return (i + j) % 3 === 0;
    case 4: return (Math.floor(i / 2) + Math.floor(j / 3)) % 2 === 0;
    case 5: return ((i * j) % 2) + ((i * j) % 3) === 0;
    case 6: return (((i * j) % 2) + ((i * j) % 3)) % 2 === 0;
    case 7: return (((i * j) % 3) + ((i + j) % 2)) % 2 === 0;
    default: throw new Error('bad maskPattern: ' + maskPattern);
  }
}

// ── QR model ──────────────────────────────────────────────────────────────
function createData(typeNumber, ecLevel, dataBytes) {
  const blocks = getRsBlocks(typeNumber, ecLevel);
  const buffer = BitBuffer();
  // Mode indicator for 8-bit byte mode = 4 (0b0100)
  buffer.put(4, 4);
  // Char count: 8 bits for type 1-9
  buffer.put(dataBytes.length, 8);
  for (const b of dataBytes) buffer.put(b, 8);

  let totalDataCount = 0;
  for (const blk of blocks) totalDataCount += blk.data;
  if (buffer.length > totalDataCount * 8) {
    throw new Error('code length overflow (' + buffer.length + ' > ' + (totalDataCount * 8) + ')');
  }
  if (buffer.length + 4 <= totalDataCount * 8) buffer.put(0, 4);
  while (buffer.length % 8 !== 0) buffer.putBit(false);
  while (true) {
    if (buffer.length >= totalDataCount * 8) break;
    buffer.put(0xEC, 8);
    if (buffer.length >= totalDataCount * 8) break;
    buffer.put(0x11, 8);
  }
  return createBytes(buffer, blocks);
}

function createBytes(buffer, blocks) {
  let offset = 0;
  let maxDc = 0, maxEc = 0;
  const dcdata = [], ecdata = [];
  for (const blk of blocks) {
    const dcCount = blk.data;
    const ecCount = blk.total - blk.data;
    maxDc = Math.max(maxDc, dcCount);
    maxEc = Math.max(maxEc, ecCount);
    const dc = new Array(dcCount);
    for (let i = 0; i < dcCount; i++) dc[i] = 0xff & buffer.buffer[i + offset];
    offset += dcCount;
    const rsPoly = errorCorrectPolynomial(ecCount);
    const rawPoly = Polynomial(dc, rsPoly.length - 1);
    const modPoly = polyMod(rawPoly, rsPoly);
    const ec = new Array(rsPoly.length - 1);
    for (let i = 0; i < ec.length; i++) {
      const modIndex = i + modPoly.length - ec.length;
      ec[i] = (modIndex >= 0) ? modPoly[modIndex] : 0;
    }
    dcdata.push(dc); ecdata.push(ec);
  }
  let total = 0;
  for (const blk of blocks) total += blk.total;
  const data = new Array(total);
  let index = 0;
  for (let i = 0; i < maxDc; i++) {
    for (let b = 0; b < blocks.length; b++) if (i < dcdata[b].length) data[index++] = dcdata[b][i];
  }
  for (let i = 0; i < maxEc; i++) {
    for (let b = 0; b < blocks.length; b++) if (i < ecdata[b].length) data[index++] = ecdata[b][i];
  }
  return data;
}

function makeMatrix(typeNumber, ecLevel, data, maskPattern) {
  const size = typeNumber * 4 + 17;
  const modules = [];
  for (let r = 0; r < size; r++) modules.push(new Array(size).fill(null));

  function setupPositionProbe(row, col) {
    for (let r = -1; r <= 7; r++) {
      if (row + r <= -1 || size <= row + r) continue;
      for (let c = -1; c <= 7; c++) {
        if (col + c <= -1 || size <= col + c) continue;
        const dark = (0 <= r && r <= 6 && (c === 0 || c === 6)) ||
          (0 <= c && c <= 6 && (r === 0 || r === 6)) ||
          (2 <= r && r <= 4 && 2 <= c && c <= 4);
        modules[row + r][col + c] = dark;
      }
    }
  }
  setupPositionProbe(0, 0);
  setupPositionProbe(size - 7, 0);
  setupPositionProbe(0, size - 7);

  // alignment patterns
  const pos = PATTERN_POSITION_TABLE[typeNumber - 1];
  for (let i = 0; i < pos.length; i++) {
    for (let j = 0; j < pos.length; j++) {
      const row = pos[i], col = pos[j];
      if (modules[row][col] !== null) continue;
      for (let r = -2; r <= 2; r++) {
        for (let c = -2; c <= 2; c++) {
          modules[row + r][col + c] =
            (r === -2 || r === 2 || c === -2 || c === 2 || (r === 0 && c === 0));
        }
      }
    }
  }
  // timing patterns
  for (let r = 8; r < size - 8; r++) if (modules[r][6] === null) modules[r][6] = (r % 2 === 0);
  for (let c = 8; c < size - 8; c++) if (modules[6][c] === null) modules[6][c] = (c % 2 === 0);

  // format info
  const bits = bchTypeInfo((ecLevel << 3) | maskPattern);
  for (let i = 0; i < 15; i++) {
    const mod = ((bits >> i) & 1) === 1;
    if (i < 6) modules[i][8] = mod;
    else if (i < 8) modules[i + 1][8] = mod;
    else modules[size - 15 + i][8] = mod;
  }
  for (let i = 0; i < 15; i++) {
    const mod = ((bits >> i) & 1) === 1;
    if (i < 8) modules[8][size - i - 1] = mod;
    else if (i < 9) modules[8][15 - i - 1 + 1] = mod;
    else modules[8][15 - i - 1] = mod;
  }
  modules[size - 8][8] = true; // dark module

  // version info (type >= 7) — not needed for small URLs (type < 7)
  if (typeNumber >= 7) {
    const vbits = bchTypeNumber(typeNumber);
    for (let i = 0; i < 18; i++) {
      const mod = ((vbits >> i) & 1) === 1;
      modules[Math.floor(i / 3)][i % 3 + size - 8 - 3] = mod;
      modules[i % 3 + size - 8 - 3][Math.floor(i / 3)] = mod;
    }
  }

  // map data
  let inc = -1;
  let row = size - 1;
  let bitIndex = 7;
  let byteIndex = 0;
  for (let col = size - 1; col > 0; col -= 2) {
    if (col === 6) col--;
    while (true) {
      for (let c = 0; c < 2; c++) {
        if (modules[row][col - c] === null) {
          let dark = false;
          if (byteIndex < data.length) dark = ((data[byteIndex] >>> bitIndex) & 1) === 1;
          if (maskFunc(maskPattern, row, col - c)) dark = !dark;
          modules[row][col - c] = dark;
          bitIndex--;
          if (bitIndex === -1) { byteIndex++; bitIndex = 7; }
        }
      }
      row += inc;
      if (row < 0 || size <= row) { row -= inc; inc = -inc; break; }
    }
  }
  return modules;
}

// pick the smallest type number (1..9) that fits the data for the given EC level
function bestTypeNumber(byteLen, ecLevel) {
  for (let t = 1; t <= 10; t++) {
    const blocks = getRsBlocks(t, ecLevel);
    let dataCount = 0;
    for (const b of blocks) dataCount += b.data;
    // header = 4 (mode) + 8 (char count) bits = 12 bits = 1.5 bytes
    const capacityBytes = dataCount - 2; // conservative for header + terminator
    if (byteLen <= capacityBytes) return t;
  }
  throw new Error('data too long for supported QR types (max ~ type 10 byte mode)');
}

function maskPenalty(modules) {
  // Simplified penalty: count adjacent same-color runs. Good enough to pick a mask.
  const size = modules.length;
  let penalty = 0;
  for (let r = 0; r < size; r++) {
    for (let c = 0; c < size; c++) {
      let same = 0;
      const dark = modules[r][c];
      for (let dr = -1; dr <= 1; dr++) {
        for (let dc = -1; dc <= 1; dc++) {
          if (dr === 0 && dc === 0) continue;
          const rr = r + dr, cc = c + dc;
          if (rr < 0 || cc < 0 || rr >= size || cc >= size) continue;
          if (modules[rr][cc] === dark) same++;
        }
      }
      if (same > 5) penalty += 3;
    }
  }
  return penalty;
}

/**
 * Build the QR module matrix for a text string.
 * @param {string} text
 * @param {{errorCorrectLevel?: 'L'|'M'|'Q'|'H'}} [opts]
 * @returns {boolean[][]} matrix where true = dark module
 */
export function qrModules(text, opts = {}) {
  const level = QRErrorCorrectLevel[opts.errorCorrectLevel || 'M'];
  const bytes = utf8Bytes(text);
  const typeNumber = bestTypeNumber(bytes.length, level);
  const data = createData(typeNumber, level, bytes);
  // Try all masks, pick lowest penalty.
  let best = null, bestPenalty = Infinity;
  for (let mask = 0; mask < 8; mask++) {
    const m = makeMatrix(typeNumber, level, data, mask);
    const p = maskPenalty(m);
    if (p < bestPenalty) { bestPenalty = p; best = m; }
  }
  return best;
}

function utf8Bytes(str) {
  const out = [];
  for (let i = 0; i < str.length; i++) {
    const c = str.charCodeAt(i);
    if (c < 0x80) out.push(c);
    else if (c < 0x800) { out.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f)); }
    else { out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f)); }
  }
  return out;
}

/**
 * Render a QR code for `text` into a <canvas> element.
 * @param {HTMLCanvasElement} canvas
 * @param {string} text
 * @param {{size?: number, margin?: number, dark?: string, light?: string,
 *          errorCorrectLevel?: 'L'|'M'|'Q'|'H'}} [opts]
 */
export function renderQrToCanvas(canvas, text, opts = {}) {
  const matrix = qrModules(text, opts);
  const count = matrix.length;
  const margin = (opts.margin === null || opts.margin === undefined) ? 4 : opts.margin;
  const pxSize = opts.size || 240;
  const cells = count + margin * 2;
  const cell = Math.floor(pxSize / cells) || 1;
  const dim = cell * cells;
  canvas.width = dim;
  canvas.height = dim;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = opts.light || '#ffffff';
  ctx.fillRect(0, 0, dim, dim);
  ctx.fillStyle = opts.dark || '#000000';
  for (let r = 0; r < count; r++) {
    for (let c = 0; c < count; c++) {
      if (matrix[r][c]) {
        ctx.fillRect((c + margin) * cell, (r + margin) * cell, cell, cell);
      }
    }
  }
}
