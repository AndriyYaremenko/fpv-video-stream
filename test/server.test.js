import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../dashboard/server.js';

const registry = {
  read_user: 'viewer', read_pass: 'rpw',
  devices: [{ id: 'pi-01', name: 'A', location: 'x', publish_pass: 'p1' }],
};
const config = {
  dashUser: 'op', dashPass: 'pw', sessionSecret: 'test-secret',
  webrtcBase: 'http://10.8.0.1:8889', readUser: 'viewer', readPass: 'rpw',
  telemetryToken: '',
};
const fakePaths = async () => ({ items: [{ name: 'pi-01', ready: true, readyTime: '2026-06-13T12:00:00Z', bytesReceived: 10, readers: [] }] });

async function startServer() {
  const app = createApp({ registry, getPaths: fakePaths, config });
  const server = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  return { server, base };
}

test('GET /api/devices requires auth', async () => {
  const { server, base } = await startServer();
  const res = await fetch(`${base}/api/devices`, { redirect: 'manual' });
  assert.equal(res.status, 401);
  server.close();
});

test('login then fetch devices returns merged status', async () => {
  const { server, base } = await startServer();
  const login = await fetch(`${base}/login`, {
    method: 'POST', redirect: 'manual',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: 'user=op&pass=pw',
  });
  // cookie-session sets two cookies (value + signature); send both back.
  const cookie = login.headers.getSetCookie().map((c) => c.split(';')[0]).join('; ');
  const res = await fetch(`${base}/api/devices`, { headers: { cookie } });
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.equal(body[0].id, 'pi-01');
  assert.equal(body[0].online, true);
  server.close();
});

test('bad login is rejected', async () => {
  const { server, base } = await startServer();
  const login = await fetch(`${base}/login`, {
    method: 'POST', redirect: 'manual',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: 'user=op&pass=WRONG',
  });
  assert.equal(login.status, 401);
  server.close();
});

test('authed /api/config exposes the read creds for WHEP', async () => {
  const { server, base } = await startServer();
  const login = await fetch(`${base}/login`, {
    method: 'POST', redirect: 'manual',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'user=op&pass=pw',
  });
  const cookie = login.headers.getSetCookie().map((c) => c.split(';')[0]).join('; ');
  const res = await fetch(`${base}/api/config`, { headers: { cookie } });
  const body = await res.json();
  assert.equal(body.webrtcBase, 'http://10.8.0.1:8889');
  assert.equal(body.readUser, 'viewer');
  assert.equal(body.readPass, 'rpw');
  server.close();
});

test('telemetry hook stores last value and surfaces it on devices', async () => {
  const { server, base } = await startServer();
  await fetch(`${base}/api/telemetry/pi-01`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rssi: -60, alarm: false }),
  });
  const login = await fetch(`${base}/login`, {
    method: 'POST', redirect: 'manual',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'user=op&pass=pw',
  });
  const cookie = login.headers.getSetCookie().map((c) => c.split(';')[0]).join('; ');
  const res = await fetch(`${base}/api/devices`, { headers: { cookie } });
  const body = await res.json();
  assert.equal(body[0].telemetry.rssi, -60);
  server.close();
});
