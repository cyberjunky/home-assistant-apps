// Deterministic made-up Starlink telemetry: the same second always gives the
// same numbers, so the live mock and the seeded history line up.

const TZ_OFFSET_S = 3600; // charts are read in Dutch winter time

function hash(n) {
  const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
}

/** Smooth 0..1 noise that changes every `period` seconds. */
function noise(t, period, salt = 0) {
  const p = t / period;
  const i = Math.floor(p);
  const f = p - i;
  const s = f * f * (3 - 2 * f);
  return hash(i + salt * 1000.123) * (1 - s) + hash(i + 1 + salt * 1000.123) * s;
}

/** How busy the household is at this hour, 0..1. */
function diurnal(t) {
  const h = (((t + TZ_OFFSET_S) % 86400) + 86400) % 86400 / 3600;
  const bump = (centre, width, height) => height * Math.exp(-(((h - centre) / width) ** 2));
  return 0.12 + bump(8, 1.5, 0.35) + bump(13, 2.5, 0.3) + bump(20.5, 2.2, 0.9);
}

export function sample(t) {
  const day = Math.floor((t + TZ_OFFSET_S) / 86400);
  const busy = diurnal(t);
  const weekend = [0, 6].includes(new Date((t + TZ_OFFSET_S) * 1000).getUTCDay()) ? 1.35 : 1;
  const dayFactor = (0.6 + 0.9 * hash(day * 3.3)) * weekend;

  let down =
    180e3 +
    2.4e6 * busy * dayFactor * (0.15 + 3.2 * noise(t, 300, 1) ** 3 + 0.5 * noise(t, 20, 2)) *
      (0.85 + 0.3 * hash(t));
  // now and then someone downloads something big
  const big = Math.min(1, Math.max(0, (noise(t, 3600, 3) - 0.86) / 0.1));
  down += 110e6 * big * noise(t, 45, 4) * busy;
  const up = 90e3 + down * (0.05 + 0.06 * noise(t, 90, 5)) + 0.8e6 * noise(t, 240, 6) ** 4;

  const badWeather = hash(day * 7.7) > 0.86 ? 1 : 0;
  let latency = 23 + 9 * noise(t, 120, 7) + 5 * noise(t, 7, 8) + badWeather * 14 * noise(t, 600, 9);
  if (hash(t * 1.7) > 0.996) latency += 90 * hash(t * 2.3);
  let drop = 0;
  if (hash(t * 3.1) > (badWeather ? 0.99 : 0.9985)) drop = 0.2 + 0.8 * hash(t * 5.9);

  const hour = (((t + TZ_OFFSET_S) % 86400) + 86400) % 86400 / 3600;
  const snowMelt = hash(day * 5.1) > 0.7 && hour > 2 && hour < 7 ? 38 : 0;
  const power = 49 + 20 * Math.min(1, down / 120e6) + 5 * noise(t, 600, 10) + snowMelt + 2 * hash(t * 0.7);

  return {
    downlinkBps: down,
    uplinkBps: up,
    latencyMs: latency,
    routerLatencyMs: latency + 1.2 + 1.5 * noise(t, 11, 11),
    dropRate: drop,
    powerW: power,
  };
}

/** Short interruptions, a few per day. */
export function outagesBetween(startS, endS) {
  const causes = ["OBSTRUCTED", "NO_PINGS", "NO_DOWNLINK", "OBSTRUCTED", "NO_SATS"];
  const list = [];
  for (let slot = Math.floor(startS / 1800); slot <= Math.floor(endS / 1800); slot++) {
    if (hash(slot * 9.17) < 0.9) continue;
    const start = slot * 1800 + Math.floor(hash(slot * 2.71) * 1700);
    if (start < startS || start > endS) continue;
    list.push({
      startS: start,
      durationS: 2 + Math.floor(hash(slot * 4.4) ** 2 * 22),
      cause: causes[Math.floor(hash(slot * 6.6) * causes.length)],
    });
  }
  return list;
}

/** Obstruction map: a round patch of sky with a tree line low in the north-west. */
export function obstructionGrid(t, size = 123) {
  const cells = new Array(size * size);
  const c = (size - 1) / 2;
  for (let row = 0; row < size; row++) {
    for (let col = 0; col < size; col++) {
      const dx = (col - c) / c;
      const dy = (row - c) / c;
      const r = Math.hypot(dx, dy);
      let value = -1;
      if (r <= 0.98) {
        value = 1;
        const angle = Math.atan2(dy, dx);
        const tree = Math.exp(-(((angle + 2.3) / 0.45) ** 2));
        const edge = 0.74 - 0.2 * tree + 0.03 * Math.sin(angle * 9);
        if (tree > 0.08 && r > edge) {
          value = Math.max(0, 1 - (r - edge) * 6 * tree - 0.25 * hash(row * 131 + col));
        }
        const chimney = Math.hypot(dx - 0.62, dy - 0.48);
        if (chimney < 0.09) value = 0.35 + 3 * chimney;
        // the map is still being surveyed at the very rim early in the week
        if (r > 0.93 && hash(row * 17 + col * 3 + Math.floor(t / 86400)) > 0.7) value = -1;
      }
      cells[row * size + col] = value;
    }
  }
  return cells;
}
