import { test } from 'node:test';
import assert from 'node:assert/strict';
import { RX5808_CHANNELS, nearestRxChannel } from '../dashboard/public/rx5808-channels.js';

test('channel table has 40 entries including A1 and R8', () => {
  assert.equal(RX5808_CHANNELS.length, 40);
  assert.deepEqual(RX5808_CHANNELS[0], { name: 'A1', freq: 5865 });
  assert.ok(RX5808_CHANNELS.some((c) => c.name === 'R8' && c.freq === 5917));
});

test('nearestRxChannel snaps to the closest channel within tolerance', () => {
  assert.deepEqual(nearestRxChannel(5865.3), { name: 'A1', freq: 5865 });
  assert.deepEqual(nearestRxChannel(5800.0), { name: 'F4', freq: 5800 });
  assert.equal(nearestRxChannel(5500.0), null);
});
