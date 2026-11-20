// Scanner - Info: the scanners that were found, which scanners work and how to connect them,
// what the app keeps and where.

function renderInfo() {
    const el = document.getElementById('tab-info');
    const s = app.status, d = app.devices;
    if (!s || !d) { el.innerHTML = '<div class="empty-state">…</div>'; return; }
    const c = s.config;
    const KINDS = {airscan: 'driverless (eSCL)', escl: 'driverless (eSCL)', test: 'test scanner', net: 'another SANE server'};
    el.innerHTML = `
        ${panel('Scanners', `<button class="btn btn-small" id="info-search">Search again</button>`, d.devices.length ? table(
            [{label: 'Scanner'}, {label: 'Kind'}, {label: 'Driver'}, {label: 'SANE name'}],
            d.devices.map(x => `<tr>${td(`<span class="name">${escapeHtml(x.label)}</span>`)}${td(escapeHtml(x.type))}${td(escapeHtml(KINDS[x.backend] || x.backend))}${td(`<span class="mono">${escapeHtml(x.id)}</span>`, 'wrap-cell')}</tr>`))
            : `<div class="all-good"><span class="sdot ${s.searching || !s.devices_at ? 'warn' : 'bad'}"></span> ${s.searching || !s.devices_at ? 'Looking for scanners…' : `No scanner found${d.error ? `: ${escapeHtml(d.error)}` : ''}.`}</div>`)}
        <div class="dash-grid">
            ${panel('Which scanners work', '', `<div class="info-grid prose">
                ${infoRow('USB, with a SANE driver', 'Most flatbed and document scanners of Canon, Epson, Fujitsu, HP, Plustek, Mustek, Avision and others: everything the <a href="http://www.sane-project.org/sane-supported-devices.html" target="_blank" rel="noopener">SANE project</a> lists as supported. Plug it into the machine Home Assistant runs on.')}
                ${infoRow('USB, driverless', `Printers with a scanner from about 2015 on (AirPrint, Mopria) speak IPP over USB and need no driver. ${c.driverless_usb ? 'On.' : '<b>Off</b> in the app\'s configuration.'} Press <i>Search again</i> after plugging one in.`)}
                ${infoRow('On the network', `Scanners that speak eSCL (AirScan): add each one with its address under <i>Network scanners</i> in the app's configuration. ${c.network_scanners.length ? `Now: ${c.network_scanners.map(n => `<span class="mono">${escapeHtml(n.name)}</span>`).join(', ')}.` : 'None added.'} The app can't search the network for them.`)}
                ${infoRow('Not supported', 'Scanners that only work with the maker\'s own Linux driver: many Brother models, older HP all-in-ones over USB (HPLIP), newer Epson models (Epson Scan 2). Over the network these often do work, as eSCL.')}
            </div>`)}
            ${panel('Good to know', '', `<div class="info-grid prose">
                ${infoRow('Every option', 'The Scan page shows every option the scanner\'s driver has, under <i>More options</i>. Options change with each other: the feeder has another paper size than the glass.')}
                ${infoRow('Batches', '<i>Feeder</i> scans until the feeder is empty. <i>Step by step</i> scans from the glass and waits for <i>Next page</i> after every page, until you press Stop. <i>Timed</i> does the same on a timer. <i>One page</i> adds a page at a time. Everything lands in the pages below, to save as one document.')}
                ${infoRow('Two-sided', 'A feeder that scans both sides has it as a source (Duplex). With a one-sided feeder: scan all fronts, turn the pile over, scan all backs, and press <i>Fronts + backs</i>.')}
                ${infoRow('The scanner\'s buttons', 'Not used: start a scan from here.')}
            </div>`)}
        </div>
        <div class="dash-grid">
            ${panel('What the app keeps', '', `<div class="info-grid prose">
                ${infoRow('Archive', `${plural(s.archive.documents, 'document')}, ${plural(s.archive.pages, 'page')}, ${fmtBytes(s.archive.size)} in <span class="mono">/data/archive</span>: a PDF per document, and an index with names, tags and the recognised text. Part of the app's backup in Home Assistant.${missing(s.archive.free) ? '' : ` ${fmtBytes(s.archive.free)} free.`}`)}
                ${infoRow('Pages not saved yet', `${plural(s.tray.pages, 'page')}${s.tray.pages ? `, ${fmtBytes(s.tray.size)}` : ''} in <span class="mono">/data/tray</span>: they stay when the app restarts.`)}
                ${infoRow('Copies', s.share.folder ? `<span class="mono">${escapeHtml(s.share.folder)}</span>${s.share.auto ? ', every saved document' : ', when you ask'}` : 'No folder chosen (Settings).')}
                ${infoRow('Logins', 'None. A network share is connected by Home Assistant, not by the app.')}
            </div>`)}
            ${panel('About', '', `<div class="info-grid prose">
                ${infoRow('App', `Scanner ${escapeHtml(s.version)} · <a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-scanner/DOCS.md" target="_blank" rel="noopener" class="plain-link"><u>documentation</u></a> · <a href="https://github.com/cyberjunky/home-assistant-apps/blob/main/app-scanner/CHANGELOG.md" target="_blank" rel="noopener" class="plain-link"><u>changes</u></a>`)}
                ${infoRow('Built on', 'SANE (sane-backends, sane-airscan, ipp-usb) for the scanners, Tesseract for the text, img2pdf and pypdf for the PDFs.')}
            </div>`)}
        </div>`;
    const button = document.getElementById('info-search');
    if (button) button.addEventListener('click', searchDevices);
}

onTab('info', () => {
    renderInfo();
    loadDevices().then(() => { if (activeTab === 'info') renderInfo(); }).catch(e => toast(e.message));
}, () => {});
