// tests/js/e2e/live/global-teardown.mjs
import { serverProcess } from './global-setup.mjs';

export default async function globalTeardown() {
  if (serverProcess && serverProcess.exitCode === null) {
    await new Promise((resolve) => {
      serverProcess.on('exit', resolve);
      serverProcess.kill();
    });
  }
}
