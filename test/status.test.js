import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mergeStatus, computeBitrateKbps } from '../lib/status.js';

const reg = {
  devices: [
    { id: 'pi-01', name: 'A', location: 'x' },
    { id: 'pi-02', name: 'B', location: 'y' },
  ],
};
const now = Date.parse('2026-06-13T12:00:10Z');
const pathsList = {
  items: [
    { name: 'pi-01', ready: true, readyTime: '2026-06-13T12:00:00Z', bytesReceived: 1000, readers: [{}, {}] },
    { name: 'ghost', ready: true, readyTime: '2026-06-13T12:00:00Z', bytesReceived: 5, readers: [] },
  ],
};

test('online device is marked online with readers and uptime', () => {
  const out = mergeStatus(reg, pathsList, now);
  const pi01 = out.find((d) => d.id === 'pi-01');
  assert.equal(pi01.online, true);
  assert.equal(pi01.readers, 2);
  assert.equal(pi01.bytesReceived, 1000);
  assert.equal(pi01.uptimeSec, 10);
});

test('expected device with no path is offline', () => {
  const out = mergeStatus(reg, pathsList, now);
  const pi02 = out.find((d) => d.id === 'pi-02');
  assert.equal(pi02.online, false);
  assert.equal(pi02.uptimeSec, null);
});

test('result preserves registry order and excludes unknown paths', () => {
  const out = mergeStatus(reg, pathsList, now);
  assert.deepEqual(out.map((d) => d.id), ['pi-01', 'pi-02']);
});

test('computeBitrateKbps divides byte delta by time delta', () => {
  assert.equal(computeBitrateKbps(0, 0, 125000, 1000), 1000);
  assert.equal(computeBitrateKbps(null, null, 100, 1000), null);
  assert.equal(computeBitrateKbps(100, 1000, 100, 1000), null);
});
