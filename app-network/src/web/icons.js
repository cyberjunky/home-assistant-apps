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
    return `<span class="kicon ${cls || ''}" data-fam="${kindFamily(kind)}"><svg viewBox="0 0 24 24" aria-hidden="true">${mdiSvg(icon)}</svg></span>`;
}
const hostIconHtml = (h, cls) => iconHtml(hostIcon(h), cls, kindOf(h));
const kindIconHtml = (kind, cls) => iconHtml(kindMdi(kind), cls, kind);

// What colour a kind has on the map: network gear, servers, people's devices, media, the smart home
const KIND_FAMILY = {
    internet: 'net', cloud: 'net', modem: 'net', router: 'net', firewall: 'net', switch: 'net', ap: 'net',
    server: 'srv', proxmox: 'srv', nas: 'srv', vm: 'srv', sbc: 'srv', ha: 'srv', ha_green: 'srv', ha_yellow: 'srv', ha_blue: 'srv',
    desktop: 'user', laptop: 'user', phone: 'user', tablet: 'user', watch: 'user', printer: 'user', scanner: 'user',
    tv: 'media', speaker: 'media', audio: 'media', console: 'media', xbox: 'media', playstation: 'media', nintendo: 'media',
    camera: 'home', doorbell: 'home', hub: 'home', zbt: 'home', zwa: 'home', iot: 'home', plug: 'home', light: 'home', thermostat: 'home',
    energy: 'home', car: 'home', appliance: 'home', vacuum: 'home', mower: 'home', miner: 'home', airco: 'home', heatpump: 'home',
    dishwasher: 'home', washer: 'home', dryer: 'home', fridge: 'home', oven: 'home', coffee: 'home', generic: 'other',
};
const kindFamily = kind => KIND_FAMILY[kind] || 'other';

// The shapes to drop on a map that aren't a tracker (a switch without a tracker of its own, the internet,
// a Connect stick on a USB port...), by family
// Short names where the full one doesn't fit the palette
const PALETTE_SHORT = {
    ha_green: 'HA Green', ha_yellow: 'HA Yellow', ha_blue: 'HA Blue', ha: 'HA (other)', zbt: 'ZBT-2', zwa: 'ZWA-2', console: 'Console',
    vm: 'VM', sbc: 'SBC (Pi)', nintendo: 'Switch', washer: 'Washer', fridge: 'Fridge', oven: 'Oven', coffee: 'Coffee', airco: 'Airco', proxmox: 'Proxmox', desktop: 'PC', audio: 'Audio', hub: 'Hub', iot: 'IoT / ESP', plug: 'Plug',
    energy: 'Energy', car: 'Car / charger', miner: 'Bitcoin miner', vacuum: 'Vacuum', mower: 'Mower', tv: 'TV', cloud: 'Cloud', ap: 'Access point',
};
const PALETTE_GROUPS = [
    {title: 'Network', kinds: ['internet', 'cloud', 'modem', 'router', 'firewall', 'switch', 'ap']},
    {title: 'Servers', kinds: ['server', 'proxmox', 'nas', 'vm', 'sbc', 'ha_green', 'ha_yellow', 'ha_blue', 'ha']},
    {title: 'Smart home', kinds: ['hub', 'zbt', 'zwa', 'iot', 'plug', 'light', 'thermostat', 'airco', 'heatpump', 'camera', 'doorbell', 'energy', 'car', 'miner']},
    {title: 'Devices', kinds: ['desktop', 'laptop', 'tablet', 'phone', 'watch', 'printer', 'scanner', 'generic']},
    {title: 'Media', kinds: ['tv', 'speaker', 'audio', 'xbox', 'playstation', 'nintendo', 'console']},
    {title: 'Appliances', kinds: ['dishwasher', 'washer', 'dryer', 'fridge', 'oven', 'coffee', 'vacuum', 'mower', 'appliance']},
];
