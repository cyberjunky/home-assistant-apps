"use strict";

const TILES = {
  "tiles-heating": ["room_temperature", "setpoint", "burner_state", "modulation", "boiler_temperature",
    "boiler_return_temperature", "boiler_setpoint", "boiler_pressure", "dhw_setpoint", "burner_hours",
    "dhw_burner_hours", "failed_burner_starts", "boiler_fault", "oem_fault_code"],
  "tiles-energy": ["power_import", "power_export", "gas_flow", "gas_total", "energy_import_low",
    "energy_import_normal", "energy_export_low", "energy_export_normal"],
  "tiles-health": ["uptime", "clock_offset", "wifi_signal", "wifi_rx_dropped", "memory_free", "memory_cached",
    "memory_gui", "memory_web", "bus_threads", "bus_fds", "meter_adapter_health", "load"],
};
// One unit per chart, never two axes
const CHARTS = [
  { title: "Room temperature (°C)", series: ["room_temperature", "setpoint"] },
  { title: "Boiler temperature (°C)", series: ["boiler_temperature", "boiler_return_temperature", "boiler_setpoint"] },
  { title: "Boiler pressure (bar)", series: ["boiler_pressure"] },
  { title: "Modulation (%)", series: ["modulation"] },
  { title: "Power (W)", series: ["power_import", "power_export"] },
  { title: "Gas flow (m³/h)", series: ["gas_flow"] },
  { title: "Toon memory (MB)", series: ["memory_free", "memory_cached", "memory_gui", "memory_web"] },
  { title: "Toon bus threads and open files", series: ["bus_threads", "bus_fds"] },
];
const RANGES = [["6 h", 6], ["24 h", 24], ["7 d", 168], ["30 d", 720], ["1 y", 8760]];
const OUTAGE_KINDS = { reboot: "Restarted", network: "Connection lost", app_stopped: "App was stopped", unknown: "Unknown" };
const BURNER = { off: "Off", heating: "Heating", hot_water: "Hot water", preheating: "Preheating", error: "Error" };
const NO_DATA = "–";

let hours = 24;
let metrics = {};

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...children) => {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
};
const svgEl = (tag, attrs = {}) => {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
};
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const when = (ts) => new Date(ts * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" });

function duration(seconds) {
  const s = Math.round(seconds);
  if (s < 90) return `${s} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${(s / 3600).toFixed(1)} h`;
  return `${Math.round(s / 86400)} d`;
}

function formatValue(key, value) {
  if (value === undefined || value === null) return NO_DATA;
  if (key === "burner_state") return BURNER[value] || value;
  const meta = metrics[key] || {};
  if (meta.binary) return value ? "On" : "Off";
  if (key === "clock_offset") return value === 0 ? "0 s" : `${value < 0 ? "−" : "+"}${duration(Math.abs(value))}`;
  const size = Math.abs(value);
  const digits = size >= 1000 ? 0 : size >= 100 ? 1 : size > 0 && size < 1 ? 3 : 2;
  const text = Number(value).toLocaleString([], { maximumFractionDigits: digits });
  return meta.unit ? `${text} ${meta.unit}` : text;
}

function renderTiles(values) {
  for (const [id, keys] of Object.entries(TILES)) {
    $(id).replaceChildren(...keys.map((key) => el("div", { className: "tile" },
      el("b", { textContent: formatValue(key, values[key]) }),
      el("span", { textContent: metrics[key] ? metrics[key].name : key }))));
  }
}

function renderStatus(status) {
  metrics = status.metrics;
  const link = $("link");
  link.textContent = status.connected ? `Connected ${duration(status.now - status.connected_since)}` : "Not connected";
  link.style.setProperty("--dot", css(status.connected ? "--good" : "--critical"));
  const info = status.info;
  $("about").textContent = [info.vers && `Firmware ${info.vers}`, info.serialNumber,
    `app ${status.version} (unofficial)`, status.mqtt.broker ? `MQTT ${status.mqtt.connected ? "connected" : "not connected"}` : "MQTT off",
    status.import.running ? "importing history" : ""].filter(Boolean).join(" · ");

  const notices = [];
  if (status.error) notices.push(status.error);
  const offset = status.values.clock_offset;
  if (offset !== undefined && Math.abs(offset) > 120) {
    notices.push(`The Toon's clock is ${duration(Math.abs(offset))} ${offset < 0 ? "behind" : "ahead"}, so its week program runs that much off.`);
  }
  $("notices").replaceChildren(...notices.map((text) => el("div", { className: "notice", textContent: text })));
  renderTiles(status.values);
}

function niceTicks(min, max, count) {
  const span = max - min || 1;
  const raw = span / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw);
  const decimals = Math.max(0, -Math.floor(Math.log10(step)));
  const ticks = [];
  for (let t = Math.ceil(min / step) * step; t <= max + step / 1e6; t += step) ticks.push(+t.toFixed(decimals));
  ticks.decimals = decimals;
  return ticks;
}

function renderChart(chart, data, start, end) {
  const colors = ["--s1", "--s2", "--s3", "--s4"].map(css);
  const figure = el("figure", {}, el("figcaption", { textContent: chart.title }));
  if (chart.series.length > 1) {
    figure.append(el("div", { className: "legend" }, ...chart.series.map((key, i) => {
      const swatch = el("i");
      swatch.style.background = colors[i];
      return el("span", {}, swatch, metrics[key].name);
    })));
  }
  const all = chart.series.flatMap((key) => (data[key] || []).map((p) => p[1]));
  if (!all.length) {
    figure.append(el("p", { className: "muted", textContent: "No data in this period yet." }));
    return figure;
  }

  const W = 520, H = 180, L = 44, R = 8, T = 8, B = 20;
  const lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo || Math.abs(hi) || 1) * 0.08;
  // Nothing here goes below zero unless the data does
  const ticks = niceTicks(lo >= 0 ? Math.max(0, lo - pad) : lo - pad, hi + pad, 4);
  const y0 = Math.min(ticks[0], lo), y1 = Math.max(ticks[ticks.length - 1], hi);
  const x = (ts) => L + ((ts - start) / (end - start)) * (W - L - R);
  const y = (v) => T + (1 - (v - y0) / (y1 - y0 || 1)) * (H - T - B);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img", "aria-label": chart.title });

  for (const tick of ticks) {
    svg.append(svgEl("line", { x1: L, x2: W - R, y1: y(tick), y2: y(tick), stroke: css("--grid"), "vector-effect": "non-scaling-stroke" }));
    const label = svgEl("text", { x: L - 6, y: y(tick) + 4, "text-anchor": "end" });
    label.textContent = tick.toLocaleString([], { minimumFractionDigits: ticks.decimals, maximumFractionDigits: ticks.decimals });
    svg.append(label);
  }
  for (let i = 0; i <= 3; i++) {
    const ts = start + ((end - start) * i) / 3;
    const label = svgEl("text", { x: x(ts), y: H - 5, "text-anchor": i === 0 ? "start" : i === 3 ? "end" : "middle" });
    const d = new Date(ts * 1000);
    label.textContent = hours <= 48 ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString([], { day: "numeric", month: "short" });
    svg.append(label);
  }

  // A line breaks where there is no data rather than drawing across an outage
  const gapLimit = Math.max(((end - start) / 600) * 8, hours <= 72 ? 2400 : 3 * 3600);
  chart.series.forEach((key, i) => {
    let path = "", last = null;
    for (const [ts, value] of data[key] || []) {
      path += `${last === null || ts - last > gapLimit ? "M" : "L"}${x(ts).toFixed(1)} ${y(value).toFixed(1)}`;
      last = ts;
    }
    svg.append(svgEl("path", { d: path, fill: "none", stroke: colors[i], "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" }));
  });

  const cross = svgEl("line", { y1: T, y2: H - B, stroke: css("--text3"), "vector-effect": "non-scaling-stroke", visibility: "hidden" });
  svg.append(cross);
  const tip = el("div", { className: "tip" });
  const nearest = (points, ts) => {
    let best = null;
    for (const point of points || []) if (!best || Math.abs(point[0] - ts) < Math.abs(best[0] - ts)) best = point;
    return best && Math.abs(best[0] - ts) <= gapLimit ? best : null;
  };
  svg.addEventListener("pointermove", (event) => {
    const box = svg.getBoundingClientRect();
    const ts = start + (((event.clientX - box.left) / box.width) * W - L) / (W - L - R) * (end - start);
    if (ts < start || ts > end) return;
    cross.setAttribute("x1", x(ts));
    cross.setAttribute("x2", x(ts));
    cross.setAttribute("visibility", "visible");
    const rows = chart.series.map((key, i) => {
      const point = nearest(data[key], ts);
      const swatch = el("i");
      swatch.style.background = colors[i];
      return el("div", {}, swatch, el("b", { textContent: point ? point[1].toLocaleString() : NO_DATA }),
        el("span", { className: "muted", textContent: metrics[key].name }));
    });
    tip.replaceChildren(el("div", { className: "muted", textContent: when(ts) }), ...rows);
    tip.style.display = "block";
    const px = event.clientX - figure.getBoundingClientRect().left;
    tip.style.left = `${Math.min(px + 12, figure.clientWidth - tip.offsetWidth - 4)}px`;
    tip.style.top = "40px";
  });
  svg.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden");
    tip.style.display = "none";
  });
  figure.append(svg, tip);

  // The same values without hovering: the latest of each series
  const table = el("table", {}, el("tr", {}, el("th", { textContent: "Series" }), el("th", { textContent: "Latest" }), el("th", { textContent: "Lowest" }), el("th", { textContent: "Highest" })));
  for (const key of chart.series) {
    const values = (data[key] || []).map((p) => p[1]);
    if (!values.length) continue;
    table.append(el("tr", {}, el("td", { textContent: metrics[key].name }),
      el("td", { textContent: values[values.length - 1].toLocaleString() }),
      el("td", { textContent: Math.min(...values).toLocaleString() }),
      el("td", { textContent: Math.max(...values).toLocaleString() })));
  }
  figure.append(el("details", {}, el("summary", { textContent: "Show as table" }), table));
  return figure;
}

async function getJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

async function loadCharts() {
  const keys = CHARTS.flatMap((chart) => chart.series);
  const data = await getJson(`api/history?hours=${hours}&metrics=${keys.join(",")}`);
  const end = Date.now() / 1000;
  const start = end - hours * 3600;
  $("charts").replaceChildren(...CHARTS.map((chart) => renderChart(chart, data, start, end)));
}

async function loadOutages() {
  const rows = await getJson("api/outages");
  const head = el("tr", {}, ...["Started", "Duration", "What happened", "Memory free before", "Reason"].map((text) => el("th", { textContent: text })));
  const now = Date.now() / 1000;
  const body = rows.map((row) => el("tr", {},
    el("td", { textContent: when(row.start_ts) }),
    el("td", { textContent: row.end_ts ? duration(row.end_ts - row.start_ts) : `${duration(now - row.start_ts)}, ongoing` }),
    el("td", { textContent: row.end_ts ? (OUTAGE_KINDS[row.kind] || row.kind) : "Not connected" }),
    el("td", { textContent: row.memory_free == null ? NO_DATA : `${row.memory_free} MB` }),
    el("td", { textContent: row.reason || "" })));
  $("outages").replaceChildren(head, ...(body.length ? body : [el("tr", {}, el("td", { colSpan: 5, className: "muted", textContent: "No outages recorded yet." }))]));
}

async function refresh() {
  try {
    renderStatus(await getJson("api/status"));
  } catch (error) {
    $("link").textContent = "Dashboard not reachable";
  }
}

function setupRanges() {
  const render = () => $("ranges").replaceChildren(...RANGES.map(([label, value]) => {
    const button = el("button", { textContent: label, onclick: () => { hours = value; render(); loadCharts(); } });
    button.setAttribute("aria-pressed", String(hours === value));
    return button;
  }));
  render();
}

setupRanges();
refresh().then(() => Promise.all([loadCharts(), loadOutages()]));
setInterval(refresh, 10000);
setInterval(() => { loadCharts(); loadOutages(); }, 120000);
