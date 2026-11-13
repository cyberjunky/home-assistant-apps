// Network dashboard - icons: Home Assistant's own (Material Design Icons, bundled in mdi.js).
// A host shows the icon picked here, else the icon it has in Home Assistant, else its kind's.

const mdiName = icon => String(icon || '').replace(/^mdi:/, '');
const hasMdi = icon => Boolean(MDI[mdiName(icon)]);
const mdiPath = icon => MDI[mdiName(icon)] || MDI.devices;
const kindMdi = kind => mdiName((app.kinds[kind] || {}).mdi || 'mdi:devices');
function haIcon(h) {
    const t = h.trackers.find(x => x.icon && hasMdi(x.icon));
    return t ? mdiName(t.icon) : '';
}
function hostIcon(h) {
    const own = meta(h.key).icon;
    return (own && hasMdi(own) ? mdiName(own) : '') || haIcon(h) || kindMdi(kindOf(h));
}
// The inside of a 24x24 svg
const mdiSvg = icon => `<path d="${mdiPath(icon)}"/>`;
function iconHtml(icon, cls, kind) {
    return `<span class="kicon ${cls || ''}" data-kind="${attr(kind || '')}"><svg viewBox="0 0 24 24" aria-hidden="true">${mdiSvg(icon)}</svg></span>`;
}
const hostIconHtml = (h, cls) => iconHtml(hostIcon(h), cls, kindOf(h));
const kindIconHtml = (kind, cls) => iconHtml(kindMdi(kind), cls, kind);

// What colour a kind has on the map: network gear, servers, people's devices, media, the smart home
const KIND_FAMILY = {
    internet: 'net', cloud: 'net', modem: 'net', router: 'net', firewall: 'net', switch: 'net', ap: 'net',
    server: 'srv', nas: 'srv', vm: 'srv', sbc: 'srv',
    desktop: 'user', laptop: 'user', phone: 'user', tablet: 'user', watch: 'user', printer: 'user',
    tv: 'media', speaker: 'media', console: 'media',
    camera: 'home', doorbell: 'home', hub: 'home', iot: 'home', plug: 'home', light: 'home', thermostat: 'home',
    energy: 'home', car: 'home', appliance: 'home', miner: 'home', generic: 'other',
};
const kindFamily = kind => KIND_FAMILY[kind] || 'other';

// The shapes to drop on a map that aren't a tracker: the internet, a cloud service, a switch without its own tracker...
const PALETTE_SHAPES = ['internet', 'cloud', 'modem', 'router', 'firewall', 'switch', 'ap', 'server', 'nas', 'generic'];
