// Fill the recorder's data folder with 60 days of made-up history so the
// day/week/month views have something to show. For screenshots only.
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { foldSamplesToMinutes } from "./core/energyBuckets.ts";
import { foldSamplesToLatencyMinutes } from "./core/latencyBuckets.ts";
import { packCells } from "./collector/obstructionStore.mts";
// @ts-expect-error plain module
import { sample, obstructionGrid } from "./mock-signal.mjs";

const dir = process.argv[2];
mkdirSync(dir, { recursive: true });
const DAYS = 95;
const endS = Math.floor(Date.now() / 60000) * 60 - 60;
const startS = endS - DAYS * 86400;
const energy = join(dir, "energy.ndjson");
const latency = join(dir, "latency.ndjson");
writeFileSync(energy, "");
writeFileSync(latency, "");

for (let chunk = startS; chunk < endS; chunk += 21600) {
  const samples = [];
  for (let t = chunk; t < Math.min(chunk + 21600, endS); t++) {
    const s = sample(t);
    samples.push({
      timestampMs: t * 1000,
      latencyMs: s.latencyMs,
      dropRate: s.dropRate,
      downlinkBps: s.downlinkBps,
      uplinkBps: s.uplinkBps,
      powerW: s.powerW,
      routerLatencyMs: s.routerLatencyMs,
      routerPingSuccessPercent: 99.9,
    });
  }
  const byMinute = <T extends { minute: number }>(map: Map<number, T>) =>
    [...map.values()].sort((a, b) => a.minute - b.minute).map((row) => JSON.stringify(row)).join("\n") + "\n";
  appendFileSync(energy, byMinute(foldSamplesToMinutes(samples)));
  appendFileSync(latency, byMinute(foldSamplesToLatencyMinutes(samples)));
}

const snapshots = [];
const hourMs = 3_600_000;
const newest = Math.floor(Date.now() / hourMs) * hourMs - 20 * 60_000;
for (let i = 7 * 24 - 2; i >= 0; i--) {
  const takenAtMs = newest - i * hourMs;
  snapshots.push(
    JSON.stringify({
      takenAtMs,
      gridSize: 123,
      packedCells: packCells(obstructionGrid(Math.floor(takenAtMs / 1000))),
      maxThetaDeg: 65,
    }),
  );
}
writeFileSync(join(dir, "obstruction.ndjson"), snapshots.join("\n") + "\n");
console.log("seeded", DAYS, "days into", dir);
