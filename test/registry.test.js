import { test } from 'node:test';
import assert from 'node:assert/strict';
import { genSecret, addDevice, ensureReadUser, ensurePublishSecrets, validateId, nextDeviceId, removeDevice, updateDevice } from '../lib/registry.js';

test('genSecret returns a long url-safe token', () => {
  const s = genSecret();
  assert.ok(s.length >= 24);
  assert.match(s, /^[A-Za-z0-9_-]+$/);
  assert.notEqual(genSecret(), genSecret());
});

test('validateId accepts valid ids and rejects bad ones', () => {
  assert.equal(validateId('pi-01'), true);
  assert.equal(validateId('cam_2'), true);
  assert.equal(validateId('UPPER'), false);     // no uppercase
  assert.equal(validateId('has space'), false);
  assert.equal(validateId('-leading'), false);
  assert.equal(validateId(''), false);
});

test('addDevice appends a device with a generated publish_pass', () => {
  const reg = { read_user: 'viewer', read_pass: 'x', devices: [] };
  const d = addDevice(reg, { id: 'pi-09', name: 'Shed', location: 'West' });
  assert.equal(d.id, 'pi-09');
  assert.equal(d.name, 'Shed');
  assert.ok(d.publish_pass.length >= 24);
  assert.equal(reg.devices.length, 1);
});

test('addDevice rejects duplicate id', () => {
  const reg = { devices: [{ id: 'pi-01', name: 'a', location: 'b', publish_pass: 'p' }] };
  assert.throws(() => addDevice(reg, { id: 'pi-01', name: 'x', location: 'y' }), /exists/);
});

test('addDevice rejects invalid id', () => {
  const reg = { devices: [] };
  assert.throws(() => addDevice(reg, { id: 'Bad Id', name: 'x', location: 'y' }), /invalid/i);
});

test('ensureReadUser fills missing read credentials', () => {
  const reg = { devices: [] };
  ensureReadUser(reg);
  assert.ok(reg.read_user);
  assert.ok(reg.read_pass.length >= 24);
});

test('ensurePublishSecrets rotates placeholder/missing publish passwords, leaves real ones', () => {
  const reg = { devices: [
    { id: 'pi-01', publish_pass: 'CHANGE_ME_PI01' },
    { id: 'pi-02', publish_pass: 'aRealStrongPassword123456' },
    { id: 'pi-03' },
  ] };
  ensurePublishSecrets(reg);
  assert.doesNotMatch(reg.devices[0].publish_pass, /^CHANGE_ME/);
  assert.ok(reg.devices[0].publish_pass.length >= 24);
  assert.equal(reg.devices[1].publish_pass, 'aRealStrongPassword123456'); // untouched
  assert.ok(reg.devices[2].publish_pass.length >= 24);                    // filled in
});

test('nextDeviceId returns the first free pi-NN', () => {
  assert.equal(nextDeviceId({ devices: [] }), 'pi-01');
  assert.equal(nextDeviceId({ devices: [{ id: 'pi-01' }, { id: 'pi-02' }] }), 'pi-03');
  assert.equal(nextDeviceId({ devices: [{ id: 'pi-02' }] }), 'pi-01'); // fills the gap
});

test('removeDevice removes and returns the device, throws if missing', () => {
  const reg = { devices: [{ id: 'pi-01' }, { id: 'pi-02' }] };
  const removed = removeDevice(reg, 'pi-01');
  assert.equal(removed.id, 'pi-01');
  assert.deepEqual(reg.devices.map((d) => d.id), ['pi-02']);
  assert.throws(() => removeDevice(reg, 'nope'), /not found/);
});

test('updateDevice changes name/location (partial), throws if missing', () => {
  const reg = { devices: [{ id: 'pi-01', name: 'A', location: 'x', publish_pass: 'p' }] };
  const d = updateDevice(reg, 'pi-01', { name: 'New', location: 'Y' });
  assert.equal(d.name, 'New');
  assert.equal(d.location, 'Y');
  updateDevice(reg, 'pi-01', { name: 'Only' });          // partial
  assert.equal(reg.devices[0].name, 'Only');
  assert.equal(reg.devices[0].location, 'Y');            // location untouched
  assert.equal(reg.devices[0].publish_pass, 'p');        // pass never changes
  assert.throws(() => updateDevice(reg, 'nope', { name: 'z' }), /not found/);
});

test('addDevice defaults kind to camera and stores an explicit scanner kind', () => {
  const reg = { devices: [] };
  const cam = addDevice(reg, { id: 'cam-1', name: 'Cam' });
  assert.equal(cam.kind, 'camera');
  const scan = addDevice(reg, { id: 'scan-01', name: 'Scanner', kind: 'scanner' });
  assert.equal(scan.kind, 'scanner');
});

test('addDevice rejects an invalid kind', () => {
  const reg = { devices: [] };
  assert.throws(() => addDevice(reg, { id: 'x-1', name: 'x', kind: 'drone' }), /invalid kind/i);
});
