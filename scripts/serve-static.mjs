#!/usr/bin/env node
/**
 * Minimal static file server for Playwright E2E tests.
 *
 * Replaces `npx serve` which has stability issues on macOS when handling
 * many sequential connections (e.g. a large Playwright test suite).
 *
 * Usage: node scripts/serve-static.js [root] [port]
 *   root  – directory to serve (default: current working directory)
 *   port  – port to listen on (default: 3123)
 *
 * Identity: GET /__serve-root answers with the root this process serves, so a
 * Playwright run can prove the server it is about to reuse belongs to its own
 * tree (tests/js/e2e/serve-root-setup.mjs). Exact path, read-only, inert for
 * every other request; it lists nothing.
 */

import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(process.argv[2] ?? '.');
const PORT = parseInt(process.argv[3] ?? '3123', 10);

// Kept in sync with SERVE_ROOT_ENDPOINT in scripts/serve-root-lib.mjs.
const IDENTITY_PATH = '/__serve-root';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js':   'application/javascript; charset=utf-8',
  '.mjs':  'application/javascript; charset=utf-8',
  '.css':  'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png':  'image/png',
  '.jpg':  'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.svg':  'image/svg+xml',
  '.ico':  'image/x-icon',
  '.woff': 'font/woff',
  '.woff2':'font/woff2',
  '.ttf':  'font/ttf',
  '.txt':  'text/plain; charset=utf-8',
};

const server = http.createServer((req, res) => {
  // Ignore query strings
  const urlPath = req.url.split('?')[0];

  // Identity endpoint (see header). Compared against the raw path on purpose:
  // a percent-encoded spelling must NOT reach it, so that a server which
  // predates this endpoint keeps answering 404 for it — the guard reads a
  // missing identity as "unknown tree", never as a pass.
  if (urlPath === IDENTITY_PATH) {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.writeHead(405, { Allow: 'GET, HEAD', 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('Method not allowed');
      return;
    }
    const body = JSON.stringify({ root: ROOT, pid: process.pid });
    res.writeHead(200, {
      'Content-Type': 'application/json; charset=utf-8',
      'Content-Length': Buffer.byteLength(body),
      'Cache-Control': 'no-store',
    });
    res.end(req.method === 'HEAD' ? undefined : body);
    return;
  }

  // Resolve to a filesystem path, preventing directory traversal
  let filePath = path.join(ROOT, decodeURIComponent(urlPath));
  if (!filePath.startsWith(ROOT)) {
    res.writeHead(403);
    res.end('Forbidden');
    return;
  }

  // If the path is a directory, try index.html (SPA-style fallback)
  let stat;
  try {
    stat = fs.statSync(filePath);
  } catch {
    // Fall back to root index.html for SPA routing
    filePath = path.join(ROOT, 'index.html');
    try {
      stat = fs.statSync(filePath);
    } catch {
      res.writeHead(404);
      res.end('Not found');
      return;
    }
  }

  if (stat.isDirectory()) {
    filePath = path.join(filePath, 'index.html');
    try {
      stat = fs.statSync(filePath);
    } catch {
      res.writeHead(404);
      res.end('Not found');
      return;
    }
  }

  const ext = path.extname(filePath).toLowerCase();
  const contentType = MIME[ext] ?? 'application/octet-stream';

  res.writeHead(200, {
    'Content-Type': contentType,
    'Content-Length': stat.size,
    // Allow caching for assets but not HTML
    'Cache-Control': ext === '.html' ? 'no-cache' : 'public, max-age=3600',
  });

  fs.createReadStream(filePath).pipe(res);
});

server.listen(PORT, '127.0.0.1', () => {
  // Log to stderr so Playwright's stdout capture doesn't interfere
  process.stderr.write(`Static server listening on http://127.0.0.1:${PORT} (root: ${ROOT})\n`);
});

server.on('error', (err) => {
  process.stderr.write(`Server error: ${err.message}\n`);
  process.exit(1);
});

// Graceful shutdown
process.on('SIGTERM', () => server.close());
process.on('SIGINT',  () => server.close());
