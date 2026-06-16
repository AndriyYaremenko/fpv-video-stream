import { test } from 'node:test';
import assert from 'node:assert/strict';
import { genSecret, addDevice, ensureReadUser, ensurePublishSecrets, validateId, nextDeviceId, removeDevice } from '../lib/registry.js';

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
