# Home Assistant App: Network

Network shows every device tracker in Home Assistant as a map of your network, served through ingress. Drag devices onto pages, group them, connect them and rename them; see what is online, what drops off the network, where your phones go, and how your internet connection is doing.

It reads Home Assistant itself (its device trackers, their devices and areas, its zones and the recorder's history) and your internet router, and needs no setup.

## Configuration

```yaml
history_days: 10
keep_days: 365
include_disabled: false
router: ""
log_level: info
```

| Option | Default | What it does |
|---|---|---|
| `history_days` | `10` | At start, how many days of the recorder's history are read to fill the statistics (Home Assistant keeps 10 by default). `0` reads none |
| `keep_days` | `365` | How long the app keeps its own record of every time a device went on or off the network |
| `include_disabled` | `false` | Also show trackers that are disabled in Home Assistant |
| `router` | | The address of your internet router (`192.168.1.1`, or `192.168.1.1:49000` for another port). Empty: the tracker that has the kind Router (or Firewall, or Modem) on the map |
| `log_level` | `info` | Amount of logging |

## The pages

- **Dashboard**: your internet connection (see below), then a page of your map, to look at. **Cards** shows every group of the page as a card in its colour, with its VLAN and subnet, how many of its devices are online and the devices themselves (away ones in red), in the order you drew them. **Drawing** shows the page as drawn, at a size you can read: drag to move around, Ctrl + wheel or the buttons to zoom. Either way, click a device for its details and a page tile to go in; **Edit this page** opens it in the editor. Below the map: how many devices are online, what needs a look (network gear that is away, two devices on one IP address, unstable, new and long-gone devices, devices on no map yet), the last 24 hours, the latest comings and goings.
- **Edit map**: the drawing. See below.
- **Devices**: every device in a table, to filter, search, sort and edit. Click one to edit it; tick several to give them one kind, network or connection.
- **Networks**: your networks and VLANs. A device is in the network of its IP address unless you pick another for it. The app suggests a network for every private /24 subnet it sees, with a guess of what it is for (IoT, cameras, servers, guests). A subnet's grid shows every address: online, away, free, or used by two devices at once.
- **Mobile**: the phones with the Home Assistant app. See below.
- **Stats**: who drops off the network, how often and when, from a day to a year: drops per day, availability, timelines, the week as a heatmap, the longest time away, the network gear's availability and every coming and going.

## Your internet connection

On top of the Dashboard, read from your router as long as the app runs:

- The router's model, maker and firmware, and whether it is connected.
- **Download** and **upload** now, and how much of the link that is.
- **Today**: what went down and up since midnight.
- **Connected for**: since when the internet connection is up (a reconnect starts it again).
- The **external address**, IPv4 and IPv6 (with the prefix in its tooltip), and the DNS servers.
- The **link**: how the router reaches the internet (Ethernet, DSL, cable…) and the speed of that link. On a router behind a fibre box this is the speed of the cable between them, not of your subscription.
- **Internet traffic**: live (the last 15 minutes, every 10 seconds), or the last 24 hours, 7 days or 30 days; and the **data per day** of the last 30 days.

The app reads the router the way Home Assistant's UPnP integration does: over UPnP (Internet Gateway Device), with the extra figures a FRITZ!Box gives there (its own speeds, 64-bit counters, IPv6), and the firmware from its TR-064 description. It needs no password. TR-069 can't be used for this: with TR-069 the router reports to its provider, and nothing on your network can ask it anything.

The router is the tracker that has the kind **Router** on the map (else Firewall, else Modem), or the address in the `router` option. When the dashboard says no router answers:

- **FRITZ!Box**: turn on *Home Network → Network → Network Settings → Transmit status information over UPnP*.
- **Other routers**: turn on UPnP. The app asks the router where its description is over SSDP, on UDP port 1900.

The traffic per minute is kept for 31 days, the data per hour for `keep_days`, in `/data/history.sqlite3`.

## The map

Drag a device from the list on the left onto a page, or double-click it. The shapes below the list are dragged the same way: a group, a text, and every kind of device without a tracker of its own, such as the internet, a cloud service or a switch. **Without an IP** has the gear that never has an address: an unmanaged switch, a patch panel, a PoE injector, a media converter, a powerline adapter, and a blank device to give any kind. Click a shape to give it a label, another kind, or a second line (a speed, an address); it says "no IP" until then. **Auto-place** puts every device that isn't on the page yet in a group per network, with the network gear (internet, modem, router, firewall, switches, access points) as a tree on top. If there are no networks yet, it offers to make them first.

- **Connect** two devices: drag the dot on the right edge of one onto the other. **Draw connections** draws what Home Assistant knows: the switch port or access point of a UniFi client, the device a device connects through, and the "Connected to" you set on a device. Wi-Fi clients are left out unless you ask, since there are many.
- **A group** takes the devices inside it along when you drag it; its corner resizes it. Give it a network and it shows its VLAN and subnet.
- **Move to its own page** (on a group): its devices go to a new page, and a tile stays with the number of devices and how many are online. Double-click the tile to go in; "← back" returns. Cables that went into the group end at the tile.
- Drag the background to move around, the wheel zooms, Shift+drag selects an area, arrows nudge, Delete removes from the page (never from Home Assistant), Ctrl+Z undoes.
- **Export**: the page as a PNG image, an SVG drawing (for Visio, draw.io or Inkscape), or printed (and saved as PDF).

## A device

Click a device on the map, or in the Devices table:

- **Name in Home Assistant**: **Rename** changes the tracker's name in Home Assistant's entity registry, and its device's name when that box is ticked. Clear it to get the integration's own name back. Trackers from `known_devices.yaml` aren't in the registry and can't be renamed from here.
- **Kind** and **icon**: guessed from the names, model and maker; pick another, or any of Home Assistant's icons in the bundled set. **Use in HA** gives the tracker that icon in Home Assistant too.
- **Network**, **connection** (wired, Wi-Fi, fiber, VPN, virtual), **connected to** and **port**, a **label on the map** and **notes**: kept by this app.
- What Home Assistant knows: IP, MAC (marked when it is a private, random address), host name, maker and model, area, Wi-Fi network and access point, switch port, when it was first seen, and its trackers with a link to their history.

**One device, many trackers**: trackers of different integrations with the same MAC address (UniFi, Nmap and your router all see your phone) are one device. It is online when any of them says so.

## Mobile

The GPS trackers of the Home Assistant app on your phones (and other trackers with a position):

- A map with your zones and the phone's **track**, or a **heatmap** of where it spends its time: the longer it stays, the warmer. The map tiles come from OpenStreetMap through Home Assistant's own tile proxy.
- How much of the time it is home, its **trips** (left, back, how long, which zones), the distance and the furthest point from home, the **battery**.
- **Habits**: the usual time it first leaves and last comes home, on weekdays and in the weekend.
- **Per day**: the hours in each zone.
- **Wi-Fi and GPS**: pick the phone's Wi-Fi tracker (one with the same name is picked for you) to see whether they agree. A phone that is home by GPS but often not on the Wi-Fi sleeps its Wi-Fi, uses mobile data, or has a new private address that shows up here as another device.

The positions are read from the recorder when you open the page, so they go back as far as Home Assistant keeps its history.

## What is kept, and where

- `/data/network.json`: the pages, what you set on devices and the networks.
- `/data/history.sqlite3`: every time a tracker went on or off the network, from the recorder at start and live after, for `keep_days`; and your internet traffic (per minute for 31 days, per hour for `keep_days`).

Both are in Home Assistant's backups of the app. Renames and icons are kept by Home Assistant itself.

## Development

The app runs without Home Assistant against a stand-in (`tools/mock_ha.py`) with about fifty trackers from UniFi, Nmap, Ping and the mobile app, two weeks of history, two phones with their days, and renaming:

```sh
python3 -m tools.mock_ha &
NETWORK_SUPERVISOR_URL=http://127.0.0.1:8124 SUPERVISOR_TOKEN=mock-token \
NETWORK_DATA_DIR=/tmp/network NETWORK_PORT=8099 python3 -m src
```

For the internet section, a stand-in router: `python3 -m tools.mock_router` plays a FRITZ!Box on port 8126 (`--generic` a plain UPnP router). Set `"router": "127.0.0.1:8126"` in the options file.

Open http://localhost:8099. The tests (`pytest`) use the same stand-ins. `tools/make_icons.py` writes the bundled icon set from the `@mdi/js` package.
