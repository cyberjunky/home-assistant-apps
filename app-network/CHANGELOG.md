# Changelog

## 0.1.6

**After updating:** nothing to do.

- **Wi-Fi networks as groups:** *Auto-place* can put Wi-Fi devices in a group per Wi-Fi network (SSID, from UniFi, MikroTik Network or the FRITZ!Box) instead of the group of their IP network. It asks when Home Assistant knows a Wi-Fi network of a device; wired devices stay in their network's group.
- **The right address:** when the trackers of one device disagree, the address from a router or controller (MikroTik Network, UniFi, the FRITZ!Box) wins over a network scan (arp-scan, Nmap, Ping). A scan sees whatever answers: a FRITZ!Box answers for its VPN connections too, and arp-scan gave it one of those addresses.
- **Renaming a device too:** with *Rename its device too* ticked, a tracker named after its device (most integrations, arp-scan too) showed the name twice ("MikroTik hAP mini MikroTik hAP mini"): the device and the tracker both got it. The device gets the name now and the tracker gives up its own. A name already doubled is fixed by renaming once more.

## 0.1.5

**After updating:** nothing to do. With [MikroTik Network](https://github.com/cyberjunky/home-assistant-mikrotik) 0.1.2 or later, a FRITZ!Box shows its own address.

- **A device on several addresses:** a FRITZ!Box answers on its own address and on others (here .181 to .187 besides .254), and showed whichever a tracker saw first. It now gets the address MikroTik Network chose knowing them all (its DHCP lease, else the default gateway's), and its details list the other addresses. For a router they are called *Answers for*: the addresses it answers on behalf of others, mostly VPN (WireGuard) connections, shown as a range (192.168.2.181–187). The Networks page shows them in the address grid as *VPN*, not as free.

## 0.1.4

**After updating:** nothing to do. With [MikroTik Network](https://github.com/cyberjunky/home-assistant-mikrotik) 0.1.1 or later, every device on a MikroTik port gets its port.

- **Every device on its port:** an integration that lists on each switch port and access point which devices are plugged into it (MikroTik Network does) now gives that port to every device, whichever integration tracks it (arp-scan, Nmap, the FRITZ!Box). Before, only devices the MikroTik tracked itself got their port; behind a FRITZ!Box that is only a few.
- **A switch's ports in its details:** a square per port (in use, free, off, the uplink outlined), how many are in use and free, and which device is on which port; for an access point its Wi-Fi networks and how many clients each has. It follows live when a device moves to another port.

## 0.1.3

**After updating:** nothing to do.

- **A device keeps its name:** when several integrations track one device, a tracker named only after its MAC or IP address no longer names it. The MikroTik Network integration tracks your router from ARP without a host name; the router showed under its MAC address.
- **Documentation:** already tracking with arp-scan or Nmap? Hide the FRITZ!Box trackers instead of disabling them, so this app still sees which FRITZ! device a client is on.

## 0.1.2

**After updating:** nothing to do.

- **FRITZ!Box and repeaters found:** a device on a FRITZ!Repeater (or the box) is linked to it also when the repeater is tracked under its MAC address or host name, by another integration such as arp-scan. The app finds the repeater through the device the FRITZ!Box integration makes for it. Needs *network device tracking* on in the FRITZ!Box integration.

## 0.1.1

**After updating:** nothing to do. The router is found by itself among the trackers that have the kind *Router*; the new option `router` sets its address when it isn't.

- **Dashboard** (was *Overview*): your internet connection on top, read from the router over UPnP without a password (a FRITZ!Box and most others): download and upload now, data today, how long it has been connected, the external IPv4 and IPv6 address, the link, and the traffic live, over 24 hours, 7 or 30 days and per day. Below it every group of a map page as a card with its network, how many are online and its devices.
- **Network map** (was *Map*): opens to look at, with zoom, export and a click for a device's details; **Edit** turns on changing it.
- **Shapes without an IP**: an unmanaged switch, patch panel, PoE injector, media converter, powerline adapter and a blank device for the gear that has no address.
- **Where devices connect**: the switch port and VLAN from the [MikroTik Network](https://github.com/cyberjunky/home-assistant-mikrotik) integration, and the FRITZ! device (box or repeater) from the FRITZ!Box integration. *Draw connections* puts the port on every cable, also on the ones you drew already. The Info page shows which of these integrations (and UniFi Network) are in use, and the Dashboard points to them while none says where your devices plug in.
- **Live on every page**: a device that comes or goes shows within a second, on every page.
- **More kinds**: Starlink, solar inverter, soundbar, fitness gear, MikroTik switches and access points, and more; the maker from the MAC address when no integration says.

## 0.1.0

- First version: every device tracker in Home Assistant as a map of your network. Pages to drag devices onto, groups with a name, colour and network, cable, Wi-Fi, fiber and VPN links, notes, undo, export as PNG, SVG or PDF; a group can move to a page of its own and stays on the overview as a tile to zoom into. Auto-place puts every device in a group per network with the network gear as a tree, and draws the connections Home Assistant knows of. Rename a tracker and its device in Home Assistant, and give it its icon there. Trackers with the same MAC address are one device. Kinds and icons guessed from names and makers. Networks and VLANs made from the subnets it sees, with every address of a subnet. Statistics of who drops off the network, kept by the app for a year and filled from the recorder at start. Phones: track and heatmap on a map, time per zone, trips, habits, battery, and whether Wi-Fi and GPS agree.
