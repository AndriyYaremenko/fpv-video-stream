import { randomBytes } from 'node:crypto';
import { readFileSync, writeFileSync, chmodSync } from 'node:fs';
import yaml from 'js-yaml';

const ID_RE = /^[a-z0-9][a-z0-9_-]{1,30}$/;

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

export function addDevice(reg, { id, name, location }) {
  if (!validateId(id)) {
    throw new Error(`invalid device id "${id}" (use lowercase a-z, 0-9, -, _; start alphanumeric)`);
  }
  reg.devices = reg.devices || [];
  if (reg.devices.some((d) => d.id === id)) {
    throw new Error(`device "${id}" already exists`);
  }
  const device = { id, name: name || id, location: location || '', publish_pass: genSecret() };
  reg.devices.push(device);
  return device;
}
