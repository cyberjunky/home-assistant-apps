# Security policy

## Scope and intent

This app looks at a KNX installation for its owner: it listens to the bus, reads the ETS project and
the KNX entities from Home Assistant, and reports what does not add up. It reads; it does not
control.

- **Who can reach it.** The pages and the API answer only Home Assistant's signed-in ingress proxy.
  The app runs on the machine's own network (finding KNX interfaces and routing need multicast), so
  its port 8023 is open on the LAN, but every request that does not come from ingress is refused.
- **What it sends to the bus.** With an interface of its own the app sends reads and nothing else:
  group value reads, device descriptor, property and memory reads, the questions which devices are in
  programming mode and which device has a serial number, and the handshake of a connection to a device. A guard in the connection refuses
  every other telegram (a write, a restart, a download, an address assignment), whatever asked for
  it. Without an interface of its own it sends nothing but a read through Home Assistant's `knx.read`.
- **Bus monitor mode** opens a second tunnel that can only listen; an interface in that mode sends
  nothing. Some interfaces close their other tunnels while it is on.
- **What it can change in Home Assistant.** The Home Assistant page can remove stray KNX entities
  and devices from the registries, and KNX entities made in the KNX panel. Each removal asks first.
  Entities written in YAML are never touched. Switch **Allow removing** off in the app's options and
  the app changes nothing in Home Assistant at all.
- **No logins.** The app asks for no account and stores none. KNX Secure interfaces and keyrings are
  not supported; Data Secure values are read through Home Assistant's connection, which holds the
  keys.
- **What it keeps.** The documents you add, the recorded telegrams (addresses, payloads, decoded values and times), the
  chosen interface, what devices answered and which findings you hid, all in the app's data. The
  recording says when lights went on and somebody was home: it is part of Home Assistant's backups,
  so treat those accordingly.
- **Documents you add.** A file is checked to be a PDF or a picture (JPEG, PNG, GIF, WebP; no SVG),
  stored in the app's data, and handed back sandboxed, so it has no say over the app's pages. A
  linked picture is loaded from its website by your browser, without a referrer. A link must start with `http://` or `https://`
  and opens in a new tab.
- **Text from the project and the bus** (names of group addresses, devices and entities) is shown as
  text, never as HTML.
- **What is downloaded.** Nothing. The page loads its fonts from Google Fonts; the chart library is
  bundled.

## Unofficial

KNX-Ray is an unofficial KNX application: it is not made by, or associated with, the KNX Association. KNX is a trademark of its owner.

## Reporting a vulnerability

Open an issue at https://github.com/cyberjunky/home-assistant-apps/issues, or for something sensitive
contact the maintainer privately.
