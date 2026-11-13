// Proxmox dashboard - Info: the connection, the app's options, what the token may read, and about.

const NOTE_LABELS = {
    version: 'Version', tasks: 'Task log', ha: 'HA status', ceph: 'Ceph', node: 'Node status', network: 'Network', disks: 'Disks', zfs: 'ZFS',
    services: 'Services', updates: 'Updates (needs Sys.Modify)', subscription: 'Subscription', fwnode: 'Node firewall rules', fwnodeopt: 'Node firewall options',
    replication: 'Replication', fwopt: 'Firewall options', fwrules: 'Firewall rules', fwgroups: 'Security groups', fwipsets: 'IP sets', fwaliases: 'Aliases',
    fwmacros: 'Firewall macros', fwgroup: 'Security group', ipset: 'IP set', config: 'Guest configuration', fw: 'Guest firewall rules', fwopt_guest: 'Guest firewall options',
    fwalias: 'Guest aliases', fwipset: 'Guest IP sets', sdn: 'SDN', backup: 'Backup jobs', backupinfo: 'Guests in no backup job', haresources: 'HA resources',
    hagroups: 'HA groups', pools: 'Pools', fwlog: 'Firewall log (needs Sys.Modify)', backups: 'Backups on storage', backuptasks: 'Backup tasks', backuplog: 'Backup job logs', current: 'Guest status', snapshots: 'Snapshots', pending: 'Pending changes',
};

function renderInfo() {
    const el = document.getElementById('tab-info');
    const s = app.status;
    if (!s) return;
    const c = s.config;
    const n = s.notes || {};
    // Ceph and SDN missing usually means the cluster doesn't use them; the rest is about permissions
    const absent = k => /^(ceph|sdn)/.test(k) && !/permission/i.test(n[k]);
    const groups = {};
    Object.entries(n).forEach(([k, v]) => {
        const [part, which] = k.split(':');
        const key = `${part}|${absent(k) ? 'absent' : v}`;
        if (!groups[key]) groups[key] = {part, text: v, which: [], absent: absent(k)};
        if (which) groups[key].which.push(which);
    });
    const noteRows = Object.values(groups).map(g => `<tr>
        ${td(escapeHtml(NOTE_LABELS[g.part] || g.part))}
        ${td(g.which.length ? escapeHtml(g.which.slice(0, 8).join(', ') + (g.which.length > 8 ? ` +${g.which.length - 8}` : '')) : '<span class="muted">cluster</span>', 'wrap-cell')}
        ${td(g.absent ? '<span class="muted">not used on this cluster</span>' : escapeHtml(g.text), 'wrap-cell')}
    </tr>`);
    const cl = clusterInfo();
    el.innerHTML = `
        <div class="page-section"><span>Connection</span><small>change these in the app's configuration in Home Assistant</small></div>
        <div class="info-grid">
            ${panel('Cluster', '', [
                infoRow('State', !c.configured ? stateLabel('warn', 'not configured') : s.connected ? stateLabel('ok', 'connected') : `<span class="state bad">${escapeHtml(s.error || 'not reachable')}</span>`),
                infoRow('Reading through', escapeHtml(s.host || '–')),
                infoRow('Cluster', cl ? `${escapeHtml(cl.name)} · ${num(cl.nodes)} nodes · ${cl.quorate ? 'quorate' : '<span class="neg">no quorum</span>'}` : escapeHtml(nodes().length ? 'single node' : '–')),
                infoRow('Proxmox VE', escapeHtml((s.pve && s.pve.version) || '–')),
                infoRow('State read', `${escapeHtml(fmtAgo(s.live_at))} <span class="muted">· every ${c.poll_seconds} s</span>`),
                infoRow('Configuration read', `${escapeHtml(fmtAgo(s.conf_at))} <span class="muted">· every 5 min and after a change</span> <button type="button" class="btn btn-small" onclick="rereadConfig()">Read now</button>`),
            ].join(''))}
            ${panel('Options', '', [
                infoRow('Hosts', c.hosts.length ? c.hosts.map(h => `<span class="mono">${escapeHtml(h)}</span>`).join('<br>') : '<span class="neg">none</span>'),
                infoRow('Token', c.token_id ? `<span class="mono">${escapeHtml(c.token_id)}</span>` : '<span class="neg">none</span>'),
                infoRow('Certificates', c.verify_ssl ? 'checked' : 'not checked <span class="muted">· Proxmox\'s own are self-signed</span>'),
                infoRow('Read every', `${c.poll_seconds} s`),
                infoRow('Read-only', c.read_only ? 'on <span class="muted">· this app changes nothing</span>' : '<span class="warn-text">off</span> <span class="muted">· power, firewall rules and VLANs can be changed</span>'),
                infoRow('Log level', escapeHtml(c.log_level)),
            ].join(''))}
        </div>
        <div class="page-section"><span>What the token can't read</span><small>the rest of the dashboard works without these</small></div>
        <div class="panel"><div class="panel-body">${noteRows.length ? table([{label: 'Part'}, {label: 'Where'}, {label: 'Proxmox said'}], noteRows)
            : '<div class="all-good"><span class="sdot ok"></span> The token can read everything this app shows.</div>'}</div></div>
        <div class="page-section"><span>Making a token</span><small>on any node, as root</small></div>
        <div class="panel"><div class="panel-body">
            <p class="panel-sub" style="margin-bottom:10px">Read-only, for the whole dashboard. <span class="mono">Sys.Modify</span> is only for the firewall log and the update list, which Proxmox guards with it; leave it out if you don't want those.</p>
            <pre class="task-log" style="min-height:0">pveum user add homeassistant@pve --comment "Home Assistant dashboard"
# Proxmox VE 9
pveum role add HADashboard --privs "Sys.Audit,Sys.Modify,VM.Audit,VM.GuestAgent.Audit,Datastore.Audit,SDN.Audit,Pool.Audit"
# Proxmox VE 8
pveum role add HADashboard --privs "Sys.Audit,Sys.Modify,VM.Audit,VM.Monitor,Datastore.Audit,SDN.Audit,Pool.Audit"
pveum acl modify / --users homeassistant@pve --roles HADashboard
pveum user token add homeassistant@pve dashboard --privsep 0</pre>
            <p class="panel-sub" style="margin:10px 0">For the buttons as well (power, firewall rules, VLANs), add <span class="mono">VM.PowerMgmt,VM.Config.Network</span> to the role with <span class="mono">pveum role modify HADashboard --append 1 --privs "VM.PowerMgmt,VM.Config.Network"</span>, and set <span class="mono">read_only: false</span>.</p>
            <p class="panel-sub">Then fill in <span class="mono">token_id: homeassistant@pve!dashboard</span> and the secret the last command printed (it is shown once).</p>
        </div></div>
        <div class="page-section"><span>About</span></div>
        <div class="info-grid">
            ${panel('This app', '', [
                infoRow('Version', escapeHtml(s.version)),
                infoRow('Changelog', '<a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-proxmox/CHANGELOG.md" target="_blank" rel="noopener">on GitHub</a>'),
                infoRow('Proxmox VE', '<a href="https://pve.proxmox.com/pve-docs/" target="_blank" rel="noopener">documentation</a>'),
                infoRow('Note', 'Unofficial: not made by or affiliated with Proxmox Server Solutions GmbH'),
            ].join(''))}
            ${panel('Donate', '', `<div class="panel-sub" style="margin-bottom:12px">This app is free and open source. If it's useful to you, please consider supporting its development.</div>
                <div class="detail-actions">
                    <a class="btn btn-primary" href="https://www.paypal.me/cyberjunkynl/" target="_blank" rel="noopener">Donate via PayPal</a>
                    <a class="btn" href="https://github.com/sponsors/cyberjunky" target="_blank" rel="noopener">Sponsor on GitHub</a>
                </div>`)}
        </div>`;
}

onTab('info', renderInfo, renderInfo);
