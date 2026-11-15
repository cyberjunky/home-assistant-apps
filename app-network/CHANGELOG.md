# Changelog

## 0.1.1

**After updating:** nothing to do. The router is found by itself among the trackers that have the kind *Router*; the new option `router` sets its address when it isn't.

- **Dashboard** (was *Overview*): your internet connection on top, read from the router over UPnP without a password (a FRITZ!Box and most others): download and upload now, data today, how long it has been connected, the external IPv4 and IPv6 address, the link, and the traffic live, over 24 hours, 7 or 30 days and per day. Below it every group of a map page as a card with its network, how many are online and its devices.
- **Network map** (was *Map*): opens to look at, with zoom, export and a click for a device's details; **Edit** turns on changing it.
- **Shapes without an IP**: an unmanaged switch, patch panel, PoE injector, media converter, powerline adapter and a blank device for the gear that has no address.
- **Where devices connect**: the switch port and VLAN from the [MikroTik Network](https://github.com/cyberjunky/home-assistant-mikrotik) integration, and the FRITZ! device (box or repeater) from the FRITZ!Box integration. *Draw connections* puts the port on every cable, also on the ones you drew already.
- **Live on every page**: a device that comes or goes shows within a second, on every page.
- **More kinds**: Starlink, solar inverter, soundbar, fitness gear, MikroTik switches and access points, and more; the maker from the MAC address when no integration says.

## 0.1.0

- First version: every device tracker in Home Assistant as a map of your network. Pages to drag devices onto, groups with a name, colour and network, cable, Wi-Fi, fiber and VPN links, notes, undo, export as PNG, SVG or PDF; a group can move to a page of its own and stays on the overview as a tile to zoom into. Auto-place puts every device in a group per network with the network gear as a tree, and draws the connections Home Assistant knows of. Rename a tracker and its device in Home Assistant, and give it its icon there. Trackers with the same MAC address are one device. Kinds and icons guessed from names and makers. Networks and VLANs made from the subnets it sees, with every address of a subnet. Statistics of who drops off the network, kept by the app for a year and filled from the recorder at start. Phones: track and heatmap on a map, time per zone, trips, habits, battery, and whether Wi-Fi and GPS agree.
