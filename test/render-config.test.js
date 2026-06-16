import { test } from 'node:test';
import assert from 'node:assert/strict';
import yaml from 'js-yaml';
import { buildConfigObject, renderConfig } from '../lib/render-config.js';

const reg = {
  read_user: 'viewer',
  read_pass: 'readsecret',
  devices: [
    { id: 'pi-01', name: 'A', location: 'x', publish_pass: 'p1' },
    { id: 'pi-02', name: 'B', location: 'y', publish_pass: 'p2' },
  ],
};
const opts = { wgIp: '10.8.0.1', rtspPort: 8554, srtPort: 8890, webrtcPort: 8889, iceUdpPort: 8189, apiHost: '127.0.0.1', apiPort: 9997 };

test('addresses are bound to the WG IP and API to localhost', () => {
  const c = buildConfigObject(reg, opts);
  assert.equal(c.rtspAddress, '10.8.0.1:8554');
  assert.equal(c.srtAddress, '10.8.0.1:8890');
  assert.equal(c.webrtcAddress, '10.8.0.1:8889');
  assert.equal(c.webrtcLocalUDPAddress, ':8189');
  assert.equal(c.apiAddress, '127.0.0.1:9997');
});

test('webrtc advertises only the WG IP as host candidate', () => {
  const c = buildConfigObject(reg, opts);
  assert.equal(c.webrtcIPsFromInterfaces, false);
  assert.deepEqual(c.webrtcAdditionalHosts, ['10.8.0.1']);
});

test('HTTP-server encryption flags are booleans, not strings (MediaMTX expects bool)', () => {
  const c = buildConfigObject(reg, opts);
  assert.strictEqual(c.webrtcEncryption, false); // MediaMTX: cannot unmarshal string into bool
  assert.strictEqual(c.rtspEncryption, 'no');    // rtsp is a string enum, stays a string
});

test('each device gets a publish-only user scoped to its own path', () => {
  const c = buildConfigObject(reg, opts);
  const pi01 = c.authInternalUsers.find((u) => u.user === 'pi-01');
  assert.equal(pi01.pass, 'p1');
  assert.deepEqual(pi01.permissions, [{ action: 'publish', path: 'pi-01' }]);
  const canCrossPublish = pi01.permissions.some((p) => p.path === 'pi-02');
  assert.equal(canCrossPublish, false);
});

test('a single read user can read all paths', () => {
  const c = buildConfigObject(reg, opts);
  const reader = c.authInternalUsers.find((u) => u.user === 'viewer');
  assert.equal(reader.pass, 'readsecret');
  assert.deepEqual(reader.permissions, [{ action: 'read' }]);
});

test('the control API is only usable from localhost', () => {
  const c = buildConfigObject(reg, opts);
  const apiUser = c.authInternalUsers.find((u) => u.permissions.some((p) => p.action === 'api'));
  assert.ok(apiUser.ips.includes('127.0.0.1'));
});

test('uses a single catch-all path (no per-device path entries)', () => {
  const c = buildConfigObject(reg, opts);
  assert.deepEqual(Object.keys(c.paths), ['all_others']);
});

test('renderConfig produces parseable YAML matching the object', () => {
  const text = renderConfig(reg, opts);
  const parsed = yaml.load(text);
  assert.equal(parsed.rtspAddress, '10.8.0.1:8554');
  assert.equal(parsed.authInternalUsers.length, 4); // api + reader + 2 devices
});

test('scanner devices get no publish user in MediaMTX config', () => {
  const regWithScanner = {
    read_user: 'viewer', read_pass: 'readsecret',
    devices: [
      { id: 'pi-01', name: 'A', location: 'x', kind: 'camera', publish_pass: 'p1' },
      { id: 'scan-01', name: 'S', location: 'z', kind: 'scanner', publish_pass: 'ps' },
    ],
  };
  const c = buildConfigObject(regWithScanner, opts);
  assert.ok(c.authInternalUsers.find((u) => u.user === 'pi-01'));
  assert.equal(c.authInternalUsers.find((u) => u.user === 'scan-01'), undefined);
});
