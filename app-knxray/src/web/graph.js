// Two pictures of the bus: the Graph (devices and the group addresses that tie them together, lighting up
// with the traffic) and the Timeline (who sent what when, a lane per sender).
'use strict';

// ---------------------------------------------------------------- graph
const GRAPH_MAX_ADDRESSES = 500;
let graph = null;   // {nodes, edges, byId, ticks, pulses, hover}
function buildGraph(devices) {
  const shown = new Set(devices.map(d => d.address));
  // Group addresses that tie shown devices together: by the project's links, and by what was seen
  let addresses = model.addresses.map(a => {
    const linked = new Set([...(a.devices || []), ...Object.keys((a.stats || {}).sources || {})].filter(d => shown.has(d)));
    return {a, linked: [...linked]};
  }).filter(x => x.linked.length);
  if (addresses.length > GRAPH_MAX_ADDRESSES) addresses = addresses.sort((x, y) => ((y.a.stats || {}).count || 0) - ((x.a.stats || {}).count || 0)).slice(0, GRAPH_MAX_ADDRESSES);
  const nodes = [], edges = [], byId = {};
  const add = (id, kind, label) => { const n = {id, kind, label, x: Math.cos(nodes.length) * (120 + nodes.length), y: Math.sin(nodes.length) * (120 + nodes.length), vx: 0, vy: 0}; nodes.push(n); byId[id] = n; return n; };
  devices.forEach(d => add(d.address, 'device', d.name || ''));
  addresses.forEach(({a, linked}) => { const n = add(a.address, 'ga', a.name || ''); linked.forEach(d => edges.push({from: byId[d], to: n})); });
  graph = {nodes, edges, byId, ticks: 0, pulses: [], hover: null, cut: model.addresses.length - addresses.length};
}
function stepGraph() {
  const {nodes, edges} = graph, k = 60;
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j]; let dx = a.x - b.x, dy = a.y - b.y; const d2 = dx * dx + dy * dy + 0.01;
      if (d2 > 250000) continue;
      const f = k * k / d2; dx *= f / 8; dy *= f / 8; a.vx += dx; a.vy += dy; b.vx -= dx; b.vy -= dy;
    }
    a.vx -= a.x * 0.004; a.vy -= a.y * 0.004;
  }
  for (const e of edges) {
    const dx = e.to.x - e.from.x, dy = e.to.y - e.from.y, d = Math.sqrt(dx * dx + dy * dy) + 0.01, f = (d - k) * 0.02;
    e.from.vx += dx / d * f; e.from.vy += dy / d * f; e.to.vx -= dx / d * f; e.to.vy -= dy / d * f;
  }
  for (const n of nodes) { n.vx *= 0.82; n.vy *= 0.82; n.x += Math.max(-12, Math.min(12, n.vx)); n.y += Math.max(-12, Math.min(12, n.vy)); }
  graph.ticks++;
}
function drawGraph() {
  const canvas = $('graph-canvas'); if (!graph || !canvas) return;
  const ratio = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * ratio) { canvas.width = w * ratio; canvas.height = h * ratio; }
  const c = canvas.getContext('2d'); c.setTransform(ratio, 0, 0, ratio, 0, 0); c.clearRect(0, 0, w, h);
  const xs = graph.nodes.map(n => n.x), ys = graph.nodes.map(n => n.y);
  const minX = Math.min(...xs, -1), maxX = Math.max(...xs, 1), minY = Math.min(...ys, -1), maxY = Math.max(...ys, 1);
  const scale = Math.min((w - 80) / (maxX - minX), (h - 60) / (maxY - minY), 2.2);
  const px = n => (n.x - (minX + maxX) / 2) * scale + w / 2, py = n => (n.y - (minY + maxY) / 2) * scale + h / 2;
  graph.view = {px, py};
  const line = cssVar('--divider-color'), text = cssVar('--primary-text-color'), muted = cssVar('--secondary-text-color'), blue = cssVar('--primary-color');
  const tone = {error: cssVar('--danger-color'), warning: cssVar('--warning-color'), info: cssVar('--info-color')};
  c.lineWidth = 1; c.strokeStyle = line; c.beginPath();
  for (const e of graph.edges) { c.moveTo(px(e.from), py(e.from)); c.lineTo(px(e.to), py(e.to)); }
  c.stroke();
  const now = performance.now();
  graph.pulses = graph.pulses.filter(p => now - p.at < 900);
  for (const p of graph.pulses) {
    const t = (now - p.at) / 900, x = px(p.from) + (px(p.to) - px(p.from)) * t, y = py(p.from) + (py(p.to) - py(p.from)) * t;
    c.fillStyle = blue; c.globalAlpha = 1 - t * 0.6; c.beginPath(); c.arc(x, y, 3.5, 0, 7); c.fill(); c.globalAlpha = 1;
  }
  const labels = graph.nodes.length < 90;
  c.font = '11px IBM Plex Mono, monospace'; c.textAlign = 'center';
  for (const n of graph.nodes) {
    const x = px(n), y = py(n), found = issuesOf(n.id), colour = found.length ? tone[worst(found)] : n.kind === 'device' ? blue : muted;
    c.fillStyle = colour; c.beginPath();
    if (n.kind === 'device') c.rect(x - 5, y - 5, 10, 10); else c.arc(x, y, 3.5, 0, 7);
    c.fill();
    if (labels || n === graph.hover || n.kind === 'device' && graph.nodes.length < 250) { c.fillStyle = n === graph.hover ? text : muted; c.fillText(n.id, x, y - 9); }
  }
  if (graph.hover) { c.fillStyle = text; c.font = '12px IBM Plex Sans, sans-serif'; c.fillText(graph.hover.label, px(graph.hover), py(graph.hover) + 20); }
}
let graphFrame = null;
function runGraph() {
  if (graphFrame) return;
  const loop = () => {
    graphFrame = null;
    if (activeTab !== 'devices' || $('devices-graph').hidden || !graph) return;
    if (graph.ticks < 400) for (let i = 0; i < 3; i++) stepGraph();
    drawGraph();
    if (graph.ticks < 400 || graph.pulses.length) graphFrame = requestAnimationFrame(loop);
  };
  graphFrame = requestAnimationFrame(loop);
}
let graphKey = '';
function renderGraph(rows) {
  const key = rows.map(d => d.address).join() + '|' + model.addresses.length;
  if (key !== graphKey) { graphKey = key; buildGraph(rows); }
  $('graph-note').textContent = `${rows.length} devices (squares), ${graph.nodes.length - rows.length} group addresses (dots)` + (graph.cut > 0 && graph.nodes.length - rows.length >= GRAPH_MAX_ADDRESSES ? `, the busiest ${GRAPH_MAX_ADDRESSES}` : '') + '. A dot travels along a link when a telegram is sent. Click for details.';
  runGraph();
}
function pulseGraph(items) {
  if (!graph || activeTab !== 'devices' || $('devices-graph').hidden) return;
  for (const t of items.slice(-40)) { const from = graph.byId[t.src], to = graph.byId[t.dst]; if (from && to) graph.pulses.push({from, to, at: performance.now()}); }
  runGraph();
}
function graphNodeAt(e) {
  if (!graph || !graph.view) return null;
  const box = $('graph-canvas').getBoundingClientRect(), x = e.clientX - box.left, y = e.clientY - box.top;
  let best = null, bestD = 144;
  for (const n of graph.nodes) { const dx = graph.view.px(n) - x, dy = graph.view.py(n) - y, d = dx * dx + dy * dy; if (d < bestD) { best = n; bestD = d; } }
  return best;
}
$('graph-canvas').addEventListener('mousemove', e => { const n = graphNodeAt(e); if (graph && n !== graph.hover) { graph.hover = n; $('graph-canvas').style.cursor = n ? 'pointer' : ''; drawGraph(); } });
$('graph-canvas').addEventListener('click', e => { const n = graphNodeAt(e); if (n) openDrawer(n.id); });
window.addEventListener('resize', () => { if (graph && activeTab === 'devices' && !$('devices-graph').hidden) drawGraph(); });

// ---------------------------------------------------------------- timeline
const TIMELINE_LANES = 14;
const timelineSpan = seg('timeline-span', () => drawTimeline());
let timelineDots = [];
function drawTimeline() {
  const canvas = $('timeline-canvas'); if (!canvas || canvas.offsetParent === null) return;
  const span = Number(timelineSpan()), until = Date.now() / 1000, since = until - span;
  const text = $('monitor-search').value.trim().toLowerCase(), type = monitorType();
  const shown = live.filter(t => t.ts >= since && matches(t, text, type));
  const counts = {};
  for (const t of shown) counts[t.src] = (counts[t.src] || 0) + 1;
  const lanes = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, TIMELINE_LANES).map(([src]) => src);
  const ratio = window.devicePixelRatio || 1, w = canvas.clientWidth, laneH = 26, left = Math.min(260, w * 0.3), top = 8, h = top + Math.max(1, lanes.length) * laneH + 26;
  canvas.style.height = h + 'px';
  canvas.width = w * ratio; canvas.height = h * ratio;
  const c = canvas.getContext('2d'); c.setTransform(ratio, 0, 0, ratio, 0, 0);
  const grid = cssVar('--grid-color'), muted = cssVar('--secondary-text-color'), ink = cssVar('--primary-text-color');
  const colour = {write: cssVar('--primary-color'), read: cssVar('--warning-color'), response: cssVar('--success-color')};
  const x = ts => left + (ts - since) / span * (w - left - 12);
  c.font = '11px IBM Plex Mono, monospace'; c.textBaseline = 'middle';
  lanes.forEach((src, i) => {
    const y = top + i * laneH + laneH / 2;
    c.strokeStyle = grid; c.beginPath(); c.moveTo(left, y); c.lineTo(w - 12, y); c.stroke();
    c.fillStyle = ink; c.textAlign = 'left'; c.fillText(src, 0, y);
    c.fillStyle = muted; c.fillText((nameOf(src) || '').slice(0, Math.max(0, Math.floor((left - 78) / 6.6))), 70, y);
  });
  c.textAlign = 'center'; c.fillStyle = muted;
  for (let i = 0; i <= 6; i++) { const ts = since + span * i / 6; c.fillText(fmtClock(ts), Math.min(w - 34, Math.max(left + 28, x(ts))), h - 10); }
  timelineDots = [];
  for (const t of shown) {
    const lane = lanes.indexOf(t.src); if (lane < 0) continue;
    const px = x(t.ts), py = top + lane * laneH + laneH / 2;
    c.fillStyle = t.text == null && t.dpt && t.type !== 'read' ? cssVar('--danger-color') : colour[t.type];
    c.globalAlpha = 0.85; c.beginPath(); c.arc(px, py, t.rep ? 5 : 3.5, 0, 7); c.fill(); c.globalAlpha = 1;
    timelineDots.push({px, py, t});
  }
  $('timeline-note').textContent = lanes.length ? `${shown.length} telegrams from the ${lanes.length} busiest senders. Blue is a write, orange a read, green a response, red a value that does not fit its type.` : 'No telegrams in this stretch of time.';
}
function timelineDotAt(e) {
  const box = $('timeline-canvas').getBoundingClientRect(), x = e.clientX - box.left, y = e.clientY - box.top;
  let best = null, bestD = 64;
  for (const d of timelineDots) { const dx = d.px - x, dy = d.py - y, dist = dx * dx + dy * dy; if (dist < bestD) { best = d; bestD = dist; } }
  return best;
}
$('timeline-canvas').addEventListener('mousemove', e => {
  const d = timelineDotAt(e), tip = $('timeline-tip');
  if (!d) { tip.hidden = true; return; }
  tip.hidden = false;
  tip.textContent = `${fmtClock(d.t.ts, true)}  ${d.t.src} → ${d.t.dst} ${nameOf(d.t.dst)}  ${d.t.type}${d.t.text != null ? '  ' + d.t.text + (d.t.unit ? ' ' + d.t.unit : '') : d.t.raw ? '  0x' + d.t.raw : ''}`;
});
$('timeline-canvas').addEventListener('click', e => { const d = timelineDotAt(e); if (d) openDrawer(d.t.dst); });
setInterval(() => { if (activeTab === 'monitor' && monitorMode === 'timeline' && !livePaused) drawTimeline(); }, 1000);
