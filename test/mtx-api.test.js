import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fetchPaths } from '../lib/mtx-api.js';

test('fetchPaths returns parsed items on success', async () => {
  const fakeFetch = async (url) => {
    assert.equal(url, 'http://127.0.0.1:9997/v3/paths/list');
    return { ok: true, json: async () => ({ items: [{ name: 'pi-01' }] }) };
  };
  const res = await fetchPaths('http://127.0.0.1:9997', fakeFetch);
  assert.equal(res.items[0].name, 'pi-01');
});

test('fetchPaths returns empty items when the API errors', async () => {
  const fakeFetch = async () => { throw new Error('ECONNREFUSED'); };
  const res = await fetchPaths('http://127.0.0.1:9997', fakeFetch);
  assert.deepEqual(res, { items: [] });
});

test('fetchPaths returns empty items on non-ok response', async () => {
  const fakeFetch = async () => ({ ok: false, status: 500, json: async () => ({}) });
  const res = await fetchPaths('http://127.0.0.1:9997', fakeFetch);
  assert.deepEqual(res, { items: [] });
});
