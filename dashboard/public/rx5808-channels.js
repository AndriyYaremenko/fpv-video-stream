// 40 standard 5.8 GHz FPV channels (Band A/B/E/F/R) — JS mirror of agent/scan/rx5808.py.
const _BANDS = {
  A: [5865, 5845, 5825, 5805, 5785, 5765, 5745, 5725],
  B: [5733, 5752, 5771, 5790, 5809, 5828, 5847, 5866],
  E: [5705, 5685, 5665, 5645, 5885, 5905, 5925, 5945],
  F: [5740, 5760, 5780, 5800, 5820, 5840, 5860, 5880],
  R: [5658, 5695, 5732, 5769, 5806, 5843, 5880, 5917],
};

export const RX5808_CHANNELS = Object.entries(_BANDS).flatMap(
  ([b, fs]) => fs.map((f, i) => ({ name: `${b}${i + 1}`, freq: f })),
);

// Nearest channel to a frequency (MHz) within tol; null if none. First wins on ties.
export function nearestRxChannel(mhz, tol = 10) {
  let best = null;
  let bestD = tol + 1e-9;
  for (const ch of RX5808_CHANNELS) {
    const d = Math.abs(ch.freq - mhz);
    if (d < bestD) { bestD = d; best = ch; }
  }
  return best;
}
