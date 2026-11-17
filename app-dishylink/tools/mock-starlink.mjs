// A pretend Starlink dish (port 19201) and router (port 19001) speaking the same
// grpc-web the real ones do, with made-up numbers. For screenshots only.

import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { createFileRegistry, fromBinary, fromJson, toBinary, toJson } from "@bufbuild/protobuf";
import { FileDescriptorSetSchema } from "@bufbuild/protobuf/wkt";
import { sample, outagesBetween, obstructionGrid } from "./mock-signal.mjs";

const registry = createFileRegistry(
  fromBinary(FileDescriptorSetSchema, readFileSync("public/dish.protoset")),
);
const RequestSchema = registry.getMessage("SpaceX.API.Device.Request");
const ResponseSchema = registry.getMessage("SpaceX.API.Device.Response");

const BOOT_S = Math.floor(Date.now() / 1000) - 12 * 86400 - 5 * 3600 - 1234;
const ROUTER_BOOT_S = BOOT_S + 95;
const GPS_OFFSET_S = 315_964_800 - 18;
const DISH_ID = "ut01000000-00000000-00d15b01";
const ROUTER_ID = "Router-010000000000000000C0FFEE";

const nowS = () => Math.floor(Date.now() / 1000);

const dishInfo = {
  id: DISH_ID,
  hardwareVersion: "rev4_panda_prod2",
  softwareVersion: "2026.10.27.mr71432",
  countryCode: "NL",
  bootcount: 37,
};
const routerInfo = {
  id: ROUTER_ID,
  hardwareVersion: "v4",
  softwareVersion: "2026.10.20.mr70318",
  countryCode: "NL",
  bootcount: 21,
};

const alignment = {
  hasActuators: "HAS_ACTUATORS_NO",
  tiltAngleDeg: 21.4,
  boresightAzimuthDeg: -3.1,
  boresightElevationDeg: 68.6,
  desiredBoresightAzimuthDeg: -1.8,
  desiredBoresightElevationDeg: 69.5,
  attitudeEstimationState: "FILTER_CONVERGED",
  attitudeUncertaintyDeg: 0.6,
};

const dishConfig = {
  snowMeltMode: "AUTO",
  locationRequestMode: "LOCAL",
  levelDishMode: "TILT_LIKE_NORMAL",
  powerSaveStartMinutes: 60,
  powerSaveDurationMinutes: 300,
  powerSaveMode: false,
  swupdateRebootHour: 3,
  swupdateThreeDayDeferralEnabled: true,
};

function dishStatus() {
  const t = nowS();
  const s = sample(t);
  return {
    deviceInfo: dishInfo,
    deviceState: { uptimeS: String(t - BOOT_S) },
    obstructionStats: {
      fractionObstructed: 0.0042,
      validS: 258_400,
      patchesValid: 9412,
      avgProlongedObstructionIntervalS: "NaN",
    },
    alerts: {},
    downlinkThroughputBps: s.downlinkBps,
    uplinkThroughputBps: s.uplinkBps,
    popPingLatencyMs: s.latencyMs,
    boresightAzimuthDeg: alignment.boresightAzimuthDeg,
    boresightElevationDeg: alignment.boresightElevationDeg,
    gpsStats: { gpsValid: true, gpsSats: 15, pntFilterConvergenceState: "FILTER_CONVERGED" },
    ethSpeedMbps: 1000,
    classOfService: "CONSUMER",
    softwareUpdateState: "IDLE",
    alignmentStats: alignment,
    connectedRouters: [ROUTER_ID],
    downstreamRouters: { [ROUTER_ID]: { role: "CONTROLLER", lastSeen: String(t - 1) } },
    dlBandwidthRestrictedReason: "NO_LIMIT",
    ulBandwidthRestrictedReason: "NO_LIMIT",
    isSnrAboveNoiseFloor: true,
    readyStates: { cady: true, scp: true, l1l2: true, xphy: true, aap: true, rf: true },
    secondsUntilSwupdateRebootPossible: -1,
    hasActuators: "HAS_ACTUATORS_NO",
    natFlag: "NAT_ENABLED",
    ned2dishQuaternion: { qScalar: 0.981, qX: 0.02, qY: 0.186, qZ: -0.052 },
    config: dishConfig,
  };
}

function dishHistory() {
  const t = nowS();
  const current = t - BOOT_S;
  const size = 900;
  const drop = new Array(size);
  const latency = new Array(size);
  const down = new Array(size);
  const up = new Array(size);
  const power = new Array(size);
  for (let counter = current - size + 1; counter <= current; counter++) {
    const s = sample(BOOT_S + counter);
    const i = counter % size;
    drop[i] = s.dropRate;
    latency[i] = s.latencyMs;
    down[i] = s.downlinkBps;
    up[i] = s.uplinkBps;
    power[i] = s.powerW;
  }
  return {
    current: String(current),
    popPingDropRate: drop,
    popPingLatencyMs: latency,
    downlinkThroughputBps: down,
    uplinkThroughputBps: up,
    powerIn: power,
    outages: outagesBetween(t - 12 * 3600, t - 30).map((o) => ({
      cause: o.cause,
      startTimestampNs: String(BigInt(o.startS - GPS_OFFSET_S) * 1_000_000_000n),
      durationNs: String(BigInt(o.durationS) * 1_000_000_000n),
      didSwitch: o.cause === "OBSTRUCTED",
    })),
  };
}

// name, ip, mac, band, signal, average megabit down, megabit up, wave period
const DEVICES = [
  ["Living room TV", 31, "10:30:47", "RF_5GHZ", -48, 9.5, 0.4, 420],
  ["MacBook Pro", 32, "3c:22:fb", "RF_5GHZ", -52, 4.2, 1.1, 150],
  ["Home Assistant", 20, "dc:a6:32", "ETH", 0, 0.5, 0.35, 600],
  ["iPhone", 33, "f0:18:98", "RF_5GHZ", -57, 1.6, 0.25, 90],
  ["Pixel 9", 34, "20:33:89", "RF_5GHZ", -61, 0.9, 0.2, 75],
  ["PlayStation 5", 35, "00:d9:d1", "RF_5GHZ_HIGH", -55, 6.5, 0.6, 900],
  ["iPad", 36, "88:66:5a", "RF_5GHZ", -63, 1.2, 0.1, 200],
  ["Work laptop", 37, "8c:16:45", "RF_5GHZ", -58, 2.4, 1.6, 260],
  ["Thermostat", 38, "18:b4:30", "RF_2GHZ", -66, 0.01, 0.01, 300],
  ["Doorbell", 39, "64:16:66", "RF_2GHZ", -70, 0.05, 0.35, 330],
  ["Printer", 40, "00:1b:a9", "RF_2GHZ", -72, 0.002, 0.001, 500],
  ["Robot vacuum", 41, "50:14:79", "RF_2GHZ", -68, 0.01, 0.02, 700],
];

/** megabits moved since the router booted, for a rate that waves around `avg`. */
function megabitsSince(avg, period, phase, seconds) {
  return avg * (seconds - 0.8 * period * (Math.cos(seconds / period + phase) - Math.cos(phase)));
}
const rateAt = (avg, period, phase, seconds) =>
  Math.max(0, avg * (1 + 0.8 * Math.sin(seconds / period + phase)));

function wifiClients() {
  const up = nowS() - ROUTER_BOOT_S;
  const clients = [
    {
      name: "Starlink",
      macAddress: "74:24:9f:XX:XX:XX",
      ipAddress: "192.168.1.1",
      role: "CONTROLLER",
      deviceId: ROUTER_ID,
      iface: "ETH",
      clientId: 1,
    },
  ];
  DEVICES.forEach(([name, host, oui, iface, signal, down, upload, period], index) => {
    const phase = index * 1.3;
    const wired = iface === "ETH";
    const downRate = rateAt(down, period, phase, up);
    const upRate = rateAt(upload, period * 0.7, phase + 1, up);
    const downBytes = Math.floor((megabitsSince(down, period, phase, up) * 1e6) / 8);
    const upBytes = Math.floor((megabitsSince(upload, period * 0.7, phase + 1, up) * 1e6) / 8);
    const client = {
      name,
      macAddress: `${oui}:XX:XX:XX`,
      ipAddress: `192.168.1.${host}`,
      role: "CLIENT",
      iface,
      ifaceName: wired ? "eth_lan" : iface === "RF_2GHZ" ? "wifi0" : "wifi1",
      clientId: 100 + index,
      captiveClientId: (index + 1).toString(16).padStart(2, "0").repeat(32),
      upstreamMacAddress: "74:24:9f:XX:XX:XX",
      hopsFromController: 0,
      associatedTimeS: Math.min(up, 3600 * (5 + index * 7)),
      dhcpLeaseActive: true,
      secondsUntilDhcpLeaseExpires: 43200 - ((up + index * 3000) % 40000),
      downloadMb: Math.floor(downBytes / 1e6),
      uploadMb: Math.floor(upBytes / 1e6),
    };
    if (!wired) {
      Object.assign(client, {
        signalStrength: signal + Math.round(2 * Math.sin(up / 40 + index)),
        snr: 95 + signal,
        channelWidth: iface === "RF_2GHZ" ? 20 : 80,
        rxStatsValid: true,
        txStatsValid: true,
        // the dashboard reads rx as the device's download and tx as its upload
        rxStats: {
          bytes: String(downBytes),
          rateMbps: iface === "RF_2GHZ" ? 144 : 866,
          nss: 2,
          mcs: iface === "RF_2GHZ" ? 7 : 9,
          bandwidth: iface === "RF_2GHZ" ? 20 : 80,
          throughputMbpsLast15sAvg: downRate,
          throughputMbpsLast1mAvg: downRate,
        },
        txStats: {
          bytes: String(upBytes),
          rateMbps: iface === "RF_2GHZ" ? 144 : 1201,
          nss: 2,
          mcs: iface === "RF_2GHZ" ? 7 : 11,
          bandwidth: iface === "RF_2GHZ" ? 20 : 80,
          throughputMbpsLast15sAvg: upRate,
        },
      });
    }
    clients.push(client);
  });
  return clients;
}

function wifiStatus() {
  const t = nowS();
  const s = sample(t);
  return {
    deviceInfo: routerInfo,
    deviceState: { uptimeS: String(t - ROUTER_BOOT_S) },
    alerts: {},
    pingLatencyMs: s.routerLatencyMs,
    dishPingLatencyMs: 0.6,
    popPingLatencyMs: s.routerLatencyMs,
    popPingDropRate5m: 0.0007,
    ipv4WanAddress: "100.71.23.184",
  };
}

const wifiConfig = {
  countryCode: "NL",
  channel2ghz: 6,
  channel5ghz: 44,
  networks: [
    {
      ipv4: "192.168.1.1/24",
      domain: "lan",
      basicServiceSets: [
        { bssid: "74:24:9f:00:00:01", ssid: "STARLINK", band: "RF_2GHZ" },
        { bssid: "74:24:9f:00:00:02", ssid: "STARLINK", band: "RF_5GHZ" },
        { bssid: "74:24:9f:00:00:03", ssid: "STARLINK", band: "RF_5GHZ_HIGH" },
      ],
    },
  ],
};

function radioStats() {
  const wobble = Math.sin(nowS() / 300);
  return {
    radioStats: [
      { band: "RF_2GHZ", thermalStatus: { temp2: 51 + wobble, dutyCycle: 100 } },
      { band: "RF_5GHZ", thermalStatus: { temp2: 57 + 2 * wobble, dutyCycle: 100 } },
      { band: "RF_5GHZ_HIGH", thermalStatus: { temp2: 55 + wobble, dutyCycle: 100 } },
    ],
  };
}

const UNIMPLEMENTED = 12;

function dishAnswer(kind) {
  switch (kind) {
    case "getStatus":
      return { dishGetStatus: dishStatus() };
    case "getHistory":
      return { dishGetHistory: dishHistory() };
    case "getDeviceInfo":
      return { getDeviceInfo: { deviceInfo: dishInfo } };
    case "getLocation":
      return { getLocation: { lla: { lat: 52.3731, lon: 4.8922, alt: 12 }, source: "GPS" } };
    case "dishGetObstructionMap":
      return {
        dishGetObstructionMap: {
          numRows: 123,
          numCols: 123,
          snr: obstructionGrid(nowS()),
          maxThetaDeg: 65,
          mapReferenceFrame: "FRAME_EARTH",
        },
      };
    case "dishGetConfig":
      return { dishGetConfig: { dishConfig } };
    case "getDiagnostics":
      return {
        dishGetDiagnostics: {
          id: dishInfo.id,
          hardwareVersion: dishInfo.hardwareVersion,
          softwareVersion: dishInfo.softwareVersion,
          alerts: {},
          disablementCode: "OKAY",
          hardwareSelfTest: "PASSED",
          alignmentStats: {
            boresightAzimuthDeg: alignment.boresightAzimuthDeg,
            boresightElevationDeg: alignment.boresightElevationDeg,
            desiredBoresightAzimuthDeg: alignment.desiredBoresightAzimuthDeg,
            desiredBoresightElevationDeg: alignment.desiredBoresightElevationDeg,
          },
        },
      };
    default:
      return UNIMPLEMENTED;
  }
}

function routerAnswer(kind) {
  switch (kind) {
    case "getStatus":
      return { wifiGetStatus: wifiStatus() };
    case "getHistory":
      return { wifiGetHistory: { current: String(nowS() - ROUTER_BOOT_S) } };
    case "getDeviceInfo":
      return { getDeviceInfo: { deviceInfo: routerInfo } };
    case "wifiGetClients":
      return { wifiGetClients: { clients: wifiClients() } };
    case "getRadioStats":
      return { getRadioStats: radioStats() };
    case "wifiGetConfig":
      return { wifiGetConfig: { wifiConfig } };
    case "getDiagnostics":
      return { wifiGetDiagnostics: {} };
    default:
      return UNIMPLEMENTED;
  }
}

function frame(flag, payload) {
  const out = Buffer.alloc(5 + payload.length);
  out[0] = flag;
  out.writeUInt32BE(payload.length, 1);
  Buffer.from(payload).copy(out, 5);
  return out;
}

const SKIP = new Set(["id", "epochId", "targetId"]);
const seenKinds = new Set();

function serve(label, answer, port) {
  createServer((request, response) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      const headers = {
        "Content-Type": "application/grpc-web+proto",
        "Access-Control-Allow-Origin": "*",
      };
      try {
        const body = Buffer.concat(chunks);
        const length = body.readUInt32BE(1);
        const json = toJson(RequestSchema, fromBinary(RequestSchema, body.subarray(5, 5 + length)), {
          registry,
        });
        const kind = Object.keys(json).find((key) => !SKIP.has(key)) ?? "none";
        const result = answer(kind);
        if (!seenKinds.has(label + kind)) {
          seenKinds.add(label + kind);
          console.log(`[mock] ${label} ${kind} -> ${typeof result === "number" ? "status " + result : "ok"}`);
        }
        if (typeof result === "number") {
          response.writeHead(200, { ...headers, "grpc-status": String(result), "grpc-message": "Unimplemented" });
          response.end();
          return;
        }
        const message = toBinary(ResponseSchema, fromJson(ResponseSchema, result, { registry }));
        response.writeHead(200, headers);
        response.end(Buffer.concat([frame(0x00, message), frame(0x80, Buffer.from("grpc-status: 0\r\n"))]));
      } catch (error) {
        console.error(`[mock] ${label} failed:`, error);
        response.writeHead(200, { ...headers, "grpc-status": "2", "grpc-message": "mock error" });
        response.end();
      }
    });
  }).listen(port, "127.0.0.1", () => console.log(`[mock] ${label} on ${port}`));
}

serve("dish", dishAnswer, 19201);
serve("router", routerAnswer, 19001);
