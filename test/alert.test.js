import { test } from 'node:test';
import assert from 'node:assert/strict';
import { detectionKey, diffNewKeys } from '../dashboard/public/alert.js';

test('detectionKey uses channel when present', () => {
  assert.equal(detectionKey({ band: '5.8G', channel: 'F4', center_mhz: 5801 }), '5.8G:F4');
});

test('detectionKey buckets center_mhz to 5 MHz when no channel (ignores jitter)', () => {
  assert.equal(detectionKey({ band: '5.8G', center_mhz: 5734 }), '5.8G:5735');
  assert.equal(detectionKey({ band: '5.8G', center_mhz: 5736 }), '5.8G:5735');
});

test('diffNewKeys baseline (null prev) yields no newKeys', () => {
  const { keys, newKeys } = diffNewKeys(null, [{ band: '5.8G', channel: 'F4' }]);
  assert.deepEqual(newKeys, []);
  assert.ok(keys.has('5.8G:F4'));
});

test('diffNewKeys flags a genuinely new key', () => {
  const prev = new Set(['5.8G:F4']);
  const { newKeys } = diffNewKeys(prev, [{ band: '5.8G', channel: 'F4' }, { band: '2.4G', channel: 'G3' }]);
  assert.deepEqual(newKeys, ['2.4G:G3']);
});

test('diffNewKeys: persisting key is not new, removed key ignored', () => {
  const prev = new Set(['5.8G:F4', '2.4G:G3']);
  const { keys, newKeys } = diffNewKeys(prev, [{ band: '5.8G', channel: 'F4' }]);
  assert.deepEqual(newKeys, []);
  assert.ok(keys.has('5.8G:F4') && !keys.has('2.4G:G3'));
});
