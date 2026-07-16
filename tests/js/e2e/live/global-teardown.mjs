// tests/js/e2e/live/global-teardown.mjs
import { rmSync, unlinkSync } from 'node:fs';
import { serverDataDir, serverProcess, SERVER_URL_FILE } from './global-setup.mjs';

/**
 * @param {import('@playwright/test').FullConfig} config
 */
export default async function globalTeardown(config) {
  const liveProject = config.projects.find(p => p.name === 'live');
  if (!liveProject) return;

  if (serverProcess && serverProcess.exitCode === null) {
    await new Promise((resolve) => {
      serverProcess.on('exit', resolve);
      // SIGTERM (Node's default kill signal) triggers uvicorn's graceful
      // shutdown + normal interpreter exit, which runs the --rollback-on-exit
      // atexit hook registered in server/__main__.py.
      serverProcess.kill();
    });
  }

  // Clean up the URL file written by globalSetup.
  try { unlinkSync(SERVER_URL_FILE); } catch { /* already gone */ }

  // Clean up the temp data dir globalSetup copied fixtures into.
  if (serverDataDir) {
    try { rmSync(serverDataDir, { recursive: true, force: true }); } catch { /* already gone */ }
  }
}
