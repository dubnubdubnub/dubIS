// tests/js/e2e/live/global-setup.mjs
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, '..', '..', '..', '..');
const FIXTURE_DIR = join(__dirname, '..', 'fixtures', 'e2e-seed');
const TEST_SOURCE = `test:${Date.now()}-${process.pid}`;

/**
 * Path to the file where globalSetup writes the server URL for workers to read.
 * Workers can't inherit process.env from globalSetup (separate processes),
 * so we use a file as the communication channel.
 */
export const SERVER_URL_FILE = join(__dirname, '.server-url');

/** @type {import('child_process').ChildProcess | null} */
let serverProcess = null;
/** @type {string | null} */
let serverDataDir = null;

/**
 * `config.projects` always lists every project declared in playwright.config.mjs
 * (functional/quality/live), regardless of which project(s) were actually
 * requested with `--project` on the CLI — so checking `config.projects.find(...)`
 * alone would spawn the live backend even for a plain `--project functional`
 * run. Playwright's globalSetup doesn't otherwise expose "which projects were
 * selected", so parse it out of argv the same way the CLI does.
 *
 * @returns {boolean} true if no --project filter was given (all projects,
 *   including 'live', will run) or if 'live' is one of the requested projects.
 */
function isLiveProjectSelected() {
  const args = process.argv.slice(2);
  const requested = [];
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--project') {
      requested.push(args[i + 1]);
    } else {
      const match = arg.match(/^--project=(.+)$/);
      if (match) requested.push(match[1]);
    }
  }
  return requested.length === 0 || requested.includes('live');
}

/**
 * @param {import('@playwright/test').FullConfig} config
 */
export default async function globalSetup(config) {
  // Only start the server when the 'live' project is actually selected for
  // this run — not merely defined in the config (see isLiveProjectSelected).
  const liveProject = config.projects.find(p => p.name === 'live');
  if (!liveProject || !isLiveProjectSelected()) return;

  const pythonExe = process.env.DUBIS_PYTHON || process.env.PYTHON ||
    (process.platform === 'win32' ? 'python' : 'python3');

  // Copy fixtures into a fresh temp data dir ourselves — python -m server
  // doesn't do fixture staging (that was tests/e2e-server.py's job, now
  // deleted). --data-dir just needs to already contain the seed CSVs.
  const { mkdtempSync, cpSync } = await import('node:fs');
  const { tmpdir } = await import('node:os');
  const dataDir = mkdtempSync(join(tmpdir(), 'dubis-live-'));
  cpSync(FIXTURE_DIR, dataDir, { recursive: true });
  serverDataDir = dataDir;

  const child = spawn(pythonExe, [
    '-m', 'server',
    '--data-dir', dataDir,
    '--port', '0',
    '--static-dir', REPO_ROOT,
    '--test-source', TEST_SOURCE,
    '--rollback-on-exit',
  ], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  child.stderr.on('data', (chunk) => {
    for (const line of chunk.toString().split('\n')) {
      if (line) process.stderr.write(`[server] ${line}\n`);
    }
  });

  const url = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      child.kill();
      reject(new Error('python -m server did not print READY:<port> within 15 seconds'));
    }, 15_000);

    let buffer = '';
    child.stdout.on('data', (chunk) => {
      buffer += chunk.toString();
      const match = buffer.match(/READY:(\d+)/);
      if (match) {
        clearTimeout(timeout);
        resolve(`http://127.0.0.1:${match[1]}`);
      }
    });

    child.on('error', (err) => {
      clearTimeout(timeout);
      reject(new Error(`Failed to spawn python -m server: ${err.message}`));
    });

    child.on('exit', (code) => {
      clearTimeout(timeout);
      reject(new Error(`python -m server exited with code ${code} before READY`));
    });
  });

  serverProcess = child;
  process.env.E2E_SERVER_URL = url;

  // Write the URL + reset endpoint info to a file so worker processes can read it.
  writeFileSync(SERVER_URL_FILE, url, 'utf8');
}

export { serverProcess, serverDataDir, TEST_SOURCE };
