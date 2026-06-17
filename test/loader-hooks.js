// test/loader-hooks.js — ESM loader hooks for the test suite.
// Rewrites browser-absolute specifiers (e.g. /alert.js) to their real on-disk paths
// so that modules designed for browser serving can be imported under `node --test`.
import { pathToFileURL, fileURLToPath } from 'node:url';
import { resolve as resolvePath } from 'node:path';

const PROJECT_ROOT = resolvePath(fileURLToPath(import.meta.url), '..', '..');

const BROWSER_ABS_MAP = {
  '/alert.js': pathToFileURL(resolvePath(PROJECT_ROOT, 'dashboard/public/alert.js')).href,
};

export function resolve(specifier, context, nextResolve) {
  if (Object.prototype.hasOwnProperty.call(BROWSER_ABS_MAP, specifier)) {
    return { shortCircuit: true, url: BROWSER_ABS_MAP[specifier] };
  }
  return nextResolve(specifier, context);
}
