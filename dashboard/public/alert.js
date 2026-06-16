// dashboard/public/alert.js — detection-alert helpers (pure) + Web Audio beep (browser only).

// Stable key for a detection so the same transmitter doesn't re-alert on small freq jitter.
export function detectionKey(d) {
  const band = d.band || '?';
  if (d.channel) return `${band}:${d.channel}`;
  const mhz = Math.round(Number(d.center_mhz) / 5) * 5;
  return `${band}:${mhz}`;
}

// Compare current detections to the previous key set.
// prevKeys null/undefined => baseline (no "new"); else newKeys = keys present now but not before.
export function diffNewKeys(prevKeys, detections) {
  const keys = new Set((detections || []).map(detectionKey));
  if (prevKeys === null || prevKeys === undefined) return { keys, newKeys: [] };
  const newKeys = [...keys].filter((k) => !prevKeys.has(k));
  return { keys, newKeys };
}

// ---- Web Audio beep (browser only; not unit-tested) ----
export class SoundAlerter {
  constructor() {
    this._ctx = null;
    this._armed = false;
  }

  get armed() {
    return this._armed;
  }

  // Must be called from a user gesture (e.g. the 🔔 click) to satisfy browser autoplay policy.
  arm() {
    if (!this._ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      this._ctx = new AC();
    }
    if (this._ctx.state === 'suspended') this._ctx.resume();
    this._armed = true;
  }

  disarm() {
    this._armed = false;
  }

  beep(freq = 880, ms = 180) {
    if (!this._armed || !this._ctx) return;
    const ctx = this._ctx;
    const t = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.3, t + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + ms / 1000);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t);
    osc.stop(t + ms / 1000 + 0.02);
  }
}
