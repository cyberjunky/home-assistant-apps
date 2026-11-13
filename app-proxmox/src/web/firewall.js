// Proxmox dashboard - Firewall: a Flow view of what may reach a guest or host (with drag and drop
// to add rules), the rules of every scope, security groups, IP sets and aliases, the log with its
// statistics, and the options and coverage of every guest.

const FW_SECTIONS = [
    {key: 'flow', label: 'Flow'}, {key: 'rules', label: 'Rules'}, {key: 'groups', label: 'Groups'},
    {key: 'sets', label: 'IP sets & aliases'}, {key: 'log', label: 'Log & stats'}, {key: 'options', label: 'Options'},
];
const FW_ACTION_COLOR = {ACCEPT: 'var(--pos)', DROP: 'var(--neg)', REJECT: 'var(--warning-color)'};
const FW_LOG_LEVELS = ['nolog', 'emerg', 'alert', 'crit', 'err', 'warning', 'notice', 'info', 'debug'];
const FW_PROTOCOLS = ['', 'tcp', 'udp', 'icmp', 'ipv6-icmp', 'sctp', 'gre', 'esp', 'ah', 'igmp'];
const FW_SERVICES = {
    20: 'FTP data', 21: 'FTP', 22: 'SSH', 23: 'Telnet', 25: 'SMTP', 53: 'DNS', 67: 'DHCP', 68: 'DHCP', 80: 'HTTP', 110: 'POP3', 111: 'rpcbind',
    123: 'NTP', 137: 'NetBIOS', 138: 'NetBIOS', 139: 'NetBIOS', 143: 'IMAP', 161: 'SNMP', 389: 'LDAP', 443: 'HTTPS', 445: 'SMB', 465: 'SMTPS',
    514: 'syslog', 587: 'SMTP submission', 636: 'LDAPS', 993: 'IMAPS', 1194: 'OpenVPN', 1433: 'MS SQL', 1883: 'MQTT', 2049: 'NFS', 3000: 'Grafana',
    3128: 'SPICE proxy', 3306: 'MySQL', 3389: 'RDP', 5353: 'mDNS', 5432: 'PostgreSQL', 5900: 'VNC', 6379: 'Redis', 6443: 'Kubernetes API',
    8006: 'Proxmox web', 8007: 'PBS web', 8080: 'HTTP alt', 8086: 'InfluxDB', 8096: 'Jellyfin', 8123: 'Home Assistant', 8443: 'HTTPS alt',
    8883: 'MQTT TLS', 9100: 'node exporter', 51820: 'WireGuard',
};

const fw = {
    section: stored('fw_section', 'flow'),
    target: stored('fw_target', 'cluster'),
    scope: stored('fw_scope', 'cluster'),
    renderedAt: -1,
    dragging: false,
    pickerQuery: '',
    log: null, logError: '', logLoading: false, logTimer: null,
    logFilter: {action: 'all', dir: 'all', target: 'all', q: ''}, logPage: 0,
};
if (!FW_SECTIONS.some(s => s.key === fw.section)) fw.section = 'flow';

// =====================================================================
// The configuration's firewall parts
// =====================================================================
const fwConf = () => (app.conf && app.conf.firewall) || null;
const fwOn = v => String(v) === '1' || v === true;
const fwBusy = () => fw.dragging || document.getElementById('modal-root').children.length > 0;

function fwScopeOf(key) {
    const [kind, ...rest] = String(key).split(':');
    const name = rest.join(':');
    if (kind === 'node') return {kind: 'node', node: name};
    if (kind === 'guest') return {kind: 'guest', vmid: Number(name)};
    if (kind === 'group') return {kind: 'group', group: name};
    return {kind: 'cluster'};
}
function fwScopeExists(key) {
    const c = fwConf();
    const s = fwScopeOf(key);
    if (!c) return false;
    if (s.kind === 'node') return Boolean(c.nodes[s.node]);
    if (s.kind === 'guest') return Boolean(app.conf.guests[String(s.vmid)]);
    if (s.kind === 'group') return c.groups.some(g => g.group === s.group);
    return true;
}
function fwScopeName(key) {
    const s = fwScopeOf(key);
    if (s.kind === 'node') return `Node ${s.node}`;
    if (s.kind === 'group') return `Group ${s.group}`;
    if (s.kind === 'guest') { const g = guestById(s.vmid); return g ? `${s.vmid} ${guestName(g)}` : `Guest ${s.vmid}`; }
    return 'Datacenter';
}
function fwScopeHtml(key) {
    const s = fwScopeOf(key);
    if (s.kind === 'guest') { const g = guestById(s.vmid); if (g) return guestLabel(g); }
    if (s.kind === 'group') return `<span class="fw-kind grp">GROUP</span> ${escapeHtml(s.group)}`;
    if (s.kind === 'node') return `<span class="fw-kind host">NODE</span> ${escapeHtml(s.node)}`;
    return '<span class="fw-kind host">DC</span> Datacenter';
}
// A scope's own rules, in order
function fwRulesOf(key) {
    const c = fwConf();
    const s = fwScopeOf(key);
    if (!c) return [];
    if (s.kind === 'node') return (c.nodes[s.node] || {}).rules || [];
    if (s.kind === 'group') return (c.groups.find(g => g.group === s.group) || {}).rules || [];
    if (s.kind === 'guest') return ((app.conf.guests[String(s.vmid)] || {}).firewall || {}).rules || [];
    return c.rules || [];
}
function fwOptionsOf(key) {
    const c = fwConf();
    const s = fwScopeOf(key);
    if (!c) return {};
    if (s.kind === 'node') return (c.nodes[s.node] || {}).options || {};
    if (s.kind === 'guest') return ((app.conf.guests[String(s.vmid)] || {}).firewall || {}).options || {};
    return c.options || {};
}
const fwGroup = name => (fwConf().groups || []).find(g => g.group === name) || null;

// What the firewall evaluates for a target, in order, security groups expanded:
// [{rule, scope, pos, via, viaScope, viaPos, origin}]
function fwEffective(key) {
    const s = fwScopeOf(key);
    const out = [];
    const add = (scopeKey, origin) => fwRulesOf(scopeKey).forEach((rule, i) => {
        const pos = missing(rule.pos) ? i : rule.pos;
        if (rule.type === 'group') {
            const group = fwGroup(rule.action);
            ((group && group.rules) || []).forEach((child, j) => out.push({
                rule: {...child, enable: fwOn(rule.enable) && fwOn(child.enable) ? 1 : 0, iface: child.iface || rule.iface},
                scope: `group:${rule.action}`, pos: missing(child.pos) ? j : child.pos, via: rule.action,
                viaScope: scopeKey, viaPos: pos, viaRule: rule, origin,
            }));
            if (!group || !group.rules.length) out.push({rule, scope: scopeKey, pos, via: rule.action, viaScope: scopeKey, viaPos: pos, viaRule: rule, origin, empty: true});
        } else {
            out.push({rule, scope: scopeKey, pos, via: null, origin});
        }
    });
    // A host is filtered by its own rules first, then the datacenter's; a guest only by its own
    if (s.kind === 'node') { add(key, 'node'); add('cluster', 'datacenter'); } else add(key, s.kind);
    return out;
}

// Source and destination: an alias, +ipset, an address, or empty (any)
function fwRefName(text) { return String(text || '').replace(/^\+/, '').replace(/^(dc|guest)\//, ''); }
function fwAddrHtml(text) {
    if (!text) return '<span class="muted">any</span>';
    return String(text).split(',').map(part => {
        const p = part.trim();
        const c = fwConf();
        if (p.startsWith('+')) {
            const set = c && c.ipsets.find(x => x.name === fwRefName(p));
            const title = set ? set.entries.map(e => (e.nomatch ? '!' : '') + e.cidr).join(', ') : 'IP set';
            return `<span class="fw-ref ipset" title="${attr(title)}">${escapeHtml(p)}</span>`;
        }
        const alias = c && c.aliases.find(a => a.name.toLowerCase() === fwRefName(p).toLowerCase());
        if (alias) return `<span class="fw-ref alias" title="${attr(alias.cidr + (alias.comment ? ' · ' + alias.comment : ''))}">${escapeHtml(p)}</span>`;
        return `<span class="mono">${escapeHtml(p)}</span>`;
    }).join(', ');
}
function fwService(rule) {
    if (rule.type === 'group') return `group ${rule.action}`;
    if (rule.macro) return rule.macro;
    if (rule.proto) return rule.proto + (rule.dport ? ':' + rule.dport : '');
    if (rule.dport) return 'port ' + rule.dport;
    return 'any';
}
function fwActionBadge(action, enabled) {
    const kind = action === 'ACCEPT' ? 'ok' : action === 'DROP' ? 'bad' : action === 'REJECT' ? 'warn' : '';
    return `<span class="badge ${kind}${enabled === false ? ' fw-faded' : ''}">${escapeHtml(action || '')}</span>`;
}
function fwPolicy(key, dir) {
    const s = fwScopeOf(key);
    const opts = s.kind === 'guest' ? fwOptionsOf(key) : fwOptionsOf('cluster');
    const value = opts[`policy_${dir}`];
    return value || (dir === 'in' ? 'DROP' : 'ACCEPT');
}

// Does the guest's firewall filter anything? Datacenter switch, its own switch, and the NICs' firewall flag
function fwApplies(key) {
    const s = fwScopeOf(key);
    const dcOn = fwOn(fwOptionsOf('cluster').enable);
    if (s.kind === 'group') return {ok: true, text: 'A security group filters wherever a rule uses it.'};
    if (s.kind === 'cluster') {
        return dcOn ? {ok: true, text: 'The datacenter firewall is on: these rules filter traffic to every node.'}
            : {ok: false, text: 'The datacenter firewall is off: nothing is filtered, on any node or guest.'};
    }
    if (s.kind === 'node') {
        const nodeOn = fwOn(fwOptionsOf(key).enable === undefined ? 1 : fwOptionsOf(key).enable);
        if (!dcOn) return {ok: false, text: 'The datacenter firewall is off: this node is not filtered.'};
        if (!nodeOn) return {ok: false, text: 'The firewall of this node is off: it is not filtered.'};
        return {ok: true, text: 'Filtering: the datacenter firewall and this node\'s firewall are on. Node rules come first, then the datacenter\'s.'};
    }
    const g = app.conf.guests[String(s.vmid)] || {};
    const nics = g.nics || [];
    const withFw = nics.filter(n => n.firewall).length;
    const reasons = [];
    if (!dcOn) reasons.push('the datacenter firewall is off');
    if (!fwOn((g.firewall || {}).options && g.firewall.options.enable)) reasons.push('this guest\'s firewall is off');
    if (!withFw) reasons.push(nics.length ? 'no network device has firewall=1' : 'it has no network device');
    if (reasons.length) return {ok: false, text: `Not filtered: ${reasons.join(', ')}.`};
    if (withFw < nics.length) return {ok: true, warn: true, text: `Filtering on ${withFw} of ${nics.length} network devices: ${nics.filter(n => !n.firewall).map(n => n.key).join(', ')} bypass the firewall.`};
    return {ok: true, text: `Filtering: the datacenter firewall, this guest's firewall and ${withFw === 1 ? 'its network device' : `all ${withFw} network devices`} are on.`};
}

// Every rule in every scope, for "used by"
function fwAllRules() {
    const c = fwConf();
    if (!c) return [];
    const out = [];
    const add = (key, rules) => (rules || []).forEach((rule, i) => out.push({key, rule, pos: missing(rule.pos) ? i : rule.pos}));
    add('cluster', c.rules);
    Object.keys(c.nodes || {}).forEach(n => add(`node:${n}`, c.nodes[n].rules));
    (c.groups || []).forEach(g => add(`group:${g.group}`, g.rules));
    Object.values(app.conf.guests || {}).forEach(g => add(`guest:${g.vmid}`, (g.firewall || {}).rules));
    return out;
}
function fwUsedBy(test) {
    const seen = new Set();
    return fwAllRules().filter(r => test(r.rule)).map(r => r.key).filter(k => !seen.has(k) && seen.add(k));
}
function fwRefersTo(rule, name, isSet) {
    return ['source', 'dest'].some(f => String(rule[f] || '').split(',').some(p => {
        const t = p.trim();
        return isSet ? t.startsWith('+') && fwRefName(t) === name : !t.startsWith('+') && fwRefName(t).toLowerCase() === name.toLowerCase();
    }));
}
function fwScopeLinks(keys) {
    if (!keys.length) return '<span class="muted">not used</span>';
    return keys.map(k => `<button type="button" class="fw-scope-link" onclick="fwOpenRules('${attr(k)}')">${fwScopeHtml(k)}</button>`).join('');
}
function fwOpenRules(key) {
    fw.scope = key;
    store('fw_scope', key);
    fwSwitchSection('rules');
}
function fwOpenFlow(key) {
    fw.target = key;
    store('fw_target', key);
    fwSwitchSection('flow');
}

// =====================================================================
// Changes: one place, with the lockout question
// =====================================================================
async function fwChange(body, done) {
    try {
        let answer = await apiPost('firewall/rule', body);
        if (answer && answer.lockout) {
            const ok = await confirmDialog({
                title: 'This may lock you out',
                message: `<p>${escapeHtml(answer.lockout)}</p><p class="panel-sub" style="margin-top:8px">Make sure an ACCEPT rule above it lets your own address in, or that the management IP set covers it.</p>`,
                confirmLabel: 'Change it anyway', danger: true,
            });
            if (!ok) return false;
            answer = await apiPost('firewall/rule', {...body, accept_risk: true});
        }
        toast(done, 'success');
        rereadConfig();
        return true;
    } catch (e) {
        toast(e.message);
        return false;
    }
}
const fwRoHint = () => readOnly() ? '<span class="fw-ro">read-only: turn off <span class="mono">read_only</span> in the app\'s configuration to change rules</span>' : '';

// The form for one rule. preset: the rule's fields. Resolves with the rule, or null
function fwRuleForm(title, preset, opts) {
    opts = opts || {};
    const c = fwConf();
    const r = {type: 'in', action: 'ACCEPT', enable: 1, ...preset};
    const isGroup = r.type === 'group';
    const option = (value, label, current) => `<option value="${attr(value)}"${String(value) === String(current || '') ? ' selected' : ''}>${escapeHtml(label)}</option>`;
    const groups = c.groups.map(g => option(g.group, g.group + (g.comment ? ` · ${g.comment}` : ''), isGroup ? r.action : ''));
    const macros = [option('', '— none —', r.macro), ...c.macros.slice().sort((a, b) => a.macro.localeCompare(b.macro)).map(m => option(m.macro, `${m.macro} · ${m.descr}`, r.macro))];
    const addrList = `<datalist id="fw-addr-list">${[...c.aliases.map(a => a.name), ...c.ipsets.map(s => '+' + s.name)].map(v => `<option value="${attr(v)}">`).join('')}</datalist>`;
    const types = opts.noGroup ? ['in', 'out'] : ['in', 'out', 'group'];
    const typeLabels = {in: 'In', out: 'Out', group: 'Security group', forward: 'Forward'};
    if (r.type === 'forward') types.push('forward');
    const body = `${addrList}<div class="form-grid">
        <div class="form-field"><label>Direction</label><select class="select" name="type">${types.map(t => option(t, typeLabels[t], r.type)).join('')}</select></div>
        <div class="form-field" data-for="rule"><label>Action</label><select class="select" name="action">${['ACCEPT', 'DROP', 'REJECT'].map(a => option(a, a, isGroup ? 'ACCEPT' : r.action)).join('')}</select></div>
        <div class="form-field" data-for="group"><label>Security group</label><select class="select" name="group">${groups.join('') || option('', 'no groups defined', '')}</select></div>
        <div class="form-field full" data-for="rule"><label>Macro</label><select class="select" name="macro">${macros.join('')}</select><div class="form-hint">A macro covers its own protocol and ports; leave it empty to give them yourself.</div></div>
        <div class="form-field" data-for="rule"><label>Protocol</label><select class="select" name="proto">${FW_PROTOCOLS.map(p => option(p, p || '— any —', r.proto)).join('')}</select></div>
        <div class="form-field" data-for="rule"><label>Destination port</label><input class="input" name="dport" value="${attr(r.dport || '')}" placeholder="22, 80:90, 443"></div>
        <div class="form-field" data-for="rule"><label>Source</label><input class="input" name="source" list="fw-addr-list" value="${attr(r.source || '')}" placeholder="any, an alias, +ipset or CIDR"></div>
        <div class="form-field" data-for="rule"><label>Destination</label><input class="input" name="dest" list="fw-addr-list" value="${attr(r.dest || '')}" placeholder="any, an alias, +ipset or CIDR"></div>
        <div class="form-field" data-for="rule"><label>Source port</label><input class="input" name="sport" value="${attr(r.sport || '')}"></div>
        <div class="form-field"><label>Interface</label><input class="input" name="iface" value="${attr(r.iface || '')}" placeholder="net0, vmbr0 …"></div>
        <div class="form-field" data-for="rule"><label>Log level</label><select class="select" name="log">${FW_LOG_LEVELS.map(l => option(l, l, r.log || 'nolog')).join('')}</select></div>
        <div class="form-field"><label>Comment</label><input class="input" name="comment" value="${attr(r.comment || '')}"></div>
        <div class="form-field full"><label class="check-row"><input type="checkbox" name="enable"${fwOn(r.enable) ? ' checked' : ''}> Enabled</label></div>
        ${opts.where ? `<div class="form-field full"><div class="form-hint">${opts.where}</div></div>` : ''}
    </div>`;
    return formDialog({
        title, body, wide: true, confirmLabel: opts.confirmLabel || 'Save',
        onOpen(form) {
            const sync = () => {
                const group = form.type.value === 'group';
                form.querySelectorAll('[data-for]').forEach(el => { el.hidden = (el.dataset.for === 'group') !== group; });
            };
            form.type.addEventListener('change', sync);
            sync();
        },
        validate(v) {
            if (v.type === 'group') return v.group ? '' : 'Choose a security group';
            if ((v.dport || v.sport) && !v.proto && !v.macro) return 'Choose a protocol (tcp or udp) for a port';
            if (v.macro && v.proto) return 'Give a macro or a protocol, not both';
            return '';
        },
    }).then(v => {
        if (!v) return null;
        const rule = {type: v.type, enable: v.enable ? 1 : 0};
        if (v.type === 'group') {
            rule.action = v.group;
        } else {
            Object.assign(rule, {action: v.action, macro: v.macro, proto: v.proto, dport: v.dport, sport: v.sport, source: v.source, dest: v.dest});
            if (v.log && v.log !== 'nolog') rule.log = v.log;
        }
        if (v.iface) rule.iface = v.iface;
        if (v.comment) rule.comment = v.comment;
        Object.keys(rule).forEach(k => { if (rule[k] === '') delete rule[k]; });
        return rule;
    });
}

async function fwAddRule(scopeKey, preset, pos) {
    if (readOnly()) return;
    const rule = await fwRuleForm(`New rule · ${fwScopeName(scopeKey)}`, preset, {
        noGroup: fwScopeOf(scopeKey).kind === 'group', confirmLabel: 'Add rule',
        where: `Added at the top of ${escapeHtml(fwScopeName(scopeKey))}'s rules, so it is checked first.`,
    });
    if (!rule) return;
    await fwChange({scope: fwScopeOf(scopeKey), op: 'create', rule, pos: pos === undefined ? 0 : pos}, `Rule added to ${fwScopeName(scopeKey)}`);
}
async function fwEditRule(scopeKey, pos) {
    if (readOnly()) return;
    const current = fwRulesOf(scopeKey).find((r, i) => (missing(r.pos) ? i : r.pos) === pos);
    if (!current) return;
    const rule = await fwRuleForm(`Rule ${pos} · ${fwScopeName(scopeKey)}`, current, {noGroup: fwScopeOf(scopeKey).kind === 'group'});
    if (!rule) return;
    await fwChange({scope: fwScopeOf(scopeKey), op: 'update', rule, pos, digest: current.digest}, 'Rule changed');
}
async function fwToggleRule(scopeKey, pos) {
    if (readOnly()) return;
    const current = fwRulesOf(scopeKey).find((r, i) => (missing(r.pos) ? i : r.pos) === pos);
    if (!current) return;
    const on = !fwOn(current.enable);
    await fwChange({scope: fwScopeOf(scopeKey), op: 'toggle', pos, rule: {enable: on}, current, digest: current.digest}, on ? 'Rule enabled' : 'Rule disabled');
}
async function fwDeleteRule(scopeKey, pos) {
    if (readOnly()) return;
    const current = fwRulesOf(scopeKey).find((r, i) => (missing(r.pos) ? i : r.pos) === pos);
    if (!current) return;
    const ok = await confirmDialog({
        title: 'Delete this rule?',
        message: `<p>${fwActionBadge(current.type === 'group' ? 'GROUP' : current.action)} <b>${escapeHtml(current.type)}</b> ${escapeHtml(fwService(current))}
            ${current.source ? ' from ' + fwAddrHtml(current.source) : ''}${current.dest ? ' to ' + fwAddrHtml(current.dest) : ''}</p>
            <p class="panel-sub" style="margin-top:8px">Rule ${pos} of ${escapeHtml(fwScopeName(scopeKey))}. ${current.comment ? escapeHtml(current.comment) : ''}</p>`,
        confirmLabel: 'Delete', danger: true,
    });
    if (!ok) return;
    await fwChange({scope: fwScopeOf(scopeKey), op: 'delete', pos, digest: current.digest}, 'Rule deleted');
}
async function fwMoveRule(scopeKey, from, toIndex) {
    if (readOnly() || from === toIndex) return;
    const current = fwRulesOf(scopeKey).find((r, i) => (missing(r.pos) ? i : r.pos) === from);
    // Proxmox puts the rule before the rule that was at "moveto": one further when moving down
    const to = toIndex > from ? toIndex + 1 : toIndex;
    await fwChange({scope: fwScopeOf(scopeKey), op: 'move', pos: from, to, digest: current && current.digest}, `Rule ${from} moved to ${toIndex}`);
}

// =====================================================================
// Rendering
// =====================================================================
function renderFirewall() {
    const el = document.getElementById('tab-firewall');
    if (!app.conf || !fwConf()) {
        el.innerHTML = `<div class="empty-state">${app.status && app.status.connected === false ? 'The cluster cannot be read.' : 'Reading the configuration…'}</div>`;
        return;
    }
    fw.renderedAt = app.confAt;
    const c = fwConf();
    const dcOn = fwOn(c.options.enable);
    const counts = {
        rules: fwAllRules().length, groups: c.groups.length, sets: c.ipsets.length + c.aliases.length,
    };
    const options = FW_SECTIONS.map(s => ({...s, count: s.key === 'rules' ? counts.rules : s.key === 'groups' ? counts.groups : s.key === 'sets' ? counts.sets : undefined}));
    el.innerHTML = `
        <div class="filters fw-head">
            ${chipsHtml('fw-sections', options, fw.section, 'Section')}
            <span class="fw-dc ${dcOn ? 'on' : 'off'}">${dcOn ? stateLabel('ok', 'Datacenter firewall on') : stateLabel('bad', 'Datacenter firewall off')}</span>
            <span class="range-text">configuration read ${escapeHtml(fmtAgo(app.confAt))}</span>
        </div>
        ${permissionNote('fwrules', 'Datacenter rules')}
        <div id="fw-body"></div>`;
    bindChips('fw-sections', fwSwitchSection);
    const body = document.getElementById('fw-body');
    if (fw.section === 'flow') fwRenderFlow(body);
    else if (fw.section === 'rules') fwRenderRules(body);
    else if (fw.section === 'groups') fwRenderGroups(body);
    else if (fw.section === 'sets') fwRenderSets(body);
    else if (fw.section === 'log') fwRenderLog(body);
    else fwRenderOptions(body);
}

function fwSwitchSection(key) {
    fw.section = key;
    store('fw_section', key);
    setSub(key);
    renderFirewall();
    fwLogTimer();
}

// ---------------------------------------------------------------- Flow
function fwTargets() {
    const c = fwConf();
    const host = [{key: 'cluster', html: fwScopeHtml('cluster'), on: fwOn(c.options.enable)}];
    Object.keys(c.nodes).sort().forEach(n => host.push({key: `node:${n}`, html: fwScopeHtml(`node:${n}`), on: fwOn(c.nodes[n].options.enable === undefined ? 1 : c.nodes[n].options.enable), name: n}));
    const byVlan = new Map();
    guests({noTemplates: true}).forEach(g => {
        const conf = app.conf.guests[String(g.vmid)];
        const nics = (conf && conf.nics) || [];
        const tags = nics.length ? [...new Set(nics.map(n => missing(n.tag) ? 'untagged' : n.tag))] : ['no network'];
        tags.forEach(t => {
            if (!byVlan.has(t)) byVlan.set(t, []);
            byVlan.get(t).push({key: `guest:${g.vmid}`, g, on: fwApplies(`guest:${g.vmid}`).ok, name: `${g.vmid} ${guestName(g)} ${guestTags(g).join(' ')}`});
        });
    });
    const vlans = [...byVlan.keys()].sort((a, b) => (typeof a === 'number' ? a : 1e5) - (typeof b === 'number' ? b : 1e5));
    const groups = c.groups.map(g => ({key: `group:${g.group}`, html: fwScopeHtml(`group:${g.group}`), on: true, name: g.group}));
    return {host, vlans: vlans.map(t => ({tag: t, items: byVlan.get(t)})), groups};
}

function fwRenderPicker() {
    const t = fwTargets();
    const q = fw.pickerQuery.toLowerCase();
    const item = (it, html) => (!q || (it.name || 'datacenter').toLowerCase().includes(q))
        ? `<button type="button" class="fw-pick${it.key === fw.target ? ' active' : ''}" data-target="${attr(it.key)}"><span class="sdot ${it.on ? 'ok' : ''}" title="${it.on ? 'filtered' : 'not filtered'}"></span>${html}</button>` : '';
    let html = `<div class="fw-pick-head">Hosts</div>${t.host.map(it => item(it, it.html)).join('')}`;
    t.vlans.forEach(v => {
        const items = v.items.map(it => item(it, guestLabel(it.g))).join('');
        if (items) html += `<div class="fw-pick-head">${typeof v.tag === 'number' ? vlanChip(v.tag) : v.tag === 'untagged' ? vlanChip(null) : escapeHtml(v.tag)}</div>${items}`;
    });
    if (t.groups.length) html += `<div class="fw-pick-head">Security groups</div>${t.groups.map(it => item(it, it.html)).join('')}`;
    const options = [...t.host.map(it => [it.key, fwScopeName(it.key)]),
        ...guests({noTemplates: true}).map(g => [`guest:${g.vmid}`, `${g.vmid} ${guestName(g)}`]), ...t.groups.map(it => [it.key, fwScopeName(it.key)])];
    return `<div class="fw-picker">
        <input class="input fw-pick-search" type="search" placeholder="Find a guest, node or group" value="${attr(fw.pickerQuery)}" aria-label="Find a target">
        <select class="select fw-pick-select" aria-label="Target">${options.map(([k, l]) => `<option value="${attr(k)}"${k === fw.target ? ' selected' : ''}>${escapeHtml(l)}</option>`).join('')}</select>
        <div class="fw-pick-list">${html}</div></div>`;
}

function fwPalette() {
    const c = fwConf();
    const ro = readOnly();
    const chip = (kind, value, label, title) => `<span class="fw-pal ${kind}" draggable="${ro ? 'false' : 'true'}" data-kind="${kind}" data-value="${attr(value)}" title="${attr(title || '')}">${escapeHtml(label)}</span>`;
    return `<div class="fw-palette${ro ? ' ro' : ''}">
        <span class="fw-pal-title">${ro ? 'Sources' : 'Drag onto the flow'}</span>
        ${chip('any', '', 'Any', 'Every address')}
        ${chip('custom', '', 'Custom address…', 'Type an address or network')}
        ${c.aliases.map(a => chip('alias', a.name, a.name, `${a.cidr}${a.comment ? ' · ' + a.comment : ''}`)).join('')}
        ${c.ipsets.map(s => chip('ipset', '+' + s.name, '+' + s.name, s.entries.map(e => (e.nomatch ? '!' : '') + e.cidr).join(', '))).join('')}
        ${c.groups.map(g => chip('group', g.group, g.group, `Security group${g.comment ? ': ' + g.comment : ''} · drop it on the target`)).join('')}
        ${fwRoHint()}
    </div>`;
}

function fwRenderFlow(body) {
    if (!fwScopeExists(fw.target)) fw.target = 'cluster';
    const key = fw.target;
    const s = fwScopeOf(key);
    const eff = fwEffective(key);
    const applies = fwApplies(key);
    const isGroup = s.kind === 'group';
    fw.edges = [];
    const side = dir => {
        const field = dir === 'in' ? 'source' : 'dest';
        const cards = new Map();
        eff.forEach(e => {
            const type = e.rule.type === 'forward' ? 'in' : e.rule.type;
            if (type !== dir && !(e.empty && dir === 'in')) return;
            const addr = e.empty ? `group:${e.via}` : String(e.rule[field] || '');
            if (!cards.has(addr)) cards.set(addr, []);
            cards.get(addr).push(e);
        });
        let html = '';
        cards.forEach((list, addr) => {
            const pills = list.map(e => {
                const i = fw.edges.push({...e, dir}) - 1;
                const on = fwOn(e.rule.enable);
                const action = e.empty ? 'GROUP' : e.rule.action;
                return `<button type="button" class="fw-pill${on ? '' : ' off'}" data-edge="${i}" style="--edge:${FW_ACTION_COLOR[action] || 'var(--idle)'}" title="${attr(e.rule.comment || '')}">
                    <b>${escapeHtml(action)}</b><span>${escapeHtml(e.empty ? 'empty group' : fwService(e.rule))}</span>${e.via ? `<em class="fw-via">${escapeHtml(e.via)}</em>` : ''}${e.origin === 'datacenter' && s.kind === 'node' ? '<em class="fw-via dc">DC</em>' : ''}${e.rule.iface ? `<em class="fw-via if">${escapeHtml(e.rule.iface)}</em>` : ''}</button>`;
            }).join('');
            const label = addr.startsWith('group:') ? `<span class="fw-kind grp">GROUP</span> ${escapeHtml(addr.slice(6))}` : fwAddrHtml(addr);
            html += `<div class="fw-card"><div class="fw-card-head">${label}</div>${pills}</div>`;
        });
        if (!isGroup) {
            const policy = fwPolicy(key, dir);
            const i = fw.edges.push({policy: true, dir, rule: {action: policy, enable: 1}}) - 1;
            html += `<div class="fw-card policy"><div class="fw-card-head">Everything else</div>
                <button type="button" class="fw-pill" data-edge="${i}" style="--edge:${FW_ACTION_COLOR[policy] || 'var(--idle)'}"><b>${escapeHtml(policy)}</b><span>policy ${dir}</span></button></div>`;
        }
        return html || '<div class="empty-state" style="padding:16px">No rules</div>';
    };
    body.innerHTML = `
        <div class="fw-flow-layout">
            ${fwRenderPicker()}
            <div class="fw-flow-main">
                ${fwPalette()}
                <div class="fw-applies ${applies.ok ? (applies.warn ? 'warn' : 'ok') : 'bad'}">${escapeHtml(applies.text)}</div>
                <div class="fw-flow" id="fw-flow">
                    <svg class="fw-edges" id="fw-edges" aria-hidden="true"></svg>
                    <div class="fw-col fw-drop" data-drop="in">
                        <div class="fw-col-head"><span>Inbound</span><span class="muted">from</span>${readOnly() ? '' : `<button type="button" class="btn btn-small" onclick="fwAddRule('${attr(key)}', {type: 'in'})">+ Rule</button>`}</div>
                        ${side('in')}
                    </div>
                    <div class="fw-gap"></div>
                    <div class="fw-target-wrap"><div class="fw-target fw-drop" data-drop="target" id="fw-target">${fwTargetBox(key)}</div></div>
                    <div class="fw-gap"></div>
                    <div class="fw-col fw-drop" data-drop="out">
                        <div class="fw-col-head"><span>Outbound</span><span class="muted">to</span>${readOnly() ? '' : `<button type="button" class="btn btn-small" onclick="fwAddRule('${attr(key)}', {type: 'out'})">+ Rule</button>`}</div>
                        ${side('out')}
                    </div>
                </div>
                <div class="legend fw-legend">${legendHtml([{name: 'ACCEPT', color: 'var(--pos)'}, {name: 'DROP', color: 'var(--neg)'}, {name: 'REJECT', color: 'var(--warning-color)'}])}
                    <span><i class="line" style="background:var(--idle)"></i>dashed: disabled</span><span>rules are checked top to bottom; the first that matches decides</span></div>
            </div>
        </div>`;
    fwWireFlow(body, key);
    requestAnimationFrame(fwDrawEdges);
}

function fwTargetBox(key) {
    const s = fwScopeOf(key);
    const flag = (on, text) => `<span class="fw-flag-item ${on ? 'on' : 'off'}">${on ? '✓' : '✕'} ${escapeHtml(text)}</span>`;
    if (s.kind === 'group') {
        const g = fwGroup(s.group);
        const users = fwUsedBy(r => r.type === 'group' && r.action === s.group);
        return `<div class="fw-target-title">${fwScopeHtml(key)}</div>
            <div class="panel-sub">${escapeHtml((g && g.comment) || 'Security group')}</div>
            <div class="fw-target-sub">Used by</div><div class="fw-users">${fwScopeLinks(users)}</div>`;
    }
    if (s.kind === 'cluster' || s.kind === 'node') {
        const dc = fwOptionsOf('cluster');
        const node = s.kind === 'node' ? fwOptionsOf(key) : null;
        const n = s.kind === 'node' ? nodeByName(s.node) : null;
        return `<div class="fw-target-title">${fwScopeHtml(key)}</div>
            <div class="panel-sub">${s.kind === 'node' ? escapeHtml((n && n.ip) || 'host') : `${Object.keys(fwConf().nodes).length} nodes`}</div>
            <div class="fw-flags">${flag(fwOn(dc.enable), 'datacenter firewall')}${node ? flag(fwOn(node.enable === undefined ? 1 : node.enable), 'node firewall') : flag(fwOn(dc.ebtables === undefined ? 1 : dc.ebtables), 'ebtables')}</div>
            <div class="fw-pol"><span>in <b>${escapeHtml(fwPolicy(key, 'in'))}</b></span><span>out <b>${escapeHtml(fwPolicy(key, 'out'))}</b></span></div>
            <div class="fw-drop-hint">${readOnly() ? '' : 'drop a source left or right'}</div>`;
    }
    const g = guestById(s.vmid);
    const conf = app.conf.guests[String(s.vmid)] || {};
    const o = (conf.firewall || {}).options || {};
    const nics = (conf.nics || []).map(n => `<div class="fw-nic"><span class="mono">${escapeHtml(n.key)}</span> <span class="mono muted">${escapeHtml(n.bridge)}</span> ${vlanChip(n.tag)} <span class="fw-flag ${n.firewall ? 'on' : 'off'}">${n.firewall ? 'FW' : 'NO FW'}</span></div>`).join('');
    return `<div class="fw-target-title">${g ? guestLabel(g) : escapeHtml(fwScopeName(key))}</div>
        <div class="panel-sub">${g ? `${escapeHtml(g.node)} · ${guestState(g)}` : ''}</div>
        <div class="fw-flags">${flag(fwOn(o.enable), 'firewall')}${flag(fwOn(o.ipfilter), 'IP filter')}${flag(fwOn(o.macfilter === undefined ? 1 : o.macfilter), 'MAC filter')}${flag(fwOn(o.dhcp === undefined ? 1 : o.dhcp), 'DHCP')}</div>
        <div class="fw-pol"><span>in <b>${escapeHtml(fwPolicy(key, 'in'))}</b></span><span>out <b>${escapeHtml(fwPolicy(key, 'out'))}</b></span></div>
        ${nics || '<div class="muted">no network device</div>'}
        <div class="fw-drop-hint">${readOnly() ? '' : 'drop a security group here'}</div>`;
}

function fwWireFlow(body, key) {
    body.querySelectorAll('.fw-pick').forEach(b => b.addEventListener('click', () => fwOpenFlow(b.dataset.target)));
    const select = body.querySelector('.fw-pick-select');
    select.addEventListener('change', () => fwOpenFlow(select.value));
    const search = body.querySelector('.fw-pick-search');
    search.addEventListener('input', () => {
        fw.pickerQuery = search.value;
        const list = fwRenderPicker();
        const tmp = document.createElement('div');
        tmp.innerHTML = list;
        body.querySelector('.fw-pick-list').replaceWith(tmp.querySelector('.fw-pick-list'));
        body.querySelectorAll('.fw-pick').forEach(b => b.addEventListener('click', () => fwOpenFlow(b.dataset.target)));
    });
    body.querySelectorAll('.fw-pill').forEach(p => p.addEventListener('click', () => fwEdgeDetails(fw.edges[Number(p.dataset.edge)], key)));
    body.querySelectorAll('.fw-pill').forEach(p => {
        p.addEventListener('mouseenter', () => fwHighlight(p.dataset.edge, true));
        p.addEventListener('mouseleave', () => fwHighlight(p.dataset.edge, false));
    });
    if (readOnly()) return;
    body.querySelectorAll('.fw-pal[draggable="true"]').forEach(chip => {
        chip.addEventListener('dragstart', (e) => {
            fw.dragging = true;
            e.dataTransfer.setData('text/plain', JSON.stringify({kind: chip.dataset.kind, value: chip.dataset.value}));
            e.dataTransfer.effectAllowed = 'copy';
            body.querySelector('#fw-flow').classList.add('dragging', 'drag-' + chip.dataset.kind);
        });
        chip.addEventListener('dragend', () => {
            fw.dragging = false;
            const flow = body.querySelector('#fw-flow');
            if (flow) flow.className = 'fw-flow';
        });
    });
    body.querySelectorAll('.fw-drop').forEach(zone => {
        zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('over'); });
        zone.addEventListener('dragleave', (e) => { if (!zone.contains(e.relatedTarget)) zone.classList.remove('over'); });
        zone.addEventListener('drop', (e) => {
            e.preventDefault();
            zone.classList.remove('over');
            fw.dragging = false;
            let item = null;
            try { item = JSON.parse(e.dataTransfer.getData('text/plain')); } catch (err) { return; }
            fwDropped(key, zone.dataset.drop, item);
        });
    });
}

async function fwDropped(key, where, item) {
    const s = fwScopeOf(key);
    if (item.kind === 'group') {
        if (s.kind === 'group') { toast('A security group cannot contain another group'); return; }
        if (where !== 'target') { toast('Drop a security group on the target in the middle'); return; }
        const g = fwGroup(item.value);
        const ok = await confirmDialog({
            title: `Use group ${item.value}?`,
            message: `<p>Adds the security group <b>${escapeHtml(item.value)}</b> at the top of ${escapeHtml(fwScopeName(key))}'s rules.</p>
                ${g && g.rules.length ? `<div class="table-wrap" style="margin-top:10px">${fwRuleTable(g.rules, null, {compact: true})}</div>` : '<p class="panel-sub">The group has no rules yet.</p>'}`,
            confirmLabel: 'Add group', wide: true,
        });
        if (!ok) return;
        await fwChange({scope: s, op: 'create', rule: {type: 'group', action: item.value, enable: 1}, pos: 0}, `Group ${item.value} added to ${fwScopeName(key)}`);
        return;
    }
    if (where === 'target') where = 'in';
    let addr = item.value;
    if (item.kind === 'custom') {
        const v = await formDialog({
            title: where === 'in' ? 'Traffic from which address?' : 'Traffic to which address?',
            body: `<div class="form-field"><label>Address or network</label><input class="input" name="addr" placeholder="192.168.1.20, 10.0.0.0/8, 2001:db8::/32"></div>`,
            confirmLabel: 'Next',
            validate: v => /^[0-9a-fA-F:.\/,\s-]+$/.test(v.addr) ? '' : 'Enter an IP address or a network in CIDR form',
        });
        if (!v) return;
        addr = v.addr.replace(/\s+/g, '');
    }
    const preset = where === 'in' ? {type: 'in', source: addr} : {type: 'out', dest: addr};
    await fwAddRule(key, preset);
}

function fwHighlight(index, on) {
    const svg = document.getElementById('fw-edges');
    if (!svg) return;
    svg.querySelectorAll(`[data-edge="${index}"]`).forEach(p => p.classList.toggle('hot', on));
    svg.classList.toggle('has-hot', on);
}

// The edges: from each rule's pill to the target box, and from the target to each outbound pill
function fwDrawEdges() {
    const flow = document.getElementById('fw-flow');
    const svg = document.getElementById('fw-edges');
    const target = document.getElementById('fw-target');
    if (!flow || !svg || !target) return;
    const box = flow.getBoundingClientRect();
    svg.setAttribute('width', box.width);
    svg.setAttribute('height', box.height);
    svg.setAttribute('viewBox', `0 0 ${box.width} ${box.height}`);
    svg.innerHTML = '';
    if (getComputedStyle(svg).display === 'none') return;
    const t = target.getBoundingClientRect();
    const pills = [...flow.querySelectorAll('.fw-pill')];
    const groups = {in: [], out: []};
    pills.forEach(p => groups[fw.edges[Number(p.dataset.edge)].dir].push(p));
    Object.entries(groups).forEach(([dir, list]) => {
        list.forEach((p, k) => {
            const e = fw.edges[Number(p.dataset.edge)];
            const r = p.getBoundingClientRect();
            const span = Math.max(1, list.length - 1);
            const ty = t.top - box.top + 24 + (t.height - 48) * (list.length === 1 ? 0.5 : k / span);
            let x1, y1, x2, y2;
            if (dir === 'in') { x1 = r.right - box.left; y1 = r.top - box.top + r.height / 2; x2 = t.left - box.left; y2 = ty; }
            else { x1 = t.right - box.left; y1 = ty; x2 = r.left - box.left; y2 = r.top - box.top + r.height / 2; }
            const mid = (x1 + x2) / 2;
            const on = fwOn(e.rule.enable);
            const path = svgEl('path', {
                d: `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`, class: `fw-edge${on ? '' : ' off'}${e.policy ? ' policy' : ''}`,
                stroke: FW_ACTION_COLOR[e.rule.action] || 'var(--idle)', 'data-edge': p.dataset.edge,
            });
            svg.appendChild(path);
            const arrowX = dir === 'in' ? x2 : x2;
            const arrowY = y2;
            svg.appendChild(svgEl('path', {d: `M${arrowX - 6},${arrowY - 4} L${arrowX},${arrowY} L${arrowX - 6},${arrowY + 4}`, class: `fw-arrow${on ? '' : ' off'}`, stroke: FW_ACTION_COLOR[e.rule.action] || 'var(--idle)', 'data-edge': p.dataset.edge}));
        });
    });
}
window.addEventListener('resize', () => { if (activeTab === 'firewall' && fw.section === 'flow') fwDrawEdges(); });

// One edge's rule, with its actions
function fwEdgeDetails(e, key) {
    if (!e) return;
    const root = document.getElementById('modal-root');
    const r = e.rule;
    const ro = readOnly();
    let actions = '';
    let detail = '';
    if (e.policy) {
        detail = `<p>Traffic ${e.dir === 'in' ? 'to' : 'from'} ${escapeHtml(fwScopeName(key))} that no rule above matched is <b>${escapeHtml(r.action)}</b>ed: the ${escapeHtml(fwScopeOf(key).kind === 'guest' ? 'guest\'s' : 'datacenter\'s')} <span class="mono">policy_${e.dir}</span>.</p>
            <p class="panel-sub" style="margin-top:8px">Policies are firewall options; this version of the app only shows them.</p>`;
    } else {
        const where = e.via ? `Rule ${e.pos} of security group <b>${escapeHtml(e.via)}</b>, used by rule ${e.viaPos} of ${escapeHtml(fwScopeName(e.viaScope))}`
            : `Rule ${e.pos} of ${escapeHtml(fwScopeName(e.scope))}`;
        const kv = [['Direction', r.type], ['Action', r.action], ['Macro', r.macro], ['Protocol', r.proto], ['Destination port', r.dport], ['Source port', r.sport],
            ['Interface', r.iface], ['Log', r.log], ['Comment', r.comment]].filter(x => x[1]);
        detail = `<p>${where}.</p>
            <div class="kv" style="margin-top:10px">
                <div class="k">source</div><div class="v">${fwAddrHtml(r.source)}</div>
                <div class="k">destination</div><div class="v">${fwAddrHtml(r.dest)}</div>
                ${kv.map(([k, v]) => `<div class="k">${escapeHtml(k.toLowerCase())}</div><div class="v">${escapeHtml(v)}</div>`).join('')}
                <div class="k">enabled</div><div class="v">${fwOn(r.enable) ? 'yes' : 'no'}${e.via && !fwOn(e.viaRule.enable) ? ' <span class="muted">(the group rule is off)</span>' : ''}</div>
            </div>`;
        if (!ro && !e.empty) {
            actions = `<button class="btn" data-act="edit">Edit</button><button class="btn" data-act="toggle">${fwOn(fwRulesOf(e.scope).find((x, i) => (missing(x.pos) ? i : x.pos) === e.pos)?.enable) ? 'Disable' : 'Enable'}</button>
                <button class="btn btn-danger" data-act="delete">Delete</button>`;
        }
        if (!ro && e.via) actions += `<button class="btn btn-danger" data-act="ungroup">Remove group from ${escapeHtml(fwScopeName(e.viaScope))}</button>`;
    }
    root.innerHTML = `<div class="modal-overlay"><div class="modal wide" role="dialog" aria-modal="true" aria-label="Rule">
        <div class="modal-head"><span>${e.policy ? 'Default policy' : `${escapeHtml(r.action)} · ${escapeHtml(e.empty ? 'empty group' : fwService(r))}`}</span><button class="modal-close" data-close aria-label="Close">✕</button></div>
        <div class="modal-body">${detail}${ro && !e.policy ? `<p class="panel-sub" style="margin-top:10px">${fwRoHint()}</p>` : ''}</div>
        <div class="modal-foot">${actions}<span class="spacer"></span><button class="btn" data-close>Close</button></div></div></div>`;
    const close = () => { root.innerHTML = ''; document.removeEventListener('keydown', onKey); };
    const onKey = (ev) => { if (ev.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    root.querySelector('.modal-overlay').addEventListener('click', (ev) => {
        if (ev.target.classList.contains('modal-overlay') || ev.target.hasAttribute('data-close')) close();
        const act = ev.target.dataset && ev.target.dataset.act;
        if (!act) return;
        close();
        if (act === 'edit') fwEditRule(e.scope, e.pos);
        else if (act === 'toggle') fwToggleRule(e.scope, e.pos);
        else if (act === 'delete') fwDeleteRule(e.scope, e.pos);
        else if (act === 'ungroup') fwDeleteRule(e.viaScope, e.viaPos);
    });
}

// ---------------------------------------------------------------- Rules
function fwRuleTable(rules, scopeKey, opts) {
    opts = opts || {};
    const editable = scopeKey && !readOnly() && !opts.compact;
    const rows = rules.map((r, i) => {
        const pos = missing(r.pos) ? i : r.pos;
        const on = fwOn(r.enable);
        const action = r.type === 'group' ? `<span class="fw-kind grp">GROUP</span> <a href="#" onclick="fwOpenFlow('group:${attr(r.action)}');return false">${escapeHtml(r.action)}</a>` : fwActionBadge(r.action);
        const toggle = scopeKey && !opts.compact ? `<button type="button" class="fw-switch ${on ? 'on' : ''}" ${editable ? `onclick="fwToggleRule('${attr(scopeKey)}', ${pos})"` : 'disabled'} title="${on ? 'enabled' : 'disabled'}" aria-label="${on ? 'Disable' : 'Enable'} rule ${pos}"><i></i></button>` : (on ? '✓' : '<span class="muted">off</span>');
        return `<tr class="${on ? '' : 'fw-row-off'}" data-pos="${pos}"${editable ? ' draggable="true"' : ''}>
            ${!opts.compact ? td(editable ? '<span class="fw-handle" title="Drag to move">⠿</span>' : '', 'tight') : ''}
            ${td(String(pos), 'n tight')}${td(toggle, 'tight')}
            ${td(escapeHtml(r.type))}${td(action)}${td(escapeHtml(r.macro || ''))}${td(escapeHtml(r.proto || ''))}
            ${td(fwAddrHtml(r.source))}${td(fwAddrHtml(r.dest))}${td(escapeHtml(r.dport || ''), 'mono')}${td(escapeHtml(r.sport || ''), 'mono')}
            ${td(escapeHtml(r.iface || ''), 'mono')}${td(r.log && r.log !== 'nolog' ? escapeHtml(r.log) : '')}${td(escapeHtml(r.comment || ''), 'wrap-cell')}
            ${editable ? td(`<button type="button" class="btn btn-small" onclick="fwEditRule('${attr(scopeKey)}', ${pos})">Edit</button> <button type="button" class="btn btn-small btn-danger" onclick="fwDeleteRule('${attr(scopeKey)}', ${pos})">✕</button>`, 'tight') : ''}
        </tr>`;
    });
    const head = [...(opts.compact ? [] : [{label: ''}]), {label: '#', n: true}, {label: 'On'}, {label: 'Dir'}, {label: 'Action'}, {label: 'Macro'}, {label: 'Proto'},
        {label: 'Source'}, {label: 'Destination'}, {label: 'D-port'}, {label: 'S-port'}, {label: 'Iface'}, {label: 'Log'}, {label: 'Comment'}, ...(editable ? [{label: ''}] : [])];
    return table(head, rows, {cls: 'fw-rules', empty: 'No rules'});
}

function fwRenderRules(body) {
    if (!fwScopeExists(fw.scope)) fw.scope = 'cluster';
    const c = fwConf();
    const key = fw.scope;
    const options = [['cluster', 'Datacenter'], ...Object.keys(c.nodes).sort().map(n => [`node:${n}`, `Node ${n}`]),
        ...guests({noTemplates: true}).map(g => [`guest:${g.vmid}`, `${g.vmid} ${guestName(g)}`]), ...c.groups.map(g => [`group:${g.group}`, `Group ${g.group}`])];
    const rules = fwRulesOf(key);
    const s = fwScopeOf(key);
    const opts = fwOptionsOf(key);
    const what = s.kind === 'cluster' ? 'filter traffic to every node (not the guests)' : s.kind === 'node' ? 'filter traffic to this node, before the datacenter\'s rules'
        : s.kind === 'group' ? 'apply wherever a rule uses this group' : 'filter this guest\'s network devices that have firewall=1';
    body.innerHTML = `
        <div class="filters">
            <select class="select fw-scope-select" id="fw-scope" aria-label="Rules of">${options.map(([k, l]) => `<option value="${attr(k)}"${k === key ? ' selected' : ''}>${escapeHtml(l)}</option>`).join('')}</select>
            <button type="button" class="btn btn-small" onclick="fwOpenFlow('${attr(key)}')">Show in Flow</button>
            ${readOnly() ? fwRoHint() : `<button type="button" class="btn btn-small btn-primary" onclick="fwAddRule('${attr(key)}', {type: 'in'})">+ Add rule</button>`}
            <span class="range-text">${rules.length} rule${rules.length === 1 ? '' : 's'}${s.kind !== 'group' ? ` · policy in ${escapeHtml(fwPolicy(key, 'in'))}, out ${escapeHtml(fwPolicy(key, 'out'))}` : ''}</span>
        </div>
        ${s.kind === 'guest' ? permissionNote(`fw:${s.vmid}`, 'Rules') : s.kind === 'node' ? permissionNote(`fwnode:${s.node}`, 'Rules') : s.kind === 'group' ? permissionNote(`fwgroup:${s.group}`, 'Rules') : ''}
        ${panel(escapeHtml(fwScopeName(key)), `these rules ${what}${s.kind !== 'group' && !fwOn(opts.enable === undefined && s.kind === 'node' ? 1 : opts.enable) ? ' · <span class="neg">its firewall is off</span>' : ''}`,
            `<div class="table-wrap" id="fw-rule-table">${fwRuleTable(rules, key)}</div>${!readOnly() && rules.length > 1 ? '<p class="panel-sub" style="margin-top:8px">Drag a row by ⠿ to change the order. The first rule that matches decides.</p>' : ''}`)}`;
    const select = document.getElementById('fw-scope');
    select.addEventListener('change', () => { fw.scope = select.value; store('fw_scope', select.value); renderFirewall(); });
    fwWireReorder(body, key);
}

function fwWireReorder(body, key) {
    if (readOnly()) return;
    let from = null;
    body.querySelectorAll('#fw-rule-table tr[draggable="true"]').forEach(tr => {
        tr.addEventListener('dragstart', (e) => {
            from = Number(tr.dataset.pos);
            fw.dragging = true;
            e.dataTransfer.setData('text/plain', String(from));
            e.dataTransfer.effectAllowed = 'move';
            tr.classList.add('fw-moving');
        });
        tr.addEventListener('dragend', () => { fw.dragging = false; tr.classList.remove('fw-moving'); });
        tr.addEventListener('dragover', (e) => {
            e.preventDefault();
            body.querySelectorAll('.fw-over-top, .fw-over-bottom').forEach(x => x.classList.remove('fw-over-top', 'fw-over-bottom'));
            const pos = Number(tr.dataset.pos);
            tr.classList.add(from !== null && pos > from ? 'fw-over-bottom' : 'fw-over-top');
        });
        tr.addEventListener('drop', (e) => {
            e.preventDefault();
            fw.dragging = false;
            body.querySelectorAll('.fw-over-top, .fw-over-bottom').forEach(x => x.classList.remove('fw-over-top', 'fw-over-bottom'));
            const source = Number(e.dataTransfer.getData('text/plain'));
            const to = Number(tr.dataset.pos);
            if (!Number.isNaN(source) && source !== to) fwMoveRule(key, source, to);
        });
    });
}

// ---------------------------------------------------------------- Groups
function fwRenderGroups(body) {
    const c = fwConf();
    if (!c.groups.length) {
        body.innerHTML = panel('Security groups', '', '<div class="empty-state">No security groups. Make them under Datacenter → Firewall → Security Group in Proxmox.</div>');
        return;
    }
    body.innerHTML = c.groups.map(g => {
        const users = fwUsedBy(r => r.type === 'group' && r.action === g.group);
        return panel(`${escapeHtml(g.group)}`, `${escapeHtml(g.comment || '')}`,
            `<div class="fw-group-users"><span class="muted">Used by</span> ${fwScopeLinks(users)}</div>
             <div class="table-wrap">${fwRuleTable(g.rules, `group:${g.group}`)}</div>
             <div class="detail-actions" style="margin-top:10px"><button type="button" class="btn btn-small" onclick="fwOpenFlow('group:${attr(g.group)}')">Show in Flow</button>
             ${readOnly() ? '' : `<button type="button" class="btn btn-small" onclick="fwAddRule('group:${attr(g.group)}', {type: 'in'})">+ Add rule</button>`}</div>`);
    }).join('');
}

// ---------------------------------------------------------------- IP sets & aliases
function fwRenderSets(body) {
    const c = fwConf();
    const aliasRows = c.aliases.map(a => `<tr>${td(`<span class="fw-ref alias">${escapeHtml(a.name)}</span>`)}${td(escapeHtml(a.cidr), 'mono')}${td(escapeHtml(a.comment || ''), 'wrap-cell')}
        ${td(fwScopeLinks(fwUsedBy(r => fwRefersTo(r, a.name, false))), 'wrap-cell')}</tr>`);
    const setRows = c.ipsets.map(s => `<tr>${td(`<span class="fw-ref ipset">+${escapeHtml(s.name)}</span>${s.name === 'management' ? '<span class="sub">lets these in to the web interface and SSH</span>' : s.name === 'blacklist' ? '<span class="sub">dropped everywhere</span>' : ''}`)}
        ${td(s.entries.length ? s.entries.map(e => `<span class="fw-cidr${e.nomatch ? ' nomatch' : ''}" title="${attr(e.comment || '')}">${e.nomatch ? '!' : ''}${escapeHtml(e.cidr)}</span>`).join('') : '<span class="muted">empty</span>', 'wrap-cell')}
        ${td(escapeHtml(s.comment || ''), 'wrap-cell')}${td(fwScopeLinks(fwUsedBy(r => fwRefersTo(r, s.name, true))), 'wrap-cell')}</tr>`);
    const guestSets = Object.values(app.conf.guests || {}).flatMap(g => ((g.firewall || {}).ipsets || []).map(s => ({g, s})));
    body.innerHTML = `
        ${panel('IP sets', 'groups of addresses; a rule refers to one as +name', `${permissionNote('fwipsets', 'IP sets')}<div class="table-wrap">${table([{label: 'Name'}, {label: 'Entries'}, {label: 'Comment'}, {label: 'Used by'}], setRows, {empty: 'No IP sets'})}</div>`)}
        ${panel('Aliases', 'a name for one address or network', `${permissionNote('fwaliases', 'Aliases')}<div class="table-wrap">${table([{label: 'Name'}, {label: 'Address'}, {label: 'Comment'}, {label: 'Used by'}], aliasRows, {empty: 'No aliases'})}</div>`)}
        ${guestSets.length ? panel('Guest IP sets', 'kept by each guest; ipfilter-netN lists the addresses its IP filter allows',
            `<div class="table-wrap">${table([{label: 'Guest'}, {label: 'IP set'}, {label: 'Comment'}], guestSets.map(({g, s}) => {
                const guest = guestById(g.vmid);
                return `<tr>${td(guest ? guestLink(guest) : escapeHtml(g.vmid))}${td(`<span class="fw-ref ipset">+${escapeHtml(s.name)}</span>`)}${td(escapeHtml(s.comment || ''))}</tr>`;
            }))}</div>`) : ''}`;
}

// ---------------------------------------------------------------- Log & stats
const FW_PRIVATE = [/^10\./, /^192\.168\./, /^172\.(1[6-9]|2\d|3[01])\./, /^127\./, /^169\.254\./, /^f[cd]/i, /^fe80:/i, /^::1$/];
const fwPrivate = ip => FW_PRIVATE.some(re => re.test(ip));
function fwTargetOf(e) { return e.vmid ? `guest:${e.vmid}` : `host:${e.node}`; }
function fwTargetLabel(key) {
    if (key.startsWith('host:')) return `<span class="fw-kind host">HOST</span> ${escapeHtml(key.slice(5))}`;
    const g = guestById(key.slice(6));
    return g ? guestLink(g) : `<span class="mono">${escapeHtml(key.slice(6))}</span>`;
}
const fwPortName = p => FW_SERVICES[Number(p)] || '';

async function fwLoadLog(force) {
    if (fw.logLoading) return;
    if (!force && fw.log && Date.now() / 1000 - fw.log.read_at < 25) return;
    fw.logLoading = true;
    try {
        fw.log = await api('firewall/log');
        fw.logError = '';
    } catch (e) {
        fw.logError = e.message;
    }
    fw.logLoading = false;
    if (activeTab === 'firewall' && fw.section === 'log' && !fwBusy()) {
        const body = document.getElementById('fw-body');
        if (body) fwRenderLog(body, true);
    }
}
function fwLogTimer() {
    clearInterval(fw.logTimer);
    fw.logTimer = null;
    if (activeTab === 'firewall' && fw.section === 'log') {
        fw.logTimer = setInterval(() => {
            if (activeTab !== 'firewall' || fw.section !== 'log') { clearInterval(fw.logTimer); fw.logTimer = null; return; }
            if (!document.hidden) fwLoadLog(true);
        }, 30000);
    }
}

function fwCount(list, keyOf) {
    const m = new Map();
    list.forEach(e => { const k = keyOf(e); if (k) m.set(k, (m.get(k) || 0) + 1); });
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function fwRenderLog(body, fromLoad) {
    if (!fw.log) {
        body.innerHTML = `${permissionNote('fwlog', 'Firewall log')}<div class="empty-state">${fw.logError ? escapeHtml(fw.logError) : 'Reading the firewall log of every node…'}</div>`;
        if (!fromLoad) fwLoadLog(true);
        return;
    }
    if (!fromLoad) fwLoadLog(false);
    const now = Date.now() / 1000;
    const day = fw.log.entries.filter(e => e.ts >= now - 86400);
    const blocked = day.filter(e => e.action !== 'ACCEPT');
    const accepted = day.filter(e => e.action === 'ACCEPT');
    const sources = fwCount(blocked, e => e.src);
    const ports = fwCount(blocked.filter(e => e.dpt), e => `${e.proto.toLowerCase()}/${e.dpt}`);
    const targets = fwCount(day, fwTargetOf);
    const external = new Set(blocked.filter(e => !fwPrivate(e.src)).map(e => e.src)).size;
    const topPort = ports[0];
    const topTarget = targets[0];
    // Per hour, the last 24 hours
    const start = Math.floor(now / 3600) * 3600 - 23 * 3600;
    const hours = Array.from({length: 24}, (_, i) => ({t: start + i * 3600, values: [0, 0, 0]}));
    day.forEach(e => {
        const i = Math.floor((e.ts - start) / 3600);
        if (i < 0 || i > 23) return;
        hours[i].values[e.action === 'DROP' ? 0 : e.action === 'REJECT' ? 1 : 2]++;
    });
    const noteText = Object.values(fw.log.notes || {});
    const portRows = ports.slice(0, 10).map(([k, n]) => {
        const [proto, port] = k.split('/');
        return `<tr>${td(`<span class="mono">${escapeHtml(port)}</span>`)}${td(escapeHtml(proto))}${td(escapeHtml(fwPortName(port)))}${td(num(n), 'n')}${td(meter(n / ports[0][1], false), 'fw-cell-meter')}</tr>`;
    });
    const srcRows = sources.slice(0, 10).map(([ip, n]) => {
        const priv = fwPrivate(ip);
        const set = (fwConf() && fwConf().ipsets || []).find(s => s.entries.some(en => en.cidr === ip || fwInCidr(ip, en.cidr)));
        return `<tr>${td(`<span class="mono">${escapeHtml(ip)}</span>`)}${td(priv ? '<span class="badge mode">private</span>' : '<span class="badge warn">internet</span>')}${td(set ? `<span class="fw-ref ipset">+${escapeHtml(set.name)}</span>` : '')}${td(num(n), 'n')}${td(meter(n / sources[0][1], false), 'fw-cell-meter')}</tr>`;
    });
    const targetRows = targets.slice(0, 10).map(([k, n]) => {
        const b = day.filter(e => fwTargetOf(e) === k && e.action !== 'ACCEPT').length;
        return `<tr>${td(fwTargetLabel(k))}${td(num(b), 'n')}${td(num(n - b), 'n')}${td(meter(n / targets[0][1], false), 'fw-cell-meter')}</tr>`;
    });
    body.innerHTML = `
        ${permissionNote('fwlog', 'Firewall log')}
        ${noteText.length ? `<div class="note warn" style="margin-bottom:12px">${noteText.map(escapeHtml).join('<br>')}</div>` : ''}
        <div class="tiles kpi-tiles">
            ${tile('Blocked · 24 h', num(blocked.length), `${num(day.filter(e => e.action === 'DROP').length)} dropped · ${num(day.filter(e => e.action === 'REJECT').length)} rejected`, blocked.length ? 'neg' : '')}
            ${tile('Accepted · 24 h', num(accepted.length), 'logged ACCEPT rules')}
            ${tile('Sources', num(new Set(blocked.map(e => e.src)).size), `${num(external)} from the internet`)}
            ${tile('Top port', topPort ? escapeHtml(topPort[0].split('/')[1]) : '–', topPort ? `${escapeHtml(fwPortName(topPort[0].split('/')[1]) || topPort[0].split('/')[0])} · ${num(topPort[1])} blocked` : '')}
            ${tile('Busiest target', topTarget ? escapeHtml(topTarget[0].startsWith('host:') ? topTarget[0].slice(5) : guestName(guestById(topTarget[0].slice(6))) || topTarget[0].slice(6)) : '–', topTarget ? `${num(topTarget[1])} lines` : '')}
        </div>
        <div class="dash-grid wide-left">
            ${panel('Per hour', 'last 24 hours, every node', '<div id="fw-hours" class="chart"></div><div class="legend" id="fw-hours-legend"></div>')}
            ${panel('Per target', 'blocked and accepted, 24 h', `<div class="table-wrap">${table([{label: 'Target'}, {label: 'Blocked', n: true}, {label: 'Accepted', n: true}, {label: ''}], targetRows, {empty: 'No lines in the last 24 hours'})}</div>`)}
        </div>
        <div class="dash-grid">
            ${panel('Top sources', 'blocked, 24 h', `<div class="table-wrap">${table([{label: 'Source'}, {label: 'Where'}, {label: 'In IP set'}, {label: 'Blocked', n: true}, {label: ''}], srcRows, {empty: 'Nothing blocked'})}</div>`)}
            ${panel('Top destination ports', 'blocked, 24 h', `<div class="table-wrap">${table([{label: 'Port'}, {label: 'Proto'}, {label: 'Service'}, {label: 'Blocked', n: true}, {label: ''}], portRows, {empty: 'Nothing blocked'})}</div>`)}
        </div>
        <div class="panel"><div class="panel-body">
            <div class="chart-title"><span>Log</span><span class="hint">${num(fw.log.entries.length)} lines from ${escapeHtml(Object.keys((app.conf && app.conf.nodes) || {}).length)} nodes · read ${escapeHtml(fmtAgo(fw.log.read_at))} · every 30 s</span></div>
            <div class="filters" id="fw-log-filters">
                ${chipsHtml('fw-log-action', [{key: 'all', label: 'All'}, {key: 'DROP', label: 'Drop'}, {key: 'REJECT', label: 'Reject'}, {key: 'ACCEPT', label: 'Accept'}], fw.logFilter.action, 'Action')}
                ${chipsHtml('fw-log-dir', [{key: 'all', label: 'Both'}, {key: 'in', label: 'In'}, {key: 'out', label: 'Out'}], fw.logFilter.dir, 'Direction')}
                <select class="select" id="fw-log-target" aria-label="Target"><option value="all">Every target</option>${targets.concat(fwCount(fw.log.entries, fwTargetOf).filter(t => !targets.some(x => x[0] === t[0]))).map(([k]) => `<option value="${attr(k)}"${k === fw.logFilter.target ? ' selected' : ''}>${escapeHtml(k.startsWith('host:') ? 'host ' + k.slice(5) : `${k.slice(6)} ${guestName(guestById(k.slice(6)))}`)}</option>`).join('')}</select>
                <input class="input" type="search" id="fw-log-q" placeholder="Address, port, chain" value="${attr(fw.logFilter.q)}" aria-label="Search the log">
                <button type="button" class="btn btn-small" onclick="fwLoadLog(true)" title="Read the log again">⟳</button>
            </div>
            <div id="fw-log-table"></div>
        </div></div>`;
    barChart(document.getElementById('fw-hours'), {
        data: hours.map(h => ({label: new Date(h.t * 1000).getHours().toString().padStart(2, '0'), tipTitle: `${fmtDay(h.t)} ${fmtTime(h.t)}`, values: h.values})),
        series: [{name: 'DROP', color: 'var(--neg)'}, {name: 'REJECT', color: 'var(--warning-color)'}, {name: 'ACCEPT', color: 'var(--pos)'}],
        height: 200, format: v => num(v), empty: 'No log lines in the last 24 hours', labelWidth: 30,
    });
    document.getElementById('fw-hours-legend').innerHTML = legendHtml([{name: 'Dropped', color: 'var(--neg)'}, {name: 'Rejected', color: 'var(--warning-color)'}, {name: 'Accepted', color: 'var(--pos)'}]);
    bindChips('fw-log-action', k => { fw.logFilter.action = k; fw.logPage = 0; fwRenderLogTable(); });
    bindChips('fw-log-dir', k => { fw.logFilter.dir = k; fw.logPage = 0; fwRenderLogTable(); });
    document.getElementById('fw-log-target').addEventListener('change', e => { fw.logFilter.target = e.target.value; fw.logPage = 0; fwRenderLogTable(); });
    document.getElementById('fw-log-q').addEventListener('input', e => { fw.logFilter.q = e.target.value; fw.logPage = 0; fwRenderLogTable(); });
    fwRenderLogTable();
}

function fwInCidr(ip, cidr) {
    const m = /^(\d+\.\d+\.\d+\.\d+)\/(\d+)$/.exec(cidr || '');
    if (!m || !/^\d+\.\d+\.\d+\.\d+$/.test(ip)) return false;
    const toInt = a => a.split('.').reduce((acc, o) => acc * 256 + Number(o), 0);
    const size = 2 ** (32 - Number(m[2]));
    return Math.floor(toInt(ip) / size) === Math.floor(toInt(m[1]) / size);
}

function fwRenderLogTable() {
    const el = document.getElementById('fw-log-table');
    if (!el || !fw.log) return;
    const f = fw.logFilter;
    const q = f.q.toLowerCase();
    const list = fw.log.entries.filter(e => (f.action === 'all' || e.action === f.action) && (f.dir === 'all' || e.dir === f.dir)
        && (f.target === 'all' || fwTargetOf(e) === f.target)
        && (!q || [e.src, e.dst, e.dpt, e.spt, e.chain, e.proto, e.node, e.iface].some(v => String(v).toLowerCase().includes(q))));
    const per = 50;
    const pages = Math.max(1, Math.ceil(list.length / per));
    fw.logPage = Math.min(fw.logPage, pages - 1);
    const rows = list.slice(fw.logPage * per, fw.logPage * per + per).map(e => `<tr>
        ${td(`<span title="${attr(new Date(e.ts * 1000).toLocaleString())}">${escapeHtml(now24(e.ts))}</span>`, 'mono')}
        ${td(fwActionBadge(e.action) + (e.policy ? ' <span class="muted">policy</span>' : ''))}${td(escapeHtml(e.dir))}${td(fwTargetLabel(fwTargetOf(e)))}
        ${td(escapeHtml(e.src), 'mono')}${td(escapeHtml(e.dst), 'mono')}${td(escapeHtml(e.proto))}
        ${td(`${escapeHtml(e.dpt)}${fwPortName(e.dpt) ? ` <span class="muted">${escapeHtml(fwPortName(e.dpt))}</span>` : ''}`, 'mono')}
        ${td(escapeHtml(e.chain), 'mono muted')}${td(escapeHtml(e.node))}</tr>`);
    el.innerHTML = `<div class="table-wrap">${table([{label: 'Time'}, {label: 'Action'}, {label: 'Dir'}, {label: 'Target'}, {label: 'Source'}, {label: 'Destination'}, {label: 'Proto'},
        {label: 'Port'}, {label: 'Chain'}, {label: 'Node'}], rows, {empty: fw.log.entries.length ? 'No lines match' : 'The log is empty: only rules and policies with a log level write here'})}</div>
        <div class="pager"><span>${num(list.length)} lines</span>
            <button type="button" class="btn btn-small" ${fw.logPage ? '' : 'disabled'} onclick="fw.logPage--;fwRenderLogTable()">‹</button>
            <span>${fw.logPage + 1} / ${pages}</span>
            <button type="button" class="btn btn-small" ${fw.logPage < pages - 1 ? '' : 'disabled'} onclick="fw.logPage++;fwRenderLogTable()">›</button></div>`;
}
function now24(ts) {
    return Date.now() / 1000 - ts > 86400 ? `${fmtDay(ts)} ${new Date(ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'})}`
        : new Date(ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
}

// ---------------------------------------------------------------- Options and coverage
function fwRenderOptions(body) {
    const c = fwConf();
    const o = c.options || {};
    const yes = (v, dflt) => fwOn(v === undefined ? dflt : v) ? stateLabel('ok', 'on') : stateLabel('bad', 'off');
    const nodeRows = Object.keys(c.nodes).sort().map(n => {
        const x = c.nodes[n].options || {};
        return `<tr>${td(`<a href="#" onclick="openNode('${attr(n)}');return false">${escapeHtml(n)}</a>`)}${td(yes(x.enable, 1))}${td(escapeHtml(x.log_level_in || 'nolog'))}${td(escapeHtml(x.log_level_out || 'nolog'))}
            ${td(yes(x.nosmurfs, 1))}${td(yes(x.tcpflags, 0))}${td(escapeHtml(x.nf_conntrack_max || ''), 'n')}${td(num((c.nodes[n].rules || []).length), 'n')}</tr>`;
    });
    const dcOn = fwOn(o.enable);
    let off = 0, bypass = 0;
    const coverage = guests({noTemplates: true}).map(g => {
        const conf = app.conf.guests[String(g.vmid)] || {};
        const x = (conf.firewall || {}).options || {};
        const nics = conf.nics || [];
        const withFw = nics.filter(n => n.firewall).length;
        const on = fwOn(x.enable);
        const ok = dcOn && on && withFw === nics.length && nics.length;
        if (!on) off++;
        else if (withFw < nics.length) bypass++;
        const flag = !on ? '<span class="badge bad">firewall off</span>' : withFw < nics.length ? `<span class="badge warn">${nics.length - withFw} NIC${nics.length - withFw === 1 ? '' : 's'} bypass</span>` : ok ? '<span class="badge ok">filtered</span>' : '';
        return `<tr class="${ok ? '' : ''}" onclick="fwOpenFlow('guest:${g.vmid}')">${td(guestLabel(g))}${td(escapeHtml(g.node))}${td(flag)}
            ${td(nics.map(n => `<span class="fw-flag ${n.firewall ? 'on' : 'off'}" title="${attr(n.bridge + (missing(n.tag) ? '' : ' VLAN ' + n.tag))}">${escapeHtml(n.key)}</span>`).join(' ') || '<span class="muted">none</span>')}
            ${td(escapeHtml(x.policy_in || 'DROP'))}${td(escapeHtml(x.policy_out || 'ACCEPT'))}${td(fwOn(x.ipfilter) ? '✓' : '<span class="muted">–</span>')}${td(fwOn(x.macfilter === undefined ? 1 : x.macfilter) ? '✓' : '<span class="muted">–</span>')}
            ${td(num(((conf.firewall || {}).rules || []).length), 'n')}${td(escapeHtml(x.log_level_in && x.log_level_in !== 'nolog' ? x.log_level_in : ''))}</tr>`;
    });
    body.innerHTML = `
        <div class="dash-grid">
            ${panel('Datacenter', 'the master switch for every node and guest', `${permissionNote('fwopt', 'Options')}
                ${infoRow('Firewall', yes(o.enable, 0))}
                ${infoRow('Input policy', escapeHtml(o.policy_in || 'DROP'))}
                ${infoRow('Output policy', escapeHtml(o.policy_out || 'ACCEPT'))}
                ${o.policy_forward ? infoRow('Forward policy', escapeHtml(o.policy_forward)) : ''}
                ${infoRow('ebtables', yes(o.ebtables, 1))}
                ${infoRow('Log rate limit', escapeHtml(o.log_ratelimit || 'default (1/second, burst 5)'))}
                <p class="panel-sub" style="margin-top:8px">Options are shown here; change them in Proxmox under Datacenter → Firewall → Options.</p>`)}
            ${panel('Coverage', 'guests that the firewall filters', `<div class="tiles" style="margin-bottom:0">
                ${tile('Filtered', num(coverage.length - off - bypass), `of ${num(coverage.length)} guests`, !dcOn ? 'bad' : '')}
                ${tile('Firewall off', num(off), 'guest option enable', off ? 'warn' : '')}
                ${tile('NICs bypass', num(bypass), 'a NIC without firewall=1', bypass ? 'warn' : '')}</div>
                ${dcOn ? '' : '<p class="neg" style="margin-top:10px">The datacenter firewall is off, so no guest is filtered.</p>'}`)}
        </div>
        ${panel('Nodes', 'each node\'s host firewall', `<div class="table-wrap">${table([{label: 'Node'}, {label: 'Firewall'}, {label: 'Log in'}, {label: 'Log out'}, {label: 'SMURFS filter'}, {label: 'TCP flags filter'}, {label: 'Conntrack max', n: true}, {label: 'Rules', n: true}], nodeRows)}</div>`)}
        ${panel('Guests', 'click a guest to see its flow', `<div class="table-wrap">${table([{label: 'Guest'}, {label: 'Node'}, {label: 'State'}, {label: 'NICs (firewall=1)'}, {label: 'Policy in'}, {label: 'Policy out'},
            {label: 'IP filter'}, {label: 'MAC filter'}, {label: 'Rules', n: true}, {label: 'Log'}], coverage, {cls: 'hover', empty: 'No guests'})}</div>`)}`;
}

// =====================================================================
onTab('firewall', (sub) => {
    if (FW_SECTIONS.some(s => s.key === sub)) { fw.section = sub; store('fw_section', sub); }
    else if (fw.section) setSub(fw.section);
    renderFirewall();
    fwLogTimer();
}, () => {
    if (fwBusy()) return;
    if (app.confAt !== fw.renderedAt) renderFirewall();
});
