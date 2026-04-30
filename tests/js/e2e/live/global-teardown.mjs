// tests/js/e2e/live/global-teardown.mjs
import { unlinkSync } from 'node:fs';
import { serverProcess, SERVER_URL_FILE } from './global-setup.mjs';

/**
 * @param {import('@playwright/test').FullConfig} config
 */
export default async function globalTeardown(config) {
  const liveProject = config.projects.find(p => p.name === 'live');
  if (!liveProject) return;

  if (serverProcess && serverProcess.exitCode === null) {
    await new Promise((resolve) => {
      serverProcess.on('exit', resolve);
      serverProcess.kill();
    });
  }

  // Clean up the URL file written by globalSetup.
  try { unlinkSync(SERVER_URL_FILE); } catch { /* already gone */ }
}
