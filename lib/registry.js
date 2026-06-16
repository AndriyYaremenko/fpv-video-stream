import { randomBytes } from 'node:crypto';
import { readFileSync, writeFileSync, chmodSync } from 'node:fs';
import yaml from 'js-yaml';

const ID_RE = /^[a-z0-9][a-z0-9_-]{1,30}$/;

const VALID_KINDS = ['camera', 'scanner'];

export function validateId(id) {
  return typeof id === 'string' && ID_RE.test(id);
}

export function genSecret(bytes = 24) {
  return randomBytes(bytes).toString('base64url');
}

export function loadRegistry(path) {
  const raw = readFileSync(path, 'utf8');
  const reg = yaml.load(raw) || {};
  reg.devices = Array.isArray(reg.devices) ? reg.devices : [];
  return reg;
}

export function saveRegistry(path, reg) {
  const out = yaml.dump(reg, { lineWidth: 120, quotingType: '"' });
  writeFileSync(path, out, 'utf8');
  try { chmodSync(path, 0o600); } catch { /* chmod unsupported (e.g. Windows dev) */ }
}

export function ensureReadUser(reg) {
  if (!reg.read_user) reg.read_user = 'viewer';
  if (!reg.read_pass || reg.read_pass === '' || /^CHANGE_ME/.test(reg.read_pass)) {
    reg.read_pass = genSecret();
  }
  return reg;
}

export function ensurePublishSecrets(reg) {
  for (const d of (reg.devices || [])) {
    if (!d.publish_pass || d.publish_pass === '' || /^CHANGE_ME/.test(d.publish_pass)) {
      d.publish_pass = genSecret();
    }
  }
  return reg;
}

export function addDevice(reg, { id, name, location, kind }) {
  if (!validateId(id)) {
    throw new Error(`invalid device id "${id}" (use lowercase a-z, 0-9, -, _; start alphanumeric)`);
  }
  const deviceKind = kind || 'camera';
  if (!VALID_KINDS.includes(deviceKind)) {
    throw new Error(`invalid kind "${kind}" (expected camera or scanner)`);
  }
  reg.devices = reg.devices || [];
  if (reg.devices.some((d) => d.id === id)) {
    throw new Error(`device "${id}" already exists`);
  }
  const device = { id, name: name || id, location: location || '', kind: deviceKind, publish_pass: genSecret() };
  reg.devices.push(device);
  return device;
}

// First free `<prefix>-NN` id (zero-padded), for auto-generating device ids in the UI.
export function nextDeviceId(reg, prefix = 'pi') {
  const used = new Set((reg.devices || []).map((d) => d.id));
  for (let n = 1; n <= 999; n += 1) {
    const id = `${prefix}-${String(n).padStart(2, '0')}`;
    if (!used.has(id)) return id;
  }
  throw new Error('no free device id');
}

export function removeDevice(reg, id) {
  const i = (reg.devices || []).findIndex((d) => d.id === id);
  if (i === -1) throw new Error(`device "${id}" not found`);
  return reg.devices.splice(i, 1)[0];
}

// Update mutable fields (name/location) of an existing device. id/publish_pass are immutable here.
export function updateDevice(reg, id, fields = {}) {
  const d = (reg.devices || []).find((x) => x.id === id);
  if (!d) throw new Error(`device "${id}" not found`);
  if (fields.name != null) d.name = fields.name;
  if (fields.location != null) d.location = fields.location;
  return d;
}
