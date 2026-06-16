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
  pushOpts: { wgIp: '10.8.0.1', rtspPort: 8554, srtPort: 8890, videoDevice: '/dev/video0', framerate: 30, videoSize: '720x576', bitrate: '2M' },
  persistRegistry: () => {},
};
const fakePaths = async () => ({ items: [{ name: 'pi-01', ready: true, readyTime: '2026-06-13T12:00:00Z', bytesReceived: 10, readers: [] }] });

async function startServer() {
  const app = createApp({ registry, getPaths: fakePaths, config });
  const server = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  return { server, base };
}

// Helpers for the device-management tests (each uses its own fresh registry to avoid cross-test state).
async function startWith(reg, cfg = config) {
  const app = createApp({ registry: reg, getPaths: fakePaths, config: cfg });
  const server = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}
async function login(base) {
  const l = await fetch(`${base}/login`, {
    method: 'POST', redirect: 'manual',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'user=op&pass=pw',
  });
  return l.headers.getSetCookie().map((c) => c.split(';')[0]).join('; ');
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

test('POST /api/devices requires auth', async () => {
  const { server, base } = await startWith({ devices: [] });
  const res = await fetch(`${base}/api/devices`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'x' }),
  });
  assert.equal(res.status, 401);
  server.close();
});

test('POST /api/devices auto-generates id, returns creds + push commands, persists', async () => {
  const reg = { read_user: 'viewer', read_pass: 'rpw', devices: [] };
  let persisted = 0;
  const { server, base } = await startWith(reg, { ...config, persistRegistry: () => { persisted += 1; } });
  const cookie = await login(base);
  const res = await fetch(`${base}/api/devices`, {
    method: 'POST', headers: { cookie, 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'Gate', location: 'North' }),
  });
  const body = await res.json();
  assert.equal(res.status, 201);
  assert.equal(body.device.id, 'pi-01');               // auto-generated
  assert.ok(body.device.publish_pass.length >= 24);
  assert.match(body.push.rtsp, /rtsp:\/\/pi-01:/);
  assert.match(body.push.srt, /streamid=#!::m=publish,r=pi-01/);
  assert.equal(persisted, 1);
  assert.equal(reg.devices.length, 1);
  server.close();
});

test('POST /api/devices honors an explicit id and rejects duplicates with 409', async () => {
  const reg = { devices: [{ id: 'pi-01', name: 'a', location: 'b', publish_pass: 'p' }] };
  const { server, base } = await startWith(reg);
  const cookie = await login(base);
  const ok = await fetch(`${base}/api/devices`, {
    method: 'POST', headers: { cookie, 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: 'cam-7', name: 'Cam 7', location: 'Door' }),
  });
  assert.equal(ok.status, 201);
  assert.equal((await ok.json()).device.id, 'cam-7');
  const dup = await fetch(`${base}/api/devices`, {
    method: 'POST', headers: { cookie, 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: 'pi-01', name: 'dup' }),
  });
  assert.equal(dup.status, 409);
  server.close();
});

test('GET /api/devices/:id/push returns push commands for an existing device', async () => {
  const reg = { devices: [{ id: 'pi-01', name: 'A', location: 'x', publish_pass: 'secretpass' }] };
  const { server, base } = await startWith(reg);
  const cookie = await login(base);
  const res = await fetch(`${base}/api/devices/pi-01/push`, { headers: { cookie } });
  const body = await res.json();
  assert.equal(res.status, 200);
  assert.match(body.push.rtsp, /rtsp:\/\/pi-01:secretpass@10\.8\.0\.1:8554\/pi-01/);
  const missing = await fetch(`${base}/api/devices/nope/push`, { headers: { cookie } });
  assert.equal(missing.status, 404);
  server.close();
});

test('DELETE /api/devices/:id removes the device and persists; 404 if missing', async () => {
  const reg = { devices: [{ id: 'pi-01', name: 'A', location: 'x', publish_pass: 'p1' }, { id: 'pi-02', name: 'B', location: 'y', publish_pass: 'p2' }] };
  let persisted = 0;
  const { server, base } = await startWith(reg, { ...config, persistRegistry: () => { persisted += 1; } });
  const cookie = await login(base);
  const del = await fetch(`${base}/api/devices/pi-01`, { method: 'DELETE', headers: { cookie } });
  assert.equal(del.status, 200);
  assert.deepEqual(reg.devices.map((d) => d.id), ['pi-02']);
  assert.equal(persisted, 1);
  const missing = await fetch(`${base}/api/devices/nope`, { method: 'DELETE', headers: { cookie } });
  assert.equal(missing.status, 404);
  server.close();
});
